"""LINZ NZ Suburbs and Localities for the web panel's selection map.

Polygons are fetched by WGS84 bbox from the LINZ WFS (version 1.0.0, geometry
attribute `shape`, srsName EPSG:4326 returns lon/lat order), water
localities dropped as tools/find_area.py does, simplified to about 5 m, and
given the region id tools/pipeline.py accepts. The request is a local copy of
src/localities._wfs (which takes no srsName) so src/ and its build keys stay
untouched.

Region ids get the LINZ-id suffix when another locality anywhere in NZ
shares the plain id; one extra attribute-only WFS query per bbox checks that,
because a bbox can cut off the homonym.

Responses are cached under data/pipeline_web/cache/localities/ for
CACHE_DAYS days; each polygon is also kept as <linz_id>.geojson for the
region catalogue. The API key never appears in a response or error text.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

import config
from src import localities as L
from src.region_ids import is_grid_selection_id, is_locality_id
from tools.pipeline_webui.common import DATA, read_json, web_dir, write_json_atomic

MAX_BBOX_DEGREES = 1.0
MAX_FEATURES = 2000
CACHE_DAYS = 7
SIMPLIFY_DEGREES = 0.00005  # about 5 m at NZ latitudes
GEOMETRY_FIELD = "shape"    # verified with WFS DescribeFeatureType on layer 113764
WATER_TYPES = ("Lake", "Inland Bay")
SEARCH_LIMIT = 50


class LocalityError(Exception):
    """A message safe to show in the browser, with the HTTP status to use."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _default_get(url, params, timeout):
    import requests
    return requests.get(url, params=params, timeout=timeout)


def _wfs(api_key: str, cql: str, fields, http_get: Callable | None = None,
         geometry: bool = False, max_features: int = MAX_FEATURES) -> list[dict]:
    if not api_key:
        raise LocalityError("LINZ_API_KEY is not set (environment, .env or Secret Manager)", 503)
    params = {
        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
        "typeName": f"data.linz.govt.nz:layer-{config.LINZ_SUBURBS_LOCALITIES_LAYER}",
        "outputFormat": "json", "maxFeatures": max_features, "cql_filter": cql,
        "propertyName": ",".join(list(fields) + ([GEOMETRY_FIELD] if geometry else [])),
    }
    if geometry:
        params["srsName"] = "EPSG:4326"
    try:
        resp = (http_get or _default_get)(L.WFS_URL.format(key=api_key), params, 120)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # the exception text can carry the keyed URL
        raise LocalityError(f"LINZ WFS request failed ({type(exc).__name__})") from None
    feats = data.get("features") if isinstance(data, dict) else None
    if feats is None:
        raise LocalityError("unexpected LINZ WFS response")
    if len(feats) >= max_features:
        raise LocalityError("too many localities in view; zoom in", 413)
    return feats


def parse_bbox(text: str) -> list[float]:
    try:
        w, s, e, n = (float(v) for v in str(text).split(","))
    except ValueError:
        raise LocalityError("bbox must be minLon,minLat,maxLon,maxLat", 400) from None
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise LocalityError("bbox must be minLon,minLat,maxLon,maxLat in WGS84", 400)
    if e - w > MAX_BBOX_DEGREES or n - s > MAX_BBOX_DEGREES:
        raise LocalityError("zoom in", 413)
    return [w, s, e, n]


def _simplify(geometry: dict) -> dict:
    from shapely.geometry import mapping, shape
    simple = shape(geometry).simplify(SIMPLIFY_DEGREES, preserve_topology=True)
    out = mapping(simple)

    def rnd(c):
        if c and isinstance(c[0], (int, float)):
            return [round(c[0], 6), round(c[1], 6)]
        return [rnd(x) for x in c]

    return {"type": out["type"], "coordinates": rnd(out["coordinates"])}


def _is_water(props: dict) -> bool:
    return props.get("type") in WATER_TYPES


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _summary(props: dict, region: str) -> dict:
    return {"region_id": region, "linz_id": props.get("id"), "name": props.get("name"),
            "type": props.get("type"), "town": props.get("major_name"),
            "council": props.get("territorial_authority"),
            "population_estimate": props.get("population_estimate")}


