"""
Faces rebuilt from straight seams.

Real roof lines are straight: a ridge, hip, valley or step runs wall to wall
or corner to corner. Faces from the LiDAR path and from SAM masks arrive as
traced boundaries, so their seams zig-zag at the survey's cell size (1 m on
frankton_arm_lake). The stages in src/face_straighten.py simplify vertices
and leave much of that: on five Wakatipu Heights houses (11 Nov), whose photo
shows plain gables and hips, straighten_faces refused three roofs outright
("a face would split in two", "area changed more than a hairline") and
shipped 71-421 vertices across 6-9 faces, and on the other two it left notched
seams. Simplifying a wobbly line does not make it the line it should be.

So the seams are rebuilt instead of simplified:

  1. LABELS. The faces are painted onto a REF_CELL_M grid inside the roof,
     the smaller face winning where two overlap (a dormer or lower level
     lies inside the larger face). Painting needs no clean coverage, so the
     overlaps and hairline gaps that make as_coverage refuse do not matter.
  2. SEAMS. Each pair of faces that touch on the grid gives the points along
     their shared boundary, split into connected pieces (two faces can meet
     in more than one place).
  3. LINES. Each piece is fitted by a straight line (total least squares).
     A piece that bends by more than SPLIT_TOL_M is split at its worst point
     and each half fitted again, at most SPLIT_DEPTH deep, if the two lines
     fit at least twice as well (SPLIT_GAIN, rms) as one: an L-shaped valley
     is two lines, a zig-zag is one. A line within SNAP_AXIS_DEG of the
     walls (src/outline_axis.py), or SNAP_HIP_DEG of 45 degrees to them,
     takes that direction through its points' centre. Each end then runs on
     to the first other line or wall it meets, up to REACH_M: the seam
     points stop short of a junction (the grid there belongs to a third
     face), and lines that do not meet close no face.
  4. FACES. The roof is cut by those lines (polygonize) and each cell goes
     to the face that held most of it on the grid. A line poking into one
     face from a junction then vanishes, because both sides go to that face.
     A face that comes out in more than one piece keeps the piece it held
     most of; the others go to the neighbour they share most edge with.
     A face no straight seam encloses (it meets its neighbours only along
     short or broken edges), or that would change too much, keeps its own
     edges simplified by FALLBACK_SIMPLIFY_M, in one more pass.

The roof's outer edge is the faces' own (the surveyed outline): untouched.
Planes are unchanged: only where one face stops and the next begins moves.

A face that no straight seam encloses is a sliver of the traced tiling; it
goes to its neighbours if under DROP_MAX_M2 or DROP_MAX_SHARE of the roof.
Kept only if every other face survives as one polygon within MAX_AREA_CHANGE of
its area (or MAX_AREA_CHANGE_M2, whichever is larger: a 3 m2 face can move
1 m2 on a 1 m grid), and the LiDAR fits no worse: the share of returns
within ON_PLANE_M of their own face's plane may fall by at most
MAX_ON_PLANE_DROP. Otherwise the roof keeps the faces it had.

Faces from Josh's markup (his lines are the answer) and plain forms
(src/structured_roof.py, exact by construction) are not touched.

SOLAR_RECTIFY_FACES=1 turns it on (off until the markup benchmark says so).
"""

import math
import os

REF_CELL_M = 0.1
MIN_SEAM_M = 0.8          # shorter shared edges are left to cell assignment
SPLIT_TOL_M = 0.6         # a seam bending more than this is two lines
SPLIT_DEPTH = 2           # at most four lines per seam piece
SPLIT_GAIN = 0.5
MIN_PIECE_M = 1.0
SNAP_AXIS_DEG = 12.0
SNAP_HIP_DEG = 8.0
EXTEND_M = 0.3            # past its own points, before reaching for a neighbour
REACH_M = 4.0
LINK_M = 0.25             # seam points closer than this are one piece
MAX_AREA_CHANGE = 0.30
MAX_AREA_CHANGE_M2 = 2.0
DROP_MAX_M2 = 6.0         # a face no seam encloses may go if this small...
DROP_MAX_SHARE = 0.05     # ...or this share of the roof
FALLBACK_SIMPLIFY_M = 0.5
ON_PLANE_M = 0.15
MAX_ON_PLANE_DROP = 0.02

