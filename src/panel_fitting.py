"""
Per roof facet: fit the maximum number of 1x2m panels, respecting an edge
setback.

The key subtlety: a facet's polygon (from roof_segmentation) is in
plan-view (map) coordinates, but a panel lying flush on a tilted roof
covers *more* roof surface than its plan-view footprint suggests -- the
dimension running up/down the slope is foreshortened in plan view by
cos(slope). Packing panels directly against the plan-view polygon would
under- or over-fit real panels.

Fix: "unroll" the facet into its own 2D on-surface coordinate frame
(u = along the ridge/contour direction, unaffected by tilt; v = up the
slope, plan-view length scaled by 1/cos(slope) to recover true surface
length), pack real 1x2m rectangles there, then map the fitted panel
corners back to plan-view world coordinates for output/mapping.

Panels are packed in uniform aligned rows (the way real installations are
racked), not general 2D bin-packing -- a handful of row/column start
offsets are tried per orientation (portrait/landscape) and the
best-fitting configuration is kept. This will sometimes miss a panel that
true irregular bin-packing could squeeze in; documented approximation,
not hidden.
"""

import sys
from pathlib import Path

import warnings

import numpy as np
from affine import Affine
from rasterio.features import rasterize
from shapely.geometry import Polygon
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

RASTER_RESOLUTION_M = 0.1  # occupancy grid cell size in surface space; 10 cells/m
OFFSET_STEPS = 10  # vertical row-start offsets tried per orientation (columns are scanned exhaustively, see _pack_orientation)


def _edge_aligned_axes(facet_polygon, aspect_deg):
    """Real installers rack panels parallel to the roof edge, not to
    whatever direction the RANSAC-fit plane's aspect happens to point --
    and the fitted aspect can be off by a few degrees from the facet's
    actual eave/ridge line (segmentation noise). This finds that real
    edge direction via the polygon's minimum-rotated-rectangle (its two
    edges are, for a roughly rectangular/parallelogram roof facet, a
    good estimate of the true eave-parallel and slope-parallel
    directions) and uses that for the packing axes instead of raw aspect.
    Falls back to the pure-aspect axes if the polygon is degenerate.
    Returns (u_hat, v_hat) unit vectors in world (east, north)."""
    theta = np.radians(aspect_deg)
    fallback_v = np.array([np.sin(theta), np.cos(theta)])
    fallback_u = np.array([np.cos(theta), -np.sin(theta)])

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mrr = facet_polygon.minimum_rotated_rectangle
        coords = list(mrr.exterior.coords)
        if len(coords) < 4:
            return fallback_u, fallback_v
        edge1 = np.array(coords[1]) - np.array(coords[0])
        edge2 = np.array(coords[2]) - np.array(coords[1])
    except Exception:
        return fallback_u, fallback_v

    if (not np.all(np.isfinite(edge1)) or not np.all(np.isfinite(edge2))
            or np.linalg.norm(edge1) < 1e-6 or np.linalg.norm(edge2) < 1e-6):
        return fallback_u, fallback_v
    edge1, edge2 = edge1 / np.linalg.norm(edge1), edge2 / np.linalg.norm(edge2)

    # Pick whichever of the two perpendicular edge directions best matches
    # the aspect-derived contour direction -- that one becomes u_hat (sign
    # doesn't matter for u), the other becomes v_hat, whose sign is then
    # set to point downslope so the foreshortening correction lands on
    # the right axis.
    if abs(np.dot(edge1, fallback_u)) >= abs(np.dot(edge2, fallback_u)):
        u_hat, v_hat = edge1, edge2
    else:
        u_hat, v_hat = edge2, edge1
    if np.dot(v_hat, fallback_v) < 0:
        v_hat = -v_hat
    return u_hat, v_hat


def _surface_transform(u_hat, v_hat, slope_deg, origin):
    """Returns (to_surface, to_world) coordinate-transform functions between
    plan-view world (x, y) and the facet's own (u, v) on-surface frame,
    where u = edge-aligned contour direction, v = true surface distance
    up-slope. u_hat/v_hat: orthonormal world-frame unit vectors from
    _edge_aligned_axes."""
    slope_rad = np.radians(slope_deg)
    cos_slope = max(np.cos(slope_rad), 1e-6)  # guard near-vertical, shouldn't happen post slope-filter
    x0, y0 = origin

    def to_surface(x, y, z=None):
        x, y = np.asarray(x), np.asarray(y)
        dx, dy = x - x0, y - y0
        u = dx * u_hat[0] + dy * u_hat[1]
        v_plan = dx * v_hat[0] + dy * v_hat[1]
        v = v_plan / cos_slope
        return u, v

    def to_world(u, v):
        u, v = np.asarray(u), np.asarray(v)
        v_plan = v * cos_slope
        dx = u * u_hat[0] + v_plan * v_hat[0]
        dy = u * u_hat[1] + v_plan * v_hat[1]
        return x0 + dx, y0 + dy

    return to_surface, to_world


