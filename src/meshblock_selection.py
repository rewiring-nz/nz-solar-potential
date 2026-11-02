"""Materialise a set of Stats NZ meshblocks as one isolated pipeline area."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from src import localities, region_ids
from src.region_build import write_json_atomic

OWNERSHIP_RULE = (
    "Transform whole LINZ building outlines to EPSG:2193; assign each outline "
    "to the lowest MB2023 ID whose polygon covers its representative point. "
    "Include it only when that owner is selected; never clip its geometry."
)
_MB_ID = re.compile(r"[A-Za-z0-9._-]{1,32}")


def selection_id(parent_region: str, meshblock_ids: list[str], dataset: dict) -> str:
    parent_region = region_ids.validate(parent_region)
    if not region_ids.is_locality_id(parent_region):
        raise ValueError("meshblock selections require a locality parent")
    ids = sorted(set(str(value) for value in meshblock_ids))
    if not ids or any(not _MB_ID.fullmatch(value) for value in ids):
        raise ValueError("select at least one valid meshblock")
    identity = {"parent": parent_region, "meshblocks": ids,
                "layer": dataset.get("layer_id"), "version": dataset.get("version")}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"{parent_region}/meshblocks-{digest[:16]}"


def select_buildings(outlines: dict, all_meshblocks: list[dict],
                     selected_ids: list[str]) -> dict:
    """Keep whole outlines assigned deterministically to selected blocks."""
    import pyproj
    from shapely.geometry import shape
    from shapely.ops import transform

    selected = set(selected_ids)
    transform_to_nztm = pyproj.Transformer.from_crs(
        "EPSG:4326", "EPSG:2193", always_xy=True).transform
    blocks = []
    for feature in all_meshblocks:
        props = feature.get("properties") or {}
        block_id = str(props.get("meshblock_id") or "")
        geometry = feature.get("geometry")
        if block_id and geometry:
            blocks.append((block_id, transform(transform_to_nztm, shape(geometry))))
    blocks.sort(key=lambda item: item[0])
    if not selected.issubset({block_id for block_id, _ in blocks}):
        raise ValueError("one or more selected meshblocks are not in the resolved parent boundaries")

    features = []
    for feature in outlines.get("features", []):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        point = shape(geometry).representative_point()
        owner = next((block_id for block_id, polygon in blocks if polygon.covers(point)), None)
        if owner in selected:
            features.append(feature)
    return {"type": "FeatureCollection", "features": features,
            "crs": outlines.get("crs")}


def acquisition_footprints(outlines: dict, buffer_m: float = localities.OUTLINE_PAD_M) -> list[list[float]]:
    """Buffered disjoint NZTM building clusters as WGS84 export bboxes."""
    import pyproj
    from shapely.geometry import shape
    from shapely.ops import unary_union

    geometries = [shape(f["geometry"]).buffer(buffer_m)
                  for f in outlines.get("features", []) if f.get("geometry")]
    if not geometries:
        return []
    merged = unary_union(geometries)
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    to_wgs84 = pyproj.Transformer.from_crs("EPSG:2193", "EPSG:4326", always_xy=True).transform
    footprints = []
    for part in parts:
        x0, y0, x1, y1 = part.bounds
        west, south = to_wgs84(x0, y0)
        east, north = to_wgs84(x1, y1)
        footprints.append([round(west, 6), round(south, 6), round(east, 6), round(north, 6)])
    return sorted(footprints, key=lambda bbox: (bbox[0], bbox[1]))


def _selection_outlines(parent: str, meshblocks: list[dict], selected_ids: list[str],
                        api_key: str, timing_path: Path) -> tuple[dict, dict]:
    import config
    import pyproj
    from shapely.geometry import shape
    from shapely.ops import transform, unary_union
    from src.dataset_timing import measure

    with measure("building outlines", f"LINZ layer {config.LINZ_BUILDING_OUTLINES_LAYER} WFS",
                 parent, "downloaded", timing_path):
        parent_props = localities.resolve(api_key, parent)
        to_nztm = pyproj.Transformer.from_crs(
            "EPSG:4326", "EPSG:2193", always_xy=True).transform
        selected_polygons = [
            transform(to_nztm, shape(feature["geometry"]))
            for feature in meshblocks
            if str((feature.get("properties") or {}).get("meshblock_id")) in selected_ids
            and feature.get("geometry")
        ]
        if not selected_polygons:
            raise ValueError("selected meshblock geometries are missing")

        union = unary_union(selected_polygons)
        components = list(union.geoms) if hasattr(union, "geoms") else [union]
        candidates = {}
        locality_filter = localities.outline_filter(parent_props)
        for component in components:
            x0, y0, x1, y1 = component.bounds
            cql = f"{locality_filter} AND BBOX(shape,{x0},{y0},{x1},{y1},'EPSG:2193')"
            for feature in localities._wfs(api_key, config.LINZ_BUILDING_OUTLINES_LAYER,
                                           cql, timeout=600):
                identity = (feature.get("properties") or {}).get("id")
                if identity is not None:
                    candidates[str(identity)] = feature
        outlines = {
            "type": "FeatureCollection", "features": list(candidates.values()),
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2193"}},
        }
        selected_outlines = select_buildings(outlines, meshblocks, selected_ids)
        if not selected_outlines["features"]:
            raise ValueError("the selected meshblocks contain no LINZ building outlines")
        return parent_props, selected_outlines


def materialise(request_path: Path, data: Path, api_key: str) -> dict:
    """Fetch only selected meshblock footprints and persist one resumable build area."""
    try:
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        request = None
    if not isinstance(request, dict):
        raise ValueError("meshblock selection request is missing or invalid")
    parent = region_ids.validate(request.get("parent_region"))
    if not region_ids.is_locality_id(parent):
        raise ValueError("meshblock selections require a locality parent")
    dataset = request.get("dataset") or {}
    meshblocks = request.get("meshblocks") or []
    selected_ids = sorted(set(request.get("meshblock_ids") or []))
    selected_id = selection_id(parent, selected_ids, dataset)
    area_dir = Path(data) / "regions" / selected_id
    selection_path = area_dir / "selection.json"
    try:
        existing = json.loads(selection_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = None
    if (isinstance(existing, dict) and existing.get("selection_id") == selected_id
            and (area_dir / "building_outlines.geojson").is_file()
            and (area_dir / "locality.json").is_file()):
        from src.dataset_timing import measure
        import config
        with measure("building outlines", f"LINZ layer {config.LINZ_BUILDING_OUTLINES_LAYER} WFS",
                     parent, "reused_from_cache", area_dir / "acquisition_timings.json"):
            pass
        return existing

    import config
    parent_props, selected_outlines = _selection_outlines(
        parent, meshblocks, selected_ids, api_key, area_dir / "acquisition_timings.json")
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union
    bbox = localities.outlines_bbox_wgs84(selected_outlines)
    footprints = acquisition_footprints(selected_outlines)
    selected_features = [f for f in meshblocks
                         if str((f.get("properties") or {}).get("meshblock_id")) in selected_ids]
    if len(selected_features) != len(selected_ids):
        raise ValueError("one or more selected meshblock boundaries are missing")

    selection_geometry = mapping(unary_union([shape(f["geometry"]) for f in selected_features]))
    manifest = {
        "selection_id": selected_id,
        "parent_region": parent,
        "parent_linz_id": request["parent_linz_id"],
        "meshblock_ids": selected_ids,
        "dataset": dataset,
        "geometry": selection_geometry,
        "building_selection_rule": OWNERSHIP_RULE,
        "buildings": len(selected_outlines["features"]),
        "building_ids": sorted(str((f.get("properties") or {}).get("id"))
                               for f in selected_outlines["features"]),
        "bbox": bbox, "acquisition_footprints": footprints,
    }
    locality_record = {
        "region": selected_id,
        "source": {"layer": config.LINZ_SUBURBS_LOCALITIES_LAYER,
                   "name": "LINZ NZ Suburbs and Localities"},
        "linz": {key: parent_props.get(key) for key in localities.LOCALITY_FIELDS},
        "buildings": manifest["buildings"],
        "bbox": bbox,
        "acquisition_footprints": footprints,
        "bbox_note": "Selected building extent plus the locality outline buffer",
        "bbox_km2": round(localities.bbox_km2(bbox), 2),
        "parent_region": parent,
        "meshblock_selection": {
            "selection_id": selected_id, "meshblock_count": len(selected_ids),
            "dataset": dataset,
        },
        "prepared_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    area_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(area_dir / "building_outlines.geojson", selected_outlines)
    write_json_atomic(area_dir / "selection.json", manifest)
    write_json_atomic(area_dir / "locality.json", locality_record)
    return manifest