REFUSED = {}


def _refuse(why):
    REFUSED[why] = REFUSED.get(why, 0) + 1
    return None


def _labels(polys, region):
    """(grid x, grid y, label per grid point: face index or -1)"""
    import numpy as np
    import shapely
    x0, y0, x1, y1 = region.bounds
    xs = np.arange(x0 + REF_CELL_M / 2, x1, REF_CELL_M)
    ys = np.arange(y0 + REF_CELL_M / 2, y1, REF_CELL_M)
    gx, gy = np.meshgrid(xs, ys)
    lab = np.full(gx.shape, -1, dtype=int)
    # larger first, so the smaller face is painted last and wins the overlap
    for i in sorted(range(len(polys)), key=lambda i: -polys[i].area):
        m = shapely.intersects_xy(polys[i], gx, gy)   # a point on an edge is still in
        lab[m] = i
    return gx, gy, lab


def _seam_points(gx, gy, lab):
    """{(i, j): N x 2 points on the boundary between faces i < j}"""
    import numpy as np
    out = {}
    for a, b, xa, ya, xb, yb in (
            (lab[:, :-1], lab[:, 1:], gx[:, :-1], gy[:, :-1], gx[:, 1:], gy[:, 1:]),
            (lab[:-1, :], lab[1:, :], gx[:-1, :], gy[:-1, :], gx[1:, :], gy[1:, :])):
        m = (a != b) & (a >= 0) & (b >= 0)
        if not m.any():
            continue
        i = np.minimum(a[m], b[m]); j = np.maximum(a[m], b[m])
        px = (xa[m] + xb[m]) / 2; py = (ya[m] + yb[m]) / 2
        for key in set(zip(i.tolist(), j.tolist())):
            k = (i == key[0]) & (j == key[1])
            out.setdefault(key, []).append(np.column_stack([px[k], py[k]]))
    return {k: np.vstack(v) for k, v in out.items()}


def _pieces(P):
    """Connected pieces of a point set (points within LINK_M chain)."""
    import numpy as np
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    if len(P) < 2:
        return [P]
    pairs = cKDTree(P).query_pairs(LINK_M, output_type="ndarray")
    if len(pairs) == 0:
        return [P[k:k + 1] for k in range(len(P))]
    n = len(P)
    g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, comp = connected_components(g, directed=False)
    return [P[comp == c] for c in np.unique(comp)]


def _snap_dir(d, axis_deg):
    ang = math.degrees(math.atan2(d[1], d[0])) % 180.0
    best = None
    for off, tol in ((0.0, SNAP_AXIS_DEG), (90.0, SNAP_AXIS_DEG), (45.0, SNAP_HIP_DEG), (135.0, SNAP_HIP_DEG)):
        t = (axis_deg + off) % 180.0
        dev = abs((ang - t + 90.0) % 180.0 - 90.0)
        if dev <= tol and (best is None or dev < best[0]):
            best = (dev, t)
    if best is None:
        return d
    r = math.radians(best[1])
    return (math.cos(r), math.sin(r))


def _rms(P):
    import numpy as np
    if len(P) < 3:
        return 0.0
    c = P.mean(axis=0)
    _, _, vt = np.linalg.svd(P - c, full_matrices=False)
    r = (P - c) @ np.array([-vt[0][1], vt[0][0]])
    return float(np.sqrt((r ** 2).mean()))


