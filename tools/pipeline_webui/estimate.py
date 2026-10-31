"""Time, download and disk estimates shown before a web-panel build starts.

Every figure is a planning assumption, not a limit. The arithmetic is pure;
network lookups (building counts for localities not yet prepared) are
injected so tests run offline. Defaults live in estimate_defaults.json, not
config.py, because editing config.py changes every building's build key.

A job may only start from an estimate that is stored and unexpired, so the
person always confirms the figures that were actually shown.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import statistics
import threading
import time
from pathlib import Path
from typing import Callable

from src.region_ids import file_slug, is_locality_id, validate
from tools.pipeline_webui.common import DATA, read_json, web_dir, write_json_atomic

DEFAULTS_FILE = Path(__file__).with_name("estimate_defaults.json")
TARGETS = ("local", "developer", "published")
MAX_REGIONS = 25            # tools/find_area.py DETAILS_LIMIT
TTL_SECONDS = 30 * 60
CALIBRATION_RUNS = 10
UNTIMED_STEPS = {"preflight", "fetch", "preview"}
FINISHED_OVERALL = {"PASS", "DEGRADED"}
DISK_WARN_FRACTION = 0.8
_ID = re.compile(r"[0-9a-f]{16}")
DISCLAIMER = ("Estimates are planning assumptions, not limits; actual time and "
              "download can be higher.")

SizeLookup = Callable[[str], dict]


class EstimateError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def load_defaults(path: Path = DEFAULTS_FILE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def calibrate(data: Path, defaults: dict) -> dict:
    """Seconds per building: median of the last finished runs, else the default."""
    data = Path(data)
    rates = []
    runs_dir = data / "pipeline_runs"
    run_dirs = sorted((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name,
                      reverse=True) if runs_dir.is_dir() else []
    for run_dir in run_dirs:
        status = read_json(run_dir / "status.json")
        if not isinstance(status, dict) or status.get("overall") not in FINISHED_OVERALL:
            continue
        summary = read_json(data / "out" / str(status.get("area")) / "summary.json") or {}
        n = summary.get("n") or 0
        seconds = sum(float(s.get("seconds") or 0) for s in status.get("steps", [])
                      if s.get("key") not in UNTIMED_STEPS)
        if n > 0 and seconds > 0:
            rates.append(seconds / n)
        if len(rates) >= CALIBRATION_RUNS:
            break
    if rates:
        return {"source": "history", "seconds_per_building": round(statistics.median(rates), 3),
                "runs_used": len(rates)}
    return {"source": "defaults", "seconds_per_building": defaults["seconds_per_building"],
            "runs_used": 0}


def _bbox_km2(bbox) -> float:
    # PROJ runs in a child process; see geo_worker for why the server never loads it.
    from tools.pipeline_webui import geo_worker
    return geo_worker.run(["km2", json.dumps(list(bbox))])["bbox_km2"]


def region_size(region: str, data: Path, lookup: SizeLookup | None) -> dict:
    """{buildings, bbox, bbox_km2, source} for a region, from disk when possible."""
    data = Path(data)
    locality = read_json(data / "regions" / region / "locality.json")
    if isinstance(locality, dict) and locality.get("bbox"):
        return {"buildings": locality.get("buildings"), "bbox": locality["bbox"],
                "bbox_km2": locality.get("bbox_km2") or _bbox_km2(locality["bbox"]),
                "source": "locality.json"}
    cache = web_dir(data) / "cache" / "estimates" / f"{file_slug(region)}.json"
    cached = read_json(cache)
    if isinstance(cached, dict) and cached.get("bbox"):
        return {**cached, "source": "cache"}
    if not is_locality_id(region):
        import config
        bbox = getattr(config, "REGIONS", {}).get(region)
        if not bbox:
            raise LookupError(f"{region} is not a configured region")
        outlines = read_json(data / "regions" / region / "building_outlines.geojson") or {}
        n = len(outlines.get("features", [])) if outlines else None
        return {"buildings": n, "bbox": list(bbox), "bbox_km2": _bbox_km2(bbox),
                "source": "config.REGIONS"}
    if lookup is None:
        raise LookupError("building count needs a LINZ lookup")
    size = lookup(region)
    write_json_atomic(cache, {k: size[k] for k in ("buildings", "bbox", "bbox_km2")})
    return {**size, "source": "LINZ"}


def linz_size_lookup(api_key_fn: Callable[[], str]) -> SizeLookup:
    """Building count and bbox as tools/find_area.py --details reads them."""
    def lookup(region: str) -> dict:
        from tools.pipeline_webui import geo_worker
        key = api_key_fn()
        if not key:
            raise LookupError("LINZ_API_KEY is not set (environment, .env or Secret Manager)")
        # The key goes in the child's environment, never its arguments.
        return geo_worker.run(["size", region], env={"LINZ_API_KEY": key})
    return lookup


def _survey(bbox, region: str) -> dict:
    from src.surveys import survey_for
    try:
        sv = survey_for(bbox, region)
    except LookupError as exc:
        return {"name": None, "elevation": None, "error": str(exc)}
    return {"name": sv.get("name", "default"), "elevation": sv.get("elevation") or "lidar"}


def estimate_region(region: str, size: dict, survey: dict, data: Path, defaults: dict,
                    seconds_per_building: float) -> dict:
    """Pure arithmetic for one region (see estimate_defaults.json for units)."""
    rdir = Path(data) / "regions" / region
    n = size.get("buildings")
    km2 = float(size.get("bbox_km2") or 0)
    cached = {
        "outlines": (rdir / "building_outlines.geojson").exists(),
        "lidar": (rdir / "pointcloud_tiles.txt").exists(),
        "dsm": (rdir / "dsm_mosaic.tif").exists(),
        "imagery": (rdir / "imagery_mosaic.tif").exists(),
        "reference_imagery": (rdir / "reference_imagery_mosaic.tif").exists(),
    }
    warnings, blockers = [], []
    synthetic = survey.get("elevation") == "synthetic"
    if survey.get("error"):
        blockers.append("No survey covers this area: " + survey["error"])
    if synthetic:
        warnings.append("No LiDAR here: builds on a modelled surface and every roof pitch is guessed.")
    imagery_gb = 0.0 if cached["imagery"] else km2 * defaults["imagery_gb_per_km2"]
    reference_gb = 0.0 if cached["reference_imagery"] else km2 * defaults["reference_imagery_gb_per_km2"]
    lidar_gb = 0.0 if (cached["lidar"] or synthetic) else km2 * defaults["lidar_gb_per_km2"]
    download_gb = imagery_gb + reference_gb + lidar_gb
    download_s = download_gb * 8000 / max(defaults["download_mbps"], 0.1)
    if not cached["outlines"]:
        download_s += defaults["linz_export_overhead_s"]
    if n is None:
        warnings.append("Building count unknown until the outlines are fetched; time excludes it.")
    buildings = n or 0
    compute_s = defaults["fixed_overhead_s"] + buildings * seconds_per_building
    disk_gb = download_gb + buildings * defaults["output_mb_per_building"] / 1024
    if buildings > defaults["large_region_buildings"]:
        warnings.append(f"{buildings:,} buildings. {defaults['large_region_note']}")
    return {
        "region": region, "buildings": n, "bbox": size.get("bbox"), "bbox_km2": round(km2, 3),
        "size_source": size.get("source"), "survey": survey.get("name"),
        "elevation": survey.get("elevation"), "cached": cached,
        "download_gb": round(download_gb, 3), "download_s": round(download_s),
        "compute_s": round(compute_s), "total_s": round(download_s + compute_s),
        "disk_gb": round(disk_gb, 3), "warnings": warnings, "blockers": blockers,
    }


def referee_note(rows: list[dict]) -> dict:
    from src import build_plan
    cap = build_plan.referee_cap_usd()
    # Same conservative bound as build_plan's prompt text: the per-region cap for every region.
    usd = cap * len(rows)
    return {"default": False, "usd_upper": round(usd, 2), "usd_per_roof": build_plan.REFEREE_USD_PER_ROOF,
            "cap_usd_per_region": cap, "text": build_plan.referee_estimate_text(len(rows))}


def estimate(regions: list[str], data: Path = DATA, target: str = "local",
             lookup: SizeLookup | None = None, defaults: dict | None = None,
             disk_free: int | None = None, survey_fn: Callable = _survey,
             now: float | None = None, cloud=None) -> dict:
    if target not in TARGETS:
        raise EstimateError(f"unknown target: {target}")
    is_cloud = target != "local"
    if is_cloud and cloud is None:
        raise EstimateError("cloud targets are not configured on this server")
    regions = validate_regions(regions)
    data = Path(data)
    defaults = defaults or load_defaults()
    calibration = calibrate(data, defaults)
    spb = calibration["seconds_per_building"]
    rows = []
    # The VM fetches its own inputs, so a cloud estimate counts nothing as cached here.
    cache_dir = data / "_cloud_estimate_uncached" if is_cloud else data
    for region in regions:
        try:
            size = region_size(region, data, lookup)
        except Exception as exc:
            # LookupError text is ours; other failures can carry a keyed URL, so show the type only.
            msg = str(exc) if isinstance(exc, LookupError) else (
                f"LINZ lookup failed ({type(exc).__name__}); try again")
            # The VM resolves the area itself, so a failed local lookup only weakens a cloud estimate.
            rows.append({"region": region, "buildings": None,
                         "warnings": [msg + " (the VM resolves the area itself)"] if is_cloud else [],
                         "blockers": [] if is_cloud else [msg], "download_gb": 0,
                         "download_s": 0, "compute_s": 0, "total_s": 0, "disk_gb": 0, "cached": {}})
            continue
        rows.append(estimate_region(region, size, survey_fn(size["bbox"], region), cache_dir,
                                    defaults, spb))
    totals = {k: round(sum(r.get(k) or 0 for r in rows), 3)
              for k in ("buildings", "download_gb", "download_s", "compute_s", "total_s", "disk_gb")}
    warnings, blockers = [], []
    cloud_block = None
    if is_cloud:
        free_gb = None
        cloud_block = cloud_estimate(cloud, target, totals["total_s"])
        warnings.extend(cloud_block.pop("warnings"))
        blockers.extend(cloud_block["blockers"])
    else:
        free = (shutil.disk_usage(data if data.exists() else data.parent).free
                if disk_free is None else disk_free)
        free_gb = free / 1e9
        if totals["disk_gb"] > free_gb:
            blockers.append(f"Needs about {totals['disk_gb']:.1f} GB; {free_gb:.1f} GB free.")
        elif totals["disk_gb"] > DISK_WARN_FRACTION * free_gb:
            warnings.append(f"Uses about {totals['disk_gb']:.1f} GB of the {free_gb:.1f} GB free.")
    now = time.time() if now is None else now
    machine = ({"cpu_count": os.cpu_count(), "workers": 1, "disk_free_gb": round(free_gb, 1),
                "note": "Builds in one checkout run one at a time."} if not is_cloud else
               {"vm": f"{cloud_block['project']}/{cloud_block['zone']}/{cloud_block['vm']}",
                "note": "Developer and published builds share one VM and run one at a time."})
    return {
        "estimate_id": secrets.token_hex(8), "status": "ready", "target": target,
        "regions": rows, "totals": totals, "warnings": warnings,
        "blockers": blockers + [f"{r['region']}: {b}" for r in rows for b in r["blockers"]],
        "machine": machine, "cloud": cloud_block,
        "cloud_cost_nzd": cloud_block["cost_nzd"] if cloud_block else "0",
        "paid_referee": referee_note(rows),
        "assumptions": {k: v for k, v in defaults.items() if not k.startswith("_")},
        "assumption_notes": defaults.get("_notes"), "calibration": calibration,
        "disclaimer": DISCLAIMER, "created": now, "expires_at": now + TTL_SECONDS,
    }


def cloud_estimate(cloud, target: str, estimated_seconds: float) -> dict:
    """VM state, cost and blockers for a developer or published build. All checks are free."""
    cfg = cloud.config()
    status = cloud.vm_status(refresh=True)
    warnings, blockers = [], []
    block = {"project": cfg["project"], "vm": cfg["vm"], "zone": cfg["zone"],
             "ref": cfg["ref"], "vm_status": status["status"], "vm_error": status["error"],
             "preview_user": None, "preview_url": None}
    if status["error"]:
        blockers.append(f"VM status unavailable: {status['error']}")
    elif status["status"] == "RUNNING":
        warnings.append("The VM is already running. If another build is in progress the "
                        "code sync refuses and this job stops before building anything.")
    elif status["status"] not in ("TERMINATED", "STOPPED"):
        blockers.append(f"The VM is {status['status']}; wait until it is RUNNING or stopped.")
    if target == "developer":
        more, alias = cloud.developer_blockers()
        blockers.extend(more)
        if alias:
            block["preview_user"] = alias
            block["preview_url"] = cloud.preview_url(alias)
        warnings.append("The VM builds the pushed code on " + cfg["ref"] +
                        ", not uncommitted or unpushed changes in this checkout.")
    else:
        blockers.extend(cloud.publish_blockers())
        warnings.append("Publishing changes the live map. The deploy gate runs first; if it "
                        "flags anything nothing is published (publishing past the gate stays "
                        "on the command line with --reviewed).")
    block.update(cloud.cost(target, estimated_seconds))
    block["blockers"] = blockers
    block["warnings"] = warnings
    return block


def validate_regions(regions) -> list[str]:
    if not isinstance(regions, list) or not regions:
        raise EstimateError("choose at least one region")
    if len(regions) > MAX_REGIONS:
        raise EstimateError(f"at most {MAX_REGIONS} regions per build")
    out = []
    for region in regions:
        try:
            region = validate(region)
        except ValueError as exc:
            raise EstimateError(str(exc)) from None
        if region not in out:
            out.append(region)
    return out


class EstimateStore:
    """Estimates computed in a background thread and kept for TTL_SECONDS."""

    def __init__(self, data: Path = DATA, lookup: SizeLookup | None = None, **estimate_kwargs):
        self.data = Path(data)
        self.lookup = lookup
        self.kwargs = estimate_kwargs
        self.dir = web_dir(self.data) / "estimates"

    def _path(self, estimate_id: str) -> Path:
        if not _ID.fullmatch(estimate_id or ""):
            raise EstimateError("unknown estimate", 404)
        return self.dir / f"{estimate_id}.json"

    def start(self, regions, target: str = "local", background: bool = True) -> str:
        regions = validate_regions(regions)
        if target not in TARGETS:
            raise EstimateError(f"unknown target: {target}")
        estimate_id = secrets.token_hex(8)
        now = time.time()
        write_json_atomic(self._path(estimate_id), {"estimate_id": estimate_id, "status": "pending",
                                                    "regions": regions, "created": now,
                                                    "expires_at": now + TTL_SECONDS})

        def work():
            try:
                result = estimate(regions, self.data, target, self.lookup, **self.kwargs)
                result["estimate_id"] = estimate_id
            except EstimateError as exc:
                result = {"estimate_id": estimate_id, "status": "error", "error": str(exc)}
            except Exception as exc:
                result = {"estimate_id": estimate_id, "status": "error",
                          "error": f"estimate failed ({type(exc).__name__})"}
            write_json_atomic(self._path(estimate_id), result)

        if background:
            threading.Thread(target=work, daemon=True, name=f"estimate-{estimate_id}").start()
        else:
            work()
        return estimate_id

    def get(self, estimate_id: str) -> dict:
        record = read_json(self._path(estimate_id))
        if not isinstance(record, dict):
            raise EstimateError("unknown estimate", 404)
        return record

    def usable(self, estimate_id: str, now: float | None = None) -> dict:
        """The stored estimate a job may start from, or EstimateError."""
        record = self.get(estimate_id)
        now = time.time() if now is None else now
        if record.get("status") != "ready":
            raise EstimateError("the estimate is not ready", 409)
        if now > float(record.get("expires_at") or 0):
            raise EstimateError("the estimate has expired; estimate again", 409)
        if record.get("blockers"):
            raise EstimateError("the estimate lists blockers", 409)
        return record
