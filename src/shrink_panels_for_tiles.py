"""Shrink panel polygons by GAP_M before tiling so adjacent panels show a
thin sliver of real roof between them (user-requested look; an outline
can't fake a gap). Run on the merged panel_layouts.geojson AFTER merge,
BEFORE tippecanoe. Facets/obstructions untouched."""
import json, sys, math
from pathlib import Path
GAP_M = 0.04  # was 0.07 -- Josh: gaps a touch smaller
DATA = Path(__file__).resolve().parent.parent / "data"
def main():
    path = DATA / "panel_layouts.geojson"
    d = json.loads(path.read_text())
    from shapely.geometry import shape, mapping
    n = 0
    for f in d["features"]:
        if f["properties"].get("kind") != "panel" or f["geometry"]["type"] != "Polygon":
            continue
        lat = f["geometry"]["coordinates"][0][0][1]
        deg = GAP_M / (111320.0 * math.cos(math.radians(lat)))
        g = shape(f["geometry"]).buffer(-deg, join_style=2)
        if not g.is_empty and g.geom_type == "Polygon":
            f["geometry"] = mapping(g)
            n += 1
    path.write_text(json.dumps(d))
    print(f"shrunk {n} panels by {GAP_M}m for tile gaps")
if __name__ == "__main__":
    main()
