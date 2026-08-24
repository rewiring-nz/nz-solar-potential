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
from scipy import ndimage
from shapely.geometry import Polygon
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

RASTER_RESOLUTION_M = 0.1  # occupancy grid cell size in surface space; 10 cells/m
OFFSET_STEPS = 10  # vertical row-start offsets tried per orientation (columns are scanned exhaustively, see _pack_orientation)


FLAT_SLOPE_DEG = 10.0        # below this, slope direction barely constrains racking
FACET_RECTANGULARITY_MIN = 0.7  # facet area / its own bounding rectangle's area


def _edge_aligned_axes(facet_polygon, aspect_deg, slope_deg=None, building_polygon=None):
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

    # On a near-flat roof there is no slope direction to rack against, and
    # the FACET polygon is a hull-derived blob whose minimum rectangle can
    # sit at any angle -- which is why flat-roof rows came out skew to the
    # parapets. The building outline is crisp and rectilinear, so use it as
    # the reference there. Pitched roofs keep using their own facet edges
    # (eave/ridge lines), which is the correct reference for them.
    # Only override when the facet's own shape is an unreliable guide: a
    # low-slope facet whose outline is blobby rather than rectangular (the
    # hull of a segmented flat roof). A clean rectangular facet already
    # agrees with the building and keeps its own edges; a pitched roof always
    # does, since its eave/ridge lines are the correct reference.
    reference = facet_polygon
    if building_polygon is not None and slope_deg is not None and slope_deg < FLAT_SLOPE_DEG:
        try:
            rect = facet_polygon.minimum_rotated_rectangle.area
            blobby = rect > 0 and (facet_polygon.area / rect) < FACET_RECTANGULARITY_MIN
        except Exception:
            blobby = False
        if blobby:
            reference = building_polygon

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mrr = reference.minimum_rotated_rectangle
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


ALIGN_LOSS_TOLERANCE = 0.05  # column-aligned packing is preferred unless it fits more than
# max(1, this fraction) fewer panels than the free per-row scan -- real installers rack rows
# with columns lined up, so a small capacity cost buys a much more realistic layout, but a
# jagged/angled facet edge where rigid columns strand serious usable area still falls back


def _pack_orientation(occupancy, res, w, h, offset_steps=OFFSET_STEPS):
    """occupancy: boolean grid, True = usable. w, h in metres (grid cells).

    Two packing strategies, best-of:
    1. Column-aligned grid (preferred): panels sit at a shared column pitch
       across every row -- every column phase is tried, so the grid snaps to
       whichever registration fits the facet best. This is how real arrays
       are racked: rows with their vertical edges lined up, a blocked slot
       skipped rather than the whole row sliding sideways.
    2. Free per-row scan (fallback): each row-band independently scans every
       column start, packing maximum panels at the cost of staggered,
       unrealistic column seams -- kept only for facets where rigid columns
       genuinely strand usable area (see ALIGN_LOSS_TOLERANCE).
    A handful of vertical (row) start offsets are tried for both, since where
    the first row-band starts can gain or lose a whole extra row lower down."""
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

    row_offsets = np.linspace(0, h_cells, offset_steps, endpoint=False, dtype=int)

    best_aligned = []
    for r_off in row_offsets:
        for c_off in range(w_cells):
            placed = []
            r0 = r_off
            while r0 + h_cells <= rows:
                c0 = c_off
                while c0 + w_cells <= cols:
                    if rect_fully_occupied(r0, r0 + h_cells, c0, c0 + w_cells):
                        placed.append((r0, c0, r0 + h_cells, c0 + w_cells))
                    c0 += w_cells  # always step by the grid pitch -- columns stay aligned
                r0 += h_cells
            if len(placed) > len(best_aligned):
                best_aligned = placed

    best_free = []
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
        if len(placed) > len(best_free):
            best_free = placed

    allowed_loss = max(1, int(np.ceil(ALIGN_LOSS_TOLERANCE * len(best_free))))
    best = best_aligned if len(best_aligned) >= len(best_free) - allowed_loss else best_free
    return best, w_cells, h_cells


