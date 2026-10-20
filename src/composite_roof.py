"""A house roof as its wings: each wing a plain roof, joined the way roofs are built.

    SOLAR_COMPOSITE_ROOFS=1   turns it on (off until it is judged: it loses to a
                              many-faced whole-outline hip on most houses tried)

WHY. A suburban house is a few rectangles -- a main block and a wing or two --
each roofed as a hip, a gable, a mono-pitch or flat, and the valleys are
simply where one wing's slope runs into another's. src/structured_roof.py
tries ONE plain form on the whole outline; on 150 Shotover / Lake Hayes houses
(20 Oct) that fitted 8, and the hip form was rejected on 86 because some face
of it came out at an impossible pitch: those houses are not one hip. Ranking
every live v52 roof by the health checks put roof lines (seams off the ridge,
ragged faces) on 39 of the worst 40 houses.

HOW.
  1. The outline, in its own axes, rasterised and cut into rectangles: the
     largest all-roof rectangle first, then the next, until what is left is
     small (WING_MIN_M2) -- the wings.
  2. Each wing tries flat, mono (4 ways), gable (both ways) and hip, each
     face's plane fitted to the returns over it; the wing keeps the form that
     explains most returns, the simpler one on a near tie (WING_TIE).
  3. Joined as solids: within a wing the roof is the LOWEST of its planes
     (two gable slopes meet at the ridge), across wings the HIGHEST (where two
     wings overlap, the taller roof shows). Each cell takes the face that
     wins there; faces are those cells, so every ridge, hip and valley is
     where two planes cross -- a straight line.
The result competes with the current faces in structured_roof.maybe_structured
on the same terms as a plain form, and wins only when it explains the
returns about as well.
"""

import math
import os

import numpy as np

GRID_M = 0.25
WING_MIN_M2 = 12.0         # a leftover smaller than this is not a wing
MAX_WINGS = 5
COVER_STOP = 0.95          # stop once the wings cover this share of the outline
INLIER_M = 0.20
MIN_FACE_POINTS = 10
MIN_SLOPE_DEG, MAX_SLOPE_DEG = 8.0, 55.0
FLAT_MAX_DEG = 5.0
WING_TIE = 0.03            # a simpler wing form wins within this much
REFIT_ROUNDS = 3


def enabled():
    return os.environ.get("SOLAR_COMPOSITE_ROOFS", "0") == "1"


def _fit(p):
    A = np.c_[p[:, 0], p[:, 1], np.ones(len(p))]
    return np.linalg.lstsq(A, p[:, 2], rcond=None)[0]


def _slope(pl):
    return math.degrees(math.atan(math.hypot(pl[0], pl[1])))


def _largest_rect(grid):
    rows, cols = grid.shape
    h = np.zeros(cols, dtype=int)
    best = (0, 0, 0, 0, 0)
    for r in range(rows):
        h = np.where(grid[r], h + 1, 0)
        stack = []
        for c in range(cols + 1):
            cur = h[c] if c < cols else 0
            start = c
            while stack and stack[-1][1] >= cur:
                s0, hh = stack.pop()
                if hh * (c - s0) > best[0]:
                    best = (hh * (c - s0), r - hh + 1, r + 1, s0, c)
                start = s0
            stack.append((start, cur))
    return best


def _extend(grid, r0, r1, c0, c1):
    """Grow a rectangle one row / column at a time while it stays all-roof,
    preferring the longer growth -- a stub becomes the wing it belongs to."""
    rows, cols = grid.shape
    def grow_rows():
        a, b = r0, r1
        while a > 0 and grid[a - 1, c0:c1].all(): a -= 1
        while b < rows and grid[b, c0:c1].all(): b += 1
        return a, b
    def grow_cols():
        a, b = c0, c1
        while a > 0 and grid[r0:r1, a - 1].all(): a -= 1
        while b < cols and grid[r0:r1, b].all(): b += 1
        return a, b
    ra, rb = grow_rows()
    ca, cb = grow_cols()
    if (rb - ra) * (c1 - c0) >= (r1 - r0) * (cb - ca):
        return ra, rb, c0, c1
    return r0, r1, ca, cb


def _axes(footprint):
    mrr = footprint.minimum_rotated_rectangle
    xs, ys = mrr.exterior.coords.xy
    ang = math.atan2(ys[1] - ys[0], xs[1] - xs[0])
    return ang


