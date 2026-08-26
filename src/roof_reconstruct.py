"""
Reconstruct a roof as a set of planes joined along real edges, instead of
tracing each facet independently out of a raster.

Why (measured on the pilot, 26 Aug): panels escaping their facet -- 0. Panels
crossing the building outline -- 0. Panels on a drawn obstruction -- 1 in
60,562. But 7.2% of panels sit more than 0.35m off the plane they were placed
on, and on the worst buildings that is 60-92% of the roof. Panel fitting is
not what is wrong. The roof model underneath it is: a barrel-vaulted building
comes through as ONE flat facet, a stepped terrace as planes spanning the
steps between levels.

The approach here builds the roof the way a roof is actually made:

1. Planes, from the point cloud rather than the DSM raster. Iterative RANSAC:
   take the best-supported plane, remove its inliers, repeat. Sawtooth bays
   separate naturally -- same normal, different offset is a different plane
   equation. A curved roof comes out as a fan of narrow strips, which is the
   correct piecewise-planar answer rather than one wrong plane.

2. Edges, analytically. Two planes that meet do so along their intersection
   line -- that IS the ridge/hip/valley, exact, straight, and shared by both
   faces. Near-parallel neighbours don't intersect; they are a step in height,
   so that boundary is fitted to where the point support actually changes.
   Outer edges come from the LINZ outline, which is surveyed and already
   straight. No boundary comes from tracing pixels.

3. Facets, by arrangement. Every edge line is cut against the outline and the
   whole set polygonized, so the roof is partitioned into cells whose borders
   are straight by construction and exactly shared between neighbours. Each
   cell goes to whichever plane the points inside it actually support, and
   cells with the same winner are merged.

Emits the same facet dicts as roof_segmentation (building_id, plane_a/b/c,
slope_deg, aspect_deg, area_m2, point_count, geometry) so it can be dropped
in behind a flag once it is proven.

Prototype: not wired into the pipeline. See src/compare_reconstruct.py.
"""

import math
import sys
from pathlib import Path

import numpy as np
import shapely.vectorized
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, split, unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RANSAC_TOL_M = 0.15        # a point this close to a plane is on it
RANSAC_ITERS = 400
MIN_PLANE_PTS = 25         # ~4.5 m2 at pilot density (5.7 pts/m2)
MAX_PLANES = 40
WALL_SLOPE_DEG = 72        # steeper than this is a wall, not a roof face
GRID_M = 0.30              # label raster step, for adjacency and step edges
PARALLEL_GRAD_TOL = 0.06   # gradient difference below this = a step, not a joint
MIN_FACET_M2 = 6.0         # smaller than ~3 panels is not a face worth racking
MIN_CELL_PTS = 4           # a cell with fewer points has no say in its own label
# Coplanar merge. Splitting a roof into more planes ALWAYS lowers residual, so
# without this the reconstruction scores well by shattering: 93 Beach St came
# out as 82 speckled fragments at "5% off-plane". Two neighbouring cells that
# describe the same physical surface have to end up as one facet.
MERGE_SLOPE_DEG = 6.0
MERGE_ASPECT_DEG = 14.0
MERGE_FLAT_DEG = 6.0       # below this slope, aspect is meaningless
MERGE_STEP_M = 0.25        # ...and they must not be parallel faces at different heights
# The angle tolerances above only decide what is WORTH trying to merge. What
# decides it is whether one plane still fits both sets of points: on the barrel
# vault, adjacent strips differ by ~5 degrees and merging on angle alone put
# the curve back under a single wrong plane (49% -> 33% off-plane, still bad).
# A merge has to keep this share of the combined points on the plane.
MERGE_MIN_INLIER_FRAC = 0.90
SMOOTH_ROUNDS = 3
# A cell is only allowed to belong to one plane if its own points mostly agree.
# 111 Hallenstein came out of the arrangement with a 109 m2 cell straddling the
# ridge, votes split 245/213/319 between three faces -- winner-takes-all handed
# the lot to one of them and the refit plane was meaningless (32% off-plane on
# a roof the shipped model got to 15%). Where the line set is incomplete, the
# cell gets cut by the two planes competing for it.
MIXED_MAX_SHARE = 0.75
REFINE_ROUNDS = 4


def fit_plane(pts):
    """Least squares z = a*x + b*y + c, centred for conditioning."""
    x0, y0 = pts[:, 0].mean(), pts[:, 1].mean()
    A = np.column_stack([pts[:, 0] - x0, pts[:, 1] - y0, np.ones(len(pts))])
    coef, *_ = np.linalg.lstsq(A, pts[:, 2], rcond=None)
    a, b, c0 = coef
    return np.array([a, b, c0 - a * x0 - b * y0])


def residuals(plane, pts):
    a, b, c = plane
    return a * pts[:, 0] + b * pts[:, 1] + c - pts[:, 2]