def _fit(P, axis_deg, depth=0):
    """Straight segments ((x0, y0), (x1, y1)) through a seam piece."""
    import numpy as np
    c = P.mean(axis=0)
    if len(P) < 3:
        return []
    _, _, vt = np.linalg.svd(P - c, full_matrices=False)
    d = vt[0]
    along = (P - c) @ d
    span = float(along.max() - along.min())
    if span < MIN_PIECE_M:
        return []
    dev = np.abs((P - c) @ np.array([-d[1], d[0]]))
    if dev.max() > SPLIT_TOL_M and depth < SPLIT_DEPTH and span >= 2 * MIN_PIECE_M:
        cut = along[int(dev.argmax())]
        # a worst point at an end cannot split the seam: halve it instead
        if cut - along.min() < MIN_PIECE_M or along.max() - cut < MIN_PIECE_M:
            cut = (along.min() + along.max()) / 2
        lo, hi = P[along <= cut], P[along > cut]
        # A BEND, NOT NOISE: two lines must fit at least SPLIT_GAIN times
        # better (rms) than one. A zig-zag traced at the grid's cell size
        # fits two lines no better than one and stays one line.
        if _rms(lo) + _rms(hi) < SPLIT_GAIN * 2 * float(np.sqrt((dev ** 2).mean())):
            segs = _fit(lo, axis_deg, depth + 1) + _fit(hi, axis_deg, depth + 1)
            if segs:
                return segs
    d = np.asarray(_snap_dir(d, axis_deg))
    along = (P - c) @ d
    a0, a1 = float(along.min()) - EXTEND_M, float(along.max()) + EXTEND_M
    return [(tuple(c + a0 * d), tuple(c + a1 * d))]


def _reach(segs, region):
    """Each segment's ends run on to the first other segment or the roof's
    edge within REACH_M."""
    import numpy as np
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union
    out = []
    for k, (a, b) in enumerate(segs):
        a, b = np.asarray(a, float), np.asarray(b, float)
        L = float(np.hypot(*(b - a)))
        if L == 0:
            continue
        d = (b - a) / L
        others = unary_union([region.boundary] + [LineString(s) for j, s in enumerate(segs) if j != k])
        ends = []
        for p, sgn in ((a, -1.0), (b, 1.0)):
            ray = LineString([p, p + sgn * REACH_M * d])
            hit = ray.intersection(others)
            if hit.is_empty:
                ends.append(p)
                continue
            dist = min(Point(p).distance(g) for g in getattr(hit, "geoms", [hit]))
            ends.append(p + sgn * (dist + 0.05) * d)
        out.append((tuple(ends[0]), tuple(ends[1])))
    return out


def _on_plane_count(facets, polys, pts):
    import numpy as np
    import shapely
    n = 0
    for f, p in zip(facets, polys):
        if p is None or f.get("plane_a") is None:
            continue
        m = shapely.contains_xy(p, pts[:, 0], pts[:, 1])
        if not m.any():
            continue
        q = pts[m]
        z = f["plane_a"] * q[:, 0] + f["plane_b"] * q[:, 1] + f["plane_c"]
        n += int((np.abs(q[:, 2] - z) <= ON_PLANE_M).sum())
    return n


def rectify_polys(polys, axis_deg=None):
    """The faces rebuilt on straight seams, in the same order (None for a
    sliver absorbed by its neighbours), or None."""
    import numpy as np
    import shapely
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union
    if len(polys) < 2:
        return None
    region = unary_union(polys).buffer(0.05, join_style="mitre").buffer(-0.05, join_style="mitre")
    if region.is_empty:
        return _refuse("empty roof")
    if axis_deg is None:
        from src.outline_axis import dominant_axis_deg
        axis_deg = dominant_axis_deg(region) or 0.0
    gx, gy, lab = _labels(polys, region)
    if (lab >= 0).sum() == 0:
        return _refuse("no grid cells")
    segs = []
    for key, P in _seam_points(gx, gy, lab).items():
        for piece in _pieces(P):
            if len(piece) * REF_CELL_M < MIN_SEAM_M:
                continue
            segs += _fit(piece, axis_deg)
    if not segs:
        return _refuse("no seams")
    segs = _reach(segs, region)
    flat_x, flat_y, flat_l = gx.ravel(), gy.ravel(), lab.ravel()
    inside = flat_l >= 0
    grid = (flat_x[inside], flat_y[inside], flat_l[inside])
    out, bad = _assemble(polys, region, grid, segs, [])
    if bad:
        # A FACE NO STRAIGHT SEAM ENCLOSES (it meets its neighbours only along
        # short or broken edges) keeps its own edges, simplified: one more
        # pass with those as cuts too.
        extra = [polys[i].simplify(FALLBACK_SIMPLIFY_M).boundary for i in bad]
        out, bad = _assemble(polys, region, grid, segs, extra)
        if bad:
            return _refuse(out)
    return out


