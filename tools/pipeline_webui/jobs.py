"""Pipeline jobs for the web panel, queued in two lanes.

The local lane builds in this checkout. Each region runs `tools/pipeline.py
<region> --no-open-browser --no-preview-server --no-paid-referee|--paid-referee`
in its own session, with stdout appended to data/pipeline_web/jobs/<id>/stdout.log.
A failed region is recorded and the job continues with the rest. When more
than one region was built, a final src/combine_regions.py puts every built
region (data/out/<id>/summary.json) back into the map dataset, because each
run's own combine step keeps only its region. Terrain tiles stay the last
region's. A job whose turn comes while a CLI build holds
data/pipeline-workspace.lock waits for it.

The cloud lane runs developer and published builds on the shared VM, one
command per job (see cloud.cloud_command), and records the preview URL, gate
result and published version from the output.

Each lane runs one job at a time; later jobs queue in order and can be removed
before they start. Cancelling a running job sends SIGINT: pipeline.py then
stops its queue workers and they release the workspace lock, and
remote_build.sh stops the VM only if it started it. SIGTERM would skip that
cleanup, so SIGTERM and SIGKILL are only escalations after CANCEL_GRACE_S.
"""

from __future__ import annotations

import base64
import json
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
from tools.pipeline_webui.cloud import parse_cloud_line
from tools.pipeline_webui.common import DATA, ROOT, read_json, web_dir, write_json_atomic

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
STEP_DONE = {"PASS", "DEGRADED", "FAIL", "SKIPPED", "NOT RUN", "INTERRUPTED"}
CANCEL_GRACE_S = 30.0
LOG_CHUNK = 256 * 1024
LIST_LIMIT = 50
RUN_DIR_PREFIX = "PIPELINE_RUN_DIR="

LOCK_POLL_S = 5.0
LANES = {"local": ("local",), "cloud": ("developer", "published")}
LANE_OF = {target: lane for lane, targets in LANES.items() for target in targets}

CommandBuilder = Callable[[str, str, bool], list]
CombineBuilder = Callable[[str, list], list]
CloudBuilder = Callable[[str, str, list, bool], list]


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


class Lane:
    """One FIFO of jobs and the single job it is running."""

    def __init__(self, name: str):
        self.name = name
        self.queue: list[str] = []
        self.active: str | None = None
        self.process: subprocess.Popen | None = None
        self.cancel_requested = False
        self.thread: threading.Thread | None = None


def lane_for(target: str) -> str:
    if target not in LANE_OF:
        raise JobError(f"unknown target: {target}")
    return LANE_OF[target]