def _pack_orientation(occupancy, res, w, h, offset_steps=OFFSET_STEPS):
    """occupancy: boolean grid, True = usable. w, h in metres (grid cells).
    Within each row-band, scans every column position (not just multiples
    of w_cells from a fixed offset) so irregular/angled facet edges -- hip
    valleys, jagged DSM-derived boundaries -- don't strand usable space
    between rigidly-gridded candidate slots. A handful of vertical
    (row) start offsets are still tried, since where the first row-band
    starts can itself gain or lose a whole extra row lower down."""
    rows, cols = occupancy.shape
    w_cells, h_cells = max(1, round(w / res)), max(1, round(h / res))
    if w_cells > cols or h_cells > rows:
        return []

    # Precompute a summed-area table so any rectangle's "all occupied?"
    # check is O(1) instead of O(w_cells*h_cells) -- matters once this runs
    # across thousands of facets.
    sat = np.zeros((rows + 1, cols + 1), dtype=np.int32)
    sat[1:, 1:] = np.cumsum(np.cumsum(occupancy.astype(np.int32), axis=0), axis=1)

    def rect_fully_occupied(r0, r1, c0, c1):
        total = sat[r1, c1] - sat[r0, c1] - sat[r1, c0] + sat[r0, c0]
        return total == (r1 - r0) * (c1 - c0)

    best = []
    row_offsets = np.linspace(0, h_cells, offset_steps, endpoint=False, dtype=int)

    for r_off in row_offsets:
        placed = []
        r0 = r_off
        while r0 + h_cells <= rows:
            c0 = 0
            while c0 + w_cells <= cols:
                if rect_fully_occupied(r0, r0 + h_cells, c0, c0 + w_cells):
                    placed.append((r0, c0, r0 + h_cells, c0 + w_cells))
                    c0 += w_cells  # jump past the panel just placed
                else:
                    c0 += 1  # fine-grained search for the next valid start in this row
            r0 += h_cells
        if len(placed) > len(best):
            best = placed

    return best, w_cells, h_cells


def fit_panels_on_facet(facet, panel_width=config.PANEL_WIDTH_M, panel_height=config.PANEL_HEIGHT_M,
                         setback=config.PANEL_EDGE_SETBACK_M, resolution=RASTER_RESOLUTION_M,
                         obstructions=None):
    """Returns list of panel dicts: {geometry (world XY Polygon), facet_id fields}.
    obstructions: optional list of world-XY Polygons (e.g. from
    obstruction_detection.detect_obstructions) to exclude from the usable
    area -- subtracted before the setback buffer, in plan-view world
    coordinates, before anything gets unrolled into surface space."""
    geom = facet["geometry"]
    aspect_deg, slope_deg = facet["aspect_deg"], facet["slope_deg"]

    if obstructions:
        geom = geom.difference(unary_union(obstructions))
        if geom.is_empty:
            return []

    origin = (facet["geometry"].centroid.x, facet["geometry"].centroid.y)
    u_hat, v_hat = _edge_aligned_axes(facet["geometry"], aspect_deg)
    to_surface, to_world = _surface_transform(u_hat, v_hat, slope_deg, origin)

    surface_poly = shapely_transform(lambda x, y, z=None: to_surface(x, y), geom)
    usable = surface_poly.buffer(-setback)
    if usable.is_empty:
        return []

    parts = list(usable.geoms) if usable.geom_type == "MultiPolygon" else [usable]
    panels = []

    for part in parts:
        if part.area < min(panel_width, panel_height) * max(panel_width, panel_height) * 0.9:
            continue
        u_min, v_min, u_max, v_max = part.bounds
        cols = max(1, int(np.ceil((u_max - u_min) / resolution)))
        rows = max(1, int(np.ceil((v_max - v_min) / resolution)))
        transform = Affine(resolution, 0, u_min, 0, resolution, v_min)
        occupancy = rasterize([(part, 1)], out_shape=(rows, cols), transform=transform,
                               fill=0, dtype=np.uint8).astype(bool)

        candidates = []
        for w, h in [(panel_width, panel_height), (panel_height, panel_width)]:
            result = _pack_orientation(occupancy, resolution, w, h)
            if result:
                placed, w_cells, h_cells = result
                candidates.append((placed, w_cells, h_cells))

        if not candidates:
            continue
        placed, w_cells, h_cells = max(candidates, key=lambda c: len(c[0]))

        for r0, c0, r1, c1 in placed:
            u0, v0 = u_min + c0 * resolution, v_min + r0 * resolution
            u1, v1 = u_min + c1 * resolution, v_min + r1 * resolution
            corners_u = [u0, u1, u1, u0]
            corners_v = [v0, v0, v1, v1]
            wx, wy = to_world(corners_u, corners_v)
            panel_poly = Polygon(zip(wx, wy))
            panels.append({
                "building_id": facet["building_id"],
                "facet_aspect_deg": aspect_deg,
                "facet_slope_deg": slope_deg,
                "geometry": panel_poly,
                "area_m2": panel_width * panel_height,  # true panel area, not plan-view (foreshortened) area
            })

    return panels
