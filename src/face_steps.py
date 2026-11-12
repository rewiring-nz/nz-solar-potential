"""Split a face whose returns lie on two planes: a step between roof levels, or a fold.

    SOLAR_STEP_SPLIT=1   splits at height steps only
    SOLAR_STEP_SPLIT=2   also at folds (a ridge or valley inside one face)
    unset or 0           off

WHY. On the drawn roofs (12 Nov, 155 scored, BACKLOG "Faces messier than real
roofs") production is under-split on stepped roofs: 22 of the 76 roofs with
four or more drawn steps are built with under two-thirds of the drawn faces,
and face IoU there is 0.53. Of the drawn lines, steps are a third by length
(5,275 m) and production finds 47% of them within 0.75 m. A step is a height
jump the LiDAR measures directly, so a face whose returns sit on two planes a
step apart is two faces. split_lower_levels (src/roof_levels.py) already cuts
out a LOWER level that is wide, flat-ish and roof-coloured; a level above the
face's plane, a narrow level, or one that fails the colour test stays merged.

WHAT THIS DOES. For each face from the readings (drawn faces are never
touched): fit one robust plane; if enough returns sit well off it, fit a
second plane to those, give each return to the nearer plane, and label a
GRID_M grid over the face by the majority of the returns around each cell.
The face is split along the label boundary when
  - each piece is big enough (MIN_PIECE_M2) and wide enough (MIN_WIDTH_M),
  - one piece's returns lie on its plane (MIN_INLIER) and the other's at
    least MIN_PIECE_INLIER (it may still hold two levels; the next pass
    splits it),
  - the two planes explain at least GAIN more of the face's returns than one,
  - and (steps only) the planes differ by at least STEP_MIN_M along the
    boundary; a fold is where they meet (difference near zero).
Pieces are tried again, to MAX_DEPTH, so a three-level face splits twice.
Edges are left to the later seam stages (fit_seams, straighten_faces).
"""

import os

import numpy as np

GRID_M = 0.25
OFF_M = 0.35          # a return this far off the first plane is evidence of a second
MIN_OFF_SHARE = 0.12  # share of a face's returns that must be off before a split is tried
INLIER_M = 0.15
MIN_INLIER = 0.60
MIN_PIECE_INLIER = 0.40  # the other piece may still span two levels
GAIN = 0.15           # two planes must explain this much more of the face than one
MIN_POINTS = 60
MIN_PIECE_POINTS = 20
MIN_PIECE_M2 = 4.0
MIN_WIDTH_M = 1.2
STEP_MIN_M = 0.45     # planes this far apart along the boundary make a step
FOLD_MAX_M = 0.25     # ...and this close make a fold
VOTE_R_M = 0.9
MAX_DEPTH = 2
MAX_SLOPE_DEG = 60.0
RANSAC_TRIES = 200
SLIVER_M2 = 1.0
STATS = {"faces": 0, "steps": 0, "folds": 0}
REJECT = {}   # why a face was not split, by gate, for tuning


def _no(why):
    REJECT[why] = REJECT.get(why, 0) + 1
    return None


def mode():
    v = os.environ.get("SOLAR_STEP_SPLIT", "0")
    return int(v) if v in ("1", "2") else 0


def _fit(p):
    from src.roof_partition import _fit_plane_robust
    return np.asarray(_fit_plane_robust(p), float)


def _res(p, pl):
    return p[:, 2] - (pl[0] * p[:, 0] + pl[1] * p[:, 1] + pl[2])


def _inl(p, pl):
    r = _res(p, pl)
    return float((np.abs(r - np.median(r)) < INLIER_M).mean()) if len(p) else 0.0


def _slope(pl):
    return float(np.degrees(np.arctan(np.hypot(pl[0], pl[1]))))