def plane_slope_aspect(plane):
    a, b, _ = plane
    slope = math.degrees(math.atan(math.hypot(a, b)))
    aspect = math.degrees(math.atan2(-a, -b)) % 360
    return slope, aspect


def ransac_planes(pts, rng):
    """Strongest plane first, remove its inliers, repeat. Returns planes and
    the index of the plane each point was claimed by (-1 = unclaimed)."""
    n = len(pts)
    owner = np.full(n, -1)
    live = np.arange(n)
    planes = []
    while len(live) >= MIN_PLANE_PTS and len(planes) < MAX_PLANES:
        sub = pts[live]
        best_inl, best_plane = None, None
        for _ in range(RANSAC_ITERS):
            idx = rng.choice(len(sub), 3, replace=False)
            tri = sub[idx]
            v1, v2 = tri[1] - tri[0], tri[2] - tri[0]
            nx, ny, nz = np.cross(v1, v2)
            if abs(nz) < 1e-6:          # vertical sample triple -- no z = f(x,y)
                continue
            plane = np.array([-nx / nz, -ny / nz,
                              (nx * tri[0, 0] + ny * tri[0, 1] + nz * tri[0, 2]) / nz])
            inl = np.abs(residuals(plane, sub)) < RANSAC_TOL_M
            if best_inl is None or inl.sum() > best_inl.sum():
                best_inl, best_plane = inl, plane
        if best_inl is None or best_inl.sum() < MIN_PLANE_PTS:
            break
        # Refit on the consensus set, then re-select: the 3-point seed plane is
        # noisy, and one refit typically pulls in another 10-20% of the face.
        plane = fit_plane(sub[best_inl])
        inl = np.abs(residuals(plane, sub)) < RANSAC_TOL_M
        if inl.sum() < MIN_PLANE_PTS:
            break
        plane = fit_plane(sub[inl])
        planes.append(plane)
        owner[live[inl]] = len(planes) - 1
        live = live[~inl]
    return planes, owner


def label_raster(outline, pts, owner, planes):
    """Nearest claimed point wins each grid cell -- only used to find which
    planes are neighbours and where the steps are, never to make a boundary."""
    minx, miny, maxx, maxy = outline.bounds
    xs = np.arange(minx, maxx + GRID_M, GRID_M)
    ys = np.arange(miny, maxy + GRID_M, GRID_M)
    gx, gy = np.meshgrid(xs, ys)
    inside = shapely.vectorized.contains(outline, gx, gy)
    claimed = owner >= 0
    if claimed.sum() == 0:
        return None, None, None
    tree = cKDTree(pts[claimed][:, :2])
    _, nn = tree.query(np.column_stack([gx[inside], gy[inside]]))
    lab = np.full(gx.shape, -1)
    lab[inside] = owner[claimed][nn]
    return lab, gx, gy


def adjacent_pairs(lab):
    """Label pairs that touch, so only real neighbours contribute an edge --
    every plane pair would be O(n^2) lines and shatter the arrangement."""
    pairs = set()
    for A, B in ((lab[:, :-1], lab[:, 1:]), (lab[:-1, :], lab[1:, :])):
        d = (A != B) & (A >= 0) & (B >= 0)
        for i, j in zip(A[d], B[d]):
            pairs.add((min(i, j), max(i, j)))
    return pairs


def _line_from_coeffs(A, B, C, bounds):
    """A*x + B*y + C = 0 as a segment long enough to cross the building."""
    minx, miny, maxx, maxy = bounds
    norm = math.hypot(A, B)
    if norm < 1e-12:
        return None
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    half = max(maxx - minx, maxy - miny) + 20.0
    t = -(A * cx + B * cy + C) / (norm * norm)
    px, py = cx + A * t, cy + B * t
    dx, dy = -B / norm, A / norm
    return LineString([(px - dx * half, py - dy * half), (px + dx * half, py + dy * half)])


def _fit_line_tls(P):
    """Total least squares through 2D points -> A*x + B*y + C = 0."""
    c = P.mean(axis=0)
    _, _, Vt = np.linalg.svd(P - c, full_matrices=False)
    dx, dy = Vt[0]
    A, B = -dy, dx
    return A, B, -(A * c[0] + B * c[1])