def fit_panels_on_facet(facet, panel_width=config.PANEL_WIDTH_M, panel_height=config.PANEL_HEIGHT_M,
                         setback=config.PANEL_EDGE_SETBACK_M, resolution=RASTER_RESOLUTION_M,
                         obstructions=None, sibling_facets=None, ridge_setback=config.RIDGE_SETBACK_M,
                         fallback_setback=config.PANEL_EDGE_SETBACK_FALLBACK_M):
    """Returns list of panel dicts: {geometry (world XY Polygon), facet_id fields}.
    obstructions: optional list of world-XY Polygons (e.g. from
    obstruction_detection.detect_obstructions) to exclude from the usable
    area -- subtracted before the setback buffer, in plan-view world
    coordinates, before anything gets unrolled into surface space.
    sibling_facets: optional list of this same building's *other* facet
    dicts -- wherever this facet's boundary is shared with one of them (a
    real ridge/hip/valley, not the roof's own outer edge), an extra
    ridge_setback clearance is eroded on top of the ordinary edge setback,
    so panels visibly stop short of a real plane change instead of
    butting flush against the next facet's grid with no gap.
    fallback_setback: if the primary `setback` leaves a facet fitting zero
    panels, retried once with this smaller value -- keeps the default
    generous edge clearance for ordinary-width facets without starving a
    genuinely narrow one down to zero, without needing one uniform setback
    to serve both cases."""
    geom = facet["geometry"]
    aspect_deg, slope_deg = facet["aspect_deg"], facet["slope_deg"]

    if obstructions:
        geom = geom.difference(unary_union(obstructions))
        if geom.is_empty:
            return []

    if sibling_facets:
        neighbour_buffer = unary_union([f["geometry"].buffer(ridge_setback) for f in sibling_facets])
        geom = geom.difference(neighbour_buffer)
        if geom.is_empty:
            return []

    origin = (facet["geometry"].centroid.x, facet["geometry"].centroid.y)
    u_hat, v_hat = _edge_aligned_axes(facet["geometry"], aspect_deg, slope_deg,
                                       facet.get("building_geometry"))
    to_surface, to_world = _surface_transform(u_hat, v_hat, slope_deg, origin)

    surface_poly = shapely_transform(lambda x, y, z=None: to_surface(x, y), geom)

    panels = _pack_surface_poly(surface_poly, setback, panel_width, panel_height, resolution, to_world, facet)
    if not panels and fallback_setback < setback:
        panels = _pack_surface_poly(surface_poly, fallback_setback, panel_width, panel_height,
                                     resolution, to_world, facet)
    return panels


def _pack_surface_poly(surface_poly, setback, panel_width, panel_height, resolution, to_world, facet):
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
        # Distance (metres) from each usable cell to the nearest excluded one (an obstruction,
        # a ridge/edge setback, the facet boundary) -- used below as a per-panel "confidence"
        # score for the density slider: a panel comfortably in the middle of a big clean area
        # scores higher than one hugging right up against an exclusion zone.
        clearance = ndimage.distance_transform_edt(occupancy) * resolution

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
                "facet_aspect_deg": facet["aspect_deg"],
                "facet_slope_deg": facet["slope_deg"],
                "geometry": panel_poly,
                "area_m2": panel_width * panel_height,  # true panel area, not plan-view (foreshortened) area
                "clearance_m": float(clearance[r0:r1, c0:c1].min()),
                # Placement sequence within this facet (row-major across parts) -- the density
                # filter fills in this order so a partial layout is contiguous rows, like a real
                # staged install, not a scatter of individually-scored panels. facet_key groups
                # a facet's panels through the flat cross-facet sort even when two facets share
                # an identical binned POA (common: two parallel strips of the same roof plane),
                # which would otherwise interleave their per-facet order sequences.
                "order": len(panels),
                "facet_key": id(facet),
            })

    return panels