class JobManager:
    """Two lanes: local builds in this checkout, and cloud builds on the shared VM.

    Each lane runs one job at a time and queues the rest in order. Developer
    and published builds share the cloud lane because they share one VM, whose
    code sync refuses while it is building.
    """

    def __init__(self, data: Path = DATA, root: Path = ROOT, python: str = "python3",
                 command_builder: CommandBuilder | None = None,
                 combine_builder: CombineBuilder | None = None,
                 cancel_grace_s: float = CANCEL_GRACE_S,
                 cloud_builder: CloudBuilder | None = None,
                 cloud_env: Callable[[], dict] | None = None,
                 lock_poll_s: float = LOCK_POLL_S):
        self.data = Path(data)
        self.root = Path(root)
        self.python = python
        self.command_builder = command_builder or pipeline_command
        self.combine_builder = combine_builder or combine_command
        self.cloud_builder = cloud_builder
        self.cloud_env = cloud_env
        self.cancel_grace_s = cancel_grace_s
        self.lock_poll_s = lock_poll_s
        self.dir = web_dir(self.data) / "jobs"
        self.lock = threading.Lock()
        self.lanes = {name: Lane(name) for name in LANES}
        self.recover()

    @property
    def active(self) -> str | None:
        """The local lane's running job (kept for callers that predate lanes)."""
        return self.lanes["local"].active

    def active_jobs(self) -> dict[str, str | None]:
        return {name: lane.active for name, lane in self.lanes.items()}

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
        """Jobs left queued or running by a stopped server are marked interrupted."""
        if not self.dir.is_dir():
            return
        for path in self.dir.glob("*/job.json"):
            job = read_json(path)
            if isinstance(job, dict) and job.get("status") in ("queued", "running"):
                job["status"] = "interrupted"
                job["finished"] = _now()
                for run in job.get("runs", []):
                    if run.get("status") in ("queued", "running"):
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

    def queue(self) -> dict:
        """Each lane's running job and its queued jobs, in run order."""
        with self.lock:
            snapshot = {n: (lane.active, list(lane.queue)) for n, lane in self.lanes.items()}
        out = {}
        for name, (active, queued) in snapshot.items():
            jobs = []
            for job_id in ([active] if active else []) + queued:
                try:
                    job = self.load(job_id)
                except JobError:
                    continue
                jobs.append({k: job.get(k) for k in (
                    "job_id", "target", "regions", "status", "created", "started",
                    "estimated_total_s", "current_index", "waiting", "approved")})
            out[name] = {"targets": list(LANES[name]), "active": active,
                         "jobs": jobs, "queued": len(queued)}
        return out

    # ---- lifecycle -----------------------------------------------------
    def start(self, estimate: dict, paid_referee: bool = False,
              cloud_cost_acknowledged: bool = False) -> str:
        """Queue a job from an approved estimate; it runs when its lane is free."""
        target = estimate.get("target", "local")
        lane_name = lane_for(target)
        if lane_name == "cloud" and self.cloud_builder is None:
            raise JobError("cloud targets are not configured on this server", 409)
        regions = [r["region"] for r in estimate.get("regions", [])]
        if not regions:
            raise JobError("the estimate names no regions")
        options = estimate.get("options") or {}
        include_imagery = options.get("include_imagery", True)
        if not isinstance(include_imagery, bool):
            raise JobError("the estimate has invalid source choices")
        if paid_referee != options.get("paid_referee", paid_referee):
            raise JobError("the estimate's paid-extra choice changed")
        if paid_referee and not include_imagery:
            raise JobError("the paid AI referee requires aerial imagery")
        cloud = estimate.get("cloud") or {}
        survey_overrides = {
            row["region"]: row["source_layers"]
            for row in estimate.get("regions", [])
            if isinstance(row.get("source_layers"), dict)
        }
        job_id = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + secrets.token_hex(3)
        job = {
            "job_id": job_id, "estimate_id": estimate.get("estimate_id"),
            "regions": regions, "target": target, "lane": lane_name,
            "paid_referee": bool(paid_referee),
            "include_imagery": include_imagery,
            "survey_overrides": survey_overrides,
            "approved": {"at": _now(),
                         "cloud_cost_acknowledged": (target == "local"
                                                     or cloud_cost_acknowledged),
                         "estimate_totals": estimate.get("totals"),
                         "paid_referee_usd_upper": (estimate.get("paid_referee") or {}).get("usd_upper")
                         if paid_referee else 0.0,
                         "cloud_cost_nzd": cloud.get("cost_nzd"),
                         "cloud_cost_text": cloud.get("cost_text")},
            "estimated_total_s": (estimate.get("totals") or {}).get("total_s"),
            "created": _now(), "started_clock": None, "finished": None,
            "status": "queued", "current_index": -1, "waiting": None,
        }
        if lane_name == "local":
            job["runs"] = [{"region": r, "run_dir": None, "exit_code": None, "status": "queued"}
                           for r in regions]
            job["final_combine"] = {"status": "not needed", "exit_code": None}
        else:
            job["runs"] = [{"region": " ".join(regions), "run_dir": None, "exit_code": None,
                            "status": "queued"}]
            job["final_combine"] = {"status": "not needed", "exit_code": None}
            job["cloud"] = {k: cloud.get(k) for k in ("project", "vm", "zone", "ref",
                                                       "preview_user", "preview_url")}
            job["preview_url"] = None
            job["gate_result"] = None
            job["published_version"] = None
        with self.lock:
            lane = self.lanes[lane_name]
            self._save(job)
            lane.queue.append(job_id)
            if lane.thread is None or not lane.thread.is_alive():
                lane.thread = threading.Thread(target=self._worker, args=(lane,), daemon=True,
                                               name=f"lane-{lane_name}")
                lane.thread.start()
        return job_id

    def _worker(self, lane: Lane) -> None:
        while True:
            with self.lock:
                if not lane.queue:
                    lane.thread = None
                    return
                job_id = lane.queue.pop(0)
                lane.active = job_id
                lane.cancel_requested = False
            try:
                job = self.load(job_id)
                if lane.name == "local":
                    self._run_local(lane, job)
                else:
                    self._run_cloud(lane, job)
            except Exception:  # noqa: BLE001 - one broken record must not stop the lane
                pass
            finally:
                with self.lock:
                    lane.active = None
                    lane.process = None

    def _popen(self, cmd: list, log, env: dict | None = None) -> subprocess.Popen:
        return subprocess.Popen(
            [str(c) for c in cmd], cwd=self.root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            start_new_session=True,
            env={**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"})

    def _launch(self, lane: Lane, cmd: list, log, env: dict | None = None) -> subprocess.Popen:
        """Start a lane's process; a cancel that arrived just before still stops it."""
        with self.lock:
            lane.process = self._popen(cmd, log, env)
            if lane.cancel_requested:
                threading.Thread(target=self._stop, args=(lane.process,), daemon=True).start()
            return lane.process

    def _stream(self, proc: subprocess.Popen, log, on_line=None) -> int:
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            log.flush()
            if on_line:
                on_line(line.rstrip("\n"))
        return proc.wait()

    def _begin(self, job: dict) -> None:
        job["status"] = "running"
        job["started"] = _now()
        job["started_clock"] = time.time()
        self._save(job)

    def _finish(self, job: dict) -> None:
        job["finished"] = _now()
        job["waiting"] = None
        self._save(job)

    def _wait_for_workspace(self, lane: Lane, job: dict, log) -> bool:
        """Wait while a CLI build holds data/pipeline-workspace.lock; False if cancelled."""
        announced = False
        while not lane.cancel_requested:
            owner = self.workspace_owner()
            if owner is None:
                if announced:
                    job["waiting"] = None
                    self._save(job)
                return True
            if not announced:
                job["waiting"] = ("a CLI build holds data/pipeline-workspace.lock "
                                  f"(pid {owner.get('pid', '?')}); waiting for it to finish")
                self._save(job)
                log.write(job["waiting"] + "\n")
                log.flush()
                announced = True
            time.sleep(self.lock_poll_s)
        return False

    def _run_local(self, lane: Lane, job: dict) -> None:
        job_dir = self._job_dir(job["job_id"])
        self._begin(job)
        try:
            with (job_dir / "stdout.log").open("a", encoding="utf-8") as log:
                for i, run in enumerate(job["runs"]):
                    if lane.cancel_requested or not self._wait_for_workspace(lane, job, log):
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
                    if not job.get("include_imagery", True):
                        cmd.append("--no-imagery")
                    env = ({"SOLAR_SURVEY_OVERRIDES": json.dumps(job["survey_overrides"])}
                           if job.get("survey_overrides") else None)
                    code = self._stream(self._launch(lane, cmd, log, env), log, on_line)
                    with self.lock:
                        lane.process = None
                    run["exit_code"] = code
                    run["finished"] = _now()
                    run["status"] = ("cancelled" if lane.cancel_requested
                                     else "succeeded" if code == 0 else "failed")
                    self._save(job)
                built = [r["region"] for r in job["runs"] if r["status"] == "succeeded"]
                if len(built) > 1 and not lane.cancel_requested:
                    every = list_regions(self.data / "out", "summary.json")
                    regions = every or built
                    job["final_combine"] = {"status": "running", "exit_code": None, "regions": regions}
                    self._save(job)
                    log.write(f"\n=== combine {len(regions)} built regions into the map ===\n")
                    log.flush()
                    code = self._stream(
                        self._launch(lane, self.combine_builder(self.python, regions), log), log)
                    with self.lock:
                        lane.process = None
                    job["final_combine"].update(exit_code=code,
                                                status="succeeded" if code == 0 else "failed")
            statuses = [r["status"] for r in job["runs"]]
            if lane.cancel_requested:
                job["status"] = "cancelled"
            elif all(s == "succeeded" for s in statuses) and job["final_combine"]["status"] != "failed":
                job["status"] = "succeeded"
            else:
                job["status"] = "failed"
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = f"job runner failed ({type(exc).__name__})"
        finally:
            self._finish(job)

    def _run_cloud(self, lane: Lane, job: dict) -> None:
        job_dir = self._job_dir(job["job_id"])
        run = job["runs"][0]
        self._begin(job)
        try:
            with (job_dir / "stdout.log").open("a", encoding="utf-8") as log:
                if lane.cancel_requested:
                    run["status"] = "cancelled"
                else:
                    job["current_index"] = 0
                    run["status"] = "running"
                    run["started"] = _now()
                    self._save(job)
                    log.write(f"\n=== {job['target']} build on the shared VM: "
                              f"{' '.join(job['regions'])} ===\n")
                    log.flush()

                    def on_line(line):
                        before = (job.get("preview_url"), job.get("gate_result"),
                                  job.get("published_version"))
                        parse_cloud_line(line, job)
                        if before != (job.get("preview_url"), job.get("gate_result"),
                                      job.get("published_version")):
                            self._save(job)

                    cmd = self.cloud_builder(job["target"], self.python, job["regions"],
                                             job["paid_referee"])
                    if not job.get("include_imagery", True):
                        cmd.append("--no-imagery")
                    overrides = job.get("survey_overrides") or {}
                    if overrides:
                        encoded = base64.b64encode(
                            json.dumps(overrides, separators=(",", ":")).encode("utf-8")
                        ).decode("ascii")
                        cmd.extend(["--survey-overrides-b64", encoded])
                        if all(not layer_set.get("pointcloud_bulk_url")
                               for layer_set in overrides.values()):
                            cmd.append("--no-pointcloud")
                    env = self.cloud_env() if self.cloud_env else None
                    code = self._stream(self._launch(lane, cmd, log, env), log, on_line)
                    with self.lock:
                        lane.process = None
                    run["exit_code"] = code
                    run["finished"] = _now()
                    run["status"] = ("cancelled" if lane.cancel_requested
                                     else "succeeded" if code == 0 else "failed")
            job["status"] = run["status"] if run["status"] != "queued" else "cancelled"
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = f"job runner failed ({type(exc).__name__})"
        finally:
            self._finish(job)

    def cancel(self, job_id: str) -> dict:
        """Remove a queued job, or stop the running one."""
        job = self.load(job_id)
        with self.lock:
            for lane in self.lanes.values():
                if job_id in lane.queue:
                    lane.queue.remove(job_id)
                    job["status"] = "cancelled"
                    job["finished"] = _now()
                    for run in job.get("runs", []):
                        run["status"] = "cancelled"
                    self._save(job)
                    return job
            lane = next((ln for ln in self.lanes.values() if ln.active == job_id), None)
            if lane is None:
                raise JobError("that job is not queued or running", 409)
            lane.cancel_requested = True
            proc = lane.process
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
        """Wait for every lane to drain (tests and shutdown)."""
        deadline = None if timeout is None else time.time() + timeout
        for lane in self.lanes.values():
            thread = lane.thread
            if thread:
                thread.join(None if deadline is None else max(deadline - time.time(), 0))

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
        job["active"] = job_id in self.active_jobs().values()
        with self.lock:
            lane = self.lanes.get(job.get("lane") or "local")
            job["queue_position"] = (lane.queue.index(job_id) + 1
                                     if lane and job_id in lane.queue else None)
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
