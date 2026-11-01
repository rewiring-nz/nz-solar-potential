"""The regions already in this checkout, as GeoJSON for the web panel's map.

A region is listed when it has inputs under data/regions/<id>/ or a built
summary at data/out/<id>/summary.json. Properties come from locality.json,
summary.json and the newest pipeline run for that area (status.json, or the
report.md header for runs written before status.json existed).
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from src.region_ids import is_locality_id, list_regions, validate
from tools.pipeline_webui.common import DATA, read_json, web_dir

SUMMARY_FIELDS = ("built_at", "git", "n", "n_est", "panel_count", "kwp", "kwh", "elevation", "centroid")
PREVIEW_ZOOM = 15.5
_REPORT_AREA = re.compile(r"^# Pipeline run: (.+)$", re.M)
_REPORT_STATUS = re.compile(r"\*\*Status:\*\* \*\*([A-Z ]+)\*\*")
_CACHE_SECONDS = 3.0
_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()


def preview_url(lon: float, lat: float, zoom: float = PREVIEW_ZOOM) -> str:
    """The view format rooftop-solar.html reads (startView: ?lat=&lng=&z=)."""
    return f"/rooftop-solar.html?lat={lat:.7f}&lng={lon:.7f}&z={zoom:g}"


def latest_runs(data: Path = DATA) -> dict[str, dict]:
    """Newest pipeline run per area: {area: {run_id, status, finished}}."""
    runs_dir = Path(data) / "pipeline_runs"
    found: dict[str, dict] = {}
    if not runs_dir.is_dir():
        return found
    for run_dir in sorted((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name):
        status = read_json(run_dir / "status.json")
        if isinstance(status, dict) and status.get("area"):
            finished = max((s.get("finished") or "" for s in status.get("steps", [])), default="")
            found[status["area"]] = {"run_id": run_dir.name, "status": status.get("overall"),
                                     "finished": finished or None}
            continue
        report = run_dir / "report.md"
        try:
            head = report.open(encoding="utf-8", errors="replace").read(2048)
        except OSError:
            continue
        area, overall = _REPORT_AREA.search(head), _REPORT_STATUS.search(head)
        if area:
            found[area.group(1).strip()] = {"run_id": run_dir.name,
                                            "status": overall.group(1).strip() if overall else None,
                                            "finished": None}
    return found


def _bbox_polygon(bbox) -> dict | None:
    try:
        w, s, e, n = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def _legacy_bbox(region: str):
    try:
        import config
    except ImportError:
        return None
    bbox = getattr(config, "REGIONS", {}).get(region)
    if bbox and len(bbox) == 4 and all(-180 <= float(v) <= 180 for v in bbox):
        return list(bbox)
    return None


def region_properties(region: str, data: Path = DATA, runs: dict | None = None) -> tuple[dict, dict | None]:
    """(properties, geometry) for one region id."""
    data = Path(data)
    props: dict[str, Any] = {"region": region,
                             "kind": "locality" if is_locality_id(region) else "legacy"}
    locality = read_json(data / "regions" / region / "locality.json") or {}
    linz = locality.get("linz") or {}
    if locality:
        props.update({
            "name": linz.get("name"), "type": linz.get("type"), "town": linz.get("major_name"),
            "council": linz.get("territorial_authority"), "linz_id": linz.get("id"),
            "buildings": locality.get("buildings"), "bbox_km2": locality.get("bbox_km2"),
            "prepared_utc": locality.get("prepared_utc"),
        })
    summary = read_json(data / "out" / region / "summary.json") or {}
    props.update({k: summary[k] for k in SUMMARY_FIELDS if k in summary})
    run = (runs if runs is not None else latest_runs(data)).get(region)
    if run:
        props.update({"last_run_id": run["run_id"], "last_run_status": run["status"],
                      "last_run_finished": run["finished"],
                      "report_url": f"/data/pipeline_runs/{run['run_id']}/report.md"})
    if run and run.get("status") == "FAIL":
        props["state"] = "failed"
    elif summary:
        props["state"] = "built"
    else:
        props["state"] = "downloaded"

    geometry = None
    if linz.get("id") is not None:
        cached = read_json(web_dir(data) / "cache" / "localities" / f"{int(linz['id'])}.geojson")
        if isinstance(cached, dict):
            geometry = cached.get("geometry")
    if geometry is None and locality.get("bbox"):
        geometry = _bbox_polygon(locality["bbox"])
    if geometry is None and not is_locality_id(region):
        geometry = _bbox_polygon(_legacy_bbox(region))
    centroid = summary.get("centroid")
    if geometry is None and centroid:
        geometry = {"type": "Point", "coordinates": list(centroid)}
    if not centroid and locality.get("bbox"):
        w, s, e, n = locality["bbox"]
        centroid = [(w + e) / 2, (s + n) / 2]
    if centroid:
        props["preview_url"] = preview_url(centroid[0], centroid[1])
    return props, geometry


def region_ids(data: Path = DATA) -> list[str]:
    data = Path(data)
    ids = set(list_regions(data / "regions")) | set(list_regions(data / "out", "summary.json"))
    return sorted(ids)


def catalog(data: Path = DATA, use_cache: bool = True) -> dict:
    """GeoJSON FeatureCollection of every region in this checkout."""
    key = str(Path(data).resolve())
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if use_cache and hit and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
    runs = latest_runs(data)
    features = []
    for region in region_ids(data):
        props, geometry = region_properties(region, data, runs)
        features.append({"type": "Feature", "id": region, "properties": props, "geometry": geometry})
    result = {"type": "FeatureCollection", "features": features}
    with _cache_lock:
        _cache[key] = (now, result)
    return result


def invalidate() -> None:
    with _cache_lock:
        _cache.clear()


def region_detail(region: str, data: Path = DATA) -> dict | None:
    """One region's properties and geometry, or None when it is not here."""
    try:
        validate(region)
    except ValueError:
        return None
    if region not in region_ids(data):
        return None
    props, geometry = region_properties(region, data)
    return {"type": "Feature", "id": region, "properties": props, "geometry": geometry}
