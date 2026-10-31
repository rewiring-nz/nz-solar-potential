"""Local queue CLI and process workers for the existing regional pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.local_queue import Coordinator, QueueClient, QueueError, TERMINAL, atomic_json, validate_task

PROVENANCE_FILES = ("config.py", "tools/pipeline.py", "tools/pipeline_queue.py", "src/local_queue.py")


def provenance(workspace: Path) -> dict[str, Any]:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace,
                            capture_output=True, text=True, check=True).stdout.strip()
    files = {name: hashlib.sha256((workspace / name).read_bytes()).hexdigest()
             for name in PROVENANCE_FILES}
    return {"commit": commit, "files": files}


def make_task(workspace: Path, region: str, python: str, *, port: int | None = None,
              no_open_browser: bool = False, step_offset: int = 0) -> dict[str, Any]:
    from tools.pipeline import _safe_area
    workspace = workspace.resolve()
    # Do not resolve the interpreter symlink: that discards virtualenv identity.
    executable = str(Path(python).expanduser().absolute())
    if not Path(executable).is_file():
        raise QueueError(f"Python executable not found: {executable}")
    return validate_task({
        "id": uuid.uuid4().hex,
        "region": _safe_area(region),
        "workspace": str(workspace),
        "python": executable,
        "options": {"port": port, "no_open_browser": no_open_browser, "step_offset": step_offset},
        "provenance": provenance(workspace),
    })


def stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()


def execute(task: dict[str, Any], attempt: str, log_path: Path, client: QueueClient,
            worker: str) -> int:
    workspace = Path(task["workspace"])
    if provenance(workspace) != task["provenance"]:
        raise QueueError("Code/configuration changed after submission; submit a new task.")
    ownership = workspace / "data" / "pipeline-workspace.lock"
    ownership.parent.mkdir(parents=True, exist_ok=True)
    try:
        ownership.mkdir()
    except FileExistsError as exc:
        raise QueueError(
            f"Workspace conflict: {ownership}. Coordinate with its owner; after a crash, "
            "stop old processes and inspect owner.json before manually removing the lock."
        ) from exc
    process: subprocess.Popen[Any] | None = None
    try:
        atomic_json(ownership / "owner.json", {"pid": os.getpid(), "task": task["id"], "attempt": attempt})
        command = [task["python"], "-m", "tools.pipeline_queue", "execute"]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command, cwd=workspace, stdin=subprocess.PIPE, stdout=log,
                stderr=subprocess.STDOUT, text=True, start_new_session=(os.name == "posix"),
                env=dict(os.environ, PYTHONUNBUFFERED="1"),
            )
            assert process.stdin is not None
            process.stdin.write(json.dumps(task))
            process.stdin.close()
            last_heartbeat = 0.0
            with log_path.open(encoding="utf-8") as output:
                while True:
                    text = output.read()
                    if text:
                        print(text, end="", flush=True)
                    returncode = process.poll()
                    if returncode is not None:
                        print(output.read(), end="", flush=True)
                        return returncode
                    if time.monotonic() - last_heartbeat >= 5:
                        client.call("heartbeat", id=task["id"], attempt=attempt, worker=worker)
                        last_heartbeat = time.monotonic()
                    time.sleep(0.1)
    finally:
        if process is not None:
            stop_process(process)
        (ownership / "owner.json").unlink(missing_ok=True)
        ownership.rmdir()


def work(directory: Path, worker: str) -> int:
    client = QueueClient(directory)
    failed = False
    while True:
        record = client.call("claim", worker=worker)
        if record.get("task") is None:
            if record["finished"]:
                return 1 if failed else 0
            time.sleep(0.1)
            continue
        task, attempt = record["task"], record["attempt"]
        log = directory / "attempts" / f"{task['id']}-{attempt}.log"
        print(f"[queue {worker}] START {task['region']}; log: {log}", flush=True)
        try:
            code = execute(task, attempt, log, client, worker)
        except (OSError, ValueError, QueueError, subprocess.SubprocessError) as exc:
            error = str(exc)
        except KeyboardInterrupt:
            client.call("fail", id=task["id"], attempt=attempt, worker=worker,
                        error="Worker interrupted; inspect the attempt log before resubmitting.")
            raise
        else:
            error = "" if code == 0 else f"Pipeline exited {code}; inspect {log}"
        if error:
            failed = True
            client.call("fail", id=task["id"], attempt=attempt, worker=worker, error=error)
            print(f"[queue {worker}] FAIL {task['region']}: {error}", file=sys.stderr, flush=True)
        else:
            client.call("complete", id=task["id"], attempt=attempt, worker=worker)
            print(f"[queue {worker}] PASS {task['region']}; numbered report details: {log}", flush=True)


def run_local(args: argparse.Namespace, area: str, workspace: Path) -> int:
    from tools.pipeline import _python_executable

    directory = (Path(args.queue_dir) if args.queue_dir else
                 workspace / "data" / "pipeline_queues" / uuid.uuid4().hex).resolve()
    processes: list[subprocess.Popen[Any]] = []
    try:
        python = _python_executable(args.python)
        task = make_task(workspace, area, str(python), port=args.port,
                         no_open_browser=args.no_open_browser, step_offset=args.step_offset)
        with Coordinator(directory):
            client = QueueClient(directory)
            client.call("submit", task=task)
            print(f"[queue] Local queue: {directory}; workers: {args.workers}. No cloud queue is used.", flush=True)
            print("[queue] Tasks sharing a checkout are serialized to protect shared inputs and combined outputs.", flush=True)
            try:
                for number in range(args.workers):
                    processes.append(subprocess.Popen(
                        [str(python), "-m", "tools.pipeline_queue", "worker", "--queue-dir", str(directory),
                         "--worker", f"local-{number + 1}"],
                        cwd=workspace, start_new_session=(os.name == "posix"),
                    ))
                while True:
                    records = client.call("status")["tasks"]
                    if all(r["status"] in TERMINAL for r in records):
                        break
                    if any(p.poll() is not None and p.returncode != 0 for p in processes):
                        raise QueueError("A local worker exited unexpectedly; run interrupted, inspect queue state and logs.")
                    if all(p.poll() is not None for p in processes):
                        raise QueueError("Workers exited before all tasks completed.")
                    time.sleep(0.1)
                for process in processes:
                    process.wait(timeout=15)
                failures = [r for r in records if r["status"] != "succeeded"]
                for record in failures:
                    print(f"[queue] {record['status'].upper()} {record['task']['region']}: {record.get('error', '')}",
                          file=sys.stderr)
                print(f"[queue] Persistent task state: {directory / 'state.json'}", flush=True)
                return 1 if failures else 0
            finally:
                for process in processes:
                    stop_process(process)
    except (OSError, ValueError, QueueError, subprocess.SubprocessError) as exc:
        print(f"[queue] FAIL: {exc}\nQueue evidence: {directory}", file=sys.stderr)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("serve", "submit", "worker", "status"):
        sub = subparsers.add_parser(action)
        sub.add_argument("--queue-dir", type=Path, required=True)
        if action == "serve":
            sub.add_argument("--port", type=int, default=0, help="loopback coordinator port; default: free port")
        if action == "submit":
            sub.add_argument("region")
            sub.add_argument("--workspace", type=Path, default=ROOT)
            sub.add_argument("--python", default=sys.executable)
            sub.add_argument("--no-open-browser", action="store_true")
        if action == "worker":
            sub.add_argument("--worker", default=f"local-{os.getpid()}")
    subparsers.add_parser("execute", help="internal task subprocess; reads its specification from stdin")
    args = parser.parse_args()
    if args.action == "execute":
        from tools.pipeline import _run_local
        task = validate_task(json.load(sys.stdin))
        if Path.cwd().resolve() != Path(task["workspace"]).resolve() or provenance(Path.cwd()) != task["provenance"]:
            raise QueueError("Execution checkout/configuration does not match the submitted task.")
        return _run_local(argparse.Namespace(python=task["python"], **task["options"]), task["region"])
    directory = args.queue_dir.resolve()
    if args.action == "serve":
        with Coordinator(directory, args.port):
            QueueClient(directory).call("status")
            print(f"Local coordinator ready: {directory}. Stop with Ctrl+C; no cloud credentials required.", flush=True)
            while True:
                time.sleep(1)
    if args.action == "submit":
        task = make_task(args.workspace, args.region, args.python, no_open_browser=args.no_open_browser)
        print(json.dumps(QueueClient(directory).call("submit", task=task)))
        return 0
    if args.action == "worker":
        return work(directory, args.worker)
    print(json.dumps(QueueClient(directory).call("status"), indent=2))
    return 0


if __name__ == "__main__":
    def interrupt(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Local queue interrupted; inspect state.json and attempt logs before resubmitting.", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, QueueError, subprocess.SubprocessError) as exc:
        print(f"Local queue error: {exc}", file=sys.stderr)
        raise SystemExit(2)
