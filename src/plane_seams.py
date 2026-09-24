"""Give back roof a face has taken from its neighbour across a hip or valley.

WHAT WAS WRONG. Two pitched faces that meet at an angle -- a hip, or the
valley where a wing joins -- meet along the line where their planes
intersect. Nothing put them there: the partition cuts straight across the
footprint in the building's frame, and ridge_snap only moves ridges between
faces that face AWAY from each other. So at a valley one face ran past the
valley into its neighbour's slope.

It costs twice. The seams are drawn in the wrong place (1 Ballarat Street:
"lines not following the ridges and valleys"), and the overshoot is read as
an OBJECT: the neighbour's slope stands well off the overreaching face's
plane, so obstruction detection marks it, over clear roof. Reproduced on the
synthetic L roof (tests/synthetic, #990000004): two plain gables, nothing on
them, four obstructions totalling 25 m2.

WHAT THIS DOES, from the evidence up. A return inside a face that sits well
off that face's plane (> BAD_M) but on a NEARBY face's plane (< GOOD_M) is
that neighbour's roof. Such returns are grouped by (face, neighbour); a
group big enough to matter is handed over, cut straight along the two
planes' intersection line and bounded by the group's own extent. Passes
repeat, so a chain unwinds: once the south face gives the east slope back,
the east slope reaches the corner the north face had taken. Whole-line and
whole-roof versions were tried first and failed (BACKLOG): the intersection
line runs on for ever, and only the stretch the returns vouch for is a seam.

Each transfer must improve the fit of the returns it moves, the receiving
face must stay one polygon, and the roof's outline never changes. Planes are
not refitted. SOLAR_PLANE_SEAMS=0 turns it off; drawn roofs are not touched.
"""

import os

import numpy as np
from shapely.geometry import MultiPoint, Polygon
from shapely.ops import unary_union

MIN_SLOPE_DEG = 10.0     # a flat face's plane intersection is an eave, not a seam
BAD_M = 0.30             # a return this far off its own face's plane is suspect...
GOOD_M = 0.15            # ...and belongs to a neighbour whose plane it sits on
NEAR_M = 3.0             # the neighbour must be this close
CELL_M = 1.0             # returns are pooled on this grid before grouping (8/m2 surveys)
MIN_GROUP_M2 = 1.0       # a smaller patch is noise
MIN_POINTS = 8
SLIVER_M2 = 1.0          # a leftover piece of a face smaller than this merges away
PASSES = 3
STATS = {"roofs": 0, "moved": 0}


def _res(f, pts):
    return pts[:, 2] - (f["plane_a"] * pts[:, 0] + f["plane_b"] * pts[:, 1] + f["plane_c"])


def _side(fi, fj, bounds, positive):
    """The half-plane where fi's plane minus fj's plane has the given sign."""
    A = fi["plane_a"] - fj["plane_a"]
    B = fi["plane_b"] - fj["plane_b"]
    C = fi["plane_c"] - fj["plane_c"]
    norm = np.hypot(A, B)
    if norm < 1e-4:
        return None
    minx, miny, maxx, maxy = bounds
    span = 4.0 * max(maxx - minx, maxy - miny, 1.0)
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    n = np.array([A, B]) / norm
    p0 = np.array([cx, cy]) - n * ((A * cx + B * cy + C) / norm)
    t = np.array([-n[1], n[0]])
    a, b = p0 - t * span, p0 + t * span
    s = 1.0 if positive else -1.0
    return Polygon([tuple(a), tuple(b), tuple(b + s * n * span), tuple(a + s * n * span)])


def _polys(g):
    return [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and not p.is_empty]