def _width(poly):
    cs = list(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    return min(np.hypot(cs[k + 1][0] - cs[k][0], cs[k + 1][1] - cs[k][1]) for k in range(2))


def _polys(g):
    if g.is_empty:
        return []
    return [g] if g.geom_type == "Polygon" else [q for q in getattr(g, "geoms", []) if q.geom_type == "Polygon"]


def _ransac(pts, tries=RANSAC_TRIES):
    """The plane through three returns that has the most returns within INLIER_M,
    refitted to them. Seeded, so a build is repeatable."""
    rng = np.random.default_rng(len(pts))
    best, best_n = None, -1
    for _ in range(tries):
        i = rng.choice(len(pts), 3, replace=False)
        a = np.c_[pts[i, 0], pts[i, 1], np.ones(3)]
        if abs(np.linalg.det(a)) < 1e-6:
            continue
        pl = np.linalg.solve(a, pts[i, 2])
        if _slope(pl) > MAX_SLOPE_DEG:
            continue
        n = int((np.abs(_res(pts, pl)) < INLIER_M).sum())
        if n > best_n:
            best, best_n = pl, n
    if best is None:
        return None
    return _fit(pts[np.abs(_res(pts, best)) < INLIER_M * 2])


def _two_planes(pts):
    """(plane1, plane2, label per return: 0, 1 or -1 for neither) or None."""
    p1 = _ransac(pts)
    if p1 is None:
        return None
    off = np.abs(_res(pts, p1)) > OFF_M
    if off.mean() < MIN_OFF_SHARE or off.sum() < MIN_PIECE_POINTS:
        return _no("few off plane")
    p2 = _ransac(pts[off])
    if p2 is None:
        return None
    r1, r2 = np.abs(_res(pts, p1)), np.abs(_res(pts, p2))
    lab = np.where(r1 <= r2, 0, 1)
    lab[np.minimum(r1, r2) > OFF_M] = -1
    if (lab == 0).sum() < MIN_PIECE_POINTS or (lab == 1).sum() < MIN_PIECE_POINTS:
        return None
    return p1, p2, lab


def _cut(poly, pts, lab):
    """The face cut into two pieces by the return labels, or None."""
    import shapely
    from scipy.spatial import cKDTree
    from rasterio import features as rf
    from rasterio.transform import from_origin
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    ok = lab >= 0
    tree = cKDTree(pts[ok, :2]); lv = lab[ok]
    minx, miny, maxx, maxy = poly.bounds
    xs = np.arange(minx, maxx, GRID_M) + GRID_M / 2
    ys = np.arange(miny, maxy, GRID_M) + GRID_M / 2
    gx, gy = np.meshgrid(xs, ys)
    fx, fy = gx.ravel(), gy.ravel()
    inside = shapely.intersects_xy(poly, fx, fy)
    grid = np.full(len(fx), -1, np.int32)
    idx = np.flatnonzero(inside)
    if not len(idx):
        return None
    near = tree.query_ball_point(np.c_[fx[idx], fy[idx]], VOTE_R_M)
    for i, nb in zip(idx, near):
        if nb:
            grid[i] = int(lv[nb].mean() >= 0.5)
        else:
            grid[i] = int(lv[tree.query([fx[i], fy[i]])[1]])
    g = np.flipud(grid.reshape(gx.shape))
    tr = from_origin(minx, ys[-1] + GRID_M / 2, GRID_M, GRID_M)
    parts = {0: [], 1: []}
    for gd, v in rf.shapes(g, mask=g >= 0, transform=tr):
        parts[int(v)].append(Polygon(gd["coordinates"][0], gd["coordinates"][1:]).buffer(0))
    # each label's largest piece; the small islands go to the other label
    big = {}
    for k in (0, 1):
        if not parts[k]:
            return None
        big[k] = max(parts[k], key=lambda q: q.area)
    a = max(_polys(big[0].buffer(0.01).intersection(poly).simplify(0.3).buffer(0)),
            key=lambda q: q.area, default=None)
    if a is None:
        return None
    rest = _polys(poly.difference(a).buffer(0))
    # crumbs along the cut go back to the first piece, so the face stays covered
    crumbs = [q for q in rest if q.area < SLIVER_M2]
    rest = [q.simplify(0.3).buffer(0) for q in rest if q.area >= SLIVER_M2]
    if crumbs:
        a = max(_polys(unary_union([a] + crumbs).buffer(0)), key=lambda q: q.area)
    rest = [max(_polys(q), key=lambda r: r.area) for q in rest if _polys(q)]
    if not rest:
        return None
    return [a] + rest


def _boundary_gap(a, b, p1, p2):
    """Median |plane1 - plane2| along the boundary the two pieces share."""
    s = a.boundary.intersection(b.buffer(0.05))
    if s.is_empty or s.length < 0.5:
        return None
    n = max(int(s.length / 0.25), 4)
    q = [s.interpolate(t, normalized=True) for t in np.linspace(0, 1, n)]
    x = np.array([p.x for p in q]); y = np.array([p.y for p in q])
    return float(np.median(np.abs((p1[0] - p2[0]) * x + (p1[1] - p2[1]) * y + (p1[2] - p2[2]))))


def _split_one(f, pc_source, kinds):
    import shapely
    from src.roof_partition import _slope_aspect
    poly = f["geometry"]
    pts = pc_source.points_in_bbox(*poly.bounds, building_only=True)
    if pts is None or len(pts) < MIN_POINTS:
        return _no("few returns")
    pts = pts[shapely.contains_xy(poly, pts[:, 0], pts[:, 1])]
    if len(pts) < MIN_POINTS:
        return _no("few returns")
    one = _inl(pts, _fit(pts))
    tp = _two_planes(pts)
    if tp is None:
        return _no("one plane")
    p1, p2, lab = tp
    cut = _cut(poly, pts, lab)
    if cut is None:
        return _no("no cut")
    planes, inl, n = [], [], []
    for piece in cut:
        pp = pts[shapely.contains_xy(piece, pts[:, 0], pts[:, 1])]
        if (len(pp) < MIN_PIECE_POINTS or piece.area < MIN_PIECE_M2
                or _width(piece) < MIN_WIDTH_M):
            return _no("small piece")
        # each piece scored on its dominant plane: a piece still holding two
        # levels scores lower here and is split again on the next pass
        pl = _ransac(pp)
        if pl is None or _slope(pl) > MAX_SLOPE_DEG:
            return _no("steep piece")
        planes.append(pl); inl.append(_inl(pp, pl)); n.append(len(pp))
    if min(inl) < MIN_PIECE_INLIER or max(inl) < MIN_INLIER:
        return _no("piece off plane")
    if sum(i * k for i, k in zip(inl, n)) / sum(n) < one + GAIN:
        return _no("small gain")
    gaps = [g for b, pl in zip(cut[1:], planes[1:])
            if (g := _boundary_gap(cut[0], b, planes[0], pl)) is not None]
    if not gaps:
        return _no("no boundary")
    gap = min(gaps)
    kind = "step" if gap >= STEP_MIN_M else ("fold" if gap <= FOLD_MAX_M else None)
    if kind not in kinds:
        return _no("not a step" if gap < STEP_MIN_M else "kind off")
    out = []
    for piece, pl in zip(cut, planes):
        slope, aspect = _slope_aspect(tuple(pl))
        out.append(dict(f, geometry=piece, area_m2=piece.area, plane_a=float(pl[0]),
                        plane_b=float(pl[1]), plane_c=float(pl[2]), slope_deg=slope,
                        aspect_deg=aspect, split_by=kind))
    STATS["steps" if kind == "step" else "folds"] += 1
    return out


def split_steps(facets, pc_source=None):
    """Return facets with every face that spans two planes cut in two."""
    m = mode()
    if not m or not facets or pc_source is None or any(f.get("from_labels") for f in facets):
        return facets
    if any(k not in f for f in facets for k in ("plane_a", "plane_b", "plane_c")):
        return facets
    kinds = {"step"} if m == 1 else {"step", "fold"}
    todo = [(f, 0) for f in facets]
    out, changed = [], False
    while todo:
        f, depth = todo.pop(0)
        res = None
        if depth < MAX_DEPTH and f["geometry"].geom_type == "Polygon":
            try:
                res = _split_one(f, pc_source, kinds)
            except Exception:
                res = None
        if res:
            changed = True
            todo.extend((g, depth + 1) for g in res)
        else:
            out.append(f)
    if changed:
        STATS["faces"] += 1
        return out
    return facets