MAIN_ARRAY_MIN_PANELS = 10  # straggler banding only applies when the building's largest
# array is at least this big: a "big commercial main array" exists. Below it (residential),
# a couple of 2-panel blocks IS the install -- never banded (direct user feedback).
STRAGGLER_RANK_FLOOR = 80  # stragglers rank 81..100: the 80% default density shows exactly
# the arrays an installer would quote; sliding past 80 progressively adds the extras.
MINOR_ARRAY_MIN_PANELS = 4  # a straggler group smaller than this is dropped (see below) --
# roughly the smallest string a real installer bothers mounting and wiring separately
MINOR_ARRAY_MIN_FRACTION = 0.25  # ...unless it's still a meaningful share of the building's
# largest array, which keeps legitimately tiny roofs (a 2-3 panel cottage) fully intact


def drop_minor_arrays(facet_panels):
    """facet_panels: list of per-facet panel lists for ONE building. Returns
    the same structure with straggler groups emptied out.

    Real installers concentrate on the good contiguous areas; a couple of
    lone panels on a far corner of the roof, while the main array sits
    elsewhere, is visual noise and not how systems get quoted or built
    (direct user feedback, matching what the Brisbane real-installation
    survey showed: installs are one or two compact arrays, not confetti).
    A facet's group is dropped when it's both small in absolute terms
    (< MINOR_ARRAY_MIN_PANELS) and small relative to the building's largest
    group (< MINOR_ARRAY_MIN_FRACTION of it) -- the relative test is what
    protects a genuinely small roof whose "largest array" is itself 2-3
    panels: there, 2 panels IS the install, not a straggler."""
    # Softened (user feedback, bug-doc cycle 22 Aug): stragglers are no longer
    # DELETED -- they're tagged, and assign_fill_ranks banishes them to fill
    # ranks above STRAGGLER_RANK_FLOOR. The 80% default density therefore
    # shows main arrays only, while 100% still shows every feasible panel.
    # Banding only happens when a big main array exists (>= MAIN_ARRAY_MIN_PANELS):
    # on a small residential roof, scattered 2-panel blocks ARE the install.
    if not facet_panels:
        return facet_panels
    largest = max(len(panels) for panels in facet_panels)
    if largest >= MAIN_ARRAY_MIN_PANELS:
        for panels in facet_panels:
            n = len(panels)
            if 0 < n < max(MINOR_ARRAY_MIN_PANELS, MINOR_ARRAY_MIN_FRACTION * largest):
                for panel in panels:
                    panel["straggler"] = True
    return facet_panels


def _erosion_order(panels, poa_key):
    """Fill order for the density slider, built by reverse erosion: strip the
    WORST panel from the full layout repeatedly, then reverse that sequence.
    Worst = least sunny, then closest to the array's edge, then furthest from
    the surviving cluster's centre.

    Why: filling row-major peels row-by-row, so a reduced system can end up a
    thin strip hugging a parapet. A real small install on a big roof is a
    compact block in the sunniest deep part of the roof (Josh's spec). The
    edge term is normalised by the building's own array extent, so on a small
    house -- where every panel is near an edge -- it vanishes and placement
    stays realistic rather than being pushed artificially inward.
    """
    if len(panels) <= 2:
        return sorted(panels, key=lambda p: (-p[poa_key], p["facet_key"], p["order"]))

    pts = np.array([[p["geometry"].centroid.x, p["geometry"].centroid.y] for p in panels])
    span = max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]))
    if span < 1e-6:
        return sorted(panels, key=lambda p: (-p[poa_key], p["facet_key"], p["order"]))

    poa = np.array([p[poa_key] for p in panels], dtype=float)
    poa_norm = (poa - poa.min()) / (np.ptp(poa) or 1.0)

    # Iterative erosion is O(n^2); on the biggest roofs (the airport fits
    # ~6,000 panels) that is minutes per building across 15k buildings. Above
    # this size, score once against the whole array's centre and sort -- same
    # compact-core behaviour, O(n log n).
    if len(panels) > 600:
        centre = pts.mean(axis=0)
        d = np.linalg.norm(pts - centre, axis=1)
        edginess = d / (d.max() or 1.0)
        score = poa_norm - 0.55 * edginess
        order = np.argsort(-score)
        return [panels[i] for i in order]

    alive = np.ones(len(panels), dtype=bool)
    removal = []
    tree_pts = pts
    for _ in range(len(panels) - 1):
        idx = np.flatnonzero(alive)
        live = tree_pts[idx]
        centre = live.mean(axis=0)
        # distance to the live cluster's edge, approximated by how far each
        # panel sits from the centre relative to the cluster's own reach
        d = np.linalg.norm(live - centre, axis=1)
        reach = d.max() or 1.0
        edginess = d / reach                     # 0 centre .. 1 rim
        score = poa_norm[idx] - 0.55 * edginess  # higher = keep longer
        worst_local = int(np.argmin(score))
        worst = int(idx[worst_local])
        removal.append(worst)
        alive[worst] = False
    removal.append(int(np.flatnonzero(alive)[0]))
    # reversed removal = fill order (last removed is filled first)
    return [panels[i] for i in reversed(removal)]


