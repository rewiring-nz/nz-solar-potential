"""Per-region build status for the Build data tree, keyed by region id.

Joins what this checkout knows about each region onto the national
catalogue (national_catalog.py) by region id:

  - built output: data/out/<id>/summary.json (region_catalog.py)
  - every attempt: data/pipeline_runs/*/status.json plus web panel job
    records, newest first, with the build options the job used
  - active work: queued or running jobs from the job manager
  - staleness: the summary's geometry/yield code hashes against the current
    ones (src/build_keys.py); a stale region would rebuild some buildings
  - staging and the active local release (tools/regional_staging.py,
    tools/local_releases.py)
  - grid cells: one-cell grid selections grouped under their locality

A failed latest attempt does not hide an earlier good build: `latest` is the
newest attempt and `built` is the newest output on disk, reported separately.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from src.region_ids import is_grid_selection_id
from tools.pipeline_webui import region_catalog as RC
from tools.pipeline_webui.common import DATA, read_json, web_dir

CACHE_SECONDS = 3.0
MODEL_CACHE_SECONDS = 60.0
RUN_STATUS = {"PASS": "succeeded", "DEGRADED": "succeeded", "FAIL": "failed"}
ACTIVE = {"queued", "running"}
_cache: dict[str, Any] = {}
_lock = threading.Lock()


def current_model() -> dict:
    """Geometry and yield code hashes a build would stamp now."""
    with _lock:
        hit = _cache.get("model")
        if hit and time.time() - hit[0] < MODEL_CACHE_SECONDS:
            return hit[1]
    from src.build_keys import GEOMETRY_STAGE, stage_code_hash, yield_code_hash
    model = {"geometry": stage_code_hash(GEOMETRY_STAGE), "yield": yield_code_hash()}
    with _lock:
        _cache["model"] = (time.time(), model)
    return model


def is_stale(summary_model: dict | None, model: dict) -> bool | None:
    """True when a rebuild would change some buildings; None when unknown."""
    if not isinstance(summary_model, dict) or not summary_model.get("geometry"):
        return None
    geometry = set(summary_model.get("geometry") or [])
    return geometry != {model["geometry"]} or summary_model.get("yield") != model["yield"]


def parent_of(region: str) -> str:
    """Locality id of a grid selection id (<ta>/<town>/<locality>/grid-<hex>)."""
    return region.rsplit("/", 1)[0]


def run_history(data: Path = DATA) -> dict[str, list[dict]]:
    """Every pipeline run per area, newest first."""
    out: dict[str, list[dict]] = {}
    runs_dir = Path(data) / "pipeline_runs"
    if not runs_dir.is_dir():
        return out
    for run_dir in sorted((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        status = read_json(run_dir / "status.json")
        if not isinstance(status, dict) or not status.get("area"):
            continue
        out.setdefault(status["area"], []).append({
            "run_id": run_dir.name,
            "status": RUN_STATUS.get(status.get("overall"), "running" if not status.get("finished") else "failed"),
            "overall": status.get("overall"),
            "started": status.get("started"), "finished": status.get("finished"),
            "git": (status.get("metadata") or {}).get("git_commit"),
            "report_url": f"/data/pipeline_runs/{run_dir.name}/report.md",
        })
    return out


def job_records(data: Path = DATA) -> list[dict]:
    jobs_dir = web_dir(data) / "jobs"
    if not jobs_dir.is_dir():
        return []
    jobs = [read_json(p) for p in jobs_dir.glob("*/job.json")]
    jobs = [j for j in jobs if isinstance(j, dict)]
    jobs.sort(key=lambda j: j.get("created") or "", reverse=True)
    return jobs


def attempts(data: Path = DATA, runs: dict | None = None, jobs: list | None = None) -> dict[str, list[dict]]:
    """Each region's attempts, newest first, merging job records and runs.

    A job's run links to its pipeline run by run_dir; runs started outside
    the panel appear without build options."""
    runs = run_history(data) if runs is None else runs
    jobs = job_records(data) if jobs is None else jobs
    by_run: dict[str, dict] = {}
    out: dict[str, list[dict]] = {}
    for job in jobs:
        options = {"job_id": job.get("job_id"), "target": job.get("target"),
                   "include_imagery": job.get("include_imagery"),
                   "paid_referee": job.get("paid_referee")}
        for run in job.get("runs") or []:
            region = run.get("region")
            if not region:
                continue
            run_id = Path(run["run_dir"]).name if run.get("run_dir") else None
            status = run.get("status") or job.get("status")
            if job.get("status") in ACTIVE and status not in ("succeeded", "failed", "cancelled"):
                status = job["status"] if status in (None, "pending", "queued") else status
            entry = {**options, "region": region, "status": status, "run_id": run_id,
                     "at": run.get("finished") or run.get("started") or job.get("created"),
                     "started": run.get("started"), "finished": run.get("finished")}
            if run_id:
                by_run[run_id] = entry
            else:
                out.setdefault(region, []).append(entry)
    for region, items in runs.items():
        for run in items:
            linked = by_run.pop(run["run_id"], None) or {}
            out.setdefault(region, []).append({
                "job_id": linked.get("job_id"), "target": linked.get("target"),
                "include_imagery": linked.get("include_imagery"),
                "paid_referee": linked.get("paid_referee"),
                "region": region, "run_id": run["run_id"],
                "status": linked.get("status") if linked.get("status") in ACTIVE else run["status"],
                "overall": run.get("overall"), "git": run.get("git"),
                "at": run.get("finished") or run.get("started"),
                "started": run.get("started"), "finished": run.get("finished"),
                "report_url": run["report_url"],
            })
    for entry in by_run.values():  # job names a run whose directory has gone
        out.setdefault(entry["region"], []).append(entry)
    for items in out.values():
        items.sort(key=lambda e: _sortable(e.get("at")), reverse=True)
    return out


def _sortable(stamp) -> str:
    # Runs write +00:00 offsets, jobs write Z; both sort as text once normalised.
    return str(stamp or "").replace("+00:00", "Z")


def _staging(artifacts) -> tuple[dict[str, dict], dict[str, str], set[str]]:
    staged: dict[str, dict] = {}
    live: dict[str, str] = {}
    if artifacts is None:
        return staged, live, set()
    try:
        staging_now = artifacts.staging_regions()
    except Exception:
        staging_now = set()
    try:
        for item in artifacts.staging.current_list():
            region = item.get("region_id")
            if region:
                staged[region] = {"staging_id": item.get("staging_id"), "staged_at": item.get("staged_at"),
                                  "built_at": item.get("built_at"), "error": item.get("error")}
    except Exception:
        pass
    try:
        active = artifacts.releases.state().get("active")
        if active:
            for region in artifacts.releases.manifest(active).get("region_builds") or {}:
                live[region] = active
    except Exception:
        pass
    return staged, live, staging_now


def _grids(data: Path, history: dict[str, list[dict]], built: dict[str, dict]) -> dict[str, dict]:
    """Grid cells per locality for the newest grid size that has an attempt.

    A grid is hidden once its locality has a successful build that finished
    after the newest cell attempt: the cells were combined."""
    by_parent: dict[str, dict[int, dict]] = {}
    for path in (web_dir(data) / "selections").glob("*.json"):
        request = read_json(path)
        if not isinstance(request, dict) or not request.get("parent_region"):
            continue
        region = path.stem.replace("__", "/")
        if not is_grid_selection_id(region):
            continue
        tries = history.get(region) or []
        if not tries and region not in built:
            continue
        size = int(request.get("grid_size") or 0)
        grid = by_parent.setdefault(request["parent_region"], {}).setdefault(
            size, {"grid_size": size, "parent_linz_id": request.get("parent_linz_id"),
                   "cells": {}, "last_attempt": ""})
        for grid_id in request.get("grid_ids") or []:
            latest = tries[0] if tries else None
            grid["cells"][grid_id] = {
                "region": region, "buildings": request.get("buildings"),
                "latest": latest, "built": region in built,
                "built_at": (built.get(region) or {}).get("built_at"),
            }
        if tries:
            grid["last_attempt"] = max(grid["last_attempt"], _sortable(tries[0].get("at")))
    out = {}
    for parent, sizes in by_parent.items():
        grid = max(sizes.values(), key=lambda g: g["last_attempt"])
        parent_ok = [e for e in history.get(parent) or [] if e.get("status") == "succeeded"]
        combined = bool(parent_ok) and _sortable(parent_ok[0].get("at")) > grid["last_attempt"]
        if combined:
            continue
        cells = grid["cells"]
        grid["built_cells"] = sum(1 for c in cells.values() if c["built"])
        grid["active_cells"] = sum(1 for c in cells.values()
                                   if (c["latest"] or {}).get("status") in ACTIVE)
        out[parent] = grid
    return out


def status(data: Path = DATA, artifacts=None, use_cache: bool = True) -> dict:
    """{model, calibration, regions: {id: {...}}, grids: {locality id: {...}}}."""
    data = Path(data)
    key = str(data)
    with _lock:
        hit = _cache.get(key)
        if use_cache and hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    model = current_model()
    history = attempts(data)
    local = {f["id"]: f["properties"] for f in RC.catalog(data)["features"]}
    built = {}
    for region in local:
        summary = read_json(data / "out" / region / "summary.json")
        if isinstance(summary, dict):
            built[region] = summary
    staged, live, staging_now = _staging(artifacts)
    regions: dict[str, dict] = {}
    for region in set(local) | set(history) | set(staged) | set(live):
        summary = built.get(region) or {}
        tries = history.get(region) or []
        latest = tries[0] if tries else None
        good = next((t for t in tries if t.get("status") == "succeeded"), None)
        # Only local builds write data/out, so only they decide whether the
        # built output is a trustworthy thing to stage.
        local_try = next((t for t in tries if (t.get("target") or "local") == "local"), None)
        row = {
            "latest": latest,
            "history": tries[:20],
            "built": bool(summary),
            "built_at": summary.get("built_at"), "git": summary.get("git"),
            "n": summary.get("n"), "kwp": summary.get("kwp"), "kwh": summary.get("kwh"),
            "stale": is_stale(summary.get("model"), model) if summary else None,
            "options": ({k: good.get(k) for k in ("include_imagery", "paid_referee", "target")}
                        if good and good.get("job_id") else None),
            "latest_local_status": (local_try or {}).get("status"),
            "staged": staged.get(region), "live": live.get(region),
            "staging_now": region in staging_now,
            "preview_url": (local.get(region) or {}).get("preview_url"),
        }
        regions[region] = row
    from tools.pipeline_webui import estimate as E
    defaults = E.load_defaults()
    calibration = E.calibrate(data, defaults)
    result = {
        "model": model,
        "calibration": {"seconds_per_building": calibration["seconds_per_building"],
                        "fixed_overhead_s": defaults["fixed_overhead_s"],
                        "source": calibration["source"]},
        "regions": regions,
        "grids": _grids(data, history, built),
    }
    with _lock:
        _cache[key] = (time.time(), result)
    return result


def invalidate() -> None:
    with _lock:
        for key in [k for k in _cache if k != "model"]:
            _cache.pop(key, None)