def _assemble(polys, region, grid, segs, extra):
    """(faces, []) or (reason, [indices of the faces that failed])"""
    import numpy as np
    import shapely
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union
    cut = [region.boundary] + [LineString(s).intersection(region.buffer(0.01)) for s in segs] \
        + [e.intersection(region.buffer(0.01)) for e in extra]
    cells = [c for c in polygonize(unary_union(cut)) if c.area > 1e-4]
    # each cell to the face that held most of it on the grid
    flat_x, flat_y, flat_l = grid
    owner = []
    for c in cells:
        m = shapely.contains_xy(c, flat_x, flat_y)
        if m.any():
            owner.append(int(np.bincount(flat_l[m], minlength=len(polys)).argmax()))
        else:
            hits = [(c.intersection(p).area, i) for i, p in enumerate(polys)]
            owner.append(max(hits)[1] if max(hits)[0] > 0 else -1)
    for _ in range(3):
        faces = []
        for i in range(len(polys)):
            mine = [c for c, o in zip(cells, owner) if o == i]
            faces.append(unary_union(mine) if mine else None)
        moved = False
        for i, g in enumerate(faces):
            if g is None or g.geom_type == "Polygon":
                continue
            parts = list(g.geoms)
            keep = max(parts, key=lambda p: p.intersection(polys[i]).area)
            for p in parts:
                if p is keep:
                    continue
                best, edge = None, 0.0
                for j, h in enumerate(faces):
                    if j == i or h is None:
                        continue
                    e = p.boundary.intersection(h.buffer(0.01)).length
                    if e > edge:
                        best, edge = j, e
                if best is None:
                    continue
                for k, c in enumerate(cells):
                    if owner[k] == i and p.covers(c.representative_point()):
                        owner[k] = best; moved = True
        if not moved:
            break
    roof_area = sum(p.area for p in polys)
    absorbed = sum(p.area for p, g in zip(polys, faces) if g is None or g.is_empty)
    out, bad, why = [], [], None
    for i, (p, g) in enumerate(zip(polys, faces)):
        if g is None or g.is_empty:
            # a sliver no straight seam encloses goes to its neighbours
            if p.area > max(DROP_MAX_M2, DROP_MAX_SHARE * roof_area):
                bad.append(i); why = why or "a face lost every cell"
            out.append(None)
            continue
        if g.geom_type != "Polygon":
            bad.append(i); why = why or "a face would split in two"
            out.append(None)
            continue
        g = shapely.set_precision(g, 0.001).simplify(0.01)
        if g.geom_type != "Polygon" or not g.is_valid:
            bad.append(i); why = why or "invalid face"
        elif abs(g.area - p.area) > max(MAX_AREA_CHANGE * p.area, MAX_AREA_CHANGE_M2) + absorbed:
            bad.append(i); why = why or "a face changed area too much"
        out.append(g)
    return (why, bad) if bad else (out, [])


def rectify_faces(facets, pc_source=None, *_, **__):
    if os.environ.get("SOLAR_RECTIFY_FACES", "0") != "1" or len(facets) < 2:
        return facets
    if any(f.get("from_labels") or f.get("structured") for f in facets):
        return facets
    polys = [f["geometry"] for f in facets]
    if any(p is None or p.geom_type != "Polygon" or p.is_empty for p in polys):
        return facets
    new = rectify_polys(polys)
    if new is None:
        return facets
    if pc_source is not None:
        import shapely
        from shapely.ops import unary_union
        b = unary_union(polys).bounds
        pts = pc_source.points_in_bbox(b[0], b[1], b[2], b[3], building_only=True)
        if pts is not None and len(pts) >= 20:
            roof = unary_union(polys)
            pts = pts[shapely.contains_xy(roof, pts[:, 0], pts[:, 1])]
            if len(pts) >= 20:
                before = _on_plane_count(facets, polys, pts)
                after = _on_plane_count(facets, new, pts)
                if after < before - MAX_ON_PLANE_DROP * len(pts):
                    _refuse("the LiDAR fits worse")
                    return facets
    out = []
    for f, g in zip(facets, new):
        if g is None:
            continue
        h = dict(f, geometry=g)
        if "area_m2" in h:
            h["area_m2"] = g.area
        out.append(h)
    return out
