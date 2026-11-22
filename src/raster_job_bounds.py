"""Operator-supplied allocation bounds for one approved local queue task."""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.raster_cache import CachePolicy, RasterCache
from src.raster_disk_plan import DiskAllocation, DiskRequirements
from src.raster_cache_config import load_cache_config
from src.raster_processing import validate_processing
from src.raster_reservations import QueueCleanupPolicy
from src.region_ids import validate


def validate_job_bounds(raw: object, *, region: str, workspace: Path, plan_digest: str,
                        include_imagery: bool, paid_referee: bool, frozen: bool) -> dict:
    """Validate bounds and their workload identity without trusting defaults."""
    fields = {"version", "region", "workspace", "plan_sha256", "include_imagery",
              "paid_referee", "evidence", "cache_policy", "work_dir", "output_dir",
              "mosaic_bytes", "scratch_bytes", "output_bytes", "export_bytes",
              "extra_allocations"}
    if not isinstance(raw, dict) or not fields | ({"cache"} if frozen else set()) <= set(raw) \
            or set(raw) - fields - ({"cache", "cleanup", "freshness"} if frozen else set()) - {"processing"} \
            or type(raw["version"]) is not int \
            or raw["version"] != 1:
        raise ValueError("Raster job bounds must contain exactly the version 1 fields")
    validate(region)
    if (raw["region"] != region or raw["workspace"] != str(workspace.resolve())
            or raw["plan_sha256"] != plan_digest or type(raw["include_imagery"]) is not bool
            or raw["include_imagery"] != include_imagery
            or type(raw["paid_referee"]) is not bool or raw["paid_referee"] != paid_referee):
        raise ValueError("Raster job bounds do not match this workspace, region, plan or options")
    if not isinstance(plan_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", plan_digest):
        raise ValueError("Raster job bounds require an approved plan digest")
    if not isinstance(raw["evidence"], str) or not raw["evidence"].strip():
        raise ValueError("Raster job bounds require an explanation of the estimates")
    if "processing" in raw:
        processing = raw["processing"]
        if not isinstance(processing, dict) or any(
            role not in ("dsm", "imagery", "reference_imagery") for role in processing
        ):
            raise ValueError("Invalid raster processing roles")
        for options in processing.values():
            if not isinstance(options, dict) or set(options) != {"resolution", "resampling"}:
                raise ValueError("Raster processing requires resolution and resampling")
            validate_processing(options["resolution"], options["resampling"])
    for key in ("cache_policy", "work_dir", "output_dir"):
        if not isinstance(raw[key], str) or not Path(raw[key]).is_absolute():
            raise ValueError(f"{key} must be an absolute path")
    for key in ("mosaic_bytes", "scratch_bytes", "output_bytes", "export_bytes"):
        if type(raw[key]) is not int or raw[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    extras = raw["extra_allocations"]
    if not isinstance(extras, list):
        raise ValueError("extra_allocations must be a list")
    names = {"cache_transfer", "exports", "mosaics", "scratch", "outputs"}
    for extra in extras:
        if (not isinstance(extra, dict) or set(extra) != {"name", "directory", "bytes"}
                or not isinstance(extra["name"], str) or not extra["name"].strip()
                or extra["name"] in names or not isinstance(extra["directory"], str)
                or not Path(extra["directory"]).is_absolute()
                or type(extra["bytes"]) is not int or extra["bytes"] <= 0):
            raise ValueError("Invalid or duplicate extra allocation")
        names.add(extra["name"])
    for directory in (raw["work_dir"], raw["output_dir"],
                      *(extra["directory"] for extra in extras)):
        if not Path(directory).is_dir():
            raise ValueError(f"Raster job bounds directory does not exist: {directory}")
    if frozen:
        cache = raw["cache"]
        if (not isinstance(cache, dict)
                or set(cache) != {"directory", "max_bytes", "min_free_bytes", "unknown_asset_bytes"}
                or not isinstance(cache["directory"], str)
                or not Path(cache["directory"]).is_absolute()):
            raise ValueError("Invalid frozen raster cache policy")
        CachePolicy(cache["max_bytes"], cache["min_free_bytes"], cache["unknown_asset_bytes"])
        if "cleanup" in raw:
            policy = job_cleanup_policy(raw)
            if policy.high_water_bytes is not None and policy.high_water_bytes > cache["max_bytes"]:
                raise ValueError("Cleanup high water cannot exceed the cache budget")
        if "freshness" in raw:
            freshness = raw["freshness"]
            if (not isinstance(freshness, dict) or set(freshness) != {"metadata_refresh_s"}
                    or type(freshness["metadata_refresh_s"]) is not int
                    or freshness["metadata_refresh_s"] <= 0):
                raise ValueError("Invalid frozen raster metadata refresh interval")
        return raw
    config, sources = load_cache_config(Path(raw["cache_policy"]))
    if any(sources[name] == "default" for name in ("max_bytes", "min_free_bytes")):
        raise ValueError("Raster job cache policy must explicitly set max_bytes and min_free_bytes")
    return {**raw, "cache": {
        "directory": str(config.directory), "max_bytes": config.max_bytes,
        "min_free_bytes": config.min_free_bytes, "unknown_asset_bytes": config.unknown_asset_bytes},
        "cleanup": {
            "lookahead": config.queue_lookahead, "near_distance_m": config.near_distance_m,
            "max_unused_age_s": config.max_unused_age_s, "partial_expiry_s": config.partial_expiry_s,
            "high_water_bytes": config.high_water_bytes, "target_bytes": config.target_bytes},
        "freshness": {"metadata_refresh_s": config.metadata_refresh_s}}


def load_job_bounds(path: Path, *, region: str, workspace: Path, plan_digest: str,
                    include_imagery: bool, paid_referee: bool) -> dict:
    """Freeze explicit per-job bounds and effective cache policy at submission."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read raster job bounds: {exc}") from exc
    return validate_job_bounds(raw, region=region, workspace=workspace, plan_digest=plan_digest,
                               include_imagery=include_imagery, paid_referee=paid_referee,
                               frozen=False)


def load_frozen_job_bounds(path: Path, *, region: str, workspace: Path, plan_digest: str,
                           include_imagery: bool, paid_referee: bool) -> dict:
    """Load an already-frozen bounds snapshot from a persisted web job."""
    try:
        job = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read raster job bounds snapshot: {exc}") from exc
    bounds = job.get("raster_admission") if isinstance(job, dict) else None
    return validate_job_bounds(bounds, region=region, workspace=workspace,
                               plan_digest=plan_digest, include_imagery=include_imagery,
                               paid_referee=paid_referee, frozen=True)


def job_resources(bounds: dict) -> tuple[RasterCache, DiskRequirements]:
    """Build resource objects from the immutable, validated task snapshot."""
    cache = bounds["cache"]
    directory = Path(cache["directory"])
    raster_cache = RasterCache(directory, CachePolicy(
        cache["max_bytes"], cache["min_free_bytes"], cache["unknown_asset_bytes"]))
    requirements = DiskRequirements(
        directory, Path(bounds["work_dir"]), Path(bounds["output_dir"]),
        bounds["mosaic_bytes"], bounds["scratch_bytes"], bounds["output_bytes"],
        cache["min_free_bytes"], bounds["export_bytes"],
        tuple(DiskAllocation(item["name"], Path(item["directory"]), item["bytes"])
              for item in bounds["extra_allocations"]))
    return raster_cache, requirements


def processing_for_snapshot(bounds: dict, snapshot: dict, region: str) -> dict:
    """Require one operator-selected processing configuration per mapped role."""
    from src.raster_plan_handoff import plan_for_regions

    selected = plan_for_regions(snapshot, [region])
    mapped = {item["role"] for item in selected["regions"][0]["inputs"]
              if item["state"] == "mapped"}
    processing = bounds.get("processing", {})
    if set(processing) != mapped:
        raise ValueError("Raster processing options must match the approved mapped roles")
    return processing


def job_cleanup_policy(bounds: dict) -> QueueCleanupPolicy:
    """Reconstruct the validated cleanup policy frozen with a submitted job."""
    cleanup = bounds.get("cleanup")
    fields = {"lookahead", "near_distance_m", "max_unused_age_s", "partial_expiry_s",
              "high_water_bytes", "target_bytes"}
    if not isinstance(cleanup, dict) or set(cleanup) != fields:
        raise ValueError("Invalid frozen raster cleanup policy")
    return QueueCleanupPolicy(**cleanup)
