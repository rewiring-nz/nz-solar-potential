"""
Per building footprint: extract the DSM patch, split into planar roof
facets, drop facets over config.MAX_ROOF_SLOPE_DEG or too small to hold a
panel.

Method: multi-plane RANSAC directly on the DSM's pixel grid (each valid
pixel inside the footprint treated as an (x, y, z) point). This is the
grid-based variant of the standard LiDAR-roof-segmentation approach from
the literature -- we don't have the raw point cloud locally (only the DSM
raster), so pixel centres stand in for points. At 1m resolution a small
garage roof is only a handful of pixels, which is the real precision
ceiling of this approach; it's fine for the pilot, and swapping in the
raw LAZ point cloud later (higher point density) would be a drop-in
upgrade to `points_from_window` without touching the RANSAC/vectorize code.
"""

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import shapes as rasterio_shapes
from rasterio.mask import mask as rasterio_mask
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely.geometry import MultiPoint, shape as shapely_shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

# RANSAC needs randomness, but a single shared RNG instance makes a building's result depend on
# how many other buildings were processed before it in the same run -- same input, different output
# depending on unrelated history, which is a debugging trap (bit us once: a standalone repro of one
# building disagreed with its result inside a full pipeline run, purely from RNG state drift). Each
# building gets its own RNG seeded from its own id instead, so results are independent of run order.

MIN_FACET_AREA_M2 = 3.0  # below this, can't usefully fit even one setback-shrunk panel
# Vertical residual to count as an inlier. Was 0.15 (~the DSM's raw noise
# floor), which sounds like the "correct" physical value but was far too
# tight in practice: real roofing has enough small-scale texture (seams,
# ribs, snow, minor sensor noise) that a single true flat plane routinely
# failed to pass a 0.15m-tolerance fit as one piece, fragmenting into many
# small, spurious "planes" instead -- exactly the "dozens of tiny facet
# outlines on an obviously simple roof" pattern reported against the live
# map. Raised to 0.35 after directly measuring the tradeoff on a 120-
# building sample: coverage rises 57%->71% and facets/building *drops*
# (3.1->2.7, i.e. less fragmentation, not less precision) between 0.15 and
# 0.35, with no increase in a same-building proxy for "wrongly merged two
# real roof planes into one" (11/120 flagged at both 0.30 and 0.35) --
# that failure mode only shows up past ~0.40, where it climbs to 13-14/120.
RANSAC_DISTANCE_THRESHOLD_M = 0.35
RANSAC_ITERATIONS = 300
RANSAC_MIN_INLIERS = 6  # pixels; below this a "plane" is just noise, not a real facet
RANSAC_SAMPLE_RADIUS_M = 3.0  # max plan-view spread of a 3-point candidate sample -- see ransac_planes
# Silently capped large/complex buildings: a big multi-wing institutional
# roof genuinely needs 15-20+ distinct planes, and this hard cap stopped
# RANSAC after 6, leaving ~40% of one real building's roof area (including
# an entire clean flat section) with no facet, no obstruction check, and
# no panels -- not because it wasn't viable, but because segmentation gave
# up before reaching it. 40 comfortably covers what real buildings in the
# pilot needed (checked: one large complex fully resolved by ~20); RANSAC
# still stops earlier on its own once no significant plane remains, so
# this doesn't slow down the many small/simple buildings at all.
MAX_PLANES_PER_BUILDING = 40


def points_from_window(dsm_array, window_transform, nodata):
    """dsm_array: 2D window clipped to (roughly) one building. Returns
    (points[N,3] in x,y,z world coords, row_idx[N], col_idx[N])."""
    rows, cols = np.where(dsm_array != nodata)
    if len(rows) == 0:
        return np.empty((0, 3)), rows, cols
    xs, ys = rasterio.transform.xy(window_transform, rows, cols)
    zs = dsm_array[rows, cols]
    points = np.column_stack([xs, ys, zs])
    return points, rows, cols


def fit_plane_lstsq(points):
    """points[N,3] -> (a, b, c) minimizing sum((a*x+b*y+c - z)^2)."""
    A = np.column_stack([points[:, 0], points[:, 1], np.ones(len(points))])
    coeffs, *_ = np.linalg.lstsq(A, points[:, 2], rcond=None)
    return coeffs  # a, b, c


def plane_residuals(points, plane):
    a, b, c = plane
    pred = a * points[:, 0] + b * points[:, 1] + c
    return np.abs(pred - points[:, 2])


