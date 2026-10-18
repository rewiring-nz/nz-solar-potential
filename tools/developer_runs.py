"""Supervise isolated developer cloud builds on the shared build VM."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config

_USER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")
_ACTIVE = {"queued", "running", "gating", "validating", "publishing", "cancelling"}
_TERMINAL = {"succeeded", "gate_failed", "failed", "cancelled", "interrupted"}
_DASHBOARD_ROOT = "run-dashboard"
_PUBLIC_LOG_ROOT = "devel/runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_root() -> Path:
    return Path(
        os.environ.get("SOLAR_DEVELOPER_RUNS_ROOT", "~/solar-map-developer-runs")
    ).expanduser()


def _workspaces_root() -> Path:
    return Path(
        os.environ.get("SOLAR_DEVELOPER_WORKSPACES_ROOT", "~/solar-map-developers")
    ).expanduser()


def _registry_path() -> Path:
    return _state_root() / "registry.json"


def _locked_registry() -> tuple[Any, list[dict[str, Any]]]:
    root = _state_root()
    root.mkdir(parents=True, exist_ok=True)
    lock = (root / "registry.lock").open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    path = _registry_path()
    try:
        records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, json.JSONDecodeError) as exc:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        raise RuntimeError(f"developer run registry is unreadable: {path}") from exc
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        raise RuntimeError(f"developer run registry has invalid structure: {path}")
    return lock, records


def _save_registry(lock: Any, records: list[dict[str, Any]]) -> None:
    path = _registry_path()
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        json.dump(records, temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary_path, path)
    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    lock.close()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _proc_start_ticks(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None
    fields = stat[stat.rfind(")") + 2:].split()
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def _process_group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_orphaned_child(record: dict[str, Any]) -> None:
    pgid = record.get("child_pgid")
    child_pid = record.get("child_pid")
    expected_start = record.get("child_start_ticks")
    if (
        not isinstance(pgid, int) or pgid != child_pid
        or not isinstance(expected_start, int)
        or _proc_start_ticks(child_pid) != expected_start
    ):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while _process_group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.25)
    if _process_group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 10
        while _process_group_alive(pgid) and time.monotonic() < deadline:
            time.sleep(0.25)


def _reconcile(records: list[dict[str, Any]]) -> bool:
    changed = False
    for record in records:
        if record.get("status") not in _ACTIVE:
            continue
        pid = record.get("pid")
        expected_start = record.get("pid_start_ticks")
        if (
            isinstance(pid, int) and _pid_alive(pid)
            and isinstance(expected_start, int)
            and _proc_start_ticks(pid) == expected_start
        ):
            continue
        _stop_orphaned_child(record)
        record.update(
            status="interrupted",
            finished_at=_now(),
            error="Supervisor process is no longer running; inspect the retained run log.",
            updated_at=_now(),
        )
        changed = True
    return changed


def _dashboard_snapshot(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    retention_days = config.CLOUD_SHARED_BUILD["preview_retention_days"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    visible = []
    for record in records:
        if record.get("status") not in _TERMINAL:
            visible.append(record)
            continue
        finished = record.get("finished_at")
        if not isinstance(finished, str):
            visible.append(record)
            continue
        try:
            expired = datetime.fromisoformat(finished.replace("Z", "+00:00")) < cutoff
        except ValueError:
            expired = False
        if not expired:
            visible.append(record)
    recent = sorted(
        visible, key=lambda item: item.get("started_at", ""), reverse=True,
    )[:200]
    for record in recent:
        rows.append({
            key: record.get(key)
            for key in (
                "run_id", "user", "regions", "commit", "status", "stage",
                "started_at", "updated_at", "finished_at", "preview_url", "log_url",
            )
        })
    return {"updated_at": _now(), "runs": rows}


def _publish_dashboard(records: list[dict[str, Any]]) -> None:
    bucket = config.CLOUD_SHARED_BUILD["preview_bucket"].rstrip("/")
    root = Path(__file__).resolve().parents[1]
    snapshot = _dashboard_snapshot(records)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", delete=False,
    ) as status_file:
        status_path = Path(status_file.name)
        json.dump(snapshot, status_file)
    try:
        subprocess.run(
            [
                "gcloud", "storage", "cp", "-q",
                "--cache-control=no-cache,max-age=0",
                str(status_path), f"{bucket}/{_DASHBOARD_ROOT}/status.json",
            ],
            check=True, text=True, capture_output=True,
        )
    finally:
        status_path.unlink(missing_ok=True)
    for name, target in (
        ("developer-runs.html", "index.html"),
        ("developer-runs.js", "developer-runs.js"),
    ):
        subprocess.run(
            [
                "gcloud", "storage", "cp", "-q",
                "--cache-control=no-cache,max-age=0",
                str(root / "tools" / name),
                f"{bucket}/{_DASHBOARD_ROOT}/{target}",
            ],
            check=True, text=True, capture_output=True,
        )


def _change(run_id: str, **updates: Any) -> dict[str, Any]:
    lock, records = _locked_registry()
    record = next((item for item in records if item.get("run_id") == run_id), None)
    if record is None:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        raise ValueError(f"unknown developer run: {run_id}")
    record.update(updates, updated_at=_now())
    _save_registry(lock, records)
    _try_publish_dashboard()
    return record


def _try_publish_dashboard() -> None:
    root = _state_root()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "dashboard.lock").open("a+") as dashboard_lock:
        fcntl.flock(dashboard_lock.fileno(), fcntl.LOCK_EX)
        lock, records = _locked_registry()
        _reconcile(records)
        _save_registry(lock, records)
        try:
            _publish_dashboard(records)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"WARNING: run dashboard upload failed: {exc}", file=sys.stderr, flush=True)


def _publish_log(record: dict[str, Any]) -> None:
    log_path = Path(record["log_path"])
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-250:]
        excerpt = "\n".join(lines) + "\n"
        excerpt = re.sub(
            r"(?i)(api[_-]?key|token|password)([=: ]+)[^\s&,]+",
            r"\1\2[redacted]",
            excerpt,
        )
        excerpt = re.sub(
            r"(?i)(authorization\s*:\s*bearer\s+)[^\s&,]+",
            r"\1[redacted]",
            excerpt,
        )
        bucket = config.CLOUD_SHARED_BUILD["preview_bucket"].rstrip("/")
        target = (
            f"{bucket}/{_PUBLIC_LOG_ROOT}/logs/"
            f"{record['user'].lower()}/{record['run_id']}.log"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".log", delete=False,
        ) as temporary:
            path = Path(temporary.name)
            temporary.write(excerpt)
        try:
            subprocess.run(
                [
                    "gcloud", "storage", "cp", "-q",
                    "--cache-control=no-cache,max-age=0",
                    str(path), target,
                ],
                check=True, text=True, capture_output=True,
            )
        finally:
            path.unlink(missing_ok=True)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"WARNING: public run-log upload failed: {exc}", file=sys.stderr, flush=True)


def _active_for_user(user: str) -> list[dict[str, Any]]:
    lock, records = _locked_registry()
    changed = _reconcile(records)
    active = [
        record for record in records
        if record.get("user", "").casefold() == user.casefold()
        and record.get("status") in _ACTIVE
    ]
    if changed:
        _save_registry(lock, records)
        _try_publish_dashboard()
    else:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    return active


def cleanup_runs() -> int:
    retention_days = config.CLOUD_SHARED_BUILD["preview_retention_days"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    lock, records = _locked_registry()
    _reconcile(records)
    expired: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") not in _TERMINAL:
            continue
        finished = record.get("finished_at")
        if not isinstance(finished, str):
            continue
        try:
            is_expired = datetime.fromisoformat(finished.replace("Z", "+00:00")) < cutoff
        except ValueError:
            continue
        if is_expired:
            expired.append(record.copy())
    _save_registry(lock, records)

    removed = 0
    for record in expired:
        user = record.get("user")
        run_id = record.get("run_id")
        if not isinstance(user, str) or not _USER.fullmatch(user):
            raise RuntimeError("refusing cleanup: registry contains an invalid developer alias")
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise RuntimeError("refusing cleanup: registry contains an invalid run ID")
        expected_root = _workspaces_root() / user.lower() / "runs" / run_id
        run_root = Path(record["run_root"]).expanduser().resolve()
        checkout = Path(record["checkout"]).expanduser().resolve()
        if (
            run_root != expected_root.resolve()
            or checkout != (expected_root / "checkout").resolve()
        ):
            raise RuntimeError(
                f"refusing cleanup outside the managed run directory: {run_root}"
            )
        if checkout.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(checkout)],
                cwd=ROOT, check=True, text=True, capture_output=True,
            )
        if run_root.exists():
            shutil.rmtree(run_root)
        removed += 1
    if expired:
        _try_publish_dashboard()
    return removed


def _update_stage(run_id: str, status: str, stage: str) -> None:
    _change(run_id, status=status, stage=stage)


def _run_command(run_id: str, command: list[str], cwd: Path, log_path: Path) -> int:
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _change(
        run_id, child_pid=process.pid, child_pgid=process.pid,
        child_start_ticks=_proc_start_ticks(process.pid),
    )
    last_heartbeat = time.monotonic()
    try:
        while process.poll() is None:
            time.sleep(2)
            if (_state_root() / "cancel" / f"{run_id}.request").exists():
                _change(run_id, status="cancelling", stage="stopping")
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 30
                while _process_group_alive(process.pid) and time.monotonic() < deadline:
                    process.poll()
                    time.sleep(0.25)
                if _process_group_alive(process.pid):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    deadline = time.monotonic() + 10
                    while _process_group_alive(process.pid) and time.monotonic() < deadline:
                        process.poll()
                        time.sleep(0.25)
                process.wait()
                if _process_group_alive(process.pid):
                    raise RuntimeError(
                        f"process group {process.pid} did not stop after SIGKILL"
                    )
                raise InterruptedError("run cancelled by an authenticated request")
            if time.monotonic() - last_heartbeat >= 20:
                record = _change(run_id, heartbeat_at=_now())
                _publish_log(record)
                last_heartbeat = time.monotonic()
        return process.returncode
    finally:
        _change(
            run_id, child_pid=None, child_pgid=None, child_start_ticks=None,
        )
        if process.stdout:
            process.stdout.close()


def _worker(run_id: str) -> int:
    lock, records = _locked_registry()
    record = next((item for item in records if item.get("run_id") == run_id), None)
    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    lock.close()
    if record is None:
        raise ValueError(f"unknown developer run: {run_id}")
    checkout = Path(record["checkout"])
    log_path = Path(record["log_path"])
    regions = record["regions"]
    user = record["user"]
    python = str(checkout / ".venv" / "bin" / "python")
    mode = record.get("mode", "")
    commands: list[tuple[str, str, list[str]]] = [
        ("running", "prepare shared sources", [
            python, "tools/developer_workspace.py", "prepare",
            "--source-data", record["source_data"], "--run-data", str(checkout / "data"),
            "--cache-root", record["cache_root"], "--code-root", str(checkout),
            "--regions", regions,
        ]),
    ]
    if record.get("fetch"):
        commands.extend([
            ("running", "fetch sources", [python, "src/fetch_regions.py", *regions.split()]),
            ("running", "promote shared sources", [
                python, "tools/developer_workspace.py", "promote",
                "--run-data", str(checkout / "data"), "--cache-root", record["cache_root"],
                "--code-root", str(checkout), "--regions", regions,
            ]),
        ])
    commands.extend([
        ("running", "build", ["bash", "src/run_district_build.sh", *([mode] if mode else []), "--regions", regions]),
        ("gating", "production gate", [python, "tools/predeploy_check.py"]),
    ])
    gate_failed = False
    try:
        for status, stage, command in commands:
            _update_stage(run_id, status, stage)
            result = _run_command(run_id, command, checkout, log_path)
            if result != 0:
                if stage == "production gate" and result != 130:
                    gate_failed = True
                    _change(run_id, gate_status="failed")
                    continue
                raise subprocess.CalledProcessError(result, command)
        _update_stage(run_id, "validating", "map contract")
        for region in regions.split():
            command = [
                python, "tools/validate_quickstart_map.py",
                "--region-out", f"data/out/{region}", "--map-data", "data",
            ]
            result = _run_command(run_id, command, checkout, log_path)
            if result:
                raise subprocess.CalledProcessError(result, command)
        _update_stage(run_id, "publishing", "developer preview")
        command = [
            python, "tools/publish_preview.py", "--user", user,
            "--regions", regions, "--run-id", run_id,
        ]
        result = _run_command(run_id, command, checkout, log_path)
        if result:
            raise subprocess.CalledProcessError(result, command)
        preview_url = (
            f"https://storage.googleapis.com/"
            f"{config.CLOUD_SHARED_BUILD['preview_bucket'].removeprefix('gs://')}"
            f"/devel/{user.lower()}/preview.html"
        )
        _publish_log(_change(run_id, status="publishing", stage="preview published"))
        terminal = "gate_failed" if gate_failed else "succeeded"
        _change(run_id, status=terminal, stage="complete", finished_at=_now(), preview_url=preview_url)
        return 0
    except InterruptedError as exc:
        record = _change(
            run_id, status="cancelled", stage="cancelled",
            finished_at=_now(), error=str(exc),
        )
        _publish_log(record)
        return 130
    except Exception as exc:
        record = _change(
            run_id, status="failed", stage="failed", finished_at=_now(), error=str(exc),
        )
        _publish_log(record)
        raise
    finally:
        (_state_root() / "cancel" / f"{run_id}.request").unlink(missing_ok=True)


def start(
    user: str,
    run_id: str,
    regions: str,
    commit: str,
    checkout: Path,
    run_root: Path,
    *,
    mode: str = "",
    fetch: bool = False,
    source_data: Path | None = None,
    parallel: bool = False,
) -> int:
    if not _USER.fullmatch(user) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid developer alias or run ID")
    active = _active_for_user(user)
    if active and not parallel:
        raise RuntimeError(
            "developer already has an active run: "
            + ", ".join(item["run_id"] for item in active)
        )
    cleanup_runs()
    run_root.mkdir(parents=True, exist_ok=True)
    log_path = run_root / "remote_build.log"
    log_path.touch()
    lock, records = _locked_registry()
    _reconcile(records)
    active = [
        item for item in records
        if item.get("user", "").casefold() == user.casefold()
        and item.get("status") in _ACTIVE
    ]
    if active and not parallel:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        raise RuntimeError(
            "developer already has an active run: "
            + ", ".join(item["run_id"] for item in active)
        )
    if any(item.get("run_id") == run_id for item in records):
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        raise ValueError(f"developer run already exists: {run_id}")
    record = {
        "run_id": run_id,
        "user": user,
        "regions": regions,
        "commit": commit,
        "checkout": str(checkout.resolve()),
        "run_root": str(run_root.resolve()),
        "log_path": str(log_path.resolve()),
        "log_url": (
            f"https://storage.googleapis.com/"
            f"{config.CLOUD_SHARED_BUILD['preview_bucket'].removeprefix('gs://')}"
            f"/{_PUBLIC_LOG_ROOT}/logs/{user.lower()}/{run_id}.log"
        ),
        "mode": mode,
        "fetch": fetch,
        "source_data": str((source_data or (ROOT / "data")).expanduser().resolve()),
        "cache_root": str(Path("~/solar-map-source-cache").expanduser()),
        "status": "queued",
        "stage": "queued",
        "started_at": _now(),
        "updated_at": _now(),
        "pid": None,
        "pid_start_ticks": None,
        "gate_status": "not_run",
    }
    records.append(record)
    try:
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "worker", "--run-id", run_id],
                cwd=checkout, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
                env={
                    **os.environ,
                    "SOLAR_DEVELOPER_RUNS_ROOT": str(_state_root()),
                    "SOLAR_DEVELOPER_WORKSPACES_ROOT": str(_workspaces_root()),
                },
            )
    except OSError:
        records.remove(record)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        raise
    record.update(
        status="running", stage="starting", pid=process.pid,
        pid_start_ticks=_proc_start_ticks(process.pid), heartbeat_at=_now(),
    )
    _save_registry(lock, records)
    _try_publish_dashboard()
    print(f"RUN_ID={run_id}")
    print(f"STATUS=running")
    return 0


def cancel(run_id: str, wait_seconds: int = 45) -> int:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run ID")
    lock, records = _locked_registry()
    record = next((item for item in records if item.get("run_id") == run_id), None)
    if record is None:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        raise ValueError(f"unknown developer run: {run_id}")
    if record.get("status") not in _ACTIVE:
        print(f"STATUS={record.get('status')}")
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        return 0
    request = _state_root() / "cancel" / f"{run_id}.request"
    request.parent.mkdir(parents=True, exist_ok=True)
    request.write_text(_now(), encoding="utf-8")
    record.update(status="cancelling", stage="stop requested", updated_at=_now())
    _save_registry(lock, records)
    _try_publish_dashboard()
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(2)
        lock, records = _locked_registry()
        _reconcile(records)
        record = next(item for item in records if item.get("run_id") == run_id)
        status = record.get("status")
        _save_registry(lock, records)
        if status in _TERMINAL:
            _try_publish_dashboard()
            print(f"STATUS={status}")
            return 0
    print("STATUS=cancelling")
    return 1


def list_runs(user: str | None) -> int:
    lock, records = _locked_registry()
    changed = _reconcile(records)
    selected = [
        item for item in records
        if user is None or item.get("user", "").casefold() == user.casefold()
    ]
    _save_registry(lock, records)
    if changed:
        _try_publish_dashboard()
    print(json.dumps(selected, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    starter = subparsers.add_parser("start")
    starter.add_argument("--user", required=True)
    starter.add_argument("--run-id", required=True)
    starter.add_argument("--regions", required=True)
    starter.add_argument("--commit", required=True)
    starter.add_argument("--checkout", type=Path, required=True)
    starter.add_argument("--run-root", type=Path, required=True)
    starter.add_argument("--mode", default="")
    starter.add_argument("--fetch", action="store_true")
    starter.add_argument("--source-data", type=Path)
    starter.add_argument("--parallel", action="store_true")
    runner = subparsers.add_parser("worker")
    runner.add_argument("--run-id", required=True)
    listing = subparsers.add_parser("list")
    listing.add_argument("--user")
    canceller = subparsers.add_parser("cancel")
    canceller.add_argument("--run-id", required=True)
    canceller.add_argument("--wait-seconds", type=int, default=45)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.action == "start":
            return start(
                args.user, args.run_id, args.regions, args.commit, args.checkout,
                args.run_root, mode=args.mode, parallel=args.parallel,
                fetch=args.fetch, source_data=args.source_data,
            )
        if args.action == "worker":
            return _worker(args.run_id)
        if args.action == "list":
            return list_runs(args.user)
        return cancel(args.run_id, args.wait_seconds)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"developer run error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
