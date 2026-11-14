"""Roof faces straight from the raw LiDAR returns (SOLAR_POINT_ROOF=1, off).

THE RULE. Per building: (1) grow planes on the returns themselves, over a
neighbour graph spread across directions (returns lie on scan lines about
1 m apart, so the nearest few are all on one line); (2) every pair of
touching planes gives a line: their exact intersection where the returns
meet along it (ridge, hip, valley), else a line fitted to where the height
jumps (step), snapped to a wall direction when near one; (3) the outline is
cut by every line, run right across the roof; (4) one graph cut gives each
cell a plane or "no roof here" (capped vertical misfit of the returns in the
cell, ground returns arguing for no roof, plus LAMBDA per metre of seam);
(5) flat planes (under FLAT_DEG) that meet without a step are one deck.
No boundary is traced from a raster, so every edge is straight and shared.

THE MEASUREMENT. The 2021 Queenstown survey carries about 9 returns/m2 and
about 2 cm of noise across a roof plane. Blind on the 70 drawn pilot roofs
(tools/markup_benchmark measures), production -> this: line F 0.583 ->
0.629, lines right 33 -> 38, faces right 26 -> 28, face IoU 0.651 -> 0.682.
LAMBDA was chosen on those 70 (0.4-4.0 gives line F 0.59-0.63); other towns
and the held-back roofs are unscored.

Writes data/point_faces/<id>.json in the selected-faces schema (source
"points", score 1.0). With SOLAR_POINT_ROOF=1 the build reads it ahead of
every other machine reading (roof_partition.selected_doc); hand markup
still wins.

Known gaps: roofs with sparse scan lines, balcony terraces, cones, hips
under FLAT_DEG (joined as one deck).

Usage: python src/point_roof.py <region> [--ids ...] [--jobs N]
"""
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import LineString, MultiPolygon
from shapely.ops import polygonize, unary_union

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

POINT_FACES_DIR = ROOT / "data" / "point_faces"
USE_POINT_ROOF = os.environ.get("SOLAR_POINT_ROOF", "0") == "1"

KNN = 24                   # returns in a normal estimate
KGROW = 10                 # growth-graph neighbours, at most two per 45 deg sector
GROW_ANGLE_DEG = 22.0      # neighbour normal against the growing plane's
GROW_DIST_M = 0.12         # neighbour distance to the growing plane
MIN_PLANE_PTS = 14         # about 1.5 m2 at 9 returns/m2
OUT_BUF_M = 0.6            # returns this far outside the outline count (eaves)
STEP_MIN_M = 0.35          # height jump that makes a boundary a step
LINE_MERGE_ANG = 6.0
LINE_MERGE_OFF = 0.35
MAX_SLOPE = math.tan(math.radians(62))   # steeper is wall or tree
LAMBDA = 1.5               # cost of a metre of seam, in metres of return misfit
FLAT_DEG = 5.0
FLAT_STEP_M = 0.2
VOID = True
REGISTER = True            # slide the outline onto the roof-height returns first
CAP = 0.5                  # most one return can cost, m

_PC = None


def pc():
    global _PC
    if _PC is None:
        from src.pointcloud_source import PointCloudSource
        _PC = PointCloudSource()
    return _PC


def outlines(region):
    """The region's outlines as surveyed. The slide onto the roof is measured
    here (register) and travels with the faces as the doc's `shift`, which
    outline_fit.fitted_outlines applies when SOLAR_POINT_ROOF=1, so faces
    and the outline the build clips to are in one frame."""
    import geopandas as gpd
    from src.region_build import area_paths
    gdf = gpd.read_file(area_paths(region)["outlines"])
    return {int(b): g for b, g in zip(gdf["building_id"], gdf.geometry)}


def point_shift(building_id):
    """The slide a building's point faces were made with, or None."""
    if not USE_POINT_ROOF:
        return None
    try:
        s = json.loads((POINT_FACES_DIR / f"{int(building_id)}.json").read_text()).get("shift")
        return (float(s[0]), float(s[1])) if s else None
    except (OSError, ValueError, TypeError):
        return None


