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
import tempfile
from decimal import Decimal
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


def _survey(bbox, region: str, *, prefer_national: bool | None = None) -> dict:
    from src.surveys import survey_for
    try:
        if prefer_national is None:
            sv = survey_for(bbox, region)
        else:
            sv = survey_for(bbox, region, prefer_national=prefer_national)
    except LookupError as exc:
        return {"name": None, "elevation": None, "error": str(exc),
                "imagery_available": False}
    imagery_s3 = sv.get("imagery_s3")
    reference_s3 = sv.get("reference_imagery_s3")
    same_reference = (False if reference_s3 and reference_s3 != imagery_s3 else
                      True if imagery_s3 else
                      bool(sv.get("imagery_layer")
                           and sv.get("imagery_layer") == sv.get("reference_imagery_layer")))
    return {
        "name": sv.get("name", "default"),
        "elevation": sv.get("elevation") or "lidar",
        "imagery_available": bool(sv.get("imagery_layer") or sv.get("imagery_s3")),
        "reference_imagery_available": bool(
            sv.get("reference_imagery_layer") or sv.get("reference_imagery_s3")),
        "reference_same_as_imagery": same_reference,
        "legacy_raster_sources": any(sv.get(field) for field in
                                     ("dsm_s3", "imagery_s3", "reference_imagery_s3")),
        "source_layers": {key: sv.get(key) for key in
                          ("dsm_layer", "raster_dsm_layer", "dem_layer", "lidar_tile_index_layer",
                           "imagery_layer", "reference_imagery_layer",
                           "imagery_index_layer", "imagery_partial",
                           "pointcloud_bulk_url", "pointcloud_tile_year")},
        "warning": sv.get("warning"),
    }


def estimate_region(region: str, size: dict, survey: dict, data: Path, defaults: dict,
                    seconds_per_building: float, include_imagery: bool = True) -> dict:
    """Pure arithmetic for one region (see estimate_defaults.json for units)."""
    rdir = Path(data) / "regions" / region
    n = size.get("buildings")
    km2 = float(size.get("bbox_km2") or 0)
    source_layers = survey.get("source_layers", {})
    pointcloud_available = bool(source_layers.get("pointcloud_bulk_url"))
    cached = {
        "outlines": (rdir / "building_outlines.geojson").exists(),
        "lidar": pointcloud_available and (rdir / "pointcloud_tiles.txt").exists(),
        "dsm": (rdir / "dsm_mosaic.tif").exists(),
        "imagery": (rdir / "imagery_mosaic.tif").exists(),
        "reference_imagery": (rdir / "reference_imagery_mosaic.tif").exists(),
    }
    warnings, blockers = [], []
    synthetic = survey.get("elevation") == "synthetic"
    imagery_available = survey.get("imagery_available", True)
    reference_imagery_available = survey.get("reference_imagery_available", imagery_available)
    imagery_layer = source_layers.get("imagery_layer")
    reference_layer = source_layers.get("reference_imagery_layer")
    reference_same_as_imagery = survey.get(
        "reference_same_as_imagery",
        imagery_layer is not None and imagery_layer == reference_layer)
    if survey.get("error"):
        blockers.append("LINZ source metadata could not be resolved: " + survey["error"])
    if survey.get("elevation") == "lidar":
        warnings.append(
            "LINZ source selection uses layer bounding extents, not verified "
            "valid-data coverage; check the selected survey before building.")
    if synthetic:
        warnings.append("No LiDAR here: builds on a modelled surface and every roof pitch is guessed.")
    if survey.get("warning"):
        warnings.append(survey["warning"])
    if not include_imagery:
        warnings.append("Aerial imagery excluded: image-based registration and obstruction checks will be skipped.")
    elif not imagery_available and not survey.get("error"):
        warnings.append("No aerial imagery source is configured for this area.")
    if ((cached["dsm"] and not synthetic) or
            (include_imagery and (cached["imagery"] or cached["reference_imagery"]))):
        warnings.append("Existing regional mosaics may be reused without verifying their "
                        "source or footprints against this estimate.")
    imagery_gb = (0.0 if cached["imagery"] or not include_imagery or not imagery_available
                  else km2 * defaults["imagery_gb_per_km2"])
    reference_gb = (0.0 if cached["reference_imagery"] or not include_imagery
                    or not reference_imagery_available or reference_same_as_imagery
                    else km2 * defaults["reference_imagery_gb_per_km2"])
    lidar_gb = (0.0 if (cached["lidar"] or synthetic or not pointcloud_available)
                else km2 * defaults["lidar_gb_per_km2"])
    dsm_gb = 0.0 if (cached["dsm"] or synthetic) else km2 * defaults["dsm_gb_per_km2"]
    download_gb = imagery_gb + reference_gb + dsm_gb + lidar_gb
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
        "source_layers": source_layers,
        "elevation": survey.get("elevation"), "cached": cached,
        "imagery_available": imagery_available,
        "reference_imagery_available": reference_imagery_available,
        "reference_same_as_imagery": reference_same_as_imagery,
        "legacy_raster_sources": survey.get("legacy_raster_sources", False),
        "include_imagery": include_imagery,
        "download_gb": round(download_gb, 3), "download_s": round(download_s),
        "dsm_gb": round(dsm_gb, 3),
        "download_components_gb": {"dsm": dsm_gb, "imagery": imagery_gb,
                                   "reference_imagery": reference_gb, "lidar": lidar_gb},
        "compute_s": round(compute_s), "total_s": round(download_s + compute_s),
        "disk_gb": round(disk_gb, 3), "warnings": warnings, "blockers": blockers,
    }


