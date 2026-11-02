"""A face the returns show is two planes is cut where the two planes cross.

WHY. On the 131 drawn roofs built blind, roofs of 5+ drawn faces come out
with 0.7-0.9 of the faces drawn and miss about 40% of the drawn lines (line
recall 0.58-0.61): the reading merged faces across a hip, valley or ridge the
point cloud resolves. Built with the drawn faces and lines instead, panel
area IoU against the drawn layout rises from 0.52 to 0.76 (the error budget,
BACKLOG "Roof lines").

THE RULE. For a face of at least FOLD_MIN_FACE_M2, its returns (inside the
face, FOLD_EDGE_M in from its edge) are fitted by two planes with RANSAC: the
best plane, then the best plane of what it leaves. The face is cut along the
line where those two planes cross only when
  * one plane misfits it (inlier share at FOLD_INLIER_M under FOLD_ONE_MAX),
  * two planes fit it (share at least FOLD_TWO_MIN, and FOLD_GAIN more than
    one plane),
  * the planes differ by FOLD_MIN_ANGLE_DEG or more (a fold, not noise),
  * the crossing line runs through the face and leaves two parts of
    FOLD_MIN_PART_M2 and FOLD_MIN_POINTS returns each, and
  * on each side, FOLD_SIDE_AGREE of the returns are nearer that side's plane
    (the crossing line is where the returns actually change plane, so a step
    between two levels -- planes that cross somewhere else -- is not cut).
A face is cut at most FOLD_MAX_DEPTH times over. Drawn faces and plain forms
are never cut. Off by default (SOLAR_FOLD_SPLIT=1) until judged.
"""
import math
import os

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.ops import split as _split

FOLD_SPLIT = os.environ.get("SOLAR_FOLD_SPLIT", "0") == "1"
FOLD_MIN_FACE_M2 = 12.0
FOLD_EDGE_M = 0.3
FOLD_INLIER_M = 0.08
FOLD_ONE_MAX = 0.80
FOLD_TWO_MIN = 0.85
FOLD_GAIN = 0.15
FOLD_MIN_ANGLE_DEG = 8.0
FOLD_MIN_PART_M2 = 4.0
FOLD_MIN_POINTS = 25
FOLD_SIDE_AGREE = 0.80
FOLD_MAX_DEPTH = 3
RANSAC_ITERS = 300


def _lsq(p):
    A = np.c_[p[:, 0], p[:, 1], np.ones(len(p))]
    return np.linalg.lstsq(A, p[:, 2], rcond=None)[0]


def _res(pl, p):
    return np.abs(p[:, 2] - (pl[0] * p[:, 0] + pl[1] * p[:, 1] + pl[2]))


def _ransac(p, rng):
    """The plane with most returns within FOLD_INLIER_M, refitted on them."""
    best, best_n = None, 0
    n = len(p)
    for _ in range(RANSAC_ITERS):
        s = p[rng.choice(n, 3, replace=False)]
        A = np.c_[s[:, 0], s[:, 1], np.ones(3)]
        try:
            pl = np.linalg.solve(A, s[:, 2])
        except np.linalg.LinAlgError:
            continue
        k = int((_res(pl, p) <= FOLD_INLIER_M).sum())
        if k > best_n:
            best, best_n = pl, k
    if best is None:
        return None
    on = _res(best, p) <= FOLD_INLIER_M
    return _lsq(p[on]) if on.sum() >= 3 else best


def _normal_angle(a, b):
    na = np.array([-a[0], -a[1], 1.0]); nb = np.array([-b[0], -b[1], 1.0])
    c = abs(na @ nb) / (np.linalg.norm(na) * np.linalg.norm(nb))
    return math.degrees(math.acos(min(1.0, c)))


