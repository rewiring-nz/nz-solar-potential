"""One pipeline job at a time for the web panel: regions built in sequence.

Each region runs `tools/pipeline.py <region> --no-open-browser
--no-preview-server --no-paid-referee|--paid-referee` in its own session, with
stdout appended to data/pipeline_web/jobs/<id>/stdout.log. A failed region
is recorded and the job continues with the rest. When more than one region
was built, a final src/combine_regions.py puts every built region
(data/out/<id>/summary.json) back into the map dataset, because each run's
own combine step keeps only its region. Terrain tiles stay the last region's.

Cancelling sends SIGINT to the running pipeline.py: it then stops its queue
workers and they release data/pipeline-workspace.lock. SIGTERM would kill it
without that cleanup, so SIGTERM and SIGKILL are only escalations after
CANCEL_GRACE_S.
"""

from __future__ import annotations

import os
import secrets
import signal
import statistics
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from src.region_ids import list_regions
from tools.pipeline_webui.common import DATA, ROOT, read_json, web_dir, write_json_atomic

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
STEP_DONE = {"PASS", "DEGRADED", "FAIL", "SKIPPED", "NOT RUN", "INTERRUPTED"}
CANCEL_GRACE_S = 30.0
LOG_CHUNK = 256 * 1024
LIST_LIMIT = 50
RUN_DIR_PREFIX = "PIPELINE_RUN_DIR="

CommandBuilder = Callable[[str, str, bool], list]
CombineBuilder = Callable[[str, list], list]


class JobError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def pipeline_command(python: str, region: str, paid_referee: bool) -> list[str]:
    return [python, "tools/pipeline.py", region, "--python", python, "--no-open-browser",
            "--no-preview-server", "--paid-referee" if paid_referee else "--no-paid-referee"]