def assign_fill_ranks(panels, poa_key="poa_kwh_m2_yr"):
    """Writes p["fill_rank"] (1..100, integer percentile) onto every panel of
    ONE building, following exactly apply_panel_density's fill order
    (sunniest facet first, then row-major within the facet). The frontend
    filters panels to fill_rank <= density% client-side, which is what makes
    the density slider work on the static deployed site with no server."""
    if not panels:
        return panels
    main = _erosion_order([p for p in panels if not p.get("straggler")], poa_key)
    extras = sorted((p for p in panels if p.get("straggler")),
                    key=lambda p: (-p[poa_key], p["facet_key"], p["order"]))
    # Main arrays occupy ranks 1..STRAGGLER_RANK_FLOOR, stragglers the band
    # above -- guaranteeing the default density cut excludes exactly the
    # stragglers regardless of their share of the building's panels.
    for i, p in enumerate(main):
        p["fill_rank"] = int(np.ceil((i + 1) / len(main) * STRAGGLER_RANK_FLOOR))
    for j, p in enumerate(extras):
        p["fill_rank"] = STRAGGLER_RANK_FLOOR + int(np.ceil((j + 1) / len(extras) * (100 - STRAGGLER_RANK_FLOOR)))
    return panels


def apply_panel_density(panels, density_pct, poa_key="poa_kwh_m2_yr"):
    """Keeps only the top density_pct% of panels across a building's *whole*
    panel list (spanning every facet), ranked sunniest-facet-first and,
    within a facet, in row-major placement order -- so a partial layout is
    the sunniest facet filling up contiguously, row by row, the way a real
    staged install grows, rather than a scatter of individually-scored
    panels (the original clearance-ranked version looked exactly like that
    scatter). density_pct=100 returns every panel unchanged --
    fit_panels_on_facet's own output is already "every feasible panel", so
    this only ever removes panels, never adds ones that placement itself
    ruled out (an obstruction, an edge, a roof join) -- density controls
    *how much* of the feasible area is used, not a relaxation of what
    counts as feasible in the first place. Each panel dict must carry
    poa_key (annual POA irradiance for its own facet's slope/aspect, e.g.
    from SolarModel.annual_poa_kwh_per_m2) plus the order and facet_key
    fields fit_panels_on_facet already attaches; facet_key keeps a facet's
    panels grouped through the sort when two facets share an identical
    binned POA."""
    if density_pct >= 100 or not panels:
        return panels
    density_pct = max(0.0, density_pct)
    ranked = sorted(panels, key=lambda p: (-p[poa_key], p["facet_key"], p["order"]))
    keep_n = int(round(len(ranked) * density_pct / 100))
    return ranked[:keep_n]
