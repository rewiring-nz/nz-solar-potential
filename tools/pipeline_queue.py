"""Local queue CLI and process workers for the existing regional pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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

from src.local_queue import (Coordinator, QueueClient, QueueError, SOURCE_ENVIRONMENT_KEYS,
                             TERMINAL, atomic_json, validate_task)

PROVENANCE_FILES = ("config.py", "tools/pipeline.py", "tools/pipeline_queue.py", "src/local_queue.py",
                    "src/region_ids.py")


def provenance(workspace: Path) -> dict[str, Any]:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace,
                            capture_output=True, text=True, check=True).stdout.strip()
    files = {name: hashlib.sha256((workspace / name).read_bytes()).hexdigest()
             for name in PROVENANCE_FILES}
    sources = sorted(
        path for directory in ("src", "tools")
        for path in (workspace / directory).rglob("*")
        if path.is_file() and path.suffix in (".py", ".sh")
    )
    tree = hashlib.sha256()
    for path in sources:
        name = path.relative_to(workspace).as_posix().encode()
        tree.update(len(name).to_bytes(8, "big"))
        tree.update(name)
        tree.update(hashlib.sha256(path.read_bytes()).digest())
    files["__source_tree__"] = tree.hexdigest()
    return {"commit": commit, "files": files}


def make_task(workspace: Path, region: str, python: str, *, port: int | None = None,
              no_open_browser: bool = False, step_offset: int = 0,
              paid_referee: bool = False, no_preview_server: bool = False,
              include_imagery: bool = True) -> dict[str, Any]:
    from tools.pipeline import _safe_area
    workspace = workspace.resolve()
    # Do not resolve the interpreter symlink: that discards virtualenv identity.
    executable = str(Path(python).expanduser().absolute())
    if not Path(executable).is_file():
        raise QueueError(f"Python executable not found: {executable}")
    task = {
        "id": uuid.uuid4().hex,
        "region": _safe_area(region),
        "workspace": str(workspace),
        "python": executable,
        "options": {"port": port, "no_open_browser": no_open_browser, "step_offset": step_offset,
                    "paid_referee": paid_referee, "no_preview_server": no_preview_server,
                    "include_imagery": include_imagery},
        "source_environment": {key: os.environ[key] for key in SOURCE_ENVIRONMENT_KEYS
                               if key in os.environ},
        "provenance": provenance(workspace),
    }
    bounds_path = os.environ.get("SOLAR_RASTER_JOB_BOUNDS")
    frozen_bounds_path = os.environ.get("SOLAR_RASTER_JOB_BOUNDS_SNAPSHOT")
    if bounds_path or frozen_bounds_path:
        if os.name != "posix":
            raise QueueError("Raster admission requires POSIX.")
        if not no_preview_server:
            raise QueueError("Raster admission requires --no-preview-server.")
        from src.raster_job_bounds import (
            load_frozen_job_bounds, load_job_bounds, processing_for_snapshot,
        )
        from src.raster_plan_handoff import RasterPlanError, optional_approved_raster_plan
        source_environment = task["source_environment"]
        try:
            approved = optional_approved_raster_plan(
                source_environment, [region], allow_mapped=True)
            if approved is None:
                raise QueueError("Raster admission requires an approved raster snapshot.")
        except RasterPlanError as exc:
            raise QueueError(f"Raster approval invalid: {exc}") from exc
        loader = load_frozen_job_bounds if frozen_bounds_path else load_job_bounds
        task["raster_admission"] = loader(
            Path(frozen_bounds_path or bounds_path), region=region, workspace=workspace,
            plan_digest=source_environment["SOLAR_RASTER_PLAN_SHA256"],
            include_imagery=include_imagery, paid_referee=paid_referee)
        processing_for_snapshot(task["raster_admission"], approved, region)
    return validate_task(task)


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


def process_group_stopped(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def recover_stale_raster_workspace(ownership: Path) -> bool:
    """Recover a queue-owned raster lock only when its recorded processes have stopped."""
    if os.name != "posix" or not ownership.is_dir():
        return False
    import fcntl
    with (ownership.parent / "pipeline-workspace-recovery.lock").open("a+b") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        if not ownership.is_dir():
            return False
        try:
            owner = json.loads((ownership / "owner.json").read_text())
        except (OSError, ValueError) as exc:
            raise QueueError(f"Workspace conflict: cannot inspect lock {ownership}: {exc}") from exc
        if not isinstance(owner, dict) or not owner.get("reservation_id"):
            return False
        pid, group = owner.get("pid"), owner.get("process_group")
        registry, reservation_id = owner.get("reservation_registry"), owner["reservation_id"]
        if type(pid) is not int or pid <= 0:
            raise QueueError(f"Cannot safely recover incomplete raster workspace owner at {ownership}")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        except PermissionError:
            return False
        else:
            return False
        if (type(group) is not int or group <= 0
                or not isinstance(registry, str) or not Path(registry).is_absolute()
                or not isinstance(reservation_id, str)
                or not re.fullmatch(r"[a-f0-9]{32}", reservation_id)):
            raise QueueError(f"Cannot safely recover incomplete raster workspace owner at {ownership}")
        if not process_group_stopped(group):
            raise QueueError(f"Cannot recover {ownership}: process group {group} is still running")
        from src.raster_reservations import DiskReservationError, DiskReservations

        registry_path = Path(registry)
        record = registry_path / f"{reservation_id}.json"
        if not registry_path.is_dir():
            raise QueueError(f"Cannot recover raster reservation: registry missing at {registry_path}")
        if record.exists() or record.is_symlink():
            try:
                DiskReservations(registry_path).recover_orphan(reservation_id, consumers_stopped=True)
            except (DiskReservationError, OSError, ValueError) as exc:
                raise QueueError(f"Cannot recover raster reservation: {exc}") from exc
        (ownership / "owner.json").unlink()
        ownership.rmdir()
        return True


def upcoming_raster_plans(client: QueueClient, cache_dir: Path, limit: int) -> list[dict]:
    """Read queued approved plans as eviction preferences, never as leases."""
    from src.raster_plan_handoff import RasterPlanError, optional_approved_raster_plan, plan_for_regions

    if limit <= 0:
        return []
    status = client.call("status")
    if not isinstance(status, dict) or not isinstance(status.get("tasks"), list):
        raise QueueError("Cannot read queued raster plans for cache cleanup.")
    plans = []
    for record in status["tasks"]:
        if not isinstance(record, dict) or record.get("status") != "pending":
            continue
        task = record.get("task")
        if not isinstance(task, dict) or not isinstance(task.get("raster_admission"), dict):
            continue
        cache = task["raster_admission"].get("cache")
        if not isinstance(cache, dict) or not isinstance(cache.get("directory"), str):
            raise QueueError("Queued raster task has an invalid frozen cache directory.")
        if Path(cache["directory"]).resolve() != cache_dir.resolve():
            continue
        try:
            snapshot = optional_approved_raster_plan(
                task.get("source_environment", {}), [task["region"]], allow_mapped=True)
            if snapshot is None:
                raise RasterPlanError("approved raster plan is missing")
            batch = plan_for_regions(snapshot, [task["region"]])["batch"]
        except (RasterPlanError, KeyError, TypeError, ValueError) as exc:
            print(f"[queue] WARNING: skipping invalid upcoming raster preference "
                  f"for {task.get('id', 'unknown task')}: {exc}", file=sys.stderr, flush=True)
            continue
        if batch is not None:
            plans.append(batch)
            if len(plans) == limit:
                break
    return plans


def report_raster_cleanup(results: dict) -> None:
    for phase in ("partials", "assets", "proactive"):
        result = results.get(phase)
        if result is None:
            continue
        removed = result["removed"]
        details = f"{len(removed)} entries, {sum(item['bytes'] for item in removed)} bytes"
        if "target_reached" in result:
            details += "; target " + ("reached" if result["target_reached"] else "not reached")
        print(f"[queue] Raster {phase} cleanup: {details}.", flush=True)


def execute(task: dict[str, Any], attempt: str, log_path: Path, client: QueueClient,
            worker: str) -> int:
    workspace = Path(task["workspace"])
    if provenance(workspace) != task["provenance"]:
        raise QueueError("Code/configuration changed after submission; submit a new task.")
    ownership = workspace / "data" / "pipeline-workspace.lock"
    ownership.parent.mkdir(parents=True, exist_ok=True)
    if recover_stale_raster_workspace(ownership):
        print("[queue] Recovered a stopped raster job's reservation and workspace lock.", flush=True)
    try:
        ownership.mkdir()
    except FileExistsError as exc:
        raise QueueError(
            f"Workspace conflict: {ownership}. Coordinate with its owner; after a crash, "
            "stop old processes and inspect owner.json before manually removing the lock."
        ) from exc
    process: subprocess.Popen[Any] | None = None
    launch_attempted = False
    admission = None
    try:
        owner = {"pid": os.getpid(), "task": task["id"], "attempt": attempt}
        atomic_json(ownership / "owner.json", owner)
        if "raster_admission" in task:
            if os.name != "posix" or not task["options"].get("no_preview_server", False):
                raise QueueError("Raster admission requires POSIX and --no-preview-server.")
            from src.raster_job_bounds import (
                job_resources, processing_for_snapshot, validate_job_bounds,
            )
            from src.raster_job_admission import admission_binding
            from src.raster_cache import RasterCacheError
            from src.raster_plan_handoff import (
                RasterPlanError, optional_approved_raster_plan, plan_for_regions,
            )
            from src.raster_reservations import (
                DiskReservationError, DiskReservations,
            )

            environment = task.get("source_environment", {})
            try:
                approved = optional_approved_raster_plan(
                    environment, [task["region"]], allow_mapped=True)
            except RasterPlanError as exc:
                raise QueueError(f"Raster approval invalid: {exc}") from exc
            if approved is None:
                raise QueueError("Raster admission requires an approved raster snapshot.")
            bounds = validate_job_bounds(
                task["raster_admission"], region=task["region"], workspace=workspace,
                plan_digest=environment["SOLAR_RASTER_PLAN_SHA256"],
                include_imagery=task["options"]["include_imagery"],
                paid_referee=task["options"]["paid_referee"], frozen=True)
            processing_for_snapshot(bounds, approved, task["region"])
            cache, requirements = job_resources(bounds)
            batch = plan_for_regions(approved, [task["region"]])["batch"]
            plan = batch or {"inputs": [], "assets": [], "export_fallbacks": []}
            if (bounds.get("freshness") is None
                    and any(item.get("source") is not None for item in plan["inputs"])):
                raise QueueError("Mapped task has no frozen metadata refresh interval; resubmit it.")
            if bounds.get("freshness") is not None:
                from src.raster_freshness import verify_due_metadata
                try:
                    refreshed = verify_due_metadata(plan, bounds["freshness"]["metadata_refresh_s"])
                except (OSError, KeyError, TypeError, ValueError) as exc:
                    raise QueueError(f"Raster metadata refresh failed: {exc}") from exc
                if refreshed:
                    print("[queue] Approved raster metadata rechecked; sources unchanged.",
                          flush=True)
            registry = cache.directory / "reservations"
            reservations = DiskReservations(registry)
            admission = reservations.reserve(
                lambda: cache.inspect_plan(plan), requirements,
                consumers_stopped=lambda: not launch_attempted or (
                    process is not None and process.poll() is not None
                    and process_group_stopped(process.pid)),
                binding=admission_binding(bounds), exclusive=True)
            try:
                forecast = admission.__enter__()
            except (DiskReservationError, RasterCacheError) as exc:
                admission = None
                raise QueueError(f"Raster disk admission failed: {exc}") from exc
            owner["reservation_id"] = forecast["reservation_id"]
            owner["reservation_registry"] = str(registry.resolve())
            atomic_json(ownership / "owner.json", owner)
            print(f"[queue] Cooperative disk reservation {forecast['reservation_id']} admitted.",
                  flush=True)
        command = [task["python"], "-m", "tools.pipeline_queue", "execute"]
        env = {key: value for key, value in os.environ.items()
               if key not in SOURCE_ENVIRONMENT_KEYS and key not in {
                   "SOLAR_RASTER_ACTIVE_ADMISSION", "SOLAR_RASTER_JOB_BOUNDS_SNAPSHOT"}}
        env.update(task.get("source_environment", {}))
        if admission is not None:
            env["SOLAR_RASTER_ACTIVE_ADMISSION"] = json.dumps(
                {"bounds": bounds, "reservation_id": forecast["reservation_id"]},
                separators=(",", ":"), allow_nan=False)
        env["PYTHONUNBUFFERED"] = "1"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log:
            launch_attempted = True
            process = subprocess.Popen(
                command, cwd=workspace, stdin=subprocess.PIPE, stdout=log,
                stderr=subprocess.STDOUT, text=True, start_new_session=(os.name == "posix"),
                env=env,
            )
            if os.name == "posix":
                owner["process_group"] = process.pid
                atomic_json(ownership / "owner.json", owner)
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
        original_error = sys.exc_info()[1]
        try:
            if process is not None:
                stop_process(process)
            if os.name == "posix" and launch_attempted and (
                process is None or not process_group_stopped(process.pid)
            ):
                raise QueueError(
                    f"Workspace consumers may still be running; lock retained at {ownership}. "
                    "Stop all consumers and inspect owner.json before manually removing the lock."
                )
        finally:
            if admission is not None:
                from src.raster_reservations import DiskReservationError
                try:
                    admission.__exit__(*sys.exc_info())
                except DiskReservationError as exc:
                    detail = (f" Original error: {type(original_error).__name__}: {original_error}."
                              if original_error is not None else "")
                    raise QueueError(f"{exc}; workspace lock retained at {ownership}. "
                                     f"Confirm all consumers stopped before manual recovery.{detail}") from exc
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
                         no_open_browser=args.no_open_browser, step_offset=args.step_offset,
                         paid_referee=bool(getattr(args, "paid_referee", False)),
                         no_preview_server=bool(getattr(args, "no_preview_server", False)),
                         include_imagery=getattr(args, "include_imagery", True))
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
