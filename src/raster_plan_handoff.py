"""Stable identity for the raster snapshot approved by a local web job."""

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path


class RasterPlanError(RuntimeError):
    """The selected raster no longer matches its acquisition plan."""


def optional_approved_raster_plan(environment: Mapping[str, str], wanted: list[str], *,
                                  allow_mapped: bool = False) -> dict | None:
    """Load an optional approval, rejecting incomplete environment handoffs."""
    path = environment.get("SOLAR_RASTER_PLAN_JOB")
    digest = environment.get("SOLAR_RASTER_PLAN_SHA256")
    if not path and not digest:
        return None
    if not path or not digest:
        raise RasterPlanError("Approved raster plan path and digest must both be supplied")
    return load_approved_raster_plan(path, wanted, digest, allow_mapped=allow_mapped)


def plan_digest(plan: dict) -> str:
    encoded = json.dumps(plan, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_approved_raster_plan(path: str, wanted: list[str], digest: str | None, *,
                              allow_mapped: bool = False) -> dict:
    """Validate a job snapshot without contacting sources."""
    if type(allow_mapped) is not bool:
        raise ValueError("allow_mapped must be boolean")
    try:
        job = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RasterPlanError(f"Cannot read approved raster plan: {exc}") from exc
    plan = job.get("raster_plan") if isinstance(job, dict) else None
    if not isinstance(plan, dict) or not isinstance(plan.get("regions"), list):
        raise RasterPlanError("Approved raster plan is missing or invalid")
    try:
        actual = plan_digest(plan)
    except (TypeError, ValueError) as exc:
        raise RasterPlanError("Approved raster plan cannot be fingerprinted") from exc
    if not digest or digest != actual:
        raise RasterPlanError("Approved raster plan changed; estimate again")
    if any(not isinstance(item, dict) for item in plan["regions"]):
        raise RasterPlanError("Approved raster selections are invalid")
    for name in wanted:
        if sum(item.get("region") == name for item in plan["regions"]) != 1:
            raise RasterPlanError(f"No unique approved raster selection for {name}")
    try:
        selected = plan_for_regions(plan, wanted)
    except (KeyError, TypeError, ValueError) as exc:
        raise RasterPlanError(f"Approved raster plan is invalid; estimate again: {exc}") from exc
    if not allow_mapped and any(status["state"] == "mapped"
           for region in selected["regions"] for status in region["inputs"]):
        raise RasterPlanError("Mapped raster plans cannot be executed by the live fetcher")
    return plan


def plan_for_regions(plan: dict, regions: list[str]) -> dict:
    """Make an independent job snapshot without refreshing its selected assets."""
    if (not isinstance(plan, dict) or not isinstance(plan.get("regions"), list)
            or not isinstance(regions, list) or not regions
            or any(not isinstance(region, str) or not region for region in regions)
            or len(regions) != len(set(regions))):
        raise ValueError("Invalid raster plan region selection")
    wanted = set(regions)
    if any(not isinstance(item, dict) for item in plan["regions"]):
        raise ValueError("Invalid raster plan regions")
    selected_regions = [item for item in plan["regions"] if item.get("region") in wanted]
    if (len(selected_regions) != len(wanted)
            or {item["region"] for item in selected_regions} != wanted):
        raise ValueError("Raster plan does not contain exactly one selection per region")
    expected = {}
    for item in selected_regions:
        statuses = item.get("inputs")
        if not isinstance(statuses, list) or any(not isinstance(status, dict) for status in statuses):
            raise ValueError("Invalid raster source selection")
        roles = set()
        for status in statuses:
            role = status.get("role")
            state = status.get("state")
            if (role not in ("dsm", "imagery", "reference_imagery")
                    or role in roles or state not in
                    ("mapped", "exports_fallback", "synthetic", "excluded",
                     "unavailable", "same_as_imagery")):
                raise ValueError("Invalid raster source selection")
            roles.add(role)
            if state in ("mapped", "exports_fallback"):
                expected[(item["region"], role)] = status
    batch = plan.get("batch")
    if batch is None:
        if expected:
            raise ValueError("Raster batch is missing selected inputs")
        return {"regions": deepcopy(selected_regions), "batch": None}
    fields = ("inputs", "export_fallbacks", "assets", "footprints_without_intersecting_assets",
              "footprint_bbox_coverage")
    if (not isinstance(batch, dict)
            or any(not isinstance(batch.get(field), list)
                   or any(not isinstance(item, dict) for item in batch[field])
                   for field in fields)):
        raise ValueError("Invalid raster batch plan")
    inputs = [item for item in batch["inputs"] if item.get("region") in wanted]
    if {(item.get("region"), item.get("role")) for item in inputs} != set(expected) \
            or len(inputs) != len(expected):
        raise ValueError("Raster batch does not match selected inputs")
    for item in inputs:
        status = expected[(item["region"], item["role"])]
        if (item.get("layer_id") != status.get("layer_id")
                or (item.get("source") is None) != (status["state"] == "exports_fallback")
                or (status["state"] == "mapped"
                    and item.get("mapping_evidence") != status.get("mapping_evidence"))):
            raise ValueError("Raster batch does not match selected source")
    fallbacks = [item for item in batch["export_fallbacks"] if item.get("region") in wanted]
    required_fallbacks = {(item["region"], item["role"]): item for item in inputs
                          if item.get("source") is None}
    seen_fallbacks = set()
    for fallback in fallbacks:
        key = (fallback.get("region"), fallback.get("role"))
        match = required_fallbacks.get(key)
        if (match is None or key in seen_fallbacks
                or fallback.get("layer_id") != match.get("layer_id")
                or fallback.get("footprints_wgs84") != match.get("footprints_wgs84")):
            raise ValueError("Raster fallback does not match selected input")
        seen_fallbacks.add(key)
    if seen_fallbacks != set(required_fallbacks):
        raise ValueError("Raster batch is missing selected fallbacks")
    if not inputs:
        return {"regions": deepcopy(selected_regions), "batch": None}
    selected = deepcopy(batch)
    for field in ("capture_coverage", "capture_surveys"):
        if field in batch:
            if (not isinstance(batch[field], list)
                    or any(not isinstance(item, dict) for item in batch[field])):
                raise ValueError(f"Invalid raster {field}")
            selected[field] = [deepcopy(item) for item in batch[field] if item.get("region") in wanted]
    for field in fields:
        if field != "assets":
            selected[field] = [deepcopy(item) for item in batch[field]
                               if item.get("region") in wanted]
    assets = []
    for asset in batch["assets"]:
        uses = asset.get("uses")
        if not isinstance(uses, list) or any(not isinstance(use, dict) for use in uses):
            raise ValueError("Invalid raster asset uses")
        retained = [use for use in uses if use.get("region") in wanted]
        if retained:
            asset = deepcopy(asset)
            asset["uses"] = deepcopy(retained)
            asset["regions"] = sorted({use["region"] for use in retained})
            assets.append(asset)
    selected["assets"] = assets
    selected["known_asset_bytes"] = sum(asset["size_bytes"] or 0 for asset in assets)
    selected["unknown_size_count"] = sum(asset["size_bytes"] is None for asset in assets)
    selected["unknown_export_size_count"] = len(selected["export_fallbacks"])
    if "cache" in selected:
        cache = selected["cache"]
        if (not isinstance(cache, dict)
                or any(asset.get("cache_state") not in
                       ("cached", "missing", "superseded", "invalid", "unversioned", "recovery_required")
                       for asset in assets)):
            raise ValueError("Invalid raster cache inspection")
        missing = [asset for asset in assets if asset["cache_state"] != "cached"]
        cache.update(cached_count=len(assets) - len(missing), missing_count=len(missing),
                     known_missing_bytes=sum(asset["size_bytes"] or 0 for asset in missing),
                     unknown_missing_count=sum(asset["size_bytes"] is None for asset in missing))
    return {"regions": deepcopy(selected_regions), "batch": selected}