def apply_cached_raster_estimates(rows: list[dict], batch: dict, defaults: dict) -> None:
    """Replace mapped export estimates with unique missing tile transfer bounds."""
    cache = batch.get("cache")
    if not isinstance(cache, dict):
        return
    by_region = {row["region"]: row for row in rows}
    mapped = {(item["region"], item["role"]) for item in batch["inputs"]
              if item.get("source") is not None}
    adjusted = {region for region, _ in mapped}
    if not adjusted:
        return
    for region, role in mapped:
        by_region[region]["download_components_gb"][role] = 0.0
    for asset in batch["assets"]:
        if asset["cache_state"] == "cached":
            continue
        uses = sorted((use["region"], use["role"]) for use in asset["uses"]
                      if (use["region"], use["role"]) in mapped)
        if not uses:
            raise EstimateError("Raster asset has no matching mapped input")
        region, role = uses[0]
        size = asset["size_bytes"]
        if size is None:
            size = cache["unknown_asset_estimate_bytes"]
        by_region[region]["download_components_gb"][role] += size / 1e9
    for region in adjusted:
        row = by_region[region]
        total = sum(row["download_components_gb"].values())
        download_s = total * 8000 / max(defaults["download_mbps"], 0.1)
        if not row["cached"]["outlines"]:
            download_s += defaults["linz_export_overhead_s"]
        row["download_gb"] = round(total, 3)
        row["download_s"] = round(download_s)
        row["total_s"] = round(download_s + row["compute_s"])
        row["disk_gb"] = round(total + (row["buildings"] or 0)
                               * defaults["output_mb_per_building"] / 1024, 3)
        row["dsm_gb"] = round(row["download_components_gb"]["dsm"], 3)
        row["warnings"].append(
            "Mapped tile transfer is estimated once per batch, assigned to the first "
            "area using each tile. Unknown sizes use the configured transfer limit; "
            "retries or changed queue order can change individual downloads.")


