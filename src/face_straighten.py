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
an edge of at least MERGE_MIN_SHARED_M, their surfaces point within
MERGE_NORMAL_DEG of each other, and each plane passes within
MERGE_MAX_STEP_M of the other face's centre and of every point of the
shared edge -- a ridge, a valley or a step between storeys does not. The larger
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
whole, the seam ends up nearer the crossing, AND the strip that changes hands
has the LiDAR on its side: its returns must sit nearer the receiving face's
plane than the giving face's. Without that last test a seam between two
poorly fitted planes moved the wrong way -- 4721266 (Arrowtown, v51 build)
lost seven panels and grew an obstruction where its face now ran up the
neighbour's slope. SOLAR_SEAM_PLANES=0 off.

WINGS MEET AT VALLEYS, NOT STRAIGHT CUTS. Faces read offline per wing
(src/face_candidates.py rect_parts) are glued along a straight cut at the
reflex corner; a real L or T roof meets there along diagonal valleys and
hips, metres from the cut -- too far for the band above. So each pitched pair
is also re-cut across its WHOLE area along the planes' crossing, both ways
round (ridge: lower plane wins; valley: higher), and the cut is kept only
when it explains at least JUNCTION_MIN_GAIN more of the pair's LiDAR returns
than the faces it replaces. Evidence decides; the geometry only proposes.

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
MERGE_NORMAL_DEG = 2.5
MERGE_MAX_STEP_M = 0.15


def _z(f, x, y):
    return f["plane_a"] * x + f["plane_b"] * y + f["plane_c"]


def _normal_angle(f, g):
    import math
    nf = (-f["plane_a"], -f["plane_b"], 1.0)
    ng = (-g["plane_a"], -g["plane_b"], 1.0)
    dot = sum(a * b for a, b in zip(nf, ng))
    c = dot / (math.sqrt(sum(a * a for a in nf)) * math.sqrt(sum(b * b for b in ng)))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _same_plane(f, g):
    """One plane: the surfaces point the same way, and each plane passes
    through the other face. Agreeing along the shared edge is NOT enough --
    two halves of a gable always meet at their ridge. The first version asked
    only that, and on low-pitched commercial roofs (2-5 degrees each way) it
    merged the halves into one face; the other half then read as one big
    obstruction (4749386 in Frankton: 750 -> 454 panels; v51 gate, 20 Oct)."""
    if f.get("plane_a") is None or g.get("plane_a") is None:
        return False
    if _normal_angle(f, g) > MERGE_NORMAL_DEG:
        return False
    shared = f["geometry"].boundary.intersection(g["geometry"].buffer(0.05))
    if shared.is_empty or shared.length < MERGE_MIN_SHARED_M:
        return False
    for a, b in ((f, g), (g, f)):
        c = b["geometry"].representative_point()
        if abs(_z(a, c.x, c.y) - _z(b, c.x, c.y)) > MERGE_MAX_STEP_M:
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


SEAM_MIN_POINTS = 8


def _returns_agree(strip, giver, taker, pc_source):
    """True when the returns in `strip` sit nearer taker's plane than giver's
    (or there are too few to say, in which case the geometry alone decides
    only when no point cloud was given at all)."""
    if pc_source is None:
        return True
    if strip.is_empty or strip.area < 0.05:
        return True
    import numpy as np
    from src.roof_segmentation import _facet_points
    try:
        pts = _facet_points(pc_source, strip)
    except Exception:
        return False
    if len(pts) < SEAM_MIN_POINTS:
        return False
    rg = np.abs(pts[:, 2] - (giver["plane_a"] * pts[:, 0] + giver["plane_b"] * pts[:, 1] + giver["plane_c"]))
    rt = np.abs(pts[:, 2] - (taker["plane_a"] * pts[:, 0] + taker["plane_b"] * pts[:, 1] + taker["plane_c"]))
    return float(np.median(rt)) < float(np.median(rg))


JUNCTION_MIN_GAIN = 0.05
JUNCTION_INLIER_M = 0.20
JUNCTION_MIN_POINTS = 30