def wings(footprint):
    """[(polygon, (cx, cy, ang))] rectangles covering the outline, in world NZTM."""
    import shapely
    from shapely import affinity
    from shapely.geometry import box
    ang = _axes(footprint)
    c = footprint.centroid
    fr = affinity.rotate(footprint, -ang, origin=c, use_radians=True)
    minx, miny, maxx, maxy = fr.bounds
    w = max(int(math.ceil((maxx - minx) / GRID_M)), 1)
    h = max(int(math.ceil((maxy - miny) / GRID_M)), 1)
    gx, gy = np.meshgrid(minx + (np.arange(w) + 0.5) * GRID_M, miny + (np.arange(h) + 0.5) * GRID_M)
    grid = shapely.contains_xy(fr, gx, gy)
    total = grid.sum()
    out, covered = [], np.zeros_like(grid)
    work = grid.copy()
    while len(out) < MAX_WINGS and work.any():
        area, r0, r1, c0, c1 = _largest_rect(work)
        if area * GRID_M * GRID_M < WING_MIN_M2:
            break
        if out:
            # A wing runs INTO the block it joins (a T's stem under its bar):
            # grow the leftover rectangle along whichever axis it can, over
            # cells already covered, while every cell stays on the roof.
            r0, r1, c0, c1 = _extend(grid, r0, r1, c0, c1)
        rect = box(minx + c0 * GRID_M, miny + r0 * GRID_M, minx + c1 * GRID_M, miny + r1 * GRID_M)
        out.append(affinity.rotate(rect, ang, origin=c, use_radians=True))
        covered[r0:r1, c0:c1] = True
        # a later wing may overlap this one (a T's stem runs into its bar), but
        # must add roof: only cells not yet covered count towards it
        work[r0:r1, c0:c1] = False
        if covered[grid].mean() >= COVER_STOP:
            break
    if not out or covered[grid].sum() < 0.8 * total:
        return [], ang
    # grow each wing back over cells it shares with others, so wings meet
    grown = []
    for q in out:
        g = q.buffer(GRID_M * 0.5, join_style=2).intersection(footprint)
        grown.append(g if g.geom_type == "Polygon" else max(getattr(g, "geoms", [q]), key=lambda x: x.area))
    return grown, ang


def _rect_frame(wing, ang):
    """Centre, unit axes (u along the wing's long side), half lengths."""
    mrr = wing.minimum_rotated_rectangle
    xs, ys = mrr.exterior.coords.xy
    e1 = np.array([xs[1] - xs[0], ys[1] - ys[0]])
    e2 = np.array([xs[2] - xs[1], ys[2] - ys[1]])
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
    u, v = e1 / np.linalg.norm(e1), e2 / np.linalg.norm(e2)
    c = np.array([mrr.centroid.x, mrr.centroid.y])
    return c, u, v, np.linalg.norm(e1) / 2, np.linalg.norm(e2) / 2


def _wing_forms(wing, ang, pts):
    """[(name, [plane...], labeller)] for one wing; labeller(x, y) -> face index."""
    c, u, v, a, b = _rect_frame(wing, ang)
    rel = lambda x, y: ((x - c[0]) * u[0] + (y - c[1]) * u[1], (x - c[0]) * v[0] + (y - c[1]) * v[1])
    px, py = rel(pts[:, 0], pts[:, 1])
    forms = []

    def planes_for(labels, k):
        pls = []
        for i in range(k):
            m = labels == i
            if m.sum() < MIN_FACE_POINTS:
                return None
            pls.append(_fit(pts[m]))
        return pls

    # flat and mono: one plane over the wing
    if len(pts) >= MIN_FACE_POINTS:
        pl = _fit(pts)
        s = _slope(pl)
        if s <= FLAT_MAX_DEG:
            forms.append(("flat", [pl], lambda x, y: np.zeros(np.shape(x), dtype=int)))
        elif MIN_SLOPE_DEG <= s <= MAX_SLOPE_DEG:
            forms.append(("mono", [pl], lambda x, y: np.zeros(np.shape(x), dtype=int)))
    # gable along the long axis (ridge along u) and across it (ridge along v)
    for name, side in (("gable", lambda ru, rv: (rv > 0).astype(int)),
                       ("gable_x", lambda ru, rv: (ru > 0).astype(int))):
        lab = side(px, py)
        pls = planes_for(lab, 2)
        if pls and all(MIN_SLOPE_DEG <= _slope(p) <= MAX_SLOPE_DEG for p in pls):
            forms.append((name, pls, (lambda s: lambda x, y: s(*rel(x, y)))(side)))
    # hip: the straight skeleton of a rectangle
    def hip_lab(ru, rv):
        du, dv = a - np.abs(ru), b - np.abs(rv)       # distance to the end / side walls
        side = np.where(rv > 0, 0, 1)
        end = np.where(ru > 0, 2, 3)
        return np.where(dv <= du, side, end)
    lab = hip_lab(px, py)
    pls = planes_for(lab, 4)
    if pls and all(MIN_SLOPE_DEG <= _slope(p) <= MAX_SLOPE_DEG for p in pls):
        forms.append(("hip", pls, lambda x, y: hip_lab(*rel(x, y))))
    return forms


