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

SEAMS WHERE THE PLANES MEET (seams_to_plane_lines). Two pitched faces meet
where their planes cross: at a ridge or hip the roof is the lower of the two
planes, at a valley the higher. A seam drawn elsewhere is "lines not
following the ridges" (11A Camp St, 1 Ballarat St), and puts panels astride
the real ridge. For each pitched pair whose shared seam is off the planes'
crossing by more than SEAM_MIN_OFF_M, the two faces are re-cut along the
crossing -- only within a band around the seam (SEAM_BAND_M at most: the
crossing line runs on for ever, and only the stretch near the drawn seam is
vouched for; src/plane_seams.py learned that). Kept only if both faces stay
whole and the seam ends up nearer the crossing. SOLAR_SEAM_PLANES=0 off.

FIRST, A CLEAN TILING (as_coverage). Neither step can work on faces that do
not tile: built by subtracting one face from another and refitting, the
faces overlap by a few cm2 here and leave hairline gaps there, so every roof
failed the coverage check and both steps silently stood aside (the first
A/B run, 19 Oct: identical to the decimal). The faces are rebuilt from one
noded set of edges: each piece goes to the face that covers it (the smaller,
where two do -- a face inside another is that face's cut-out), and a hairline gap (narrower than GAP_FILL_M) goes to the
neighbour it shares most edge with. Real gaps -- a cut-out, a part of the
outline no face claimed -- stay gaps.
"""

import os

STRAIGHTEN_M = float(os.environ.get("SOLAR_STRAIGHTEN_M", "1.0"))
STRAIGHTEN_MAX_AREA_CHANGE = 0.15
GAP_FILL_M = 0.10
SNAP_GRID_M = 0.01
REPAIR_MAX_AREA_CHANGE = 0.03   # tidying hairlines, not deciding who owns a real overlap


REFUSED = {}   # why as_coverage gave up, counted per process (tools/layout_ab.py diagnostics)


def _refuse(why):
    REFUSED[why] = REFUSED.get(why, 0) + 1
    return None


NESTED_SHARE = 0.9


def _nested(inner, outer):
    return inner.intersection(outer).area >= NESTED_SHARE * inner.area


def as_coverage(polys):
    """The same faces as a clean coverage, or None if that cannot be made."""
    import shapely
    from shapely.ops import polygonize, unary_union
    polys = [shapely.set_precision(p, SNAP_GRID_M) for p in polys]
    if any(p.is_empty for p in polys):
        return _refuse("empty after snapping")
    # the faces' outer edge, hairline notches closed, so a gap that runs out
    # to the roof edge is a closed piece too
    closed = unary_union(polys).buffer(GAP_FILL_M, join_style="mitre").buffer(-GAP_FILL_M, join_style="mitre")
    cells = list(polygonize(unary_union([p.boundary for p in polys] + [closed.boundary])))
    parts = [[] for _ in polys]
    ceded = [0.0 for _ in polys]     # area each face gave to a smaller face NESTED in it (a cut-out)
    gaps = []
    for c in cells:
        if c.area <= 0:
            continue
        pt = c.representative_point()
        hits = [i for i, p in enumerate(polys) if p.covers(pt)]
        if hits:
            # where two faces claim a piece, the SMALLER keeps it: a face
            # lying inside another (a lower level, a dormer) is a cut-out
            # the bigger face should have lost (36 of 36 refusals on an
            # Arrowtown sample, 19 Oct, went the other way)
            win = min(hits, key=lambda i: polys[i].area)
            parts[win].append(c)
            for i in hits:
                if i != win and _nested(polys[win], polys[i]):
                    ceded[i] += c.area     # only a NESTED face's area is a cut-out
        elif c.buffer(-GAP_FILL_M / 2).is_empty:
            gaps.append(c)
    for c in gaps:
        best, edge = None, 0.0
        for i, ps in enumerate(parts):
            if not ps:
                continue
            e = c.boundary.intersection(unary_union(ps).boundary).length
            if e > edge:
                best, edge = i, e
        if best is not None:
            parts[best].append(c)
    out = []
    for p, ps, lost in zip(polys, parts, ceded):
        if not ps:
            return _refuse("a face lost every piece")
        u = unary_union(ps)
        # NEVER keep the bigger piece and drop the rest. A small face across
        # a narrow neck split 12/16 Kent Street's roof in two and the 230 m2
        # right wing vanished (v51 build, 20 Oct); 7 Panorama Terrace lost
        # its middle section the same way.
        if u.geom_type != "Polygon":
            return _refuse("a face would split in two")
        # a nested face's area may go (it is a cut-out); anything else must be a hairline
        if abs(u.area - (p.area - lost)) > REPAIR_MAX_AREA_CHANGE * p.area:
            return _refuse("area changed more than a hairline")
        out.append(u)
    try:
        if not shapely.coverage_is_valid(out, gap_width=0.0):
            return _refuse("still not a coverage")
    except Exception:
        return _refuse("coverage check raised")
    return out


def straighten_faces(facets, *_, **__):
    if os.environ.get("SOLAR_STRAIGHTEN_FACES", "1") == "0" or len(facets) < 2:
        return facets
    if any(f.get("from_labels") for f in facets):
        return facets
    import shapely
    polys = [f["geometry"] for f in facets]
    if any(p is None or p.geom_type != "Polygon" or not p.is_valid for p in polys):
        return facets
    cov = as_coverage(polys)
    if cov is None:
        return facets
    try:
        simple = shapely.coverage_simplify(cov, STRAIGHTEN_M, simplify_boundary=False)
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
    cov = as_coverage([f["geometry"] for f in facets])
    if cov is None:
        return facets
    faces = [dict(f, geometry=g) for f, g in zip(facets, cov)]
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


SEAM_MIN_SLOPE_DEG = 10.0
SEAM_MIN_SHARED_M = 1.5
SEAM_MIN_OFF_M = 0.15
SEAM_BAND_M = 1.5
SEAM_MIN_GRAD = 0.05          # planes this near parallel have no crossing worth trusting
SEAM_MAX_AREA_CHANGE = 0.25


def _seam_offset(f, g, seam):
    """Median distance (m, in plan) from the seam to where the planes cross."""
    import math
    A, B = f["plane_a"] - g["plane_a"], f["plane_b"] - g["plane_b"]
    grad = math.hypot(A, B)
    pts = [seam.interpolate(t / 10, normalized=True) for t in range(11)]
    d = sorted(abs(_z(f, p.x, p.y) - _z(g, p.x, p.y)) / grad for p in pts)
    return d[len(d) // 2]


def _half(f, g, bounds, positive):
    """Where f's plane is above (positive) or below g's, as a big polygon."""
    from src.plane_seams import _side
    return _side(f, g, bounds, positive)


def seams_to_plane_lines(facets, *_, **__):
    import math
    if os.environ.get("SOLAR_SEAM_PLANES", "1") == "0" or len(facets) < 2:
        return facets
    if any(f.get("from_labels") for f in facets):
        return facets
    cov = as_coverage([f["geometry"] for f in facets])
    if cov is None:
        return facets
    faces = [dict(f, geometry=g) for f, g in zip(facets, cov)]
    moved = False
    for i in range(len(faces)):
        for j in range(i + 1, len(faces)):
            f, g = faces[i], faces[j]
            if f.get("plane_a") is None or g.get("plane_a") is None:
                continue
            if min(f.get("slope_deg", 0.0), g.get("slope_deg", 0.0)) < SEAM_MIN_SLOPE_DEG:
                continue
            if math.hypot(f["plane_a"] - g["plane_a"], f["plane_b"] - g["plane_b"]) < SEAM_MIN_GRAD:
                continue
            Fi, Fj = f["geometry"], g["geometry"]
            seam = Fi.boundary.intersection(Fj.buffer(0.02))
            if seam.is_empty or seam.length < SEAM_MIN_SHARED_M:
                continue
            off = _seam_offset(f, g, seam)
            if off < SEAM_MIN_OFF_M:
                continue
            both = Fi.union(Fj)
            band = seam.buffer(min(SEAM_BAND_M, 2 * off + 0.3)).intersection(both)
            above = _half(f, g, both.bounds, True)
            below = _half(f, g, both.bounds, False)
            if above is None or below is None:
                continue
            # ridge or hip: f is the roof where it is the LOWER plane; valley: the higher
            ridge = Fi.intersection(band).intersection(below).area >= Fi.intersection(band).intersection(above).area
            side_f, side_g = (below, above) if ridge else (above, below)
            ni = Fi.difference(band).union(band.intersection(side_f))
            nj = Fj.difference(band).union(band.intersection(side_g))
            if ni.geom_type != "Polygon" or nj.geom_type != "Polygon" or not (ni.is_valid and nj.is_valid):
                continue
            if abs(ni.area - Fi.area) > SEAM_MAX_AREA_CHANGE * Fi.area or abs(nj.area - Fj.area) > SEAM_MAX_AREA_CHANGE * Fj.area:
                continue
            new_seam = ni.boundary.intersection(nj.buffer(0.02))
            if new_seam.is_empty or new_seam.length < 0.5 * seam.length:
                continue
            if _seam_offset(f, g, new_seam) >= off:
                continue
            faces[i] = dict(f, geometry=ni)
            faces[j] = dict(g, geometry=nj)
            moved = True
    if not moved:
        return facets
    cov = as_coverage([f["geometry"] for f in faces])
    if cov is None:
        return facets
    out = []
    for f, g in zip(faces, cov):
        h = dict(f, geometry=g)
        if "area_m2" in h:
            h["area_m2"] = g.area
        out.append(h)
    return out
