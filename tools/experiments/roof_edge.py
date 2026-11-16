"""The roof's edge from the LiDAR returns, in place of the surveyed outline.

THE RULE. The surveyed outline says WHICH building; the returns say where
its roof ends. Planes are grown on every roof-height return within REACH_M
of the outline; a plane belongs to this roof when at least OWN_SHARE of its
returns lie over the outline (a neighbour's roof or a tree lies mostly off
it). The returns of those planes are binned on a CELL_M grid turned to the
outline's main wall direction, closed over the scan-line spacing, and the
filled area's boundary is the roof edge: stair steps along the walls'
directions, simplified to SIMPLIFY_M.

THE MEASUREMENT is in BACKLOG.md (Roof lines, 16 Nov).
"""
import math
import numpy as np
from scipy import ndimage as ndi
from shapely.geometry import Polygon, MultiPolygon, box
from shapely.ops import unary_union
from shapely.affinity import rotate, translate
from shapely import contains_xy

REACH_M = 2.5        # how far past the outline an eave may run
OWN_SHARE = 0.5      # of a plane's returns over the outline (+0.6 m)
CELL_M = 0.2
CLOSE_M = 0.7        # scan lines are about 1 m apart
GROW_M = 0.15        # the outermost return is short of the edge by half the return spacing
SIMPLIFY_M = 0.3
MIN_PART_M2 = 4.0


def roof_edge(pr, poly):
    """Polygon of the roof as the returns have it, or `poly` when there is
    too little to go on. `pr` is src.point_roof; `poly` the outline already
    slid onto the returns."""
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    pts, _, ground = pr.all_points(poly, buf=REACH_M)
    if len(pts) < 30:
        return poly
    ox, oy = poly.centroid.x, poly.centroid.y
    loc = pts - np.array([ox, oy, ground])
    lab, eqs, idx = pr.grow_planes(loc)
    near = contains_xy(poly.buffer(0.6), pts[:, 0], pts[:, 1])
    keep = np.zeros(len(pts), bool)
    for k in set(lab[lab >= 0]):
        m = lab == k
        if near[m].mean() >= OWN_SHARE:
            keep |= m
    if keep.sum() < 30:
        return poly
    dirs = pr.dominant_dirs(poly)
    ang = dirs[0] if dirs else 0.0
    c, s = math.cos(math.radians(-ang)), math.sin(math.radians(-ang))
    x = (pts[keep, 0] - ox) * c - (pts[keep, 1] - oy) * s
    y = (pts[keep, 0] - ox) * s + (pts[keep, 1] - oy) * c
    pad = CLOSE_M + 1.0
    x0, y0 = x.min() - pad, y.min() - pad
    W, H = int((x.max() + pad - x0) / CELL_M) + 1, int((y.max() + pad - y0) / CELL_M) + 1
    occ = np.zeros((H, W), bool)
    occ[((y - y0) / CELL_M).astype(int), ((x - x0) / CELL_M).astype(int)] = True
    r = int(round(CLOSE_M / CELL_M))
    occ = ndi.binary_closing(occ, np.ones((2 * r + 1, 2 * r + 1)))
    occ = ndi.binary_fill_holes(occ)
    occ = ndi.binary_opening(occ, np.ones((3, 3)))
    lbl, n = ndi.label(occ)
    parts = []
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lbl == i)
        if len(ys) * CELL_M * CELL_M < MIN_PART_M2:
            continue
        # run-length boxes per row: axis-aligned edges in the turned frame
        cells = []
        for yy in np.unique(ys):
            row = np.sort(xs[ys == yy]); st = row[0]; pv = row[0]
            for v in row[1:]:
                if v != pv + 1:
                    cells.append(box(x0 + st * CELL_M, y0 + yy * CELL_M, x0 + (pv + 1) * CELL_M, y0 + (yy + 1) * CELL_M)); st = v
                pv = v
            cells.append(box(x0 + st * CELL_M, y0 + yy * CELL_M, x0 + (pv + 1) * CELL_M, y0 + (yy + 1) * CELL_M))
        parts.append(unary_union(cells))
    if not parts:
        return poly
    g = unary_union(parts).buffer(GROW_M, join_style=2).simplify(SIMPLIFY_M)
    g = translate(rotate(g, ang, origin=(0, 0)), ox, oy).buffer(0)
    if g.is_empty:
        return poly
    # the parts that are this building: touching the outline
    gs = [q for q in (g.geoms if hasattr(g, "geoms") else [g]) if q.intersects(poly) and q.area >= MIN_PART_M2]
    if not gs:
        return poly
    g = Polygon(max(gs, key=lambda q: q.intersection(poly).area).exterior)
    # straight by construction: edges along the walls' directions, few corners
    try:
        from src.roof_partition import _regularise_machine_face
        reg = _regularise_machine_face(g, poly)
        if reg is not None and abs(reg[0].area - g.area) < 0.12 * g.area:
            g = reg[0]
    except Exception:
        pass
    return g


