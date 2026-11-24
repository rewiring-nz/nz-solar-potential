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

Web submissions use start_batch: one job per estimated region or cell,
enqueued together in selection order. The last job in a multi-area local
submission combines existing emitted regions once; it does not rebuild them.
start still accepts legacy multi-region jobs for existing callers/history.

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
from datetime import datetime
from pathlib import Path
from typing import Callable

from src.region_ids import list_regions
from src.raster_plan_handoff import plan_digest, plan_for_regions
from tools.pipeline_webui.cloud import parse_cloud_line
from tools.pipeline_webui.estimate import referee_note
from tools.pipeline_webui.common import DATA, ROOT, read_json, web_dir, write_json_atomic

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
STEP_DONE = {"PASS", "DEGRADED", "SKIPPED"}
CANCEL_GRACE_S = 30.0
LOG_CHUNK = 256 * 1024
LIST_LIMIT = 50
RECENT_QUEUE_S = 600
RECENT_QUEUE_LIMIT = 3
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


def automatic_raster_bounds(row: dict, data: Path, workspace: Path, digest: str,
                            include_imagery: bool, paid_referee: bool) -> dict:
    """Freeze the web estimate in the existing admission contract."""
    from src.raster_job_bounds import validate_job_bounds

    estimate = row["raster_auto_estimate"]
    config = estimate["cache_policy"]
    if estimate["work_dir"] != str(data.resolve()) or estimate["output_dir"] != str(data.resolve()):
        raise ValueError("Automatic disk estimate no longer matches the data directory")
    bounds = {
        "version": 1, "region": row["region"], "workspace": str(workspace.resolve()),
        "plan_sha256": digest, "include_imagery": include_imagery,
        "paid_referee": paid_referee,
        "evidence": "Automatically estimated from area, buildings and download assumptions; "
                    "not an upper bound or enforced limit.",
        "cache_policy": str(Path(os.environ.get(
            "SOLAR_RASTER_CACHE_POLICY") or data / "raster-cache-policy.json").resolve()),
        "work_dir": estimate["work_dir"], "output_dir": estimate["output_dir"],
        "mosaic_bytes": estimate["mosaic_bytes"], "scratch_bytes": estimate["scratch_bytes"],
        "output_bytes": estimate["output_bytes"], "export_bytes": estimate["export_bytes"],
        "extra_allocations": [
            {"name": "other_inputs", "directory": estimate["work_dir"],
             "bytes": estimate["other_bytes"]},
            {"name": "temporary_files", "directory": estimate["temp_dir"],
             "bytes": estimate["scratch_bytes"]},
        ],
        "processing": {item["role"]: {
            "resolution": 1 if item["role"] == "dsm" else 0.1, "resampling": "average"}
            for item in row.get("raster_sources", [{"role": "dsm", "state": "mapped"}])
            if item["state"] == "mapped"},
        "cache": {key: config[key] for key in
                  ("directory", "max_bytes", "min_free_bytes", "unknown_asset_bytes")},
        "cleanup": {
            "lookahead": config["queue_lookahead"], "near_distance_m": config["near_distance_m"],
            "max_unused_age_s": config["max_unused_age_s"],
            "partial_expiry_s": config["partial_expiry_s"],
            "high_water_bytes": config["high_water_bytes"], "target_bytes": config["target_bytes"]},
        "freshness": {"metadata_refresh_s": config["metadata_refresh_s"]},
    }
    return validate_job_bounds(bounds, region=row["region"], workspace=workspace,
                               plan_digest=digest, include_imagery=include_imagery,
                               paid_referee=paid_referee, frozen=True)


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
        if run.get("status") == "succeeded":
            total += 1.0
        else:
            total += run_fraction(statuses[i] if i < len(statuses) else None, weights)
    return round(min(total / len(regions), 0.99), 4)


