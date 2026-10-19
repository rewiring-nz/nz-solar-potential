"""
Straight edges between roof faces.

A face's edges with its neighbours are where the LiDAR partition happened to
split scattered returns, so they wobble: a ridge drawn as a dozen short
zig-zags, a hip that bulges into the next face. On the map that is the
"ragged" roof -- lines that do not follow the ridges -- and panels racked
against a wobbly edge lose a column. About 30% of Queenstown's roofs had at
least one such face (tools/layout_health.py, 18 Oct 2026).

The faces are simplified together, as a COVERAGE (shapely.coverage_simplify),
so two neighbours keep one shared boundary: no gaps, no overlaps. The
building's outer edge is not touched (it is the surveyed outline, already
straight), and nor are faces drawn from Josh's markup (his lines are the
answer). All or nothing: if the faces are not a clean coverage, or any face
would change by more than STRAIGHTEN_MAX_AREA_CHANGE, the roof keeps the
faces it had. The planes are unchanged -- only where one face stops and the
next begins moves, by at most STRAIGHTEN_M.

SOLAR_STRAIGHTEN_FACES=0 turns it off (for A/B runs: tools/layout_ab.py).
"""

import os

STRAIGHTEN_M = float(os.environ.get("SOLAR_STRAIGHTEN_M", "0.5"))
STRAIGHTEN_MAX_AREA_CHANGE = 0.15


def straighten_faces(facets, *_, **__):
    if os.environ.get("SOLAR_STRAIGHTEN_FACES", "1") == "0" or len(facets) < 2:
        return facets
    if any(f.get("from_labels") for f in facets):
        return facets
    import shapely
    polys = [f["geometry"] for f in facets]
    if any(p is None or p.geom_type != "Polygon" or not p.is_valid for p in polys):
        return facets
    try:
        if not shapely.coverage_is_valid(polys, gap_width=0.0):
            return facets
        simple = shapely.coverage_simplify(polys, STRAIGHTEN_M, simplify_boundary=False)
    except Exception:
        return facets
    out = []
    for f, p, q in zip(facets, polys, simple):
        if q is None or q.is_empty or q.geom_type != "Polygon" or not q.is_valid:
            return facets
        if abs(q.area - p.area) > STRAIGHTEN_MAX_AREA_CHANGE * p.area:
            return facets
        g = dict(f, geometry=q)
        if "area_m2" in g:
            g["area_m2"] = q.area
        out.append(g)
    return out