def _envelope(x, y, wing_list, chosen):
    """Face id and height per cell: lowest plane within a wing, highest wing."""
    import shapely
    z_best = np.full(len(x), -np.inf)
    fid = np.full(len(x), -1)
    base = 0
    for wi, (wing, _form) in enumerate(zip(wing_list, chosen)):
        name, pls, _lab = _form
        inside = shapely.contains_xy(wing, x, y)
        Z = np.stack([p[0] * x + p[1] * y + p[2] for p in pls])
        zw, fw = Z.min(axis=0), Z.argmin(axis=0)
        win = inside & (zw > z_best)
        z_best[win] = zw[win]
        fid[win] = base + fw[win]
        base += len(pls)
    return fid


def composite_faces(footprint, pts):
    """[(polygon, plane)] for the outline as wings, or None."""
    import shapely
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from shapely.geometry import shape as _shape
    from shapely.ops import unary_union
    ws, ang = wings(footprint)
    if len(ws) < 2:
        return None            # one wing is a plain form: structured_roof's job
    chosen = []
    for wing in ws:
        on = shapely.contains_xy(wing, pts[:, 0], pts[:, 1])
        wp = pts[on]
        if len(wp) < MIN_FACE_POINTS:
            return None
        best = None
        for name, pls, lab in _wing_forms(wing, ang, wp):
            idx = lab(wp[:, 0], wp[:, 1])
            r = np.abs(wp[:, 2] - np.choose(idx, [p[0] * wp[:, 0] + p[1] * wp[:, 1] + p[2] for p in pls]))
            share = float((r < INLIER_M).mean())
            if best is None or share > best[0] + WING_TIE or (abs(share - best[0]) <= WING_TIE and len(pls) < len(best[2])):
                best = (share, name, pls, lab)
        if best is None:
            return None
        chosen.append((best[1], best[2], best[3]))
    # Refit: once joined, each face is refitted on the returns it actually
    # wins, and the roof joined again -- the junction returns a wing's own
    # fit had to share go to the right face.
    for _round in range(REFIT_ROUNDS):
        fid_pts = _envelope(pts[:, 0], pts[:, 1], ws, chosen)
        k = 0
        new = []
        for name, pls, lab in chosen:
            npl = []
            for p in pls:
                m = fid_pts == k
                npl.append(_fit(pts[m]) if m.sum() >= MIN_FACE_POINTS else p)
                k += 1
            new.append((name, npl, lab))
        chosen = new
    planes = [p for _n, pls, _l in chosen for p in pls]
    minx, miny, maxx, maxy = footprint.bounds
    w = max(int(math.ceil((maxx - minx) / GRID_M)), 1)
    h = max(int(math.ceil((maxy - miny) / GRID_M)), 1)
    gx, gy = np.meshgrid(minx + (np.arange(w) + 0.5) * GRID_M, maxy - (np.arange(h) + 0.5) * GRID_M)
    x, y = gx.ravel(), gy.ravel()
    inside = shapely.contains_xy(footprint, x, y)
    fid = np.where(inside, _envelope(x, y, ws, chosen), -1).reshape(h, w).astype(np.int32)
    t = from_origin(minx, maxy, GRID_M, GRID_M)
    parts = {}
    for geom, val in shapes(fid, mask=fid >= 0, transform=t):
        parts.setdefault(int(val), []).append(_shape(geom))
    faces = []
    for k, ps in parts.items():
        f = unary_union(ps).simplify(GRID_M, preserve_topology=True).intersection(footprint)
        for q in ([f] if f.geom_type == "Polygon" else getattr(f, "geoms", [])):
            if q.geom_type == "Polygon" and q.area >= 1.0:
                faces.append((q, tuple(float(v) for v in planes[k])))
    if len(faces) < 2:
        return None
    return faces, [n for n, _p, _l in chosen]