def _crossing(a, b, g):
    """The line where planes a and b cross, long enough to cut g, or None."""
    da, db, dc = a[0] - b[0], a[1] - b[1], a[2] - b[2]      # da x + db y + dc = 0
    if math.hypot(da, db) < 1e-6:
        return None
    c = g.centroid
    # nearest point of the line to the centroid, and the line's direction
    t = -(da * c.x + db * c.y + dc) / (da * da + db * db)
    x0, y0 = c.x + t * da, c.y + t * db
    ux, uy = -db, da
    L = math.hypot(ux, uy); ux, uy = ux / L, uy / L
    R = 2 * math.dist(g.bounds[:2], g.bounds[2:]) + 1
    return LineString([(x0 - R * ux, y0 - R * uy), (x0 + R * ux, y0 + R * uy)])


def _try_split(g, pts, rng):
    """[(part, plane), (part, plane)] or None."""
    one = _lsq(pts)
    s1 = float((_res(one, pts) <= FOLD_INLIER_M).mean())
    if s1 >= FOLD_ONE_MAX:
        return None
    a = _ransac(pts, rng)
    if a is None:
        return None
    rest = pts[_res(a, pts) > FOLD_INLIER_M]
    if len(rest) < FOLD_MIN_POINTS:
        return None
    b = _ransac(rest, rng)
    if b is None or _normal_angle(a, b) < FOLD_MIN_ANGLE_DEG:
        return None
    s2 = float((np.minimum(_res(a, pts), _res(b, pts)) <= FOLD_INLIER_M).mean())
    if s2 < FOLD_TWO_MIN or s2 < s1 + FOLD_GAIN:
        return None
    line = _crossing(a, b, g)
    if line is None or not line.intersects(g):
        return None
    try:
        parts = [q for q in _split(g, line).geoms if q.geom_type == "Polygon"]
    except Exception:
        return None
    if len(parts) != 2 or any(q.area < FOLD_MIN_PART_M2 for q in parts):
        return None
    out = []
    for q in parts:
        sub = pts[shapely.contains_xy(q, pts[:, 0], pts[:, 1])]
        if len(sub) < FOLD_MIN_POINTS:
            return None
        ra, rb = _res(a, sub), _res(b, sub)
        near_a = float((ra < rb).mean())
        if near_a >= FOLD_SIDE_AGREE:
            pl = a
        elif 1 - near_a >= FOLD_SIDE_AGREE:
            pl = b
        else:
            return None
        out.append((q, pl, sub))
    if out[0][1] is out[1][1]:
        return None            # both sides on one plane: the planes cross elsewhere
    return [(q, _lsq(sub), sub) for q, _pl, sub in out]


def split_folds(facets, pc_source):
    if not FOLD_SPLIT or not facets:
        return facets
    if any(f.get("from_labels") for f in facets):
        return facets
    from src.roof_partition import _slope_aspect
    rng = np.random.default_rng(0)
    todo = [(f, 0) for f in facets]
    out = []
    while todo:
        f, depth = todo.pop()
        g = f["geometry"]
        if (depth >= FOLD_MAX_DEPTH or g.geom_type != "Polygon" or g.area < FOLD_MIN_FACE_M2
                or f.get("structured")):
            out.append(f)
            continue
        try:
            pts = pc_source.points_in_bbox(*g.bounds, building_only=True)
        except Exception:
            pts = None
        core = g.buffer(-FOLD_EDGE_M)
        if pts is None or len(pts) < 2 * FOLD_MIN_POINTS or core.is_empty:
            out.append(f)
            continue
        pts = pts[shapely.contains_xy(core, pts[:, 0], pts[:, 1])]
        r = _try_split(g, pts, rng) if len(pts) >= 2 * FOLD_MIN_POINTS else None
        if r is None:
            out.append(f)
            continue
        for q, pl, sub in r:
            slope, aspect = _slope_aspect(pl)
            todo.append(({**f, "geometry": q, "plane_a": float(pl[0]), "plane_b": float(pl[1]),
                          "plane_c": float(pl[2]), "slope_deg": slope, "aspect_deg": aspect,
                          "area_m2": float(q.area), "point_count": int(len(sub)),
                          "fold_split": True}, depth + 1))
    return out
