"""PROJ-using calculations for the panel, run in a short-lived child process.

The panel server must never load PROJ itself: once pyproj opens proj.db, PROJ
registers a fork handler, and on macOS the child of a fork from the
multithreaded server crashes in it ("Python quit unexpectedly"). Every later
git, gcloud or pipeline launch from the panel would then fail. Running these
calculations here keeps PROJ out of the server process.

Usage (JSON on stdout):
    python tools/pipeline_webui/geo_worker.py km2 '<[w, s, e, n]>'
    python tools/pipeline_webui/geo_worker.py size <locality-id>   # LINZ_API_KEY in env
    python tools/pipeline_webui/geo_worker.py catalog <data-dir>
    python tools/pipeline_webui/geo_worker.py raster-plan < request.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_SECONDS = 300


def _km2(bbox: list[float]) -> dict:
    from src.localities import bbox_km2
    return {"bbox_km2": bbox_km2(bbox)}


def _size(region: str) -> dict:
    from src import localities as L
    key = os.environ.get("LINZ_API_KEY", "")
    props = L.resolve(key, region)
    outlines = L.fetch_outlines(key, props)
    n = len(outlines["features"])
    if not n:
        raise LookupError(f"{props.get('name')} has no building outlines; choose another area")
    bbox = L.outlines_bbox_wgs84(outlines)
    return {"buildings": n, "bbox": bbox, "bbox_km2": round(L.bbox_km2(bbox), 3)}


def _selection(request_path: str, data: str) -> dict:
    from src.grid_selection import materialise
    result = materialise(Path(request_path), Path(data), os.environ.get("LINZ_API_KEY", ""))
    from src.localities import bbox_km2
    footprints = result.get("acquisition_footprints") or [result["bbox"]]
    selection_count = len(result["grid_ids"])
    return {"region": result["selection_id"], "buildings": result["buildings"],
            "bbox": result["bbox"], "bbox_km2": round(bbox_km2(result["bbox"]), 3),
            "acquisition_footprints": footprints,
            "acquisition_km2": round(sum(bbox_km2(bbox) for bbox in footprints), 3),
            "selection_count": selection_count}


def _catalog(data: str) -> dict:
    from src.map_layer_catalog import build_local_catalog
    from src.raster_cache_config import display_cache_config
    config, _ = display_cache_config(Path(data))
    cache_dir = config.directory if config.directory.is_dir() else None
    return build_local_catalog(Path(data), raster_cache_dir=cache_dir)


def _national_dsm_preview(request: dict) -> dict:
    """Inspect an advisory national DSM plan without changing build selections."""
    from src.fetch_regions import acquisition_footprints
    from src.raster_cache_config import display_cache_config
    from src.raster_region_plan import plan_regions
    from src.raster_source_selection import load_layer_mappings
    from src.region_ids import validate

    data = Path(request["data"])
    config, _ = display_cache_config(data)
    rows = {}
    synthetic = []
    for row in request["regions"]:
        region = validate(row["region"])
        if region in rows or region in synthetic:
            raise LookupError(f"Duplicate preview region: {region}")
        if not row.get("bbox") or row.get("elevation") not in ("lidar", "synthetic"):
            raise LookupError(f"Cannot plan {region}: its location or elevation source is unresolved")
        if row["elevation"] == "synthetic":
            synthetic.append(region)
        else:
            rows[region] = row
    if not rows:
        return {"assets": [], "synthetic": synthetic, "capture_coverage": [],
                "footprint_bbox_coverage": [], "metadata_checked_at": None,
                "cache_directory": str(config.directory), "area_count": len(synthetic)}
    mappings = load_layer_mappings(ROOT / "tools" / "national-dsm-mapping.json")
    result = plan_regions(
        list(rows), mappings=mappings, include_imagery=False,
        bbox_for=lambda region: rows[region]["bbox"],
        footprints_for=lambda region, bbox: acquisition_footprints(region, bbox, data / "regions"),
        survey_for=lambda bbox, region: {
            "name": rows[region].get("survey"), "elevation": "lidar",
            "dsm_layer": 122082, "raster_dsm_layer": 122082,
        })
    batch = config.cache().inspect_plan(result["batch"])
    return {
        "assets": [{key: asset.get(key) for key in
                    ("item_id", "collection_id", "asset_key", "bbox_wgs84",
                     "size_bytes", "cache_state", "regions")}
                   for asset in batch["assets"]],
        "synthetic": synthetic,
        "capture_coverage": [{key: item[key] for key in ("region", "state")}
                             for item in batch["capture_coverage"]],
        "footprint_bbox_coverage": batch["footprint_bbox_coverage"],
        "metadata_checked_at": batch["metadata_checked_at"],
        "cache_directory": str(config.directory),
        "area_count": len(rows) + len(synthetic),
    }


def _national_dsm_view(request: dict) -> dict:
    """Inspect viewport tiles using a locally persisted, refreshable metadata index."""
    import fcntl
    import time
    from src.raster_acquisition_plan import RasterSource, _bounds, discover_assets
    from src.raster_cache_config import display_cache_config
    from src.raster_source_selection import load_layer_mappings
    from tools.pipeline_webui.common import read_json, web_dir, write_json_atomic

    data = Path(request["data"])
    west, south, east, north = _bounds(request["bbox"], "map viewport")
    mappings = load_layer_mappings(ROOT / "tools" / "national-dsm-mapping.json")
    mapping = next(item for item in mappings if item.layer_id == 122082 and item.role == "dsm")
    source = RasterSource(mapping.collection_url, mapping.asset_key, mapping.collection_id)
    directory = web_dir(data) / "cache"
    directory.mkdir(parents=True, exist_ok=True)
    index_path = directory / "national_dsm_tiles.json"
    identity = [source.collection_url, source.asset_key, source.expected_collection_id]
    with (directory / "national_dsm_tiles.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        index = read_json(index_path)
        if (not isinstance(index, dict) or index.get("version") != 1
                or index.get("source") != identity or not isinstance(index.get("assets"), list)
                or not isinstance(index.get("checked_at"), (float, int))
                or not 0 <= time.time() - index["checked_at"] < 86400
                or request.get("refresh") is True):
            assets = discover_assets(source)
            index = {"version": 1, "source": identity, "checked_at": time.time(), "assets": assets}
            write_json_atomic(index_path, index)
    matches = []
    for asset in index["assets"]:
        w, s, e, n = _bounds(asset["bbox_wgs84"], "indexed tile")
        if w <= east and e >= west and s <= north and n >= south:
            matches.append(asset)
    offset = request["offset"]
    result = {"total": len(matches), "limit": 150, "offset": offset,
              "metadata_checked_at": index["checked_at"], "assets": []}
    if len(matches) > result["limit"]:
        return result
    config, _ = display_cache_config(data)
    result["cache_directory"] = str(config.directory)
    inspected = config.cache().inspect_assets(matches[offset:offset + 8])
    result["assets"] = [{key: item.get(key) for key in
                         ("item_id", "collection_id", "asset_key", "bbox_wgs84",
                          "size_bytes", "cache_state")}
                        for item in inspected]
    return result


def _raster_plan(request: dict) -> dict:
    from src.fetch_regions import acquisition_footprints
    from src.raster_region_plan import plan_regions
    from src.raster_source_selection import load_layer_mappings
    from src.region_ids import validate

    data = Path(request["data"])
    mappings_path = os.environ.get("SOLAR_RASTER_SOURCE_MAPPINGS")
    if not mappings_path and request.get("national_default") is True:
        mappings_path = str(ROOT / "tools" / "national-dsm-mapping.json")
    try:
        mappings = load_layer_mappings(Path(mappings_path)) if mappings_path else []
    except ValueError as exc:
        raise LookupError(f"Raster source mapping failed: {exc}") from exc
    rows = {}
    for row in request["regions"]:
        region = validate(row["region"])
        if region in rows:
            raise ValueError(f"Duplicate raster plan region: {region}")
        rows[region] = row
    try:
        result = plan_regions(
            list(rows), mappings=mappings, include_imagery=request["include_imagery"],
            bbox_for=lambda region: rows[region]["bbox"],
            footprints_for=lambda region, bbox: acquisition_footprints(region, bbox, data / "regions"),
            survey_for=lambda bbox, region: {
                "name": rows[region]["survey"], "elevation": rows[region]["elevation"],
                **rows[region]["source_layers"],
            })
    except OSError as exc:
        raise LookupError("Raster metadata could not be fetched; check network access and "
                          "the configured collection.") from exc
    except ValueError as exc:
        raise LookupError("Raster plan is invalid; check the configured mapping, collection "
                          "metadata and regional footprints.") from exc
    if request.get("national_default") is True and result["batch"] is not None:
        from src.raster_acquisition_plan import NATIONAL_DSM_COLLECTION, resolve_asset_sizes
        batch = result["batch"]
        selected = [asset for asset in batch["assets"]
                    if asset.get("collection_url") == NATIONAL_DSM_COLLECTION]
        try:
            resolve_asset_sizes(selected)
        except (OSError, ValueError) as exc:
            raise LookupError("Cannot determine national DSM tile transfer sizes; "
                              "check source availability and try estimating again.") from exc
        batch["known_asset_bytes"] = sum(asset["size_bytes"] or 0 for asset in batch["assets"])
        batch["unknown_size_count"] = sum(asset["size_bytes"] is None for asset in batch["assets"])
    policy_path = os.environ.get("SOLAR_RASTER_CACHE_POLICY")
    if policy_path or request.get("national_default") is True:
        from src.raster_cache import RasterCacheError
        from src.raster_cache_config import display_cache_config, load_cache_config
        try:
            config, sources = (load_cache_config(Path(policy_path)) if policy_path else
                               display_cache_config(data))
            batch = result["batch"] or {"assets": [], "export_fallbacks": []}
            inspected = config.cache().inspect_plan(batch)
            if result["batch"] is not None:
                result["batch"] = inspected
            result["cache_overview"] = {
                "cache": inspected["cache"],
                "policy": config.describe(sources),
            }
        except (ValueError, OSError, RasterCacheError) as exc:
            raise LookupError(f"Raster cache inspection failed: {exc}") from exc
    return result


def run(args: list[str], env: dict | None = None, python: str = sys.executable,
        timeout: int = TIMEOUT_SECONDS, input_text: str | None = None) -> dict:
    """Run one calculation in a child process; raise LookupError with its message on failure."""
    try:
        # On macOS, launching a child from this threaded server must use
        # posix_spawn: PROJ's registered fork handler can segfault in SQLite.
        # Direct script invocation removes the need to change the child's cwd.
        command = [python, str(ROOT / "tools" / "pipeline_webui" / "geo_worker.py"), *args]
        proc = subprocess.run(
            command,
            input=input_text, stdin=subprocess.DEVNULL if input_text is None else None,
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **(env or {})}, check=False,
            # Python-created descriptors are non-inheritable; this lets CPython
            # use posix_spawn on macOS instead of forking a PROJ-loaded process.
            close_fds=sys.platform != "darwin")
    except subprocess.TimeoutExpired:
        raise LookupError("area calculation timed out") from None
    except OSError as exc:
        raise LookupError(f"area calculation could not start ({type(exc).__name__})") from None
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        if proc.returncode < 0:
            detail = f"terminated by signal {-proc.returncode}"
        else:
            detail = f"exited with status {proc.returncode}"
        raise LookupError(f"area calculation worker returned no result ({detail})") from None
    if "error" in out:
        raise LookupError(out["error"])
    return out


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    try:
        if len(argv) == 2 and argv[0] == "km2":
            out = _km2(json.loads(argv[1]))
        elif len(argv) == 2 and argv[0] == "size":
            out = _size(argv[1])
        elif len(argv) == 3 and argv[0] == "selection":
            out = _selection(argv[1], argv[2])
        elif len(argv) == 2 and argv[0] == "catalog":
            out = _catalog(argv[1])
        elif argv == ["raster-plan"]:
            out = _raster_plan(json.load(sys.stdin))
        elif argv == ["national-dsm-preview"]:
            out = _national_dsm_preview(json.load(sys.stdin))
        elif argv == ["national-dsm-view"]:
            out = _national_dsm_view(json.load(sys.stdin))
        else:
            out = {"error": "usage: geo_worker km2 <bbox-json> | size <region> | "
                            "selection <request-json> <data-dir> | catalog <data-dir> | "
                            "raster-plan < request.json | national-dsm-preview < request.json"}
    except LookupError as exc:
        out = {"error": str(exc)}
    except Exception as exc:
        description = ("national DSM metadata or cache inspection"
                       if argv in (["national-dsm-preview"], ["national-dsm-view"]) else "area lookup")
        out = {"error": f"{description} failed ({type(exc).__name__})"}
    print(json.dumps(out))
    return 1 if "error" in out else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