def ransac_planes(points, rng, distance_threshold=RANSAC_DISTANCE_THRESHOLD_M,
                   iterations=RANSAC_ITERATIONS, min_inliers=RANSAC_MIN_INLIERS,
                   max_planes=MAX_PLANES_PER_BUILDING):
    """Iteratively extract dominant planes. Returns list of (plane, inlier_mask_into_points)."""
    remaining_idx = np.arange(len(points))
    planes = []

    while len(remaining_idx) >= min_inliers and len(planes) < max_planes:
        pts = points[remaining_idx]
        best_inlier_local = None

        if len(pts) < 3:
            break

        # 3-point samples are drawn from a small spatial neighbourhood (see
        # RANSAC_SAMPLE_RADIUS_M below), not from anywhere in the whole
        # point cloud -- found directly on a real hip/pyramid roof (~4m of
        # true elevation change, individual faces around 20-30 deg): fully
        # random sampling let a 3-point sample straddle multiple true faces
        # and construct a spurious near-flat "compromise" plane that, on a
        # roughly symmetric multi-face roof, can rack up MORE inliers within
        # tolerance than any single true face does (many points sit near the
        # roof's average elevation even though they're on four different
        # slopes) -- exactly the "thinks it's all one flat plane" failure
        # reported against a real building. A spatially-local sample can't
        # span two faces of a normal-sized roof, so it can't construct that
        # cross-face compromise plane in the first place.
        tree = cKDTree(pts[:, :2])
        for _ in range(iterations):
            anchor = rng.integers(len(pts))
            neighbor_idx = tree.query_ball_point(pts[anchor, :2], RANSAC_SAMPLE_RADIUS_M)
            if len(neighbor_idx) < 3:
                continue
            sample_idx = rng.choice(neighbor_idx, size=3, replace=False)
            sample = pts[sample_idx]
            # Skip near-degenerate (collinear) samples -- cross product of
            # two edge vectors near zero means no well-defined plane normal.
            v1 = sample[1] - sample[0]
            v2 = sample[2] - sample[0]
            normal = np.cross(v1, v2)
            if np.linalg.norm(normal[:2]) > 1e6 or abs(normal[2]) < 1e-9:
                continue
            try:
                plane = fit_plane_lstsq(sample)
            except np.linalg.LinAlgError:
                continue
            residuals = plane_residuals(pts, plane)
            inlier_local = residuals < distance_threshold
            if best_inlier_local is None or inlier_local.sum() > best_inlier_local.sum():
                best_inlier_local = inlier_local

        if best_inlier_local is None or best_inlier_local.sum() < min_inliers:
            break

        # Refit on all inliers for a stabler plane, then recompute the
        # inlier set once against that refit plane.
        refit_plane = fit_plane_lstsq(pts[best_inlier_local])
        residuals = plane_residuals(pts, refit_plane)
        inlier_local = residuals < distance_threshold
        if inlier_local.sum() < min_inliers:
            break

        global_inlier_idx = remaining_idx[inlier_local]
        planes.append((refit_plane, global_inlier_idx))
        remaining_idx = remaining_idx[~inlier_local]

    return planes


def slope_aspect_from_plane(a, b):
    """z = a*x + b*y + c. Returns (slope_deg, aspect_deg). Aspect is the
    compass bearing (0=N, 90=E, clockwise) the surface faces -- i.e. the
    downhill direction, which is also the direction a mounted panel would
    face. Derivation: downhill vector = -(a, b); bearing = atan2(east, north)."""
    slope_deg = np.degrees(np.arctan(np.hypot(a, b)))
    aspect_deg = np.degrees(np.arctan2(-a, -b)) % 360
    return slope_deg, aspect_deg


RECT_FIT_MIN_FILL_FRACTION = 0.7  # hull area / its minimum-rotated-rectangle area
SHAPE_FIT_TOLERANCE_M = 1.0  # how far past the traced pixel footprint a fitted
# rectangle/hull may reach when snapping to clean edges -- enough to smooth
# 1m-grid staircase noise and small dropout notches, not enough for a
# handful of stray far-flung inlier points (RANSAC noise, or a coincidental
# planar match on a separate, physically unconnected roof wing) to balloon
# the fitted shape across the whole building. Found by direct testing: an
# earlier, unbounded version of this fit let one dominant plane's rectangle
# bleed across and eat several other, physically separate roof wings on a
# real building, leaving them as thin useless slivers.