def edge_lines(planes, pairs, lab, gx, gy, bounds):
    """A joint between two planes is their intersection line -- exact. A step
    between two parallel planes has no intersection, so that one edge is fitted
    to the grid cells where the label actually changes."""
    lines = []
    for i, j in pairs:
        ai, bi, ci = planes[i]
        aj, bj, cj = planes[j]
        A, B, C = ai - aj, bi - bj, ci - cj
        if math.hypot(A, B) >= PARALLEL_GRAD_TOL:
            ln = _line_from_coeffs(A, B, C, bounds)
            if ln is not None:
                lines.append(ln)
            continue
        # Parallel: find the boundary cells between the two labels and fit.
        bnd = []
        for (Aa, Bb, ia, ja) in ((lab[:, :-1], lab[:, 1:], 0, 1), (lab[:-1, :], lab[1:, :], 1, 0)):
            d = ((Aa == i) & (Bb == j)) | ((Aa == j) & (Bb == i))
            if d.any():
                if ia == 0:
                    yy, xx = np.nonzero(d)
                    bnd.append(np.column_stack([gx[yy, xx] + GRID_M / 2, gy[yy, xx]]))
                else:
                    yy, xx = np.nonzero(d)
                    bnd.append(np.column_stack([gx[yy, xx], gy[yy, xx] + GRID_M / 2]))
        if not bnd:
            continue
        P = np.vstack(bnd)
        if len(P) < 3:
            continue
        ln = _line_from_coeffs(*_fit_line_tls(P), bounds)
        if ln is not None:
            lines.append(ln)
    return lines


