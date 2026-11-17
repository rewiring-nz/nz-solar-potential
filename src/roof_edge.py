"""The roof's edge from the LiDAR returns, wall by wall, and the roof's faces
run out to it (SOLAR_ROOF_EDGE=0 turns it off).

THE RULE. The surveyed outline says WHICH building and keeps its corners
and wall directions; the returns say where the roof ends. Each wall of
EDGE_MIN_M or more moves along its normal (IN_MAX_M in to OUT_MAX_M out) to
where this roof's returns stop: the last STRIP_M strip holding
DENSITY_SHARE of the roof's own return density, then the 95th percentile
of the returns in it plus half the return spacing. A wall whose strip
expects under MIN_STRIP_RETURNS, or with a tree over it (TREE_SHARE), is
not fitted and takes the roof's median move. Courtyards (holes in the
surveyed outline) are kept as surveyed. The faces found on the surveyed
outline are kept and run out to the fitted edge (extend_to_edge); the
inner partition is not redone.

THE MEASUREMENT (16 Nov, BACKLOG.md Roof lines). 36 Stanley Street: 696 m2
against 688 m2 drawn by hand (surveyed outline 561 m2). Walls on 1 Shotover
Street move -1.0 to +0.9 m, on 15B Frankton Road -0.3 to +0.5 m. Owner's
verdict on docs/review/three_houses_16nov_roof_edge.jpg: the edge from the
returns is better than the surveyed outline. Inner lines on 50 marked
roofs, surveyed outline / extended to the fitted edge: right at 0.3 m
28 / 24, at 0.75 m 33 / 33 (the markup's outer edge is the surveyed
outline, so it cannot score the edge itself). On 40 random unmarked houses
(17 Nov, docs/review/placement_v82_*.jpg) about a quarter had a roof whose
real edge was not the surveyed outline.

`pr` is the src.point_roof module (passed in: it may be running as
__main__, and its point-cloud cache must not be loaded twice).
"""
import os

import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely import contains_xy

USE_ROOF_EDGE = os.environ.get("SOLAR_ROOF_EDGE", "1") != "0"
REACH_M = 2.5        # how far past the outline an eave may run
OWN_SHARE = 0.5      # of a plane's returns over the outline (+0.6 m)

# Per-edge fit: the surveyed outline keeps its corners and directions; each
# wall line moves out or in to where this roof's returns stop.
EDGE_MIN_M = 1.5      # shorter edges take their neighbours' move
STRIP_M = 0.4         # depth of the strip whose return density is read
OUT_MAX_M = 2.0       # eaves, verandas
IN_MAX_M = 1.0
DENSITY_SHARE = 0.4   # of the roof's own density: under this the strip is not roof
HALF_SPACING_M = 0.1
MIN_STRIP_RETURNS = 10


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


def tree_returns(pr, poly, reach=REACH_M):
    """Vegetation returns (LAS classes 3-5) over or beside the roof: x, y, z."""
    minx, miny, maxx, maxy = poly.buffer(reach + 1).bounds
    xs, ys, zs = [], [], []
    P = pr.pc()
    for path in P._tiles_overlapping(minx, miny, maxx, maxy):
        tx, ty, tz, tc = P._load_tile(path)
        m = (tx >= minx) & (tx <= maxx) & (ty >= miny) & (ty <= maxy) & (tc >= 3) & (tc <= 5)
        xs.append(tx[m]); ys.append(ty[m]); zs.append(tz[m])
    return np.column_stack([np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)]) if xs else np.zeros((0, 3))


TREE_SHARE = 0.2      # of a wall's length under vegetation: the returns cannot say where it ends