def component_shape(points_xy, component_mask, window_transform):
    """Reconstruct one connected component's facet boundary geometrically
    from its actual inlier point locations, instead of tracing the raw
    per-pixel mask -- but bounded to stay near where pixels were actually
    claimed.

    Tracing the pixel mask (the original approach) makes the facet boundary
    exactly as noisy as the 1m DSM grid and whatever RANSAC noise excluded
    individual pixels near the edges -- it produces blobby, undersized
    shapes that don't look like the roof plane they represent, and panel
    packing (which aligns to the facet's own minimum-rotated-rectangle)
    inherits that same misalignment. A real roof plane's footprint is
    almost always a simple, mostly-rectangular polygon, so: take the convex
    hull of the inlier points; if it already fills most (>=70%) of its own
    minimum-rotated-rectangle, the "gaps" are just edge noise/small
    dropouts, not a genuine non-rectangular notch, so snap to the clean
    rectangle. Otherwise (a genuinely L-shaped/hipped point cloud) keep the
    hull. Either way, clip the result to the traced pixel footprint buffered
    by SHAPE_FIT_TOLERANCE_M -- the hull/rectangle is fit from point
    *locations* alone, with no idea how sparse or outlier-driven those
    points are, so left unbounded it can reach far past the real plane."""
    if len(points_xy) < 3:
        return None
    hull = MultiPoint(points_xy).convex_hull
    if hull.geom_type != "Polygon" or hull.area <= 0:
        return None  # collinear/degenerate -- not a usable facet shape
    min_rect = hull.minimum_rotated_rectangle
    if min_rect.geom_type == "Polygon" and min_rect.area > 0 and hull.area / min_rect.area >= RECT_FIT_MIN_FILL_FRACTION:
        candidate = min_rect
    else:
        candidate = hull

    traced = [
        shapely_shape(geom)
        for geom, val in rasterio_shapes(component_mask.astype(np.uint8), mask=component_mask, transform=window_transform)
        if val == 1
    ]
    if not traced:
        return None
    bound = unary_union(traced).buffer(SHAPE_FIT_TOLERANCE_M, join_style="mitre", mitre_limit=5)
    bounded = candidate.intersection(bound)
    if bounded.is_empty:
        return None
    if bounded.geom_type == "MultiPolygon":
        bounded = max(bounded.geoms, key=lambda p: p.area)
    elif bounded.geom_type != "Polygon":
        return None
    return bounded


def _dedupe_overlaps(facets, min_area):
    """Facet shapes now come from a fitted rectangle/hull over each plane's
    points (see component_shape) rather than the raw claimed-pixel mask, so
    two facets from different planes can end up overlapping where one
    plane's fitted rectangle bleeds past its actual pixels into a
    neighbour's territory -- pixel-mask tracing couldn't do that (each
    pixel could only belong to one plane), but a geometric fit can.
    Processes largest-first and subtracts already-claimed area from each
    smaller facet, so no roof area (and no panel) is ever double-counted
    across two overlapping facets."""
    ordered = sorted(facets, key=lambda f: -f["area_m2"])
    claimed = None
    result = []
    for f in ordered:
        geom = f["geometry"] if claimed is None else f["geometry"].difference(claimed)
        if not geom.is_empty:
            pieces = list(geom.geoms) if geom.geom_type in ("MultiPolygon", "GeometryCollection") else [geom]
            for piece in pieces:
                if piece.geom_type == "Polygon" and piece.area >= min_area:
                    new_f = dict(f)
                    new_f["geometry"] = piece
                    new_f["area_m2"] = piece.area
                    result.append(new_f)
        claimed = f["geometry"] if claimed is None else claimed.union(f["geometry"])
    return result


MERGE_SLOPE_DIFF_DEG = 5.0
MERGE_ASPECT_DIFF_DEG = 20.0
MERGE_LOW_SLOPE_DEG = 7.0  # below this, aspect is noise (near-flat roof) -- ignore it for merging
MERGE_BUFFER_M = 0.5  # facets within this gap still count as "adjacent" (grid/RANSAC edge noise)