def all_points(poly, buf=OUT_BUF_M):
    """Every return over the outline (+buf), with class; and local ground z."""
    minx, miny, maxx, maxy = poly.buffer(8).bounds
    xs, ys, zs, cs = [], [], [], []
    P = pc()
    for path in P._tiles_overlapping(minx, miny, maxx, maxy):
        tx, ty, tz, tc = P._load_tile(path)
        m = (tx >= minx) & (tx <= maxx) & (ty >= miny) & (ty <= maxy)
        xs.append(tx[m]); ys.append(ty[m]); zs.append(tz[m]); cs.append(tc[m])
    x, y, z, c = map(np.concatenate, (xs, ys, zs, cs))
    g = c == 2
    ground = np.percentile(z[g], 50) if g.sum() > 5 else np.percentile(z, 5)
    from shapely import contains_xy
    inside = contains_xy(poly.buffer(buf), x, y)
    # height above the ground nearest each return (sites slope)
    if g.sum() > 5:
        gt = cKDTree(np.column_stack([x[g], y[g]]))
        _, gi = gt.query(np.column_stack([x, y]), k=min(5, int(g.sum())))
        hag = z - np.median(z[g][gi], axis=1)
    else:
        hag = z - ground
    veg = (c >= 3) & (c <= 5)
    keep = inside & ~g & (hag > 1.5) & (c != 7) & (c != 18) & ~veg
    low = inside & (g | (hag < 0.8)) & ~veg
    pts = np.column_stack([x[keep], y[keep], z[keep]])
    global _LOW
    _LOW = np.column_stack([x[low], y[low]])
    return pts, c[keep], ground


_LOW = None


def register(poly, max_shift=2.5, step=0.25):
    """Slide the outline (traced on a photo, where a tall roof leans) onto the
    roof the LiDAR sees: the shift that best overlaps the outline with the
    footprint of roof-height returns. Returns (dx, dy)."""
    from shapely import contains_xy
    minx, miny, maxx, maxy = poly.buffer(max_shift + 3).bounds
    xs, ys, zs, cs = [], [], [], []
    P = pc()
    for path in P._tiles_overlapping(minx, miny, maxx, maxy):
        tx, ty, tz, tc = P._load_tile(path)
        m = (tx >= minx) & (tx <= maxx) & (ty >= miny) & (ty <= maxy)
        xs.append(tx[m]); ys.append(ty[m]); zs.append(tz[m]); cs.append(tc[m])
    x, y, z, c = map(np.concatenate, (xs, ys, zs, cs))
    g = c == 2
    ground = np.percentile(z[g], 50) if g.sum() > 5 else np.percentile(z, 5)
    roof = (z > ground + 2.0) & ~((c >= 3) & (c <= 5)) & (c != 2)
    cell = 0.25
    W, H = int((maxx - minx) / cell) + 1, int((maxy - miny) / cell) + 1
    occ = np.zeros((H, W), bool)
    occ[((y[roof] - miny) / cell).astype(int), ((x[roof] - minx) / cell).astype(int)] = True
    from scipy.ndimage import binary_closing, binary_opening
    occ = binary_closing(occ, np.ones((5, 5)))
    occ = binary_opening(occ, np.ones((3, 3)))
    gx = minx + (np.arange(W) + 0.5) * cell; gy = miny + (np.arange(H) + 0.5) * cell
    GX, GY = np.meshgrid(gx, gy)
    base = contains_xy(poly, GX, GY)
    # only the returns that could be this roof: within reach of the outline
    reach = contains_xy(poly.buffer(max_shift + 0.5), GX, GY)
    occ &= reach
    best, bs = (0.0, 0.0), -1
    k = int(max_shift / step)
    for i in range(-k, k + 1):
        for j in range(-k, k + 1):
            sh = np.roll(np.roll(base, j, axis=0), i, axis=1)
            inter = (sh & occ).sum()
            sc = inter / max(sh.sum(), 1) - 0.002 * math.hypot(i, j)
            if sc > bs:
                bs, best = sc, (i * step, j * step)
    sh0 = (base & occ).sum() / max(base.sum(), 1)
    # move only for a clear gain
    if bs - sh0 < 0.03:
        return (0.0, 0.0)
    return best