def automatic_raster_estimate(row: dict, cache_overview: dict, data: Path) -> dict:
    """Estimate allocations for admission; these are not verified upper bounds."""
    values = cache_overview["policy"]["values"]
    mib = 1024 ** 2
    km2 = float(row["bbox_km2"])
    raw_dsm = max(64 * mib, int(km2 * 4_000_000))
    # RGB plus alpha at the 0.1 m processing grid, including a working-copy allowance.
    imagery_roles = sum(item["state"] == "mapped"
                        and item["role"] in ("imagery", "reference_imagery")
                        for item in row.get("raster_sources", []))
    raw_imagery = int(km2 * 400_000_000) * imagery_roles
    components = row["download_components_gb"]
    export = max(64 * mib, int(max(components["imagery"],
                                    components["reference_imagery"]) * 1e9))
    other = max(64 * mib, int(components["lidar"] * 1e9))
    output = max(64 * mib, int((row["buildings"] or 0) * 102_400))
    return {
        "cache_policy": values,
        "work_dir": str(data.resolve()),
        "output_dir": str(data.resolve()),
        "temp_dir": str(Path(tempfile.gettempdir()).resolve()),
        "mosaic_bytes": 4 * raw_dsm + 2 * raw_imagery,
        "scratch_bytes": 2 * raw_dsm + 2 * export + 2 * raw_imagery,
        "output_bytes": output,
        "export_bytes": export,
        "other_bytes": other,
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
             now: float | None = None, cloud=None, selection: dict | None = None,
             grid_lookup: Callable | None = None,
             api_key_fn: Callable[[], str] | None = None,
             include_imagery: bool = True, paid_referee: bool = False,
             web_native_default: bool = False, imagery_choices: dict | None = None) -> dict:
    if target not in TARGETS:
        raise EstimateError(f"unknown target: {target}")
    if not isinstance(include_imagery, bool) or not isinstance(paid_referee, bool):
        raise EstimateError("source choices must be true or false")
    if paid_referee and not include_imagery:
        raise EstimateError("the paid AI referee requires aerial imagery")
    is_cloud = target != "local"
    preference = os.environ.get("SOLAR_PREFER_NATIONAL_DSM", "1")
    if web_native_default and preference not in ("0", "1"):
        raise EstimateError("SOLAR_PREFER_NATIONAL_DSM must be 0 or 1")
    native_default = web_native_default and not is_cloud and preference == "1"
    if native_default and survey_fn is _survey:
        survey_fn = lambda bbox, region: _survey(bbox, region, prefer_national=True)
    if is_cloud and cloud is None:
        raise EstimateError("cloud targets are not configured on this server")
    data = Path(data)
    defaults = defaults or load_defaults()
    calibration = calibrate(data, defaults)
    spb = calibration["seconds_per_building"]
    rows = []
    selection_info = None
    if selection is not None:
        if target != "local":
            raise EstimateError("partial area-selection builds currently run locally only")
        selection = validate_selection(selection)
        if grid_lookup is None or api_key_fn is None:
            raise EstimateError("area selection is not configured on this server", 503)
        collection = grid_lookup(selection["parent_region"], selection["parent_linz_id"],
                                 selection["grid_size"])
        features = collection.get("features") or []
        found = {str((f.get("properties") or {}).get("grid_id")) for f in features}
        if collection.get("grid_size") != selection["grid_size"]:
            raise EstimateError("grid definition changed; reload the locality grid", 409)
        if not set(selection["grid_ids"]).issubset(found):
            raise EstimateError("selection contains an area outside the selected locality", 409)
        from src.grid_selection import selection_id
        from tools.pipeline_webui import geo_worker
        empty_cells = []
        for grid_id in selection["grid_ids"]:
            area = selection_id(selection["parent_region"], [grid_id], selection["grid_size"])
            request_path = web_dir(data) / "selections" / f"{file_slug(area)}.json"
            request = {
                "parent_region": selection["parent_region"],
                "parent_linz_id": selection["parent_linz_id"],
                "grid_size": selection["grid_size"],
                "grid_ids": [grid_id],
                # All cells define boundary ownership, even for a one-cell job.
                "areas": features,
            }
            write_json_atomic(request_path, request)
            resolved = geo_worker.run(
                ["selection", str(request_path), str(data)],
                env={"LINZ_API_KEY": api_key_fn()})
            # The status view counts a grid complete when every cell with buildings has built.
            write_json_atomic(request_path, {**request, "buildings": resolved["buildings"]})
            if not resolved["buildings"]:
                empty_cells.append(grid_id)
                continue
            size = {"buildings": resolved["buildings"], "bbox": resolved["bbox"],
                    "bbox_km2": resolved.get("acquisition_km2", resolved["bbox_km2"]),
                    "source": "grid selection"}
            row = estimate_region(resolved["region"], size,
                                  survey_fn(size["bbox"], resolved["region"]),
                                  data, defaults, spb, include_imagery)
            row.update(selection_count=1, grid_size=selection["grid_size"], grid_ids=[grid_id],
                       parent_region=selection["parent_region"],
                       acquisition_footprints=resolved.get("acquisition_footprints", []))
            rows.append(row)
        selection_info = {"parent_region": selection["parent_region"],
                          "grid_ids": selection["grid_ids"],
                          "grid_size": selection["grid_size"],
                          "empty_grid_ids": empty_cells,
                          "selection_ids": [row["region"] for row in rows]}
        if not rows:
            raise EstimateError("the selected grid cells contain no buildings")
    else:
        regions = validate_regions(regions)
        # The VM fetches its own inputs, so a cloud estimate counts nothing as cached here.
        cache_dir = data / "_cloud_estimate_uncached" if is_cloud else data
    for region in ([] if selection_info else regions):
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
                                    defaults, spb, include_imagery))
    raster_plan = None
    cache_overview = None
    raster_tiles = None
    if not is_cloud:
        eligible = []
        for row in rows:
            layers = row.get("source_layers") or {}
            elevation = row.get("elevation")
            if row.get("legacy_raster_sources"):
                row["warnings"].append(
                    "Legacy S3 raster inputs are not represented in the experimental "
                    "raster plan; download sizes remain area-based estimates.")
            if (row.get("bbox") and not row.get("legacy_raster_sources")
                    and (elevation == "synthetic"
                         or (elevation == "lidar" and type(layers.get("dsm_layer")) is int
                             and layers["dsm_layer"] > 0))):
                eligible.append({key: row.get(key) for key in
                                 ("region", "bbox", "survey", "elevation", "source_layers")})
        if eligible:
            from tools.pipeline_webui import geo_worker
            request = {"data": str(data.resolve()), "regions": eligible,
                       "include_imagery": include_imagery}
            if web_native_default and include_imagery:
                request["native_imagery"] = True
                request["imagery_choices"] = imagery_choices or {}
            if native_default:
                request["national_default"] = True
            try:
                raster_plan = geo_worker.run(
                    ["raster-plan"], input_text=json.dumps(request), timeout=900,
                    env={"LINZ_API_KEY": api_key_fn() or ""} if api_key_fn else None)
            except LookupError as exc:
                raise EstimateError(f"Raster acquisition plan failed: {exc}") from exc
            cache_overview = raster_plan.pop("cache_overview", None)
            imagery_selections = {item["region"]: item
                                  for item in raster_plan.pop("imagery_selections", [])}
            for row in rows:
                if row["region"] in imagery_selections:
                    item = imagery_selections[row["region"]]
                    row.update({key: item[key] for key in
                                ("source_layers", "imagery_selected", "imagery_candidates")})
                    layers = row["source_layers"]
                    reference = layers.get("reference_imagery_layer")
                    row["imagery_available"] = True
                    row["reference_imagery_available"] = reference is not None
                    row["reference_same_as_imagery"] = reference == layers["imagery_layer"]
                    row["download_components_gb"]["reference_imagery"] = (
                        float(row["bbox_km2"]) * defaults["reference_imagery_gb_per_km2"]
                        if reference is not None and not row["reference_same_as_imagery"]
                        and not row["cached"]["reference_imagery"] else 0.0)
                    row["warnings"] = [warning for warning in row["warnings"]
                                       if warning != "No aerial imagery source is configured for this area."]
                    if not item["imagery_selected"]["fully_covered"]:
                        percent = 100 * item["imagery_selected"]["coverage_fraction"]
                        row["warnings"].append(
                            f"PARTIAL IMAGERY: selected survey covers {percent:.1f}% of the acquisition "
                            "area. Uncovered pixels remain invalid; image-based checks cannot use them.")
            batch = raster_plan.get("batch")
            if batch is not None and any(item.get("source") is not None for item in batch["inputs"]):
                assets = batch["assets"]
                cache = batch.get("cache")
                raster_tiles = {
                    "required_count": len(assets),
                    "cached_count": cache["cached_count"] if cache else None,
                    "missing_count": cache["missing_count"] if cache else None,
                    "known_missing_bytes": cache["known_missing_bytes"] if cache else None,
                    "unknown_missing_count": cache["unknown_missing_count"] if cache else None,
                    "unknown_asset_estimate_bytes": cache["unknown_asset_estimate_bytes"] if cache else None,
                    "known_asset_bytes": batch["known_asset_bytes"],
                    "unknown_size_count": batch["unknown_size_count"],
                }
                apply_cached_raster_estimates(rows, batch, defaults)
            by_region = {item["region"]: item["inputs"] for item in raster_plan["regions"]}
            coverage = batch["footprint_bbox_coverage"] if batch is not None else []
            for row in rows:
                if row["region"] in by_region:
                    row["raster_sources"] = by_region[row["region"]]
                    if (native_default and (row.get("source_layers") or {}).get("raster_dsm_layer") == 122082
                            and any(item["role"] == "dsm" and item["state"] != "mapped"
                                    for item in row["raster_sources"])):
                        row["blockers"].append(
                            "The selected national DSM has no native tile mapping. "
                            "Review the configured source mappings before building.")
                    if any(item["state"] == "mapped" for item in row["raster_sources"]):
                        from src.raster_plan_handoff import plan_digest, plan_for_regions
                        try:
                            row["raster_plan_sha256"] = plan_digest(
                                plan_for_regions(raster_plan, [row["region"]]))
                            row["raster_metadata_checked_at"] = batch.get("metadata_checked_at")
                        except (KeyError, TypeError, ValueError) as exc:
                            raise EstimateError(f"Raster acquisition plan is invalid: {exc}") from exc
                        if not os.environ.get("SOLAR_RASTER_JOB_BOUNDS"):
                            mapped_roles = {item["role"] for item in row["raster_sources"]
                                            if item["state"] == "mapped"}
                            if (web_native_default and cache_overview
                                    and mapped_roles <= {"dsm", "imagery", "reference_imagery"}):
                                row["raster_auto_estimate"] = automatic_raster_estimate(
                                    row, cache_overview, data)
                                row["warnings"].append(
                                    "Automatic disk allocations are rough estimates, not guaranteed "
                                    "bounds or enforced limits. Actual usage can be higher.")
                            else:
                                row["blockers"].append(
                                    "Mapped raster plans are preview-only without operator-supplied "
                                    "whole-job bounds (SOLAR_RASTER_JOB_BOUNDS).")
                        else:
                            row["warnings"].append(
                                "Mapped build requires matching operator bounds.")
                    if any(item["state"] == "exports_fallback" for item in row["raster_sources"]):
                        row["warnings"].append(
                            "Raster inputs use LINZ exports; download sizes are estimates, "
                            "not shared-tile cache predictions.")
                    for source in row["raster_sources"]:
                        if source["state"] != "mapped":
                            continue
                        role = source["role"]
                        mosaic = data / "regions" / row["region"] / f"{role}_mosaic.tif"
                        sidecar = mosaic.with_name(mosaic.name + ".source.json")
                        if mosaic.exists() and role in ("imagery", "reference_imagery") and row.get("imagery_selected"):
                            row["warnings"].append(
                                f"Existing {role} will be verified against this selection. "
                                "A different or legacy photo is preserved in previous_imagery/ "
                                "before preparing the selected native tiles.")
                            continue
                        if mosaic.exists() and not sidecar.is_file():
                            row["blockers"].append(
                                f"Existing {role} mosaic has no verified source provenance: {mosaic}. "
                                "Preserve and move it aside before building with native tiles.")
                    partial_imagery = row["source_layers"].get("imagery_partial") and any(
                        use["region"] == row["region"] and use["role"] == "imagery"
                        for asset in (batch or {}).get("assets", []) for use in asset["uses"])
                    gaps = [item for item in coverage
                            if item["region"] == row["region"]
                            and item["bbox_coverage"] != "fully_bounded"
                            and not (item["role"] == "imagery" and partial_imagery)]
                    if gaps:
                        roles = ", ".join(sorted({item["role"] for item in gaps}))
                        row["warnings"].append(
                            f"Mapped {roles} item bounds do not cover {len(gaps)} requested "
                            "footprint(s); preparation would reject this plan. Item bounds "
                            "do not establish valid-pixel coverage.")
                        row["blockers"].append(
                            f"Mapped {roles} item bounds do not cover the acquisition footprints.")
                    captures = [item for item in (batch or {}).get("capture_coverage", [])
                                if item["region"] == row["region"] and item["state"] != "covered"]
                    if captures:
                        row["warnings"].append(
                            f"Published national DSM capture geometry does not fully cover "
                            f"{len(captures)} footprint(s). Its simplified boundary can include "
                            "nodata; inspect coverage before building.")
                        if any(item["state"] == "outside" for item in captures):
                            row["blockers"].append(
                                "A mapped DSM footprint is outside the published capture geometry; "
                                "review the source before building.")
                    surveys = [item for item in (batch or {}).get("capture_surveys", [])
                               if item["region"] == row["region"] and item["role"] == "dsm"]
                    if surveys:
                        names = {survey["title"] for item in surveys for survey in item["surveys"]}
                        if row.get("survey") not in names:
                            row["warnings"].append(
                                "The selected reference survey name is not among the national "
                                "DSM's intersecting survey names. Check point-cloud and reference-"
                                "imagery alignment; names alone cannot prove dataset identity.")
    totals = {k: round(sum(r.get(k) or 0 for r in rows), 3)
              for k in ("buildings", "download_gb", "download_s", "compute_s", "total_s", "disk_gb")}
    warnings, blockers = [], []
    cloud_block = None
    if is_cloud:
        free_gb = None
        cloud_block = cloud_estimate(cloud, target, totals["total_s"])
        for row in rows:
            row["cloud"] = {**cloud_block, **cloud.cost(target, row["total_s"])}
        if len(rows) > 1:
            amount = sum(Decimal(row["cloud"]["cost_nzd"]) for row in rows)
            cloud_block.update(
                cost_nzd=str(amount), vm_hours=sum(row["cloud"]["vm_hours"] for row in rows),
                cost_text=(f"Estimated Google Cloud cost: up to NZD {amount} for "
                           f"{len(rows)} separate sequential jobs. This is an estimate, not a "
                           "spending cap; actual charges can be higher."),
                cost_basis="Each job includes its own VM/startup/storage allowance. " +
                           " ".join(f"{row['region']}: {row['cloud']['cost_basis']}" for row in rows))
        warnings.extend(cloud_block.pop("warnings"))
        blockers.extend(cloud_block["blockers"])
    else:
        warnings.append(
            "Disk figures are estimates, not peak-space bounds or reservations. "
            "Mapped jobs use cooperative disk admission, which does not allocate or cap space.")
        if os.environ.get("SOLAR_RASTER_JOB_BOUNDS") and len(rows) != 1:
            blockers.append("Operator-bounded local jobs must be submitted one area at a time.")
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
    referee = referee_note(rows)
    referee["selected"] = paid_referee
    referee["selected_usd_upper"] = referee["usd_upper"] if paid_referee else 0.0
    return {
        "estimate_id": secrets.token_hex(8), "status": "ready", "target": target,
        "regions": rows, "totals": totals, "warnings": warnings,
        "blockers": blockers + [f"{r['region']}: {b}" for r in rows for b in r["blockers"]],
        "machine": machine, "cloud": cloud_block,
        "selection": selection_info,
        "raster_plan": raster_plan,
        "raster_cache_overview": cache_overview,
        "raster_tile_preview": raster_tiles,
        "options": {"include_imagery": include_imagery, "paid_referee": paid_referee},
        "cloud_cost_nzd": cloud_block["cost_nzd"] if cloud_block else "0",
        "paid_referee": referee,
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


def validate_selection(selection: dict) -> dict:
    """Validate one locality parent and a non-empty set of grid cells."""
    if not isinstance(selection, dict):
        raise EstimateError("area selection must be an object")
    try:
        parent = validate(selection.get("parent_region"))
    except ValueError as exc:
        raise EstimateError(str(exc)) from None
    if not is_locality_id(parent):
        raise EstimateError("area selection parent must be one LINZ locality")
    linz_id = selection.get("parent_linz_id")
    if isinstance(linz_id, bool):
        raise EstimateError("parent_linz_id must be a positive LINZ locality id")
    try:
        linz_id = int(linz_id)
    except (TypeError, ValueError):
        raise EstimateError("parent_linz_id must be a positive LINZ locality id") from None
    if linz_id < 1:
        raise EstimateError("parent_linz_id must be a positive LINZ locality id")
    try:
        grid_size = int(selection.get("grid_size"))
    except (TypeError, ValueError):
        raise EstimateError("grid_size must be from 2 through 10") from None
    if not 2 <= grid_size <= 10:
        raise EstimateError("grid_size must be from 2 through 10")
    ids = selection.get("grid_ids")
    if not isinstance(ids, list) or not ids:
        raise EstimateError("choose at least one grid cell")
    valid = all(isinstance(value, str) and
                (match := re.fullmatch(r"r(\d{2})c(\d{2})", value)) is not None
                and 1 <= int(match.group(1)) <= grid_size
                and 1 <= int(match.group(2)) <= grid_size for value in ids)
    if len(ids) > 5000 or not valid:
        raise EstimateError("grid selection contains invalid IDs or is too large")
    return {"parent_region": parent, "parent_linz_id": linz_id,
            "grid_ids": sorted(set(ids)),
            "grid_size": grid_size}


class EstimateStore:
    """Estimates computed in a background thread and kept for TTL_SECONDS."""

    def __init__(self, data: Path = DATA, lookup: SizeLookup | None = None,
                 grid_lookup: Callable | None = None,
                 api_key_fn: Callable[[], str] | None = None, **estimate_kwargs):
        self.data = Path(data)
        self.lookup = lookup
        self.grid_lookup = grid_lookup
        self.api_key_fn = api_key_fn
        self.kwargs = estimate_kwargs
        self.dir = web_dir(self.data) / "estimates"
        self._dsm_view_lock = threading.Lock()
        self._dsm_view_records_lock = threading.Lock()
        self._dsm_view_records: dict[str, dict] = {}

    def _path(self, estimate_id: str) -> Path:
        if not _ID.fullmatch(estimate_id or ""):
            raise EstimateError("unknown estimate", 404)
        return self.dir / f"{estimate_id}.json"

    def start(self, regions, target: str = "local", background: bool = True,
              selection: dict | None = None, include_imagery: bool = True,
              paid_referee: bool = False, imagery_choices: dict | None = None) -> str:
        if imagery_choices is not None:
            if (not isinstance(imagery_choices, dict) or len(imagery_choices) > 5000
                    or any(not isinstance(region, str) or type(layer) is not int or layer <= 0
                           for region, layer in imagery_choices.items())):
                raise EstimateError("imagery choices must map area IDs to positive LINZ layer IDs")
            for region in imagery_choices:
                try:
                    validate(region)
                except ValueError:
                    raise EstimateError("invalid imagery choice area") from None
            if target != "local" or not include_imagery:
                raise EstimateError("imagery choices require a local build with imagery included")
        if not isinstance(include_imagery, bool) or not isinstance(paid_referee, bool):
            raise EstimateError("source choices must be true or false")
        if paid_referee and not include_imagery:
            raise EstimateError("the paid AI referee requires aerial imagery")
        if selection is not None:
            if regions:
                raise EstimateError("choose either full areas or a grid selection")
            selection = validate_selection(selection)
            if target != "local":
                raise EstimateError("partial area-selection builds currently run locally only")
            regions = []
        else:
            regions = validate_regions(regions)
        if target not in TARGETS:
            raise EstimateError(f"unknown target: {target}")
        estimate_id = secrets.token_hex(8)
        now = time.time()
        write_json_atomic(self._path(estimate_id), {"estimate_id": estimate_id, "status": "pending",
                                                    "regions": regions, "selection": selection,
                                                    "include_imagery": include_imagery,
                                                    "paid_referee": paid_referee,
                                                    "created": now,
                                                    "expires_at": now + TTL_SECONDS})

        def work():
            try:
                result = estimate(regions, self.data, target, self.lookup, selection=selection,
                                  grid_lookup=self.grid_lookup,
                                  api_key_fn=self.api_key_fn,
                                  include_imagery=include_imagery,
                                  paid_referee=paid_referee,
                                  **({"imagery_choices": imagery_choices} if imagery_choices else {}),
                                  **self.kwargs)
                result["estimate_id"] = estimate_id
            except EstimateError as exc:
                result = {"estimate_id": estimate_id, "status": "error", "error": str(exc)}
            except LookupError as exc:
                result = {"estimate_id": estimate_id, "status": "error",
                          "error": str(exc) or "area lookup failed"}
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

    def start_dsm_preview(self, estimate_ids: object) -> str:
        """Plan the national DSM for already-estimated local selections."""
        if (not isinstance(estimate_ids, list) or not estimate_ids
                or len(estimate_ids) > 200
                or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in estimate_ids)
                or len(set(estimate_ids)) != len(estimate_ids)):
            raise EstimateError("select valid local estimates for the DSM preview")
        rows = []
        seen = set()
        for identifier in estimate_ids:
            record = self.get(identifier)
            if (record.get("status") != "ready" or record.get("target") != "local"
                    or time.time() > float(record.get("expires_at") or 0)):
                raise EstimateError("local estimate unavailable or expired; estimate again", 409)
            for row in record.get("regions") or []:
                region = row.get("region")
                if not region or region in seen:
                    raise EstimateError("duplicate or invalid preview area; estimate again", 409)
                if any(str(value).startswith("LINZ source metadata could not be resolved")
                       for value in row.get("blockers", [])):
                    raise EstimateError(f"source metadata unresolved for {region}; estimate again", 409)
                seen.add(region)
                rows.append({key: row.get(key) for key in ("region", "bbox", "elevation", "survey")})
        if not rows or len(rows) > 5000:
            raise EstimateError("DSM preview requires between 1 and 5000 estimated areas")
        preview_id = secrets.token_hex(8)
        write_json_atomic(self._path(preview_id), {
            "estimate_id": preview_id, "kind": "dsm-preview", "status": "pending"})

        def work():
            from tools.pipeline_webui import geo_worker
            try:
                result = geo_worker.run(["national-dsm-preview"], timeout=900,
                                        input_text=json.dumps({"data": str(self.data.resolve()),
                                                               "regions": rows}))
                result.update(estimate_id=preview_id, kind="dsm-preview", status="ready")
            except LookupError as exc:
                result = {"estimate_id": preview_id, "kind": "dsm-preview",
                          "status": "error", "error": str(exc)}
            write_json_atomic(self._path(preview_id), result)

        threading.Thread(target=work, daemon=True, name=f"dsm-preview-{preview_id}").start()
        return preview_id

    def start_imagery_preview(self, estimate_ids: object, refresh: object = False,
                              background: bool = True) -> str:
        """Preview survey-index matches without changing approved build sources."""
        if (not isinstance(estimate_ids, list) or not estimate_ids or len(estimate_ids) > 200
                or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in estimate_ids)
                or len(set(estimate_ids)) != len(estimate_ids)):
            raise EstimateError("select valid local estimates for imagery preview")
        if type(refresh) is not bool:
            raise EstimateError("refresh must be true or false")
        rows = []
        seen = set()
        for identifier in estimate_ids:
            record = self.get(identifier)
            if (record.get("status") != "ready" or record.get("target") != "local"
                    or time.time() > float(record.get("expires_at") or 0)):
                raise EstimateError("local estimate unavailable or expired; estimate again", 409)
            for row in record.get("regions") or []:
                region = row.get("region")
                if not region or region in seen or not row.get("bbox"):
                    raise EstimateError("duplicate or unresolved imagery preview area", 409)
                seen.add(region)
                rows.append({"region": region, "bbox": row["bbox"]})
        if not rows or len(rows) > 5000:
            raise EstimateError("imagery preview requires between 1 and 5000 areas")
        preview_id = secrets.token_hex(8)
        write_json_atomic(self._path(preview_id), {
            "estimate_id": preview_id, "kind": "imagery-preview", "status": "pending"})

        def work():
            from tools.pipeline_webui import geo_worker
            try:
                key = self.api_key_fn() if self.api_key_fn else ""
                result = geo_worker.run(
                    ["imagery-survey-preview"], timeout=900, env={"LINZ_API_KEY": key or ""},
                    input_text=json.dumps({"data": str(self.data.resolve()), "regions": rows,
                                           "refresh": refresh}))
                result.update(estimate_id=preview_id, kind="imagery-preview", status="ready")
            except LookupError as exc:
                result = {"estimate_id": preview_id, "kind": "imagery-preview",
                          "status": "error", "error": str(exc)}
            except Exception as exc:
                result = {"estimate_id": preview_id, "kind": "imagery-preview",
                          "status": "error", "error": f"imagery preview failed ({type(exc).__name__})"}
            write_json_atomic(self._path(preview_id), result)

        if background:
            threading.Thread(target=work, daemon=True, name=f"imagery-preview-{preview_id}").start()
        else:
            work()
        return preview_id

    def start_dsm_view(self, bbox: object, refresh: object = False, offset: object = 0) -> str:
        import math
        if (not isinstance(bbox, list) or len(bbox) != 4
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in bbox)
                or not (-180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90)):
            raise EstimateError("invalid DSM map viewport")
        if type(refresh) is not bool:
            raise EstimateError("refresh must be true or false")
        if type(offset) is not int or not 0 <= offset < 150:
            raise EstimateError("invalid DSM inspection offset")
        if not self._dsm_view_lock.acquire(blocking=False):
            raise EstimateError("DSM map inspection already running; retry shortly", 409)
        identifier = secrets.token_hex(8)
        with self._dsm_view_records_lock:
            while len(self._dsm_view_records) >= 32:
                del self._dsm_view_records[next(iter(self._dsm_view_records))]
            self._dsm_view_records[identifier] = {
                "estimate_id": identifier, "kind": "dsm-view", "status": "pending"}

        def work():
            from tools.pipeline_webui import geo_worker
            try:
                try:
                    result = geo_worker.run(["national-dsm-view"], timeout=900,
                                            input_text=json.dumps({"data": str(self.data.resolve()),
                                                                   "bbox": bbox, "refresh": refresh,
                                                                   "offset": offset}))
                    result.update(estimate_id=identifier, kind="dsm-view", status="ready")
                except LookupError as exc:
                    result = {"estimate_id": identifier, "kind": "dsm-view",
                              "status": "error", "error": str(exc)}
                except Exception as exc:
                    result = {"estimate_id": identifier, "kind": "dsm-view", "status": "error",
                              "error": f"DSM inspection failed ({type(exc).__name__})"}
                with self._dsm_view_records_lock:
                    self._dsm_view_records[identifier] = result
            finally:
                self._dsm_view_lock.release()

        try:
            threading.Thread(target=work, daemon=True, name=f"dsm-view-{identifier}").start()
        except RuntimeError:
            self._dsm_view_lock.release()
            with self._dsm_view_records_lock:
                del self._dsm_view_records[identifier]
            raise
        return identifier

    def get_dsm_view(self, identifier: str) -> dict:
        with self._dsm_view_records_lock:
            record = self._dsm_view_records.get(identifier)
            if record is None:
                raise EstimateError("unknown DSM inspection", 404)
            return dict(record)

    def usable(self, estimate_id: str, now: float | None = None) -> dict:
        """The stored estimate a job may start from, or EstimateError."""
        record = self.get(estimate_id)
        if record.get("kind") in ("dsm-preview", "dsm-view", "imagery-preview"):
            raise EstimateError("a preview is not a build estimate", 409)
        now = time.time() if now is None else now
        if record.get("status") != "ready":
            raise EstimateError("the estimate is not ready", 409)
        if now > float(record.get("expires_at") or 0):
            raise EstimateError("the estimate has expired; estimate again", 409)
        if record.get("blockers"):
            raise EstimateError("the estimate lists blockers", 409)
        return record