def progress_summary(job: dict, statuses: list[dict | None], weights: dict[str, float],
                     now: float) -> dict:
    runs = job.get("runs") or []
    current = next((i for i, run in enumerate(runs) if run.get("status") == "running"), None)
    status = statuses[current] if current is not None and current < len(statuses) else None
    step = next((s for s in (status or {}).get("steps", []) if s.get("status") == "RUNNING"),
                None)
    known = job.get("status") == "succeeded" or any(
        (s or {}).get("steps") or r.get("status") == "succeeded"
        for r, s in zip(runs, statuses))
    cloud = job.get("target", "local") != "local"
    if job.get("waiting"):
        stage = "Waiting for workspace"
    elif (job.get("final_combine") or {}).get("status") == "running":
        stage = "Combining map outputs"
    elif cloud:
        stage = "Running on the shared VM"
    elif step:
        stage = step.get("label") or step["key"]
    else:
        stage = ("Preparing area" if current is not None else
                 "Finalizing build" if runs and all(r.get("status") in TERMINAL for r in runs)
                 else "Preparing build")
    started = job.get("started_clock")
    finished = job.get("finished")
    end = datetime.fromisoformat(finished).timestamp() if finished else now
    elapsed = max(0, round(end - started)) if started is not None else 0
    estimate = job.get("estimated_total_s")
    active = job.get("status") == "running"
    eta = max(0, round(float(estimate) - elapsed)) if active and estimate is not None else None
    return {
        "progress": progress(job, statuses, weights),
        "progress_known": bool(known and not cloud) or job.get("status") == "succeeded",
        "current_stage": stage if active else None,
        "current_region": runs[current]["region"] if current is not None and not cloud else None,
        "area_number": current + 1 if current is not None and not cloud else None,
        "elapsed_s": elapsed,
        "eta_s": 0 if job.get("status") in TERMINAL else eta,
        "eta_overdue": active and eta == 0,
    }


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
        weights = step_weights(self.data)
        now = time.time()
        recent = [j for j in self.list() if j.get("status") in TERMINAL and j.get("finished")
                  and now - datetime.fromisoformat(j["finished"]).timestamp() <= RECENT_QUEUE_S]
        recent.sort(key=lambda j: j["finished"], reverse=True)

        def compact(job: dict) -> dict:
            view = self._view(job, weights, now)
            return {k: view.get(k) for k in (
                "job_id", "target", "regions", "status", "created", "started", "finished",
                "estimated_total_s", "current_index", "waiting", "approved", "queue_position",
                "progress", "progress_known", "current_stage", "current_region", "area_number",
                "elapsed_s", "eta_s", "eta_overdue", "last_activity_at", "preview_url",
                "gate_result", "published_version", "error", "reports")}

        out = {}
        for name, (active, queued) in snapshot.items():
            jobs = []
            for job_id in ([active] if active else []) + queued:
                try:
                    job = self.load(job_id)
                except JobError:
                    continue
                job = compact(job)
                job["queue_position"] = queued.index(job_id) + 1 if job_id in queued else None
                jobs.append(job)
            finished = [j for j in recent
                        if (j.get("lane") or lane_for(j.get("target", "local"))) == name
                        and j["job_id"] not in ([active] if active else []) + queued]
            out[name] = {"targets": list(LANES[name]), "active": active,
                         "jobs": jobs, "queued": len(queued),
                         "recent": [compact(j) for j in finished[:RECENT_QUEUE_LIMIT]]}
        return out

    # ---- lifecycle -----------------------------------------------------
    def start(self, estimate: dict, paid_referee: bool = False,
              cloud_cost_acknowledged: bool = False) -> str:
        """Queue a job from an approved estimate; it runs when its lane is free."""
        job = self._prepare_job(estimate, paid_referee, cloud_cost_acknowledged)
        self._enqueue([job])
        return job["job_id"]

    def start_batch(self, estimate: dict, paid_referee: bool = False,
                    cloud_cost_acknowledged: bool = False) -> list[str]:
        """Queue one independently cancellable job per estimated area, in order."""
        rows = estimate.get("regions") or []
        if not rows:
            raise JobError("the estimate names no regions")
        if (estimate.get("target", "local") == "local"
                and (os.environ.get("SOLAR_RASTER_JOB_BOUNDS")
                     or any(row.get("raster_auto_estimate") for row in rows))
                and len(rows) != 1):
            raise JobError("operator-bounded local jobs must be submitted one area at a time", 409)
        if any(row.get("selection_count", 1) != 1 for row in rows):
            raise JobError("this estimate groups grid cells; estimate again for separate jobs", 409)
        jobs = []
        for row in rows:
            child = {
                **estimate, "regions": [row],
                "totals": {key: row.get(key) for key in
                           ("buildings", "download_gb", "download_s", "compute_s", "total_s", "disk_gb")},
                "paid_referee": referee_note([row]),
            }
            if estimate.get("target", "local") == "local" and estimate.get("raster_plan") is not None:
                try:
                    plan = estimate["raster_plan"]
                    if ("raster_sources" not in row and isinstance(plan, dict)
                            and isinstance(plan.get("regions"), list)
                            and all(isinstance(item, dict) and item.get("region") != row["region"]
                                    for item in plan["regions"])):
                        child["raster_plan"] = None
                    else:
                        child["raster_plan"] = plan_for_regions(plan, [row["region"]])
                except (KeyError, TypeError, ValueError) as exc:
                    raise JobError(f"the estimate's raster plan is invalid; estimate again: {exc}", 409) from exc
            if estimate.get("target", "local") != "local":
                if len(rows) > 1 and not row.get("cloud"):
                    raise JobError("this estimate lacks per-job cloud costs; estimate again", 409)
                child["cloud"] = row.get("cloud") or estimate.get("cloud")
            jobs.append(self._prepare_job(child, paid_referee, cloud_cost_acknowledged))
        if len(jobs) > 1 and jobs[-1]["lane"] == "local":
            jobs[-1]["combine_existing"] = True
        self._enqueue(jobs)
        return [job["job_id"] for job in jobs]

    def _prepare_job(self, estimate: dict, paid_referee: bool,
                     cloud_cost_acknowledged: bool) -> dict:
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
        raster_plan = estimate.get("raster_plan")
        raster_admission = None
        if (lane_name == "local" and raster_plan is None
                and any("raster_sources" in row for row in estimate["regions"])):
            raise JobError("the estimate's raster plan is missing; estimate again", 409)
        if lane_name == "local" and raster_plan is not None:
            if not isinstance(raster_plan, dict) or not isinstance(raster_plan.get("regions"), list):
                raise JobError("the estimate's raster plan is invalid; estimate again", 409)
            planned_regions = raster_plan["regions"]
            if any(not isinstance(entry, dict) for entry in planned_regions):
                raise JobError("the estimate's raster plan is invalid; estimate again", 409)
            if any(not isinstance(entry.get("inputs"), list) for entry in planned_regions):
                raise JobError("the estimate's raster source selections are invalid; estimate again", 409)
            batch = raster_plan.get("batch")
            if batch is not None and (not isinstance(batch, dict)
                                      or not isinstance(batch.get("inputs"), list)
                                      or any(not isinstance(item, dict) for item in batch["inputs"])):
                raise JobError("the estimate's raster batch is invalid; estimate again", 409)
            mapped = any(isinstance(status, dict) and status.get("state") == "mapped"
                   for entry in planned_regions for status in entry["inputs"]) or (
                       batch is not None and any(item.get("source") is not None
                                                 for item in batch["inputs"]))
            automatic = len(regions) == 1 and estimate["regions"][0].get("raster_auto_estimate")
            if mapped and not os.environ.get("SOLAR_RASTER_JOB_BOUNDS") and not automatic:
                raise JobError("mapped raster plans are preview-only; live cache acquisition "
                               "requires operator-supplied whole-job bounds", 409)
            counts = {region: sum(entry.get("region") == region for entry in planned_regions)
                      for region in regions}
            if any(count > 1 for count in counts.values()):
                raise JobError("the estimate has duplicate raster selections; estimate again", 409)
            for row in estimate["regions"]:
                if "raster_sources" in row and counts[row["region"]] != 1:
                    raise JobError("the estimate's raster selection is missing; estimate again", 409)
            if not all(count == 1 for count in counts.values()):
                raster_plan = None
            else:
                try:
                    plan_for_regions(raster_plan, regions)
                except (KeyError, TypeError, ValueError) as exc:
                    raise JobError(f"the estimate's raster plan is invalid; estimate again: {exc}", 409) from exc
            if mapped or os.environ.get("SOLAR_RASTER_JOB_BOUNDS"):
                if len(regions) != 1 or raster_plan is None:
                    raise JobError("operator-bounded jobs require one region and its approved plan", 409)
                try:
                    from src.raster_job_bounds import load_job_bounds, processing_for_snapshot
                    if os.environ.get("SOLAR_RASTER_JOB_BOUNDS"):
                        bounds = load_job_bounds(
                            Path(os.environ["SOLAR_RASTER_JOB_BOUNDS"]), region=regions[0],
                            workspace=self.root, plan_digest=plan_digest(raster_plan),
                            include_imagery=include_imagery, paid_referee=paid_referee)
                    else:
                        bounds = automatic_raster_bounds(
                            estimate["regions"][0], self.data, self.root,
                            plan_digest(raster_plan), include_imagery, paid_referee)
                    processing_for_snapshot(bounds, raster_plan, regions[0])
                    raster_admission = bounds
                except (KeyError, TypeError, OSError, ValueError) as exc:
                    raise JobError(f"job bounds are invalid: {exc}", 409) from exc
        elif lane_name == "local" and os.environ.get("SOLAR_RASTER_JOB_BOUNDS"):
            raise JobError("operator-bounded jobs require an approved raster plan", 409)
        job_id = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + secrets.token_hex(3)
        job = {
            "job_id": job_id, "estimate_id": estimate.get("estimate_id"),
            "regions": regions, "target": target, "lane": lane_name,
            "paid_referee": bool(paid_referee),
            "include_imagery": include_imagery,
            "survey_overrides": survey_overrides,
            "raster_plan": raster_plan if lane_name == "local" else None,
            "raster_admission": raster_admission,
            "raster_plan_sha256": (plan_digest(raster_plan)
                                   if lane_name == "local" and raster_plan is not None else None),
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
        return job

    def _enqueue(self, jobs: list[dict]) -> None:
        with self.lock:
            lane = self.lanes[jobs[0]["lane"]]
            if lane.name == "local" and any(job.get("raster_admission") for job in jobs):
                if len(jobs) != 1 or lane.active or lane.queue:
                    raise JobError("Mapped jobs require an idle local queue", 409)
            if lane.name == "local" and (lane.active or lane.queue):
                existing = ([lane.active] if lane.active else []) + lane.queue
                if any(self.load(identifier).get("raster_admission") for identifier in existing):
                    raise JobError("A mapped job is already queued or running; wait for it "
                                   "to finish before submitting another", 409)
            for job in jobs:
                self._save(job)
            lane.queue.extend(job["job_id"] for job in jobs)
            if lane.thread is None or not lane.thread.is_alive():
                lane.thread = threading.Thread(target=self._worker, args=(lane,), daemon=True,
                                               name=f"lane-{lane.name}")
                lane.thread.start()

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
            if owner.get("reservation_id"):
                from tools.pipeline_queue import recover_stale_raster_workspace
                if recover_stale_raster_workspace(self.data / "pipeline-workspace.lock"):
                    log.write("Recovered a stopped raster job's reservation and workspace lock.\n")
                    log.flush()
                    continue
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
                    env = {
                        "SOLAR_RASTER_PLAN_JOB": (
                            str((job_dir / "job.json").resolve()) if job.get("raster_plan") else ""),
                        "SOLAR_RASTER_PLAN_SHA256": job.get("raster_plan_sha256") or "",
                        "SOLAR_RASTER_JOB_BOUNDS_SNAPSHOT": (
                            str((job_dir / "job.json").resolve()) if job.get("raster_admission") else ""),
                        "SOLAR_RASTER_JOB_BOUNDS": "",
                        "SOLAR_SURVEY_OVERRIDES": json.dumps(job.get("survey_overrides") or {}),
                        "SOLAR_SURVEY_OVERRIDES_B64": "",
                    }
                    code = self._stream(self._launch(lane, cmd, log, env), log, on_line)
                    with self.lock:
                        lane.process = None
                    run["exit_code"] = code
                    run["finished"] = _now()
                    run["status"] = ("cancelled" if lane.cancel_requested
                                     else "succeeded" if code == 0 else "failed")
                    self._save(job)
                built = [r["region"] for r in job["runs"] if r["status"] == "succeeded"]
                if (len(built) > 1 or job.get("combine_existing")) and not lane.cancel_requested:
                    every = list_regions(self.data / "out", "summary.json")
                    regions = every or built
                    if len(regions) > 1:
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
            job["error"] = f"job runner failed ({type(exc).__name__}): {exc}"
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
        job = self._view(job, step_weights(self.data), time.time())
        job["active"] = job_id in self.active_jobs().values()
        with self.lock:
            lane = self.lanes.get(job.get("lane") or "local")
            job["queue_position"] = (lane.queue.index(job_id) + 1
                                     if lane and job_id in lane.queue else None)
        return job

    def _view(self, job: dict, weights: dict[str, float], now: float) -> dict:
        statuses = [self.run_status(r) for r in job.get("runs", [])]
        activity = [job["started_clock"]] if job.get("started_clock") is not None else []
        job["reports"] = []
        for run, status in zip(job.get("runs", []), statuses):
            run["status_json"] = status
            if run.get("run_dir"):
                path = Path(run["run_dir"])
                path = path if path.is_absolute() else self.root / path
                if path.resolve().is_relative_to(self.data.resolve()):
                    relative = path.resolve().relative_to(self.root.resolve()) \
                        if path.resolve().is_relative_to(self.root.resolve()) else None
                    run["report_url"] = "/" + relative.as_posix() + "/report.md" if relative else None
                    if run["report_url"]:
                        job["reports"].append({"region": run["region"], "url": run["report_url"]})
                    if status:
                        activity.append((path / "status.json").stat().st_mtime)
        log = self._job_dir(job["job_id"]) / "stdout.log"
        if log.exists():
            activity.append(log.stat().st_mtime)
        job["last_activity_at"] = max(activity) if activity else None
        job.update(progress_summary(job, statuses, weights, now))
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