# ---------------------------------------------------------------------------
# Per-edge fit: the surveyed outline keeps its corners and directions; each
# wall line moves out or in to where this roof's returns stop.
EDGE_MIN_M = 1.5      # shorter edges take their neighbours' move
STRIP_M = 0.4         # depth of the strip whose return density is read
OUT_MAX_M = 2.0       # eaves, verandas
IN_MAX_M = 1.0
DENSITY_SHARE = 0.4   # of the roof's own density: under this the strip is not roof
HALF_SPACING_M = 0.1


def own_returns(pr, poly, reach=REACH_M):
    pts, _, ground = pr.all_points(poly, buf=reach)
    if len(pts) < 30:
        return np.zeros((0, 3))
    ox, oy = poly.centroid.x, poly.centroid.y
    lab, eqs, idx = pr.grow_planes(pts - np.array([ox, oy, ground]))
    near = contains_xy(poly.buffer(0.6), pts[:, 0], pts[:, 1])
    keep = np.zeros(len(pts), bool)
    for k in set(lab[lab >= 0]):
        m = lab == k
        if near[m].mean() >= OWN_SHARE:
            keep |= m
    return pts[keep]


def fit_edges(pr, poly):
    """`poly` with each long edge moved along its normal to where the roof's
    returns stop. Returns (polygon, moves) or (poly, None)."""
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    from shapely.geometry.polygon import orient
    poly = orient(Polygon(poly.exterior), 1.0)       # counter-clockwise: outward normal is to the right
    P = own_returns(pr, poly)
    if len(P) < 60:
        return poly, None
    inside = contains_xy(poly, P[:, 0], P[:, 1])
    rho = inside.sum() / max(poly.area, 1.0)
    c = np.array(poly.exterior.coords)[:-1]
    n = len(c)
    moves = [None] * n
    for i in range(n):
        a, b = c[i], c[(i + 1) % n]
        t = b - a; L = np.hypot(*t)
        if L < EDGE_MIN_M:
            continue
        t = t / L; nrm = np.array([t[1], -t[0]])
        rel = P[:, :2] - a
        along = rel @ t; across = rel @ nrm
        on = (along > 0.3) & (along < L - 0.3)
        best = None
        for d in np.arange(-IN_MAX_M, OUT_MAX_M + 1e-9, 0.1):
            dens = ((across[on] > d - STRIP_M) & (across[on] <= d)).sum() / ((L - 0.6) * STRIP_M)
            if dens >= DENSITY_SHARE * rho:
                best = d
            elif best is not None and d > 0:
                break            # the returns have stopped: do not jump a gap to a neighbour
        if best is None:
            moves[i] = 0.0
            continue
        # the strip rule is good to the strip's depth; the edge itself is
        # where the returns of that last stretch end
        last = across[on & (across > best - 2 * STRIP_M) & (across <= best)]
        moves[i] = (float(np.percentile(last, 95)) + HALF_SPACING_M) if len(last) >= 5 else best
    if all(m is None for m in moves):
        return poly, None
    for i in range(n):           # short edges: the mean of the nearest fitted neighbours
        if moves[i] is None:
            nb = [moves[j % n] for j in (i - 1, i + 1) if moves[j % n] is not None]
            moves[i] = float(np.mean(nb)) if nb else 0.0
    lines = []
    for i in range(n):
        a, b = c[i], c[(i + 1) % n]
        t = (b - a) / max(np.hypot(*(b - a)), 1e-9); nrm = np.array([t[1], -t[0]])
        lines.append((a + nrm * moves[i], t, nrm))
    out = []
    for i in range(n):
        (p0, t0, n0), (p1, t1, n1) = lines[i - 1], lines[i]
        cr = t0[0] * t1[1] - t0[1] * t1[0]
        if abs(cr) < 0.15:
            out.append(c[i] + (n0 * moves[i - 1] + n1 * moves[i]) / 2)
        else:
            s = ((p1 - p0)[0] * t1[1] - (p1 - p0)[1] * t1[0]) / cr
            q = p0 + t0 * s
            out.append(q if np.hypot(*(q - c[i])) < 3.5 else c[i] + (n0 * moves[i - 1] + n1 * moves[i]) / 2)
    g = Polygon(out).buffer(0)
    if g.is_empty or g.geom_type != "Polygon" or not (0.6 * poly.area < g.area < 1.8 * poly.area):
        return poly, None
    return g, moves