def _circular_diff(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def merge_similar_facets(facets):
    """Two RANSAC passes over the same physical plane (common on large
    near-flat roofs, where residual noise lets a second near-duplicate
    plane pass the inlier threshold) leave behind two adjacent facets with
    near-identical slope/aspect. Merge those back into one."""
    if len(facets) < 2:
        return facets

    n = len(facets)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        for j in range(i + 1, n):
            fi, fj = facets[i], facets[j]
            slope_close = abs(fi["slope_deg"] - fj["slope_deg"]) <= MERGE_SLOPE_DIFF_DEG
            both_flat = fi["slope_deg"] < MERGE_LOW_SLOPE_DEG and fj["slope_deg"] < MERGE_LOW_SLOPE_DEG
            aspect_close = both_flat or _circular_diff(fi["aspect_deg"], fj["aspect_deg"]) <= MERGE_ASPECT_DIFF_DEG
            if not (slope_close and aspect_close):
                continue
            if fi["geometry"].buffer(MERGE_BUFFER_M).intersects(fj["geometry"].buffer(MERGE_BUFFER_M)):
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    merged = []
    for idx_group in groups.values():
        if len(idx_group) == 1:
            merged.append(facets[idx_group[0]])
            continue
        group_facets = [facets[i] for i in idx_group]
        total_pts = sum(f["point_count"] for f in group_facets) or 1
        merged_geom = unary_union([f["geometry"] for f in group_facets])
        if merged_geom.geom_type == "MultiPolygon":
            # The buffer-touches check that grouped these said they were
            # adjacent, but the union came out disconnected anyway (touching
            # only at a single point, or the buffer tolerance let through a
            # near-miss) -- keep the largest part rather than pass a
            # MultiPolygon downstream, which panel_fitting and the renderer
            # both assume never happens for a single facet.
            merged_geom = max(merged_geom.geoms, key=lambda p: p.area)
        a = sum(f["plane_a"] * f["point_count"] for f in group_facets) / total_pts
        b = sum(f["plane_b"] * f["point_count"] for f in group_facets) / total_pts
        c = sum(f["plane_c"] * f["point_count"] for f in group_facets) / total_pts
        slope_deg, aspect_deg = slope_aspect_from_plane(a, b)
        merged.append({
            "building_id": group_facets[0]["building_id"],
            "plane_a": a, "plane_b": b, "plane_c": c,
            "slope_deg": slope_deg,
            "aspect_deg": aspect_deg,
            "area_m2": merged_geom.area,
            "point_count": total_pts,
            "geometry": merged_geom,
        })

    return merged


def segment_building(dsm_ds, building_geom, building_id, ransac_distance_threshold=None, min_facet_area_m2=None):
    """Returns a list of facet dicts for one building footprint (shapely
    geometry, in the DSM's CRS). ransac_distance_threshold/min_facet_area_m2
    override the module defaults -- exposed so a caller (e.g. the live
    parameter-tuning server) can experiment without editing config."""
    try:
        window_array, window_transform = rasterio_mask(
            dsm_ds, [building_geom], crop=True, nodata=dsm_ds.nodata, filled=True
        )
    except ValueError:
        return []  # geometry doesn't overlap the raster at all

    min_facet_area_m2 = MIN_FACET_AREA_M2 if min_facet_area_m2 is None else min_facet_area_m2
    window_array = window_array[0]
    points, rows, cols = points_from_window(window_array, window_transform, dsm_ds.nodata)
    if len(points) < RANSAC_MIN_INLIERS:
        return []

    rng = np.random.default_rng(building_id)
    ransac_kwargs = {} if ransac_distance_threshold is None else {"distance_threshold": ransac_distance_threshold}
    planes = ransac_planes(points, rng, **ransac_kwargs)

    facets = []
    for plane, inlier_idx in planes:
        a, b, c = plane
        slope_deg, aspect_deg = slope_aspect_from_plane(a, b)
        if slope_deg > config.MAX_ROOF_SLOPE_DEG:
            continue

        facet_mask = np.zeros(window_array.shape, dtype=bool)
        facet_mask[rows[inlier_idx], cols[inlier_idx]] = True
        labeled, n_components = ndimage.label(facet_mask)
        point_labels = labeled[rows[inlier_idx], cols[inlier_idx]]

        for label_id in range(1, n_components + 1):
            component_idx = inlier_idx[point_labels == label_id]
            component_mask = labeled == label_id
            polygon = component_shape(points[component_idx, :2], component_mask, window_transform)
            if polygon is None:
                continue

            # The fitted rectangle/hull is built from DSM point locations,
            # which can reach slightly past the true roofline; the building
            # outline is imagery-derived (0.1m) and traces the real edge,
            # so clip back to it.
            polygon = polygon.intersection(building_geom)
            if polygon.is_empty:
                continue
            if polygon.geom_type == "MultiPolygon":
                polygon = max(polygon.geoms, key=lambda p: p.area)
            elif polygon.geom_type not in ("Polygon",):
                continue  # intersection degenerated to a line/point -- not a usable facet
            if polygon.area < min_facet_area_m2:
                continue

            facets.append({
                "building_id": building_id,
                "plane_a": a, "plane_b": b, "plane_c": c,
                "slope_deg": slope_deg,
                "aspect_deg": aspect_deg,
                "area_m2": polygon.area,
                "point_count": len(component_idx),
                "geometry": polygon,
            })

    facets = _dedupe_overlaps(facets, min_facet_area_m2)
    return merge_similar_facets(facets)