def _one_pass(facets, pts):
    import shapely
    owner = np.full(len(pts), -1)
    for k, f in enumerate(facets):
        m = (owner < 0) & shapely.contains_xy(f["geometry"], pts[:, 0], pts[:, 1])
        owner[m] = k
    R = np.stack([np.abs(_res(f, pts)) for f in facets], axis=1)
    moved = False
    for i, fi in enumerate(facets):
        mine = owner == i
        if mine.sum() < MIN_POINTS:
            continue
        bad = mine & (R[:, i] > BAD_M)
        if bad.sum() < MIN_POINTS:
            continue
        for j, fj in enumerate(facets):
            if j == i or fj.get("slope_deg", 0) < MIN_SLOPE_DEG or fi.get("slope_deg", 0) < MIN_SLOPE_DEG:
                continue
            if fi["geometry"].distance(fj["geometry"]) > NEAR_M:
                continue
            take = bad & (R[:, j] < GOOD_M)
            if take.sum() < MIN_POINTS:
                continue
            # pool on a grid, drop cells with too little support
            cells = {}
            for x, y in pts[take, :2]:
                cells.setdefault((int(np.floor(x / CELL_M)), int(np.floor(y / CELL_M))), 0)
                cells[(int(np.floor(x / CELL_M)), int(np.floor(y / CELL_M)))] += 1
            keep = [(cx, cy) for (cx, cy), n in cells.items() if n >= 2]
            if len(keep) * CELL_M ** 2 < MIN_GROUP_M2:
                continue
            corners = [((cx + dx) * CELL_M, (cy + dy) * CELL_M) for cx, cy in keep
                       for dx in (0, 1) for dy in (0, 1)]
            # Near the seam the two planes agree to within BAD_M, so the misfit
            # starts a little way in. Reach back to where the faces meet, or
            # the patch floats free of the face it belongs to.
            from shapely.ops import nearest_points
            patch = MultiPoint(corners).convex_hull
            shared = fi["geometry"].buffer(0.2).intersection(fj["geometry"].buffer(0.2))
            reach = [shared] if not shared.is_empty else \
                [nearest_points(patch, fj["geometry"])[1].buffer(0.2)]
            extent = unary_union([patch] + reach).convex_hull
            # j's side of the seam: where j's plane is the one the returns sit on
            probe = pts[take]
            pos = _side(fj, fi, fi["geometry"].bounds, positive=True)
            neg = _side(fj, fi, fi["geometry"].bounds, positive=False)
            if pos is None:
                continue
            inpos = shapely.contains_xy(pos, probe[:, 0], probe[:, 1]).mean()
            half = pos if inpos >= 0.5 else neg
            T = fi["geometry"].intersection(half).intersection(extent)
            T = unary_union([p for p in _polys(T) if p.area >= 0.25])
            if T.is_empty or T.area < MIN_GROUP_M2:
                continue
            nj = unary_union([fj["geometry"].buffer(0.05, join_style=2), T.buffer(0.05, join_style=2)]
                             ).buffer(-0.05, join_style=2)
            if nj.geom_type != "Polygon":
                continue
            ni = fi["geometry"].difference(T)
            # the moved returns must fit better where they go
            inT = shapely.contains_xy(T, pts[:, 0], pts[:, 1]) & mine
            if inT.sum() < MIN_POINTS or not R[inT, j].mean() < 0.5 * R[inT, i].mean():
                continue
            fi["geometry"], fj["geometry"] = ni, nj
            STATS["moved"] += 1
            moved = True
            return moved     # geometry changed: recompute ownership
    return moved


def snap_seams_to_plane_intersections(facets, pc_source, dsm=None):
    """Return facets with neighbours' roof handed back across hips and valleys."""
    if os.environ.get("SOLAR_PLANE_SEAMS", "1") == "0":
        return facets
    if not facets or len(facets) < 2 or any(f.get("from_labels") for f in facets):
        return facets
    need = ("plane_a", "plane_b", "plane_c")
    if any(k not in f for f in facets for k in need):
        return facets
    STATS["roofs"] += 1
    from src.roof_segmentation import _facet_points, _dsm_points_in
    roof = unary_union([f["geometry"] for f in facets])
    try:
        pts = _facet_points(pc_source, roof) if pc_source is not None else np.empty((0, 3))
        if len(pts) < 4 * MIN_POINTS:
            pts = _dsm_points_in(roof, dsm)
    except Exception:
        return facets
    if len(pts) < 4 * MIN_POINTS:
        return facets
    work = [dict(f) for f in facets]
    try:
        for _ in range(PASSES * len(work)):
            if not _one_pass(work, pts):
                break
    except Exception:
        return facets
    if all(w["geometry"].equals(f["geometry"]) for w, f in zip(work, facets)):
        return facets
    # a face may now be in pieces (a wing's slope either side of the main
    # ridge it runs into); each piece is a face, slivers merge into the
    # neighbour they touch most
    out = []
    for w in work:
        for p in sorted(_polys(w["geometry"]), key=lambda p: -p.area):
            out.append(dict(w, geometry=p))
    big = [o for o in out if o["geometry"].area >= SLIVER_M2]
    for o in out:
        if o["geometry"].area >= SLIVER_M2:
            continue
        host = max(big, key=lambda b: b["geometry"].buffer(0.05).intersection(o["geometry"].buffer(0.05)).area,
                   default=None)
        if host is not None:
            g = unary_union([host["geometry"], o["geometry"].buffer(0.02)]).buffer(-0.02)
            if g.geom_type == "Polygon":
                host["geometry"] = g
    for o in big:
        if "area_m2" in o:
            o["area_m2"] = o["geometry"].area
    return big