def _pair_share(pi, fi, pj, fj, pts):
    """Share of the returns in pi+pj within JUNCTION_INLIER_M of their face's plane."""
    import numpy as np
    import shapely
    ok = n = 0
    for poly, f in ((pi, fi), (pj, fj)):
        on = shapely.contains_xy(poly, pts[:, 0], pts[:, 1])
        r = pts[on, 2] - (f["plane_a"] * pts[on, 0] + f["plane_b"] * pts[on, 1] + f["plane_c"])
        ok += int((np.abs(r) <= JUNCTION_INLIER_M).sum())
        n += int(on.sum())
    return ok / n if n else 0.0


SLIVER_DROP_M2 = 0.05


def _whole(poly):
    """The polygon, rid of floating-point slivers a cut leaves -- or None if
    the cut genuinely made two pieces (never keep one and lose the other)."""
    if poly.geom_type == "Polygon":
        return poly if poly.is_valid else None
    parts = sorted((q for q in getattr(poly, "geoms", []) if q.geom_type == "Polygon"),
                   key=lambda q: -q.area)
    if not parts or sum(q.area for q in parts[1:]) > SLIVER_DROP_M2:
        return None
    return parts[0] if parts[0].is_valid else None


def _junction_region(seam, f, g, both):
    """The area between the drawn seam and the stretch of the planes' crossing
    that starts at it -- the crossing line runs on for ever, and beyond the
    junction it is meaningless (an L's other wing face lies across it)."""
    import math
    from shapely.geometry import LineString, MultiPoint
    A = f["plane_a"] - g["plane_a"]
    B = f["plane_b"] - g["plane_b"]
    C = f["plane_c"] - g["plane_c"]
    n = math.hypot(A, B)
    if n < 1e-6:
        return None
    minx, miny, maxx, maxy = both.bounds
    span = 4 * max(maxx - minx, maxy - miny, 1.0)
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    d = (A * cx + B * cy + C) / n
    px, py = cx - A / n * d, cy - B / n * d
    tx, ty = -B / n, A / n
    line = LineString([(px - tx * span, py - ty * span), (px + tx * span, py + ty * span)])
    inside = line.intersection(both)
    parts = [q for q in getattr(inside, "geoms", [inside]) if q.geom_type == "LineString" and q.length > 0.5]
    if not parts:
        return None
    comp = min(parts, key=lambda q: q.distance(seam))
    if comp.distance(seam) > 0.5:
        return None
    pts = list(comp.coords) + [c for g2 in getattr(seam, "geoms", [seam]) for c in g2.coords]
    region = MultiPoint(pts).convex_hull.intersection(both)
    return region if region.area > 0.5 else None


def _junction_recut(Fi, f, Fj, g, both, above, below, pc_source):
    """(ni, nj) re-cut between the seam and the crossing if the returns clearly prefer it, else None."""
    import shapely
    from src.roof_segmentation import _facet_points
    try:
        pts = _facet_points(pc_source, both)
    except Exception:
        return None
    if len(pts) < JUNCTION_MIN_POINTS:
        return None
    seam = Fi.boundary.intersection(Fj.buffer(0.02))
    region = _junction_region(seam, f, g, both)
    if region is None:
        return None
    current = _pair_share(Fi, f, Fj, g, pts)
    best = None
    for side_f, side_g in ((below, above), (above, below)):
        wi = _whole(Fi.difference(region).union(region.intersection(side_f)))
        wj = _whole(Fj.difference(region).union(region.intersection(side_g)))
        if wi is None or wj is None:
            continue
        if min(wi.area, wj.area) < 0.25 * min(Fi.area, Fj.area):
            continue
        share = _pair_share(wi, f, wj, g, pts)
        if best is None or share > best[0]:
            best = (share, wi, wj)
    if best is None or best[0] < current + JUNCTION_MIN_GAIN:
        return None
    return best[1], best[2]


def seams_to_plane_lines(facets, pc_source=None, *_, **__):
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
            if pc_source is not None and os.environ.get("SOLAR_JUNCTIONS", "1") != "0":
                rc = _junction_recut(Fi, f, Fj, g, both, above, below, pc_source)
                if rc is not None:
                    faces[i] = dict(f, geometry=rc[0])
                    faces[j] = dict(g, geometry=rc[1])
                    moved = True
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
            # the LiDAR must agree with each strip that changes hands
            if not (_returns_agree(ni.difference(Fi), g, f, pc_source)
                    and _returns_agree(nj.difference(Fj), f, g, pc_source)):
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