def localities_in_bbox(bbox: list[float], api_key: str, data: Path = DATA,
                       http_get: Callable | None = None, now: float | None = None) -> dict:
    """FeatureCollection of land localities intersecting `bbox` (WGS84)."""
    now = time.time() if now is None else now
    cache_dir = web_dir(data) / "cache" / "localities"
    key = "_".join(f"{v:.3f}" for v in bbox)
    cache_file = cache_dir / f"bbox-{key}.json"
    cached = read_json(cache_file)
    if isinstance(cached, dict) and now - cached.get("fetched", 0) < CACHE_DAYS * 86400:
        return cached["collection"]

    w, s, e, n = bbox
    cql = f"BBOX({GEOMETRY_FIELD},{w},{s},{e},{n},'EPSG:4326')"
    feats = [f for f in _wfs(api_key, cql, L.LOCALITY_FIELDS, http_get, geometry=True)
             if f.get("geometry") and not _is_water(f.get("properties") or {})]
    names = sorted({f["properties"]["name"] for f in feats if f["properties"].get("name")})
    ambiguous: set[int] = set()
    if names:
        homonyms = _wfs(api_key, "name IN (" + ",".join(_quote(x) for x in names) + ")",
                        L.LOCALITY_FIELDS, http_get)
        by_id = {p["id"]: p for p in [h["properties"] for h in homonyms] +
                 [f["properties"] for f in feats]}
        ambiguous = L.ambiguous_ids(list(by_id.values()))
    out = []
    for f in feats:
        props = f["properties"]
        region = L.display_region_id(props, ambiguous)
        feature = {"type": "Feature", "id": props["id"],
                   "properties": _summary(props, region), "geometry": _simplify(f["geometry"])}
        out.append(feature)
        write_json_atomic(cache_dir / f"{int(props['id'])}.geojson", feature)
    collection = {"type": "FeatureCollection", "features": out}
    write_json_atomic(cache_file, {"fetched": now, "bbox": bbox, "collection": collection})
    return collection


def search(text: str, api_key: str, http_get: Callable | None = None) -> list[dict]:
    """Land localities whose names contain `text`, with their region ids."""
    text = (text or "").strip()
    if not 2 <= len(text) <= 64:
        raise LocalityError("search text must be 2 to 64 characters", 400)
    pattern = _quote("%" + text.replace("%", "") + "%")
    cql = f"(name ILIKE {pattern} OR name_ascii ILIKE {pattern} OR additional_name ILIKE {pattern})"
    feats = _wfs(api_key, cql, L.LOCALITY_FIELDS, http_get, geometry=True)
    found = [f["properties"] for f in feats]
    bboxes = {id(f["properties"]): _bbox(f.get("geometry")) for f in feats}
    ambiguous = L.ambiguous_ids(found)
    land = sorted((p for p in found if not _is_water(p)),
                  key=lambda p: (-(p.get("population_estimate") or 0), p.get("name") or ""))
    return [dict(_summary(p, L.display_region_id(p, ambiguous)), bbox=bboxes[id(p)])
            for p in land[:SEARCH_LIMIT]]


def feature_for_region(region: str, api_key: str, data: Path = DATA,
                       http_get: Callable | None = None) -> dict:
    """Resolve a build area to its LINZ locality geometry for map navigation."""
    if is_grid_selection_id(region):
        region = region.rsplit("/", 1)[0]
    if not is_locality_id(region):
        raise LocalityError("this build area has no LINZ locality boundary", 404)

    council_slug, town_slug, suburb_slug = region.split("/")
    suburb_slugs = [suburb_slug]
    without_linz_suffix = re.sub(r"-\d+$", "", suburb_slug)
    if without_linz_suffix != suburb_slug:
        suburb_slugs.append(without_linz_suffix)
    found = []
    for candidate_slug in suburb_slugs:
        name_pattern = "%" + "%".join(candidate_slug.split("-")) + "%"
        clauses = [
            f"name_ascii ILIKE {_quote(name_pattern)}",
            f"territorial_authority_ascii ILIKE {_quote('%' + '%'.join(council_slug.split('-')) + '%')}",
        ]
        if town_slug == L.NO_TOWN:
            clauses.append("(major_name_ascii IS NULL OR major_name_ascii = '')")
        else:
            clauses.append(
                f"major_name_ascii ILIKE {_quote('%' + '%'.join(town_slug.split('-')) + '%')}")
        found = _wfs(api_key, " AND ".join(clauses), L.LOCALITY_FIELDS, http_get)
        ambiguous = L.ambiguous_ids([item["properties"] for item in found])
        raw = next((item for item in found
                    if L.display_region_id(item["properties"], ambiguous) == region), None)
        if raw is not None:
            break
    else:
        raise LocalityError("no LINZ locality boundary found for this build area", 404)

    linz_id = int(raw["properties"]["id"])
    cache_file = web_dir(data) / "cache" / "localities" / f"{linz_id}.geojson"
    cached = read_json(cache_file)
    if isinstance(cached, dict) and cached.get("properties", {}).get("linz_id") == linz_id:
        feature = cached
        feature["properties"]["region_id"] = region
        return feature

    features = _wfs(api_key, f"id = {linz_id}", L.LOCALITY_FIELDS, http_get, geometry=True)
    raw_geometry = next((item for item in features
                         if (item.get("properties") or {}).get("id") == linz_id), None)
    if raw_geometry is None or not raw_geometry.get("geometry"):
        raise LocalityError("LINZ did not return a boundary for this build area", 404)
    props = _summary(raw_geometry["properties"], region)
    feature = {"type": "Feature", "id": linz_id, "properties": props,
               "geometry": _simplify(raw_geometry["geometry"])}
    write_json_atomic(cache_file, feature)
    return feature


def _bbox(geometry: dict | None) -> list[float] | None:
    """WGS84 [west, south, east, north] of a GeoJSON geometry, for flying the map to a result."""
    if not geometry:
        return None
    try:
        from shapely.geometry import shape
        return [round(v, 6) for v in shape(geometry).bounds]
    except Exception:
        return None