def normals(pts, k=KNN):
    tree = cKDTree(pts[:, :2])
    k = min(k, len(pts))
    d, idx = tree.query(pts[:, :2], k=k)
    nb = pts[idx]                                   # n,k,3
    cen = nb.mean(axis=1, keepdims=True)
    q = nb - cen
    cov = np.einsum("nki,nkj->nij", q, q) / k
    w, v = np.linalg.eigh(cov)
    n = v[:, :, 0]
    n[n[:, 2] < 0] *= -1
    curv = w[:, 0] / np.maximum(w.sum(axis=1), 1e-12)
    # growth graph: fewer neighbours, but spread over directions -- returns sit
    # on scan lines ~1 m apart, so the nearest few are all on one line
    kg = min(KGROW * 3, len(pts))
    d2, i2 = tree.query(pts[:, :2], k=kg)
    g = np.zeros((len(pts), KGROW), int)
    for i in range(len(pts)):
        v = pts[i2[i], :2] - pts[i, :2]
        ang = (np.degrees(np.arctan2(v[:, 1], v[:, 0])) // 45).astype(int)
        pick, seen = [], {}
        for j in range(1, kg):
            if d2[i, j] > 2.0: break
            if seen.get(ang[j], 0) < 2:
                seen[ang[j]] = seen.get(ang[j], 0) + 1; pick.append(i2[i, j])
            if len(pick) == KGROW - 1: break
        pick = [i] + pick + [i] * (KGROW - 1 - len(pick))
        g[i] = pick
    return n, curv, g, tree


def fit_plane(p):
    """z = a x + b y + c by least squares on centred coords -> (a,b,c), rms."""
    A = np.column_stack([p[:, 0], p[:, 1], np.ones(len(p))])
    sol, *_ = np.linalg.lstsq(A, p[:, 2], rcond=None)
    r = A @ sol - p[:, 2]
    return sol, float(np.sqrt((r ** 2).mean()))


def grow_planes(pts):
    """Region growing over the kNN graph. Returns labels (-1 = none), planes."""
    n, curv, idx, tree = normals(pts)
    N = len(pts)
    lab = -np.ones(N, int)
    order = np.argsort(curv)
    planes = []
    cosang = math.cos(math.radians(GROW_ANGLE_DEG))
    for seed in order:
        if lab[seed] >= 0 or curv[seed] > 0.02:
            continue
        L = len(planes)
        nn = n[seed]; p0 = pts[seed]
        members = [seed]; lab[seed] = L
        stack = [seed]
        refit_at = 12
        while stack:
            i = stack.pop()
            for j in idx[i]:
                if lab[j] >= 0:
                    continue
                if abs(n[j] @ nn) < cosang:
                    continue
                if abs((pts[j] - p0) @ nn) > GROW_DIST_M:
                    continue
                lab[j] = L; members.append(j); stack.append(j)
                if len(members) >= refit_at:
                    refit_at *= 2
                    m = pts[members]
                    c = m.mean(0)
                    w, v = np.linalg.eigh(np.cov((m - c).T))
                    nn = v[:, 0] if v[2, 0] > 0 else -v[:, 0]
                    p0 = c
        if len(members) < MIN_PLANE_PTS:
            lab[members] = -2            # tried, too small
            continue
        planes.append(members)
    lab[lab == -2] = -1
    # refit, then reassign every point to the plane it fits best among those
    # its neighbours carry (cleans ragged borders)
    eqs = [fit_plane(pts[m])[0] for m in planes]
    for _ in range(2):
        new = lab.copy()
        for i in range(N):
            cand = set(lab[idx[i]]) - {-1}
            if not cand:
                continue
            best, bd = -1, GROW_DIST_M * 1.5
            for L in cand:
                a, b, c = eqs[L]
                d = abs(a * pts[i, 0] + b * pts[i, 1] + c - pts[i, 2]) / math.sqrt(1 + a * a + b * b)
                if d < bd:
                    best, bd = L, d
            new[i] = best
        lab = new
        keep = [L for L in range(len(eqs)) if (lab == L).sum() >= MIN_PLANE_PTS]
        remap = {L: k for k, L in enumerate(keep)}
        lab = np.array([remap.get(L, -1) for L in lab])
        eqs = [fit_plane(pts[lab == k])[0] for k in range(len(keep))]
    lab, eqs = merge_coplanar(pts, lab, eqs, idx)
    lab, eqs = merge_flat(pts, lab, eqs, idx)
    # walls and tree faces are not roof
    ok = [k for k, (a, b, c) in enumerate(eqs) if math.hypot(a, b) < MAX_SLOPE]
    remap = {L: k for k, L in enumerate(ok)}
    lab = np.array([remap.get(L, -1) for L in lab])
    eqs = [eqs[L] for L in ok]
    return lab, eqs, idx


def merge_coplanar(pts, lab, eqs, idx):
    """Adjacent regions on one plane become one."""
    changed = True
    while changed:
        changed = False
        adj = adjacency(lab, idx)
        for (i, j), cnt in sorted(adj.items(), key=lambda kv: -kv[1]):
            if cnt < 4:
                continue
            pi, pj = pts[lab == i], pts[lab == j]
            a, b, c = eqs[i]
            dj = np.abs(a * pj[:, 0] + b * pj[:, 1] + c - pj[:, 2])
            a2, b2, c2 = eqs[j]
            di = np.abs(a2 * pi[:, 0] + b2 * pi[:, 1] + c2 - pi[:, 2])
            if np.median(dj) < 0.08 and np.median(di) < 0.08:
                lab[lab == j] = i
                eqs[i] = fit_plane(pts[lab == i])[0]
                keep = sorted(set(lab) - {-1})
                remap = {L: k for k, L in enumerate(keep)}
                lab = np.array([remap.get(L, -1) for L in lab])
                eqs = [eqs[L] for L in keep]
                changed = True
                break
    return lab, eqs


def merge_flat(pts, lab, eqs, idx):
    """A flat deck drains by falls of a degree or two in several directions;
    for panels it is one face. Adjacent planes both under FLAT_DEG whose
    surfaces meet (no step at the seam) are joined."""
    flat = [math.degrees(math.atan(math.hypot(a, b))) < FLAT_DEG for a, b, c in eqs]
    parent = list(range(len(eqs)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for (a, b), prs in boundary_pairs(pts, lab, idx).items():
        if not (flat[a] and flat[b]) or len(prs) < 4:
            continue
        ii = np.array([p[0] for p in prs]); jj = np.array([p[1] for p in prs])
        if np.median(np.abs(pts[ii, 2] - pts[jj, 2])) < FLAT_STEP_M:
            parent[find(a)] = find(b)
    roots = sorted({find(i) for i in range(len(eqs))})
    remap = {r: k for k, r in enumerate(roots)}
    lab = np.array([remap[find(L)] if L >= 0 else -1 for L in lab])
    eqs = [fit_plane(pts[lab == k])[0] for k in range(len(roots))]
    return lab, eqs


def adjacency(lab, idx):
    adj = {}
    for i in range(len(lab)):
        a = lab[i]
        if a < 0:
            continue
        for j in idx[i, 1:]:
            b = lab[j]
            if b < 0 or b == a:
                continue
            k = (min(a, b), max(a, b))
            adj[k] = adj.get(k, 0) + 1
    return adj


def boundary_pairs(pts, lab, idx):
    """For each adjacent plane pair: midpoints between neighbouring points on
    opposite sides (where the seam is), and the two points' heights."""
    out = {}
    for i in range(len(lab)):
        a = lab[i]
        if a < 0:
            continue
        for j in idx[i, 1:]:
            b = lab[j]
            if b <= a:
                continue
            out.setdefault((a, b), []).append((i, j))
    return out


def dominant_dirs(poly):
    """Wall directions (mod 180), length-weighted, for snapping step lines."""
    c = np.array(poly.exterior.coords)
    d = c[1:] - c[:-1]
    ln = np.hypot(d[:, 0], d[:, 1])
    ang = (np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 180)
    dirs = []
    for a, l in sorted(zip(ang, ln), key=lambda t: -t[1]):
        if l < 1.0:
            continue
        if all(min(abs(a - b), 180 - abs(a - b)) > 8 for b in dirs):
            dirs.append(a)
    return dirs


def candidate_lines(pts, lab, eqs, idx, poly):
    """(point, direction, kind, pair) for every seam between adjacent planes."""
    dirs = dominant_dirs(poly)
    lines = []
    for (a, b), prs in boundary_pairs(pts, lab, idx).items():
        if len(prs) < 3:
            continue
        ii = np.array([p[0] for p in prs]); jj = np.array([p[1] for p in prs])
        mid = (pts[ii, :2] + pts[jj, :2]) / 2
        A, B = np.array(eqs[a]), np.array(eqs[b])
        # height gap between the planes at the seam midpoints
        za = A[0] * mid[:, 0] + A[1] * mid[:, 1] + A[2]
        zb = B[0] * mid[:, 0] + B[1] * mid[:, 1] + B[2]
        gap = np.abs(za - zb)
        dn = A[:2] - B[:2]
        made = False
        if np.hypot(*dn) > 0.03:
            # intersection line: (A-B).(x,y,1) = 0
            nrm = dn / np.hypot(*dn)
            off = (A[2] - B[2]) / np.hypot(*dn)          # nrm.x + off = 0
            dist = np.abs(mid @ nrm + off)
            near = dist < 0.6
            if near.sum() >= max(3, 0.35 * len(prs)):
                p0 = -off * nrm
                d = np.array([-nrm[1], nrm[0]])
                t = (mid[near] - p0) @ d
                lines.append({"p": p0 + d * np.median(t), "d": d, "kind": "fold", "pair": (a, b),
                              "span": (float(t.min() - np.median(t)), float(t.max() - np.median(t))), "n": int(near.sum())})
                made = True
                rest = ~near
            else:
                rest = np.ones(len(prs), bool)
        else:
            rest = np.ones(len(prs), bool)
        # step: the remaining boundary, where heights jump
        st = rest & (gap > STEP_MIN_M)
        if st.sum() >= 3:
            m = mid[st]
            # bias the line to the upper roof's edge is not attempted; fit by
            # PCA then snap to a wall direction when close
            for seg in split_runs(m):
                if len(seg) < 3:
                    continue
                c = seg.mean(0)
                w, v = np.linalg.eigh(np.cov((seg - c).T))
                d = v[:, 1]
                ang = math.degrees(math.atan2(d[1], d[0])) % 180
                for wd in dirs:
                    if min(abs(ang - wd), 180 - abs(ang - wd)) < 15:
                        d = np.array([math.cos(math.radians(wd)), math.sin(math.radians(wd))])
                        break
                t = (seg - c) @ d
                if t.max() - t.min() < 0.8:
                    continue
                lines.append({"p": c, "d": d, "kind": "step", "pair": (a, b),
                              "span": (float(t.min()), float(t.max())), "n": len(seg)})
    return lines


def split_runs(m):
    """Split boundary midpoints into straight-ish runs (a step boundary often
    turns a corner): recursive split at the point farthest from the PCA line."""
    out = []

    def rec(seg, depth):
        if len(seg) < 3:
            return
        c = seg.mean(0)
        w, v = np.linalg.eigh(np.cov((seg - c).T))
        d = v[:, 1]; nrm = v[:, 0]
        off = (seg - c) @ nrm
        if np.percentile(np.abs(off), 90) < 0.45 or depth >= 3:
            out.append(seg); return
        # split by sign of the perpendicular direction clusters: 2-means along angle to centroid
        t = (seg - c) @ d
        order = np.argsort(t)
        seg = seg[order]; off = off[order]
        k = int(np.argmax(np.abs(off)))
        k = min(max(k, 2), len(seg) - 2)
        rec(seg[:k], depth + 1); rec(seg[k:], depth + 1)

    rec(m, 0)
    return out


def merge_lines(lines):
    out = []
    for l in sorted(lines, key=lambda l: (l["kind"] != "fold", -l["n"])):
        ang = math.degrees(math.atan2(l["d"][1], l["d"][0])) % 180
        dup = False
        for o in out:
            oa = math.degrees(math.atan2(o["d"][1], o["d"][0])) % 180
            if min(abs(ang - oa), 180 - abs(ang - oa)) > LINE_MERGE_ANG:
                continue
            nrm = np.array([-o["d"][1], o["d"][0]])
            if abs((l["p"] - o["p"]) @ nrm) < LINE_MERGE_OFF:
                dup = True; break
        if not dup:
            out.append(l)
    return out


def arrangement(poly, lines, extend=3.0):
    """Cells of the outline cut by every line, each run right across the roof
    (the labelling step merges cells a line had no business cutting)."""
    R = 300.0
    segs = [LineString([l["p"] - l["d"] * R, l["p"] + l["d"] * R]) for l in lines]
    net = unary_union([poly.boundary] + segs)
    return [c for c in polygonize(net) if c.area > 0.02 and c.representative_point().within(poly)]


def label_cells(cells, pts, lab, eqs, lam=LAMBDA, low=None):
    """Alpha-expansion: each cell takes one plane. Data: capped vertical misfit
    of the returns inside the cell. Smoothness: seam length."""
    import maxflow
    from shapely import STRtree, contains_xy
    K, C = len(eqs), len(cells)
    if K == 0:
        return np.zeros(C, int)
    D = np.zeros((C, K))
    cell_of = -np.ones(len(pts), int)
    for ci, c in enumerate(cells):
        minx, miny, maxx, maxy = c.bounds
        m = (pts[:, 0] >= minx) & (pts[:, 0] <= maxx) & (pts[:, 1] >= miny) & (pts[:, 1] <= maxy)
        ii = np.where(m)[0]
        if len(ii):
            ins = contains_xy(c, pts[ii, 0], pts[ii, 1])
            cell_of[ii[ins]] = ci
    E = np.array(eqs)
    zhat = pts[:, :1] * E[None, :, 0] + pts[:, 1:2] * E[None, :, 1] + E[None, :, 2]   # n,K
    mis = np.minimum(np.abs(zhat - pts[:, 2:3]), CAP)
    for ci in range(C):
        m = cell_of == ci
        if m.any():
            D[ci] = mis[m].sum(0)
    if VOID and low is not None:
        # one more label: no roof here. Roof returns argue against it, ground
        # returns for it (and against every plane).
        nroof = np.bincount(cell_of[cell_of >= 0], minlength=C).astype(float)
        nlow = np.zeros(C)
        if len(low):
            for ci, c in enumerate(cells):
                minx, miny, maxx, maxy = c.bounds
                m = (low[:, 0] >= minx) & (low[:, 0] <= maxx) & (low[:, 1] >= miny) & (low[:, 1] <= maxy)
                if m.any():
                    nlow[ci] = contains_xy(c, low[m, 0], low[m, 1]).sum()
        D = np.column_stack([D + (CAP * nlow)[:, None], CAP * nroof])
        K += 1
    # a cell with no returns: mild prior to the plane of the nearest returns is
    # left to the smoothness term.
    # adjacency by shared boundary length
    tree = STRtree(cells)
    edges = []
    for i, c in enumerate(cells):
        for j in tree.query(c):
            if j <= i:
                continue
            s = c.boundary.intersection(cells[j].boundary)
            L = s.length if not s.is_empty else 0.0
            if L > 0.05:
                edges.append((i, j, L))
    x = D.argmin(1)
    def energy(x):
        return D[np.arange(C), x].sum() + lam * sum(L for i, j, L in edges if x[i] != x[j])
    best = energy(x)
    for sweep in range(4):
        improved = False
        for alpha in range(K):
            g = maxflow.Graph[float](C, len(edges) * 2)
            nodes = g.add_nodes(C)
            INF = 1e9
            for i in range(C):
                # source side = keep label, sink side = take alpha
                g.add_tedge(i, D[i, alpha], INF if x[i] == alpha else D[i, x[i]])
            for i, j, L in edges:
                w = lam * L
                if x[i] == x[j]:
                    if x[i] != alpha:
                        g.add_edge(i, j, w, w)
                else:
                    # standard alpha-expansion auxiliary node
                    a = g.add_nodes(1)[0]
                    wi = w if x[i] != alpha else 0.0
                    wj = w if x[j] != alpha else 0.0
                    g.add_edge(i, a, wi, wi)
                    g.add_edge(a, j, wj, wj)
                    g.add_tedge(a, 0, w)
            g.maxflow()
            nx = x.copy()
            for i in range(C):
                if g.get_segment(i) == 1:
                    nx[i] = alpha
            e = energy(nx)
            if e < best - 1e-9:
                x, best, improved = nx, e, True
        if not improved:
            break
    return x


def reconstruct(bid, poly, lam=None):
    from shapely.affinity import translate
    lam = LAMBDA if lam is None else lam
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    dx = dy = 0.0
    if REGISTER:
        dx, dy = register(poly)
        poly = translate(poly, dx, dy)
    pts, cls, ground = all_points(poly)
    world = poly
    # local frame: plane fits in raw NZTM (1e6 m) are ill-conditioned
    ox, oy = poly.centroid.x, poly.centroid.y
    oz = ground
    poly = translate(poly, -ox, -oy)
    pts = pts - np.array([ox, oy, oz])
    out = {"faces": [], "pts": pts + np.array([ox, oy, oz]), "poly": world, "shift": [dx, dy]}
    if len(pts) < 30:
        return out
    lab, eqs, idx = grow_planes(pts)
    lines = merge_lines(candidate_lines(pts, lab, eqs, idx, poly))
    cells = arrangement(poly, lines)
    x = label_cells(cells, pts, lab, eqs, lam, low=(_LOW - np.array([ox, oy])) if _LOW is not None else None)
    faces = []
    for k in sorted(set(x)):
        if k >= len(eqs):
            continue
        u = unary_union([cells[i] for i in range(len(cells)) if x[i] == k]).buffer(0)
        for g in (u.geoms if hasattr(u, "geoms") else [u]):
            if g.area > 0.5:
                a, b, c = eqs[k]
                faces.append({"poly": translate(g, ox, oy), "plane": (a, b, c), "slope": math.degrees(math.atan(math.hypot(a, b))), "k": int(k)})
    out.update(faces=faces, lab=lab, eqs=eqs, lines=lines, n_cells=len(cells))
    return out


def write_faces(bid, poly, out_dir=POINT_FACES_DIR):
    rec = reconstruct(bid, poly)
    faces = [[[round(x, 2), round(y, 2)] for x, y in f["poly"].exterior.coords] for f in rec["faces"]]
    if not faces:
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{bid}.json").write_text(json.dumps({"source": "points", "score": 1.0, "shift": rec["shift"], "faces": faces}))
    return len(faces)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("--ids", nargs="*", type=int, default=None)
    ap.add_argument("--sample-file", default=None)
    ap.add_argument("--out", default=str(POINT_FACES_DIR))
    a = ap.parse_args()
    O = outlines(a.region)
    ids = a.ids or (json.loads(Path(a.sample_file).read_text()) if a.sample_file else sorted(O))
    n = 0
    for bid in ids:
        if bid not in O:
            continue
        try:
            n += 1 if write_faces(bid, O[bid], Path(a.out)) else 0
        except Exception as exc:
            print(f"  point_roof: {bid} failed ({exc!r})", flush=True)
    print(f"point_roof: {n} of {len(ids)} roofs written to {a.out}")


if __name__ == "__main__":
    main()
