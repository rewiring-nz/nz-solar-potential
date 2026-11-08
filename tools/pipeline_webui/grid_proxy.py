"""Grid cells clipped to a LINZ locality boundary for partial-area builds."""

from __future__ import annotations

from src import localities as L
from tools.pipeline_webui import locality_proxy as LP

MIN_GRID_SIZE = 2
MAX_GRID_SIZE = 10


def grid_cells(region: str, linz_id: int, api_key: str,
               http_get=None, grid_size: int = 4) -> dict:
    """Return a selectable grid clipped to one LINZ locality."""
    try:
        region = L.validate(region)
        linz_id = int(linz_id)
        grid_size = int(grid_size)
    except (ValueError, TypeError):
        raise LP.LocalityError("a valid locality, LINZ id, and grid size are required", 400) from None
    if not L.is_locality_id(region) or linz_id < 1:
        raise LP.LocalityError("grid cells can only be selected within a locality", 400)
    if not MIN_GRID_SIZE <= grid_size <= MAX_GRID_SIZE:
        raise LP.LocalityError(
            f"grid size must be from {MIN_GRID_SIZE}x{MIN_GRID_SIZE} to "
            f"{MAX_GRID_SIZE}x{MAX_GRID_SIZE}", 400)

    parent = LP._wfs(api_key, f"id = {linz_id}", L.LOCALITY_FIELDS, http_get, geometry=True)
    matching = [f for f in parent if int((f.get("properties") or {}).get("id") or 0) == linz_id]
    if not matching:
        raise LP.LocalityError("the locality selection no longer matches LINZ; select it again", 409)
    props = matching[0]["properties"]
    if region not in {L.region_id(props), L.region_id(props, with_id=True)}:
        raise LP.LocalityError("the locality region and LINZ id do not match", 400)
    geometry = matching[0].get("geometry")
    if not geometry:
        raise LP.LocalityError("LINZ returned no locality boundary")

    from shapely.geometry import box, mapping, shape

    boundary = shape(geometry)
    west, south, east, north = boundary.bounds
    width, height = (east - west) / grid_size, (north - south) / grid_size
    features = []
    for row in range(grid_size):
        top = north - row * height
        bottom = top - height
        for column in range(grid_size):
            left = west + column * width
            right = left + width
            clipped = box(left, bottom, right, top).intersection(boundary)
            if clipped.is_empty or clipped.geom_type not in {"Polygon", "MultiPolygon"}:
                continue
            grid_id = f"r{row + 1:02}c{column + 1:02}"
            features.append({
                "type": "Feature", "id": grid_id,
                "properties": {"grid_id": grid_id, "parent_region": region},
                "geometry": mapping(clipped),
            })
    return {
        "type": "FeatureCollection",
        "features": features,
        "grid_size": grid_size,
    }
