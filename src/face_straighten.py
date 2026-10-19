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
next begins moves.

STRAIGHTEN_M IS NOT A DISTANCE. coverage_simplify is Visvalingam-Whyatt: it
drops a vertex when the triangle it makes with its neighbours is smaller
than STRAIGHTEN_M squared. At 0.5 a zig-zag 0.4 m deep every 0.8 m survived
untouched (triangle 0.32 m2 > 0.25); at 1.0 it goes, as does a 0.5 m bulge
over 4 m, while a real corner -- a hip meeting a ridge, metres each way --
stays.

SOLAR_STRAIGHTEN_FACES=0 turns it off (for A/B runs: tools/layout_ab.py).

ONE PLANE, ONE FACE (merge_coplanar_faces, run just before). Most "ragged"
faces were not wobbly but bent -- 85% of them, Arrowtown and the CBD,
19 Oct -- and many of those were one plane cut in two: a flat or low roof
whose seams wander across it, each piece notched where the other bites in.
segment_building_best merges such pairs, but the stages after it re-split
roofs, so the pass is repeated last. Two neighbours merge when they share
an edge of at least MERGE_MIN_SHARED_M, match on slope and (unless both
are near flat) direction, and their planes agree to MERGE_MAX_STEP_M all
along that edge -- a valley or a step between storeys does not. The larger
face's plane is kept; the planes differ by less than the step anyway.
SOLAR_MERGE_COPLANAR=0 turns it off.
"""

import os

STRAIGHTEN_M = float(os.environ.get("SOLAR_STRAIGHTEN_M", "1.0"))
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


MERGE_MIN_SHARED_M = 1.0
MERGE_SLOPE_DEG = 3.0
MERGE_ASPECT_DEG = 15.0
MERGE_FLAT_DEG = 5.0
MERGE_MAX_STEP_M = 0.15


def _z(f, x, y):
    return f["plane_a"] * x + f["plane_b"] * y + f["plane_c"]


def _same_plane(f, g):
    if f.get("plane_a") is None or g.get("plane_a") is None:
        return False
    sf, sg = f.get("slope_deg", 90.0), g.get("slope_deg", 90.0)
    if abs(sf - sg) > MERGE_SLOPE_DEG:
        return False
    if not (sf < MERGE_FLAT_DEG and sg < MERGE_FLAT_DEG):
        d = abs(f.get("aspect_deg", 0.0) - g.get("aspect_deg", 0.0)) % 360
        if min(d, 360 - d) > MERGE_ASPECT_DEG:
            return False
    shared = f["geometry"].boundary.intersection(g["geometry"].buffer(0.05))
    if shared.is_empty or shared.length < MERGE_MIN_SHARED_M:
        return False
    n = max(2, int(shared.length / 0.5))
    for k in range(n + 1):
        p = shared.interpolate(k / n, normalized=True)
        if abs(_z(f, p.x, p.y) - _z(g, p.x, p.y)) > MERGE_MAX_STEP_M:
            return False
    return True


def merge_coplanar_faces(facets, *_, **__):
    if os.environ.get("SOLAR_MERGE_COPLANAR", "1") == "0" or len(facets) < 2:
        return facets
    if any(f.get("from_labels") for f in facets):
        return facets
    from shapely.ops import unary_union
    faces = list(facets)
    merged = True
    while merged:
        merged = False
        faces.sort(key=lambda f: -f["geometry"].area)
        for i in range(len(faces)):
            for j in range(i + 1, len(faces)):
                f, g = faces[i], faces[j]
                if not f["geometry"].buffer(0.05).intersects(g["geometry"]) or not _same_plane(f, g):
                    continue
                u = unary_union([f["geometry"], g["geometry"]])
                if u.geom_type != "Polygon" or not u.is_valid:
                    continue
                m = dict(f, geometry=u)
                if "area_m2" in m:
                    m["area_m2"] = u.area
                if "point_count" in f and "point_count" in g:
                    m["point_count"] = f["point_count"] + g["point_count"]
                faces = [x for k, x in enumerate(faces) if k not in (i, j)] + [m]
                merged = True
                break
            if merged:
                break
    return faces