def fit_edges(pr, poly):
    """`poly` with each long edge moved along its normal to where the roof's
    returns stop. Returns (polygon, moves) or (poly, None)."""
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    whole = poly
    from shapely.geometry.polygon import orient
    holes = [Polygon(r) for r in poly.interiors]
    poly = orient(Polygon(poly.exterior), 1.0)       # counter-clockwise: outward normal is to the right
    P = own_returns(pr, poly)
    if len(P) < 60:
        return whole, None
    inside = contains_xy(poly, P[:, 0], P[:, 1])
    V = tree_returns(pr, poly)
    rho = inside.sum() / max(poly.area, 1.0)
    c = np.array(poly.exterior.coords)[:-1]
    n = len(c)
    moves = [None] * n
    for i in range(n):
        a, b = c[i], c[(i + 1) % n]
        t = b - a; L = np.hypot(*t)
        # a wall is fitted only where its strip holds enough returns to
        # read a density from: 1 Shotover Street's 2.4 m wall expected 5
        # and moved 1 m inwards on noise
        if L < EDGE_MIN_M or (L - 0.6) * STRIP_M * rho < MIN_STRIP_RETURNS:
            continue
        t = t / L; nrm = np.array([t[1], -t[0]])
        # a tree over the wall hides the roof under it: where the returns
        # stop there is where the tree starts, so the wall keeps the roof's
        # typical move
        rel = P[:, :2] - a
        if len(V):
            # only vegetation at or above this wall's roof, over the roof
            # side of the wall, hides anything: every vegetation return
            # within reach stopped 192 of 365 walls on 50 roofs (garden
            # shrubs beside the wall)
            al0, ac0 = rel @ t, rel @ nrm
            nearwall = (al0 > 0) & (al0 < L) & (ac0 > -1.5) & (ac0 <= 1.0)
            if nearwall.sum() >= 5:
                zr = np.median(P[nearwall, 2])
                rv = V[:, :2] - a
                va, vc = rv @ t, rv @ nrm
                hit = va[(va > 0) & (va < L) & (vc > -IN_MAX_M) & (vc < 0.5) & (V[:, 2] > zr - 0.3)]
                if len(np.unique((hit / 0.5).astype(int))) * 0.5 > TREE_SHARE * L:
                    continue
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
        return whole, None
    # unfitted walls take the roof's typical move: eaves run all round a roof
    typical = float(np.median([m for m in moves if m is not None]))
    moves = [typical if m is None else m for m in moves]
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
        return whole, None
    if holes:
        g = g.difference(unary_union(holes)).buffer(0)
        if g.is_empty or g.geom_type != "Polygon":
            return whole, None
    return g, moves


def extend_to_edge(faces, edge, reach=3.0):
    """The same faces, run out (or cut back) to `edge`.

    The inner partition is not redone on the moved outline: on 1 Shotover
    Street a wall moved by half a metre changed which cells the labelling
    joined and redrew a hip end that was right. A face is a plane, so past
    the old outline it simply continues: the strip between the old outline
    and the edge is cut by every seam carried straight on, and each piece
    joins the face it lies against."""
    from shapely.geometry import LineString
    from shapely.ops import polygonize
    polys = [f["poly"] for f in faces]
    old = unary_union([p.buffer(0.02) for p in polys]).buffer(-0.02)
    strips = edge.difference(old.buffer(0.01))
    cuts = []
    for i, p in enumerate(polys):
        for q in polys[i + 1:]:
            s = p.boundary.intersection(q.buffer(0.05))
            for g in getattr(s, "geoms", [s]):
                if g.geom_type != "LineString" or g.length < 0.5:
                    continue
                # direction from a stretch set back from the end: the last
                # 5 cm of a shared boundary turns along the outline
                L = g.length
                for d0, d1 in ((0.6, 0.1), (L - 0.6, L - 0.1)):
                    pa, pb = g.interpolate(min(max(d0, 0), L)), g.interpolate(min(max(d1, 0), L))
                    end = g.interpolate(0 if d1 < d0 else L)
                    if end.distance(old.boundary) > 0.3:
                        continue
                    d = np.array([pb.x - pa.x, pb.y - pa.y]); n = np.hypot(*d)
                    if n < 1e-6:
                        continue
                    d = d / n
                    cuts.append(LineString([(pb.x, pb.y), (pb.x + d[0] * reach, pb.y + d[1] * reach)]))
    pieces = []
    for st in getattr(strips, "geoms", [strips]):
        if st.is_empty or st.geom_type != "Polygon" or st.area < 0.05:
            continue
        net = unary_union([st.boundary] + [c for c in cuts if c.intersects(st)])
        ps = [g for g in polygonize(net) if g.representative_point().within(st.buffer(0.01))]
        pieces.extend(ps or [st])
    out = [dict(f, poly=f["poly"].intersection(edge)) for f in faces]
    for pc in pieces:
        best, bl = None, 0.05
        for i, f in enumerate(faces):
            sh = pc.boundary.intersection(f["poly"].buffer(0.06)).length
            if sh > bl:
                best, bl = i, sh
        if best is not None:
            out[best]["poly"] = out[best]["poly"].union(pc.buffer(0.03)).buffer(-0.02)
    res = []
    for f in out:
        p = f["poly"]
        if p.is_empty:
            continue
        if p.geom_type != "Polygon":
            p = max(p.geoms, key=lambda g: g.area) if hasattr(p, "geoms") else None
        if p is not None and p.geom_type == "Polygon" and p.area > 0.3:
            res.append(dict(f, poly=p))
    return res
