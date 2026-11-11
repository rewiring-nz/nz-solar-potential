"""Discover LINZ elevation and imagery sources from the LINZ layer catalogue.

Layer metadata is cached under data/sources/linz, which is local generated
data and is not committed. The source catalogue is public; no API key is
needed to read its metadata.
"""

from __future__ import annotations

import html
import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
API = "https://data.linz.govt.nz/services/api/v1.x"
CACHE_NAME = "survey_layer_catalog.json"
CACHE_TTL_S = 7 * 24 * 60 * 60
KEYS = (
    "dsm_layer", "dem_layer", "imagery_layer", "lidar_tile_index_layer",
    "pointcloud_bulk_url", "pointcloud_tile_year", "reference_imagery_layer",
    "elevation", "dsm_s3", "imagery_s3", "reference_imagery_s3",
)
_CACHE_LOCK = threading.RLock()


def _http_json(url: str) -> dict | list:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise LookupError(f"LINZ source catalogue request failed ({type(exc).__name__})") from exc


def _catalog_path(data_dir: Path) -> Path:
    return Path(data_dir) / "sources" / "linz" / CACHE_NAME


def _read_catalog(path: Path) -> dict:
    if not path.exists():
        return {"queries": {}, "layers": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LookupError(f"LINZ source cache is unreadable ({type(exc).__name__}): {path}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("queries"), dict) \
            or not isinstance(value.get("layers"), dict):
        raise LookupError(f"LINZ source cache has an unsupported format: {path}")
    return value


def _write_catalog(path: Path, catalog: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(catalog, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _query_layers(query: str, catalog: dict, path: Path, get_json, now: float) -> list[dict]:
    entry = catalog["queries"].get(query)
    if isinstance(entry, dict) and now - float(entry.get("fetched_at", 0)) < CACHE_TTL_S:
        return entry["layers"]

    url = f"{API}/layers/?{urllib.parse.urlencode({'q': query})}"
    result = get_json(url)
    if not isinstance(result, list):
        raise LookupError("LINZ source catalogue returned an unexpected layer-list response")
    layers = []
    for item in result:
        if not isinstance(item, dict) or item.get("id") is None or not item.get("title"):
            continue
        layers.append({"id": int(item["id"]), "title": str(item["title"]),
                       "url": str(item.get("url") or f"{API}/layers/{int(item['id'])}/")})
    catalog["queries"][query] = {"fetched_at": now, "layers": layers}
    _write_catalog(path, catalog)
    return layers


def _layer_detail(layer: dict, catalog: dict, path: Path, get_json, now: float) -> dict:
    layer_id = str(layer["id"])
    cached = catalog["layers"].get(layer_id)
    if isinstance(cached, dict) and now - float(cached.get("fetched_at", 0)) < CACHE_TTL_S:
        return cached["metadata"]
    metadata = get_json(layer["url"])
    if not isinstance(metadata, dict):
        raise LookupError(f"LINZ layer {layer_id} returned an unexpected metadata response")
    catalog["layers"][layer_id] = {"fetched_at": now, "metadata": metadata}
    _write_catalog(path, catalog)
    return metadata


def _pinned_layer(layer_id, bbox, predicate, catalog, path, get_json, now) -> dict | None:
    if layer_id is None:
        return None
    if isinstance(layer_id, bool):
        raise LookupError("pinned LINZ source layer ID is invalid")
    try:
        layer_id = int(layer_id)
    except (TypeError, ValueError):
        raise LookupError("pinned LINZ source layer ID is invalid") from None
    layer = {"id": layer_id, "title": "", "url": f"{API}/layers/{layer_id}/"}
    metadata = _layer_detail(layer, catalog, path, get_json, now)
    title = str(metadata.get("title") or "")
    bounds = _extent_bounds(metadata)
    if not predicate(title) or not _covers(bounds, bbox):
        raise LookupError(f"pinned LINZ layer {layer_id} no longer covers this area")
    return {"id": layer_id, "title": title, "bounds": bounds, "metadata": metadata}


def _coordinates(value):
    if not isinstance(value, list):
        return
    if len(value) >= 2 and all(isinstance(v, (int, float)) for v in value[:2]):
        yield float(value[0]), float(value[1])
        return
    for item in value:
        yield from _coordinates(item)


def _extent_bounds(metadata: dict) -> list[float] | None:
    extent = (metadata.get("data") or {}).get("extent")
    if not isinstance(extent, dict):
        return None
    points = list(_coordinates(extent.get("coordinates")))
    if not points:
        return None
    bounds = [min(x for x, _ in points), min(y for _, y in points),
              max(x for x, _ in points), max(y for _, y in points)]
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        return None
    return bounds


def _covers(bounds: list[float] | None, bbox) -> bool:
    if bounds is None:
        return False
    west, south, east, north = (float(v) for v in bbox)
    return bounds[0] <= west and bounds[1] <= south and bounds[2] >= east and bounds[3] >= north


def _years(title: str) -> list[int]:
    return [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", title)]


def _place_terms(region: str) -> list[str]:
    if region == "pilot":
        return ["Queenstown"]
    parts = region.split("/")
    candidates = ([parts[1], parts[2], parts[0]] if len(parts) >= 3
                  else [region.replace("_", " ").replace("-", " ")])
    output = []
    for value in candidates:
        value = re.sub(r"[-_]+", " ", value).strip()
        if value and value.casefold() not in {x.casefold() for x in output}:
            output.append(value.title())
    return output


def _layers_for(query: str, bbox, predicate, catalog, path, get_json, now) -> list[dict]:
    layers = _query_layers(query, catalog, path, get_json, now)
    found = []
    for layer in layers:
        if not predicate(layer["title"]):
            continue
        metadata = _layer_detail(layer, catalog, path, get_json, now)
        bounds = _extent_bounds(metadata)
        if _covers(bounds, bbox):
            found.append({"id": layer["id"], "title": layer["title"],
                          "bounds": bounds, "metadata": metadata})
    return found


def _select_dsm(bbox, terms, catalog, path, get_json, now) -> dict | None:
    predicate = lambda title: "lidar" in title.casefold() and "dsm" in title.casefold()
    for place in terms:
        candidates = _layers_for(f"{place} LiDAR 1m DSM", bbox, predicate,
                                 catalog, path, get_json, now)
        if candidates:
            candidates.sort(key=lambda item: (
                max(_years(item["title"]), default=0),
                -(item["bounds"][2] - item["bounds"][0])
                * (item["bounds"][3] - item["bounds"][1]),
            ), reverse=True)
            return candidates[0]
    candidates = _layers_for("LiDAR 1m DSM", bbox, predicate, catalog, path, get_json, now)
    candidates = [item for item in candidates
                  if "new zealand lidar" not in item["title"].casefold()]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (
        max(_years(item["title"]), default=0),
        -(item["bounds"][2] - item["bounds"][0])
        * (item["bounds"][3] - item["bounds"][1]),
    ), reverse=True)
    return candidates[0]


def _layer_id(description: str, label: str) -> int | None:
    description = html.unescape(description or "")
    description = re.sub(
        r'<a\b[^>]*href=["\'][^"\']*/layer/(\d+)[^"\']*["\'][^>]*>(.*?)</a>',
        lambda match: f"{match.group(2)} layer/{match.group(1)}",
        description,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]*>", " ", description)
    match = re.search(
        rf"{label}.{{0,240}}?(?:data\.linz\.govt\.nz/)?layer/(\d+)",
        text, re.IGNORECASE | re.DOTALL,
    )
    return int(match.group(1)) if match else None


def _select_imagery(bbox, terms, dsm_years, catalog, path, get_json, now) -> tuple[dict | None, dict | None]:
    predicate = lambda title: "aerial" in title.casefold() and "photo" in title.casefold()
    candidates = []
    for place in terms:
        candidates = _layers_for(f"{place} aerial photos", bbox, predicate,
                                 catalog, path, get_json, now)
        if candidates:
            break
    if not candidates:
        return None, None

    def rank(item):
        data = item["metadata"].get("data") or {}
        resolution = data.get("raster_resolution")
        return (max(_years(item["title"]), default=0),
                -(float(resolution) if isinstance(resolution, (int, float)) and resolution > 0 else 999))

    candidates.sort(key=rank, reverse=True)
    current = candidates[0]
    reference = next((item for item in candidates
                     if dsm_years and set(_years(item["title"])) & set(dsm_years)), None)
    return current, reference


def survey_for(bbox, name=None, *, data_dir: Path = DATA_DIR,
               get_json=None, now: float | None = None,
               overrides: dict | None = None) -> dict:
    """Resolve the newest covering LINZ surface and compatible aerial photos.

    Metadata is cached locally for seven days. If no LiDAR layer covers the
    area, the caller uses the modelled-surface path rather than guessing a
    source layer.
    """
    get_json = get_json or _http_json
    now = time.time() if now is None else now
    path = _catalog_path(Path(data_dir))
    if overrides is None:
        encoded_overrides = os.environ.get("SOLAR_SURVEY_OVERRIDES_B64")
        raw_overrides = os.environ.get("SOLAR_SURVEY_OVERRIDES")
        if encoded_overrides:
            try:
                raw_overrides = base64.b64decode(encoded_overrides, validate=True).decode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise LookupError("encoded source overrides are invalid") from exc
        if raw_overrides:
            try:
                configured = json.loads(raw_overrides)
            except json.JSONDecodeError as exc:
                raise LookupError("source overrides are not valid JSON") from exc
            if not isinstance(configured, dict):
                raise LookupError("source overrides must be a region-to-layers object")
            overrides = configured.get(str(name))
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise LookupError("pinned source layers must be an object")
    with _CACHE_LOCK:
        catalog = _read_catalog(path)
        terms = _place_terms(str(name or ""))
        if "dsm_layer" in overrides:
            dsm = _pinned_layer(
                overrides["dsm_layer"], bbox,
                lambda title: "lidar" in title.casefold() and "dsm" in title.casefold(),
                catalog, path, get_json, now,
            )
        else:
            dsm = _select_dsm(bbox, terms, catalog, path, get_json, now)
        years = _years(dsm["title"]) if dsm else []
        if "imagery_layer" in overrides:
            imagery = _pinned_layer(
                overrides["imagery_layer"], bbox,
                lambda title: "aerial" in title.casefold() and "photo" in title.casefold(),
                catalog, path, get_json, now,
            )
            reference = _pinned_layer(
                overrides.get("reference_imagery_layer"), bbox,
                lambda title: "aerial" in title.casefold() and "photo" in title.casefold(),
                catalog, path, get_json, now,
            ) if overrides.get("reference_imagery_layer") is not None else None
        else:
            imagery, reference = _select_imagery(
                bbox, terms, years, catalog, path, get_json, now)
        _write_catalog(path, catalog)

    out = {key: None for key in KEYS}
    out.update({
        "name": dsm["title"] if dsm else f"No LiDAR source covers {name or bbox}",
        "bbox": dsm["bounds"] if dsm else None,
        "elevation": "lidar" if dsm else "synthetic",
        "dsm_layer": dsm["id"] if dsm else None,
        "dem_layer": (overrides.get("dem_layer") if "dem_layer" in overrides else
                      _layer_id(dsm["metadata"].get("description_html")
                                or dsm["metadata"].get("description", ""),
                                r"(?:DEM|Digital Elevation Model)")) if dsm else None,
        "lidar_tile_index_layer": (
            overrides.get("lidar_tile_index_layer") if "lidar_tile_index_layer" in overrides else
            _layer_id(dsm["metadata"].get("description_html")
                      or dsm["metadata"].get("description", ""),
                      r"(?:Index Tiles?|Tile Index)")) if dsm else None,
        "pointcloud_bulk_url": (overrides.get("pointcloud_bulk_url")
                                if "pointcloud_bulk_url" in overrides else None),
        "pointcloud_tile_year": (overrides.get("pointcloud_tile_year")
                                 if "pointcloud_tile_year" in overrides else
                                 str(max(years)) if years else None),
        "imagery_layer": imagery["id"] if imagery else None,
        "reference_imagery_layer": reference["id"] if reference else None,
    })
    if dsm and not out["pointcloud_bulk_url"]:
        out["warning"] = (
            "No verified raw point-cloud bulk source was found in the LINZ catalogue; "
            "this build will use the DSM where raw point-cloud tiles are unavailable."
        )
    if imagery:
        out["imagery_title"] = imagery["title"]
    if dsm:
        out["dsm_title"] = dsm["title"]
    return out