def _circ_diff(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _same_surface(f, g):
    """Do these two facets describe one physical plane, or two?"""
    if abs(f["slope_deg"] - g["slope_deg"]) > MERGE_SLOPE_DEG:
        return False
    both_flat = f["slope_deg"] < MERGE_FLAT_DEG and g["slope_deg"] < MERGE_FLAT_DEG
    if not both_flat and _circ_diff(f["aspect_deg"], g["aspect_deg"]) > MERGE_ASPECT_DEG:
        return False
    # Same tilt and bearing still leaves the sawtooth case: parallel faces one
    # bay apart in height. Compare the two planes where they actually meet.
    shared = f["geometry"].intersection(g["geometry"].buffer(0.05))
    if shared.is_empty:
        return False
    c = shared.centroid
    zf = f["plane_a"] * c.x + f["plane_b"] * c.y + f["plane_c"]
    zg = g["plane_a"] * c.x + g["plane_b"] * c.y + g["plane_c"]
    return abs(zf - zg) <= MERGE_STEP_M


def merge_coplanar(facets, pts):
    """Repeatedly fuse touching facets that are the same surface."""
    changed = True
    while changed and len(facets) > 1:
        changed = False
        for i in range(len(facets)):
            for j in range(i + 1, len(facets)):
                f, g = facets[i], facets[j]
                if not f["geometry"].buffer(0.05).intersects(g["geometry"]):
                    continue
                if not _same_surface(f, g):
                    continue
                geom = unary_union([f["geometry"], g["geometry"]]).buffer(0.02).buffer(-0.02)
                if geom.geom_type != "Polygon":
                    continue
                inside = pts[shapely.vectorized.contains(geom, pts[:, 0], pts[:, 1])]
                if len(inside) < 8:
                    continue
                plane = fit_plane(inside)
                # Does one plane still describe both faces? If not, they are
                # genuinely different surfaces however similar their angles.
                if (np.abs(residuals(plane, inside)) < RANSAC_TOL_M).mean() < MERGE_MIN_INLIER_FRAC:
                    continue
                slope, aspect = plane_slope_aspect(plane)
                facets[i] = {**f, "geometry": geom, "area_m2": float(geom.area),
                             "plane_a": float(plane[0]), "plane_b": float(plane[1]),
                             "plane_c": float(plane[2]), "slope_deg": float(slope),
                             "aspect_deg": float(aspect), "point_count": int(len(inside))}
                facets.pop(j)
                changed = True
                break
            if changed:
                break
    return facets


def _cell_votes(cell, planes, pts):
    inside = pts[shapely.vectorized.contains(cell, pts[:, 0], pts[:, 1])]
    if len(inside) < MIN_CELL_PTS:
        return inside, []
    return inside, [int((np.abs(residuals(p, inside)) < RANSAC_TOL_M).sum()) for p in planes]


def refine_mixed_cells(cells, planes, pts, bounds):
    """Split any cell whose own points disagree about which plane it is, along
    the intersection of the two planes competing for it. Each split strictly
    reduces the mixing, so this terminates."""
    for _ in range(REFINE_ROUNDS):
        out, split_any = [], False
        for cell in cells:
            inside, votes = _cell_votes(cell, planes, pts)
            if not votes or sum(votes) == 0:
                out.append(cell)
                continue
            order = np.argsort(votes)[::-1]
            top, second = int(order[0]), int(order[1]) if len(order) > 1 else None
            if second is None or votes[second] < MIN_CELL_PTS \
                    or votes[top] / max(sum(votes), 1) >= MIXED_MAX_SHARE:
                out.append(cell)
                continue
            a1, b1, c1 = planes[top]
            a2, b2, c2 = planes[second]
            ln = _line_from_coeffs(a1 - a2, b1 - b2, c1 - c2, bounds)
            if ln is None:
                out.append(cell)
                continue
            try:
                pieces = [g for g in split(cell, ln).geoms if g.area > 0.5]
            except Exception:
                pieces = []
            if len(pieces) < 2:
                out.append(cell)
                continue
            out.extend(pieces)
            split_any = True
        cells = out
        if not split_any:
            break
    return cells


def reconstruct(building_id, outline, pts, seed=0):
    """Point cloud + surveyed outline -> straight-edged, plane-backed facets."""
    if len(pts) < MIN_PLANE_PTS:
        return []
    rng = np.random.default_rng(seed)
    planes, owner = ransac_planes(pts, rng)
    if not planes:
        return []
    lab, gx, gy = label_raster(outline, pts, owner, planes)
    if lab is None:
        return []
    pairs = adjacent_pairs(lab)
    lines = edge_lines(planes, pairs, lab, gx, gy, outline.bounds)

    clipped = []
    for ln in lines:
        piece = ln.intersection(outline)
        if piece.is_empty:
            continue
        clipped.extend(piece.geoms if piece.geom_type == "MultiLineString" else [piece])
    cells = list(polygonize(unary_union([outline.boundary] + clipped)))
    if not cells:
        cells = [outline]
    cells = refine_mixed_cells(cells, planes, pts, outline.bounds)

    # Each cell goes to the plane its own points support. Cells with too few
    # points to vote inherit from the nearest labelled point instead of being
    # guessed at.
    tree = cKDTree(pts[:, :2])
    claimed = owner >= 0
    ntree = cKDTree(pts[claimed][:, :2]) if claimed.any() else None
    assigned, cell_label = {}, []
    for cell in cells:
        if cell.area < 0.5:
            continue
        idx = tree.query_ball_point(np.array(cell.centroid.coords[0]),
                                    r=math.hypot(*(np.array(cell.bounds[2:]) - np.array(cell.bounds[:2]))) / 2 + 1.0)
        if idx:
            sub = pts[idx]
            inside = shapely.vectorized.contains(cell, sub[:, 0], sub[:, 1])
            sub = sub[inside]
        else:
            sub = pts[:0]
        best, best_n = None, 0
        if len(sub) >= MIN_CELL_PTS:
            for pi, plane in enumerate(planes):
                n = int((np.abs(residuals(plane, sub)) < RANSAC_TOL_M).sum())
                if n > best_n:
                    best, best_n = pi, n
        if best is None and ntree is not None:
            _, nn = ntree.query(np.array(cell.centroid.coords[0]))
            best = int(owner[claimed][nn])
        if best is None:
            continue
        cell_label.append((cell, best, sub))

    # Speckle removal: an isolated cell whose neighbours all say otherwise, and
    # whose own points do not strongly disagree, takes the neighbours' label.
    for _ in range(SMOOTH_ROUNDS):
        moved = 0
        for k, (cell, lab_k, sub) in enumerate(cell_label):
            share = {}
            for m, (other, lab_m, _) in enumerate(cell_label):
                if m == k or lab_m == lab_k:
                    continue
                b = cell.buffer(0.05).intersection(other).area
                if b > 0:
                    share[lab_m] = share.get(lab_m, 0.0) + b
            if not share:
                continue
            cand = max(share, key=share.get)
            if len(sub) < MIN_CELL_PTS:
                cell_label[k] = (cell, cand, sub)
                moved += 1
                continue
            n_own = int((np.abs(residuals(planes[lab_k], sub)) < RANSAC_TOL_M).sum())
            n_cand = int((np.abs(residuals(planes[cand], sub)) < RANSAC_TOL_M).sum())
            if n_cand >= 0.8 * n_own:
                cell_label[k] = (cell, cand, sub)
                moved += 1
        if not moved:
            break
    for cell, lab_k, _ in cell_label:
        assigned.setdefault(lab_k, []).append(cell)

    facets = []
    for pi, cs in assigned.items():
        geom = unary_union(cs)
        for poly in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]):
            if poly.area < MIN_FACET_M2:
                continue
            member = pts[np.abs(residuals(planes[pi], pts)) < RANSAC_TOL_M]
            inside = member[shapely.vectorized.contains(poly, member[:, 0], member[:, 1])] \
                if len(member) else member
            plane = fit_plane(inside) if len(inside) >= 8 else planes[pi]
            slope, aspect = plane_slope_aspect(plane)
            if slope > WALL_SLOPE_DEG:
                continue
            facets.append({
                "building_id": building_id,
                "plane_a": float(plane[0]), "plane_b": float(plane[1]), "plane_c": float(plane[2]),
                "slope_deg": float(slope), "aspect_deg": float(aspect),
                "area_m2": float(poly.area), "point_count": int(len(inside)),
                "geometry": Polygon(poly.exterior, [r for r in poly.interiors]),
            })
    return merge_coplanar(facets, pts)
