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
from shapely.geometry import shape as shapely_shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

# RANSAC needs randomness, but a single shared RNG instance makes a building's result depend on
# how many other buildings were processed before it in the same run -- same input, different output
# depending on unrelated history, which is a debugging trap (bit us once: a standalone repro of one
# building disagreed with its result inside a full pipeline run, purely from RNG state drift). Each
# building gets its own RNG seeded from its own id instead, so results are independent of run order.

MIN_FACET_AREA_M2 = 3.0  # below this, can't usefully fit even one setback-shrunk panel
RANSAC_DISTANCE_THRESHOLD_M = 0.15  # vertical residual to count as an inlier; ~DSM noise floor
RANSAC_ITERATIONS = 300
RANSAC_MIN_INLIERS = 6  # pixels; below this a "plane" is just noise, not a real facet
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

        for _ in range(iterations):
            sample_idx = rng.choice(len(pts), size=3, replace=False)
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


def label_raster_to_polygons(mask_2d, window_transform, crs):
    """Vectorize a boolean facet mask into one Polygon per spatially
    connected component, each paired with its pixel count.

    RANSAC has no spatial-contiguity constraint -- a single plane fit
    routinely claims pixels from more than one physically separate part
    of a complex roof (both ends of a hip, a chunk beyond a dormer, a
    separate wing at the same pitch/orientation). An earlier version of
    this function vectorized the whole mask and kept only the single
    largest connected piece (`max(polygons, key=area)`), silently
    discarding every other real, correctly plane-fit chunk -- on one
    building this alone dropped ~52% of already-claimed roof area before
    the building-outline clip even ran, and produced exactly the "large
    obviously-usable roof sections with zero facets" pattern reported
    against the live map. Each component is now returned as its own
    facet candidate instead."""
    labeled, n = ndimage.label(mask_2d)
    results = []
    for label_id in range(1, n + 1):
        component_mask = labeled == label_id
        pixel_count = int(component_mask.sum())
        polygons = [
            shapely_shape(geom)
            for geom, val in rasterio_shapes(component_mask.astype(np.uint8), mask=component_mask, transform=window_transform)
            if val == 1
        ]
        if not polygons:
            continue
        results.append((max(polygons, key=lambda p: p.area), pixel_count))
    return results


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
        components = label_raster_to_polygons(facet_mask, window_transform, dsm_ds.crs)

        for polygon, pixel_count in components:
            # The DSM is 1m grid -- vectorizing it gives a blocky, staircase
            # edge that can overhang the true roofline by up to ~0.7m diagonal.
            # The building outline is imagery-derived (0.1m) and traces the
            # real edge, so clip back to it: snaps facet boundaries to the
            # accurate roofline instead of the DSM pixel grid.
            polygon = polygon.intersection(building_geom)
            if polygon.is_empty:
                continue
            if polygon.geom_type == "MultiPolygon":
                polygon = max(polygon.geoms, key=lambda p: p.area)
            elif polygon.geom_type not in ("Polygon",):
                continue  # intersection degenerated to a line/point -- not a usable facet
            if polygon.area < min_facet_area_m2:
                continue

            # Real roof ridges/valleys/hips are straight lines; the boundary
            # here still staircases wherever it came from the raw DSM pixels
            # (i.e. everywhere except where it got clipped to the building
            # outline above). That jaggedness both under-fills panels right up
            # against a straight real edge and confuses obstruction detection
            # (blended/mixed colour right on the staircase reads as anomalous).
            # Smooth it: morphological closing bridges the single-pixel-deep
            # notches, then simplify collapses the remaining staircase into a
            # small number of straight segments approximating the true line.
            smoothed = polygon.buffer(0.6, join_style="mitre", mitre_limit=5).buffer(
                -0.6, join_style="mitre", mitre_limit=5
            ).simplify(0.3, preserve_topology=True)
            # Re-clip to the building outline -- the closing buffer can push
            # the smoothed edge slightly outside it again.
            smoothed = smoothed.intersection(building_geom)
            if not smoothed.is_empty and smoothed.geom_type == "MultiPolygon":
                smoothed = max(smoothed.geoms, key=lambda p: p.area)
            if not smoothed.is_empty and smoothed.geom_type == "Polygon" and smoothed.area >= MIN_FACET_AREA_M2:
                polygon = smoothed

            facets.append({
                "building_id": building_id,
                "plane_a": a, "plane_b": b, "plane_c": c,
                "slope_deg": slope_deg,
                "aspect_deg": aspect_deg,
                "area_m2": polygon.area,
                "point_count": pixel_count,
                "geometry": polygon,
            })

    return merge_similar_facets(facets)