def combine_command(python: str, regions: list[str]) -> list[str]:
    return [python, "src/combine_regions.py", "--regions", *regions, "--out-root", "data/out",
            "--dest", "data", "--skip-markup-lines"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def step_weights(data: Path, runs: int = 10) -> dict[str, float]:
    """Median seconds per step key over the last finished runs."""
    runs_dir = Path(data) / "pipeline_runs"
    if not runs_dir.is_dir():
        return {}
    seen: dict[str, list[float]] = {}
    used = 0
    for run_dir in sorted(runs_dir.iterdir(), key=lambda p: p.name, reverse=True):
        status = read_json(run_dir / "status.json")
        if not isinstance(status, dict) or status.get("overall") not in {"PASS", "DEGRADED"}:
            continue
        for s in status.get("steps", []):
            if s.get("seconds") is not None:
                seen.setdefault(s["key"], []).append(float(s["seconds"]))
        used += 1
        if used >= runs:
            break
    return {k: statistics.median(v) for k, v in seen.items()}


def run_fraction(status: dict | None, weights: dict[str, float]) -> float:
    """Share of a run's expected time that its finished steps account for."""
    steps = (status or {}).get("steps") or []
    if not steps:
        return 0.0
    w = [max(weights.get(s.get("key"), 1.0), 1.0) if weights else 1.0 for s in steps]
    done = sum(wi for wi, s in zip(w, steps) if s.get("status") in STEP_DONE)
    return min(done / sum(w), 1.0)


def progress(job: dict, statuses: list[dict | None], weights: dict[str, float]) -> float:
    regions = job.get("regions") or []
    if not regions:
        return 0.0
    if job.get("status") == "succeeded":
        return 1.0
    total = 0.0
    for i, run in enumerate(job.get("runs") or []):
        if run.get("status") in ("succeeded", "failed", "cancelled"):
            total += 1.0
        else:
            total += run_fraction(statuses[i] if i < len(statuses) else None, weights)
    return round(min(total / len(regions), 1.0), 4)


class JobManager:
    def __init__(self, data: Path = DATA, root: Path = ROOT, python: str = "python3",
                 command_builder: CommandBuilder | None = None,
                 combine_builder: CombineBuilder | None = None,
                 cancel_grace_s: float = CANCEL_GRACE_S):
        self.data = Path(data)
        self.root = Path(root)
        self.python = python
        self.command_builder = command_builder or pipeline_command
        self.combine_builder = combine_builder or combine_command
        self.cancel_grace_s = cancel_grace_s
        self.dir = web_dir(self.data) / "jobs"
        self.lock = threading.Lock()
        self.active: str | None = None
        self.process: subprocess.Popen | None = None
        self.cancel_requested = False
        self.thread: threading.Thread | None = None
        self.recover()

    # ---- records -------------------------------------------------------
    def _job_dir(self, job_id: str) -> Path:
        if not job_id or not all(c in "0123456789abcdef-T" for c in job_id) or len(job_id) > 40:
            raise JobError("unknown job", 404)
        return self.dir / job_id

    def _save(self, job: dict) -> None:
        write_json_atomic(self._job_dir(job["job_id"]) / "job.json", job)

    def load(self, job_id: str) -> dict:
        job = read_json(self._job_dir(job_id) / "job.json")
        if not isinstance(job, dict):
            raise JobError("unknown job", 404)
        return job

    def recover(self) -> None:
        """Jobs left running by a stopped server are marked interrupted."""
        if not self.dir.is_dir():
            return
        for path in self.dir.glob("*/job.json"):
            job = read_json(path)
            if isinstance(job, dict) and job.get("status") in ("queued", "running"):
                job["status"] = "interrupted"
                job["finished"] = _now()
                for run in job.get("runs", []):
                    if run.get("status") == "running":
                        run["status"] = "interrupted"
                write_json_atomic(path, job)

    def list(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        jobs = [read_json(p) for p in self.dir.glob("*/job.json")]
        jobs = [j for j in jobs if isinstance(j, dict)]
        jobs.sort(key=lambda j: j.get("created") or "", reverse=True)
        return jobs[:LIST_LIMIT]

    def workspace_owner(self) -> dict | None:
        lock = self.data / "pipeline-workspace.lock"
        if not lock.exists():
            return None
        return read_json(lock / "owner.json", {}) or {}

    # ---- lifecycle -----------------------------------------------------
    def start(self, estimate: dict, paid_referee: bool = False) -> str:
        with self.lock:
            if self.active:
                raise JobError(f"job {self.active} is still running", 409)
            owner = self.workspace_owner()
            if owner is not None:
                raise JobError("a CLI build is running in this checkout "
                               f"(data/pipeline-workspace.lock, pid {owner.get('pid', '?')})", 409)
            job_id = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + secrets.token_hex(3)
            job = {
                "job_id": job_id, "estimate_id": estimate.get("estimate_id"),
                "regions": [r["region"] for r in estimate.get("regions", [])],
                "target": estimate.get("target", "local"), "paid_referee": bool(paid_referee),
                "approved": {"confirm": "yes", "at": _now(),
                             "estimate_totals": estimate.get("totals"),
                             "paid_referee_usd_upper": (estimate.get("paid_referee") or {}).get("usd_upper")
                             if paid_referee else 0.0},
                "estimated_total_s": (estimate.get("totals") or {}).get("total_s"),
                "created": _now(), "started_clock": None, "finished": None,
                "status": "queued", "current_index": -1,
                "runs": [{"region": r["region"], "run_dir": None, "exit_code": None, "status": "queued"}
                         for r in estimate.get("regions", [])],
                "final_combine": {"status": "not needed", "exit_code": None},
            }
            if not job["regions"]:
                raise JobError("the estimate names no regions")
            self._save(job)
            self.active = job_id
            self.cancel_requested = False
            self.thread = threading.Thread(target=self._run, args=(job,), daemon=True,
                                           name=f"job-{job_id}")
            self.thread.start()
            return job_id

    def _popen(self, cmd: list, log) -> subprocess.Popen:
        return subprocess.Popen(
            [str(c) for c in cmd], cwd=self.root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})

    def _stream(self, proc: subprocess.Popen, log, on_line=None) -> int:
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            log.flush()
            if on_line:
                on_line(line.rstrip("\n"))
        return proc.wait()

    def _run(self, job: dict) -> None:
        job_dir = self._job_dir(job["job_id"])
        job["status"] = "running"
        job["started"] = _now()
        job["started_clock"] = time.time()
        self._save(job)
        try:
            with (job_dir / "stdout.log").open("a", encoding="utf-8") as log:
                for i, run in enumerate(job["runs"]):
                    if self.cancel_requested:
                        run["status"] = "cancelled"
                        continue
                    job["current_index"] = i
                    run["status"] = "running"
                    run["started"] = _now()
                    self._save(job)
                    log.write(f"\n=== [{i + 1}/{len(job['runs'])}] {run['region']} ===\n")
                    log.flush()

                    def on_line(line, run=run):
                        if line.startswith(RUN_DIR_PREFIX):
                            run["run_dir"] = line[len(RUN_DIR_PREFIX):].strip()
                            self._save(job)

                    cmd = self.command_builder(self.python, run["region"], job["paid_referee"])
                    with self.lock:
                        self.process = self._popen(cmd, log)
                    code = self._stream(self.process, log, on_line)
                    with self.lock:
                        self.process = None
                    run["exit_code"] = code
                    run["finished"] = _now()
                    run["status"] = ("cancelled" if self.cancel_requested
                                     else "succeeded" if code == 0 else "failed")
                    self._save(job)
                built = [r["region"] for r in job["runs"] if r["status"] == "succeeded"]
                if len(built) > 1 and not self.cancel_requested:
                    every = list_regions(self.data / "out", "summary.json")
                    regions = every or built
                    job["final_combine"] = {"status": "running", "exit_code": None, "regions": regions}
                    self._save(job)
                    log.write(f"\n=== combine {len(regions)} built regions into the map ===\n")
                    log.flush()
                    with self.lock:
                        self.process = self._popen(self.combine_builder(self.python, regions), log)
                    code = self._stream(self.process, log)
                    with self.lock:
                        self.process = None
                    job["final_combine"].update(exit_code=code,
                                                status="succeeded" if code == 0 else "failed")
            statuses = [r["status"] for r in job["runs"]]
            if self.cancel_requested:
                job["status"] = "cancelled"
            elif all(s == "succeeded" for s in statuses) and job["final_combine"]["status"] != "failed":
                job["status"] = "succeeded"
            else:
                job["status"] = "failed"
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = f"job runner failed ({type(exc).__name__})"
        finally:
            job["finished"] = _now()
            self._save(job)
            with self.lock:
                self.active = None
                self.process = None

    def cancel(self, job_id: str) -> dict:
        job = self.load(job_id)
        with self.lock:
            if self.active != job_id:
                raise JobError("that job is not running", 409)
            self.cancel_requested = True
            proc = self.process
        if proc is not None:
            threading.Thread(target=self._stop, args=(proc,), daemon=True).start()
        return job

    def _stop(self, proc: subprocess.Popen) -> None:
        for sig, wait in ((signal.SIGINT, self.cancel_grace_s), (signal.SIGTERM, 10.0),
                          (signal.SIGKILL, None)):
            if proc.poll() is not None:
                return
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, sig)
                else:
                    proc.terminate()
            except ProcessLookupError:
                return
            if wait is None:
                break
            try:
                proc.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue

    def wait(self, timeout: float | None = None) -> None:
        thread = self.thread
        if thread:
            thread.join(timeout)

    # ---- views ---------------------------------------------------------
    def run_status(self, run: dict) -> dict | None:
        if not run.get("run_dir"):
            return None
        path = Path(run["run_dir"])
        path = path if path.is_absolute() else self.root / path
        try:
            path.resolve().relative_to(self.data.resolve())
        except ValueError:
            return None
        return read_json(path / "status.json")

    def view(self, job_id: str) -> dict:
        job = self.load(job_id)
        statuses = [self.run_status(r) for r in job.get("runs", [])]
        for run, status in zip(job.get("runs", []), statuses):
            run["status_json"] = status
            if run.get("run_dir"):
                run["report_url"] = "/" + Path(run["run_dir"]).as_posix().lstrip("/") + "/report.md" \
                    if not Path(run["run_dir"]).is_absolute() else None
        job["progress"] = progress(job, statuses, step_weights(self.data))
        elapsed = time.time() - job["started_clock"] if job.get("started_clock") else 0
        if job.get("status") in TERMINAL:
            job["eta_s"] = 0
        elif job.get("estimated_total_s") is not None:
            job["eta_s"] = max(round(float(job["estimated_total_s"]) - elapsed), 0)
        job["active"] = self.active == job_id
        return job

    def log(self, job_id: str, offset: int = 0) -> dict:
        path = self._job_dir(job_id) / "stdout.log"
        self.load(job_id)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        offset = max(0, min(int(offset), size))
        with (path.open("rb") if size else open(os.devnull, "rb")) as f:
            f.seek(offset)
            chunk = f.read(LOG_CHUNK)
        job = self.load(job_id)
        next_offset = offset + len(chunk)
        return {"text": chunk.decode("utf-8", errors="replace"), "next_offset": next_offset,
                "eof": job.get("status") in TERMINAL and next_offset >= size}
