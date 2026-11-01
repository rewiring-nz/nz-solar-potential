"""Stats NZ meshblock boundaries clipped to a selected LINZ locality."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from src import localities as L
from tools.pipeline_webui import locality_proxy as LP
from tools.pipeline_webui.common import DATA, read_json, web_dir, write_json_atomic

MESHBLOCK_LAYER = 111228
MESHBLOCK_VERSION = "2023 generalised"
MESHBLOCK_FIELD = "MB2023"
GEOMETRY_FIELD = "shape"
MAX_FEATURES = 5000
CACHE_SECONDS = 7 * 86400
_MESHBlock_ID = re.compile(r"[A-Za-z0-9._-]{1,32}")


class MeshblockError(LP.LocalityError):
    pass


def _wfs(api_key: str, cql: str, http_get: Callable | None = None) -> list[dict]:
    if not api_key:
        raise MeshblockError("LINZ_API_KEY is not set (environment, .env or Secret Manager)", 503)
    params = {
        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
        "typeName": f"data.linz.govt.nz:layer-{MESHBLOCK_LAYER}",
        "outputFormat": "json", "maxFeatures": MAX_FEATURES, "cql_filter": cql,
        "propertyName": f"{MESHBLOCK_FIELD},{GEOMETRY_FIELD}", "srsName": "EPSG:4326",
    }
    try:
        resp = (http_get or LP._default_get)(L.WFS_URL.format(key=api_key), params, 120)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise MeshblockError(f"LINZ meshblock request failed ({type(exc).__name__})") from None
    feats = data.get("features") if isinstance(data, dict) else None
    if feats is None:
        raise MeshblockError("unexpected LINZ meshblock response")
    if len(feats) >= MAX_FEATURES:
        raise MeshblockError("too many meshblocks in this locality; choose a smaller parent area", 413)
    return feats


def _parent_region_id(props: dict) -> str:
    return L.region_id(props)


def _simplify(geometry: dict) -> dict:
    from shapely.geometry import mapping, shape

    simple = mapping(shape(geometry).simplify(0.00002, preserve_topology=True))

    def rounded(coords):
        if coords and isinstance(coords[0], (int, float)):
            return [round(coords[0], 6), round(coords[1], 6)]
        return [rounded(item) for item in coords]

    return {"type": simple["type"], "coordinates": rounded(simple["coordinates"])}


def meshblocks(region: str, linz_id: int, api_key: str, data: Path = DATA,
               http_get: Callable | None = None, now: float | None = None,
               exact: bool = False) -> dict:
    """Return meshblocks intersecting the selected locality boundary (WGS84)."""
    try:
        region = L.validate(region)
        linz_id = int(linz_id)
    except (ValueError, TypeError):
        raise MeshblockError("a valid locality region and LINZ locality id are required", 400) from None
    if not L.is_locality_id(region) or linz_id < 1:
        raise MeshblockError("meshblocks can only be selected within a locality", 400)
    now = time.time() if now is None else now
    cache_dir = web_dir(data) / "cache" / "meshblocks"
    cache = cache_dir / f"{linz_id}.json"
    cached = read_json(cache)
    if (isinstance(cached, dict) and cached.get("region") == region
            and now - cached.get("fetched", 0) < CACHE_SECONDS
            and (not exact or isinstance(cached.get("ownership_features"), list))):
        if exact:
            return {**cached["collection"], "features": cached["ownership_features"]}
        return cached["collection"]

    parent = LP._wfs(api_key, f"id = {linz_id}", L.LOCALITY_FIELDS, http_get, geometry=True)
    matching = [f for f in parent if int((f.get("properties") or {}).get("id") or 0) == linz_id]
    if not matching:
        raise MeshblockError("the locality selection no longer matches LINZ; select it again", 409)
    props = matching[0]["properties"]
    if region not in {_parent_region_id(props), L.region_id(props, with_id=True)}:
        raise MeshblockError("the locality region and LINZ id do not match", 400)
    parent_geometry = matching[0].get("geometry")
    if not parent_geometry:
        raise MeshblockError("LINZ returned no locality boundary")

    from shapely.geometry import shape

    boundary = shape(parent_geometry)
    west, south, east, north = boundary.bounds
    cql = f"BBOX({GEOMETRY_FIELD},{west},{south},{east},{north},'EPSG:4326')"
    candidates = _wfs(api_key, cql, http_get)
    features, ownership_features = [], []
    for candidate in candidates:
        props = candidate.get("properties") or {}
        block_id = str(props.get(MESHBLOCK_FIELD) or "")
        geometry = candidate.get("geometry")
        if not block_id or not _MESHBlock_ID.fullmatch(block_id) or not geometry:
            continue
        polygon = shape(geometry)
        if not polygon.intersects(boundary):
            continue
        exact_feature = {
            "type": "Feature", "id": block_id,
            "properties": {"meshblock_id": block_id, "parent_region": region},
            "geometry": geometry,
        }
        ownership_features.append(exact_feature)
        features.append({**exact_feature, "geometry": _simplify(geometry)})
    features.sort(key=lambda f: f["properties"]["meshblock_id"])
    collection = {
        "type": "FeatureCollection",
        "features": features,
        "dataset": {"name": "Stats NZ Meshblock 2023 (generalised)",
                    "layer_id": MESHBLOCK_LAYER, "version": MESHBLOCK_VERSION,
                    "native_crs": "EPSG:2193", "response_crs": "EPSG:4326",
                    "id_field": MESHBLOCK_FIELD},
    }
    write_json_atomic(cache, {"fetched": now, "region": region, "collection": collection,
                              "ownership_features": ownership_features})
    return ({**collection, "features": ownership_features} if exact else collection)
