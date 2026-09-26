"""Give a lower roof level its own face instead of carving it as an obstruction.

WHAT WAS WRONG. When segmentation stretches one face across two roof levels,
the lower level's returns sit well below that face's plane, and the sunken
detector in obstruction_detection carves them out as an object. 9 Marine
Parade (#5370328): one face spanned the main roof and a flat lower roof, and
168 m2 of its 193 m2 of "obstructions" were that lower roof -- two patches of
94 m2 and 27 m2 lying 0.7 m below the plane, flat (0.2 and 4 degrees), their
own returns on one plane (inlier 0.70 and 0.68). Clear roof, marked as not
roof.

WHAT THIS DOES. It finds the same sunken regions the detector would
(`_sunken_regions`, so the two can never disagree about what is sunken) and
asks of each whether it is a ROOF LEVEL: wide, big enough to rack, and its
own returns on a clean plane of roof pitch. A region that is gets cut out of
its host face along straight edges and becomes a face with its own plane.
Everything else -- the walls between levels (narrow, near vertical, returns on
no plane), a deck, clutter -- is left for the sunken detector to carve as
before.

WHY WIDTH SEPARATES A DECK. It is the rule drop_balcony_levels already
uses: a balcony or deck is a strip along a facade, the storey below is not
(roof_segmentation.BALCONY_MAX_DEPTH_M, 4 m). A region narrower than that
stays an obstruction -- UNLESS IT LOOKS LIKE THE ROOF BESIDE IT. A deck is
timber, tile or paving with furniture on it; a lower strip of roof is the
same roofing as the face it drops from. #4721836 has a 54 m2 strip 0.8 m
below its flat roof and #4728664 one of 29 m2 at 1.1 m, both roofing in the
photo and both panelled by the 22 Sep build, both carved once the sunken
detector ran on every face. A strip wide enough for a panel row, big enough
to matter, on a clean plane, whose colour in the survey-year photo matches
its host face's, is a level. Drawn roofs are not touched; the markup governs
them. SOLAR_LEVEL_SPLIT=0 turns this off.
"""

import os

import numpy as np
from shapely.ops import unary_union

MIN_LEVEL_M2 = 8.0          # smaller than ~4 panels is not worth its own face
MIN_INLIER = 0.60           # share of the region's returns within INLIER_M of its own plane
INLIER_M = 0.15
MIN_POINTS = 30
NARROW_MIN_WIDTH_M = 1.8    # a narrow level must still take a row of panels
NARROW_MIN_M2 = 15.0
NARROW_MAX_RGB_DIFF = 25.0  # median colour of strip vs host face, 0-255 per channel
SLIVER_M2 = 1.0             # a leftover piece of the host smaller than this merges away
STATS = {"roofs": 0, "levels": 0, "m2": 0.0}


def _polys(g):
    if g.is_empty:
        return []
    return [g] if g.geom_type == "Polygon" else [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]


def _width(poly):
    cs = list(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    e = [np.hypot(cs[(k + 1) % 4][0] - cs[k][0], cs[(k + 1) % 4][1] - cs[k][1]) for k in range(2)]
    return min(e)


def _median_rgb(photo, poly):
    """Median RGB of the photo inside `poly`, or None."""
    try:
        import rasterio.features
        from rasterio.windows import from_bounds
        w = from_bounds(*poly.bounds, photo.transform).round_offsets().round_lengths()
        if w.width < 2 or w.height < 2:
            return None
        a = photo.read([1, 2, 3], window=w)
        m = rasterio.features.geometry_mask([poly], out_shape=a.shape[1:],
                                            transform=photo.window_transform(w), invert=True)
        if m.sum() < 20:
            return None
        return np.median(a[:, m], axis=1).astype(float)
    except Exception:
        return None


def _looks_like_host(region, host, photo):
    """True when the strip's colour matches the host face around it."""
    if photo is None:
        return False
    inner = region.buffer(-0.3)
    near = host.difference(region.buffer(0.5)).intersection(region.buffer(4.0))
    if inner.is_empty or near.is_empty:
        return False
    a, b = _median_rgb(photo, inner), _median_rgb(photo, near)
    if a is None or b is None:
        return False
    return float(np.max(np.abs(a - b))) <= NARROW_MAX_RGB_DIFF


def _wide_enough(poly, host, photo):
    w = _width(poly)
    if w > _balcony_depth():
        return True
    return (w >= NARROW_MIN_WIDTH_M and poly.area >= NARROW_MIN_M2
            and _looks_like_host(poly, host, photo))


def _balcony_depth():
    from src.roof_segmentation import BALCONY_MAX_DEPTH_M
    return BALCONY_MAX_DEPTH_M


def _level(region, host, pc_source, footprint, photo=None):
    """(polygon, plane, slope, aspect) when `region` is a roof level, else None."""
    import shapely
    import config
    from src.roof_partition import _fit_plane_robust, _slope_aspect, _regularise_machine_face
    if region.area < MIN_LEVEL_M2 or not _wide_enough(region, host, photo):
        return None
    pts = pc_source.points_in_bbox(*region.bounds, building_only=True)
    if pts is None or len(pts) < MIN_POINTS:
        return None
    q = pts[shapely.contains_xy(region, pts[:, 0], pts[:, 1])]
    if len(q) < MIN_POINTS:
        return None
    plane = _fit_plane_robust(q)
    if plane is None:
        return None
    res = np.abs(q[:, 2] - (plane[0] * q[:, 0] + plane[1] * q[:, 1] + plane[2]))
    if float((res < INLIER_M).mean()) < MIN_INLIER:
        return None
    slope, aspect = _slope_aspect(plane)
    if slope > config.MAX_ROOF_SLOPE_DEG:
        return None
    # straight by construction: the region arrives as a union of grid cells
    reg = _regularise_machine_face(region, footprint)
    poly = reg[0] if reg else region.simplify(0.5)
    poly = poly.intersection(host)
    parts = _polys(poly)
    if not parts:
        return None
    poly = max(parts, key=lambda p: p.area)
    if poly.area < MIN_LEVEL_M2 or not _wide_enough(poly, host, photo):
        return None
    return poly, plane, slope, aspect


def split_lower_levels(facets, pc_source, dsm=None, photo=None):
    """Return facets with each lower roof level cut out as its own face."""
    if os.environ.get("SOLAR_LEVEL_SPLIT", "1") == "0":
        return facets
    if not facets or pc_source is None or any(f.get("from_labels") for f in facets):
        return facets
    need = ("plane_a", "plane_b", "plane_c")
    if any(k not in f for f in facets for k in need):
        return facets
    from src.obstruction_detection import _sunken_regions
    out, changed = [], False
    try:
        # inside the guard: GEOS rejects some face sets as a union
        # (#5371034, 25 Sep, "unable to assign free hole to a shell")
        footprint = unary_union([f["geometry"] for f in facets])
        for f in facets:
            plane = (f["plane_a"], f["plane_b"], f["plane_c"])
            host = f["geometry"]
            levels = []
            for region in _sunken_regions(pc_source, host, plane):
                for part in _polys(region):
                    lv = _level(part, host, pc_source, footprint, photo)
                    if lv is None:
                        continue
                    poly, lp, slope, aspect = lv
                    host = host.difference(poly)
                    levels.append(dict(f, geometry=poly, plane_a=lp[0], plane_b=lp[1], plane_c=lp[2],
                                       slope_deg=slope, aspect_deg=aspect, area_m2=poly.area,
                                       lower_level=True))
            if not levels:
                out.append(f)
                continue
            changed = True
            pieces = sorted(_polys(host), key=lambda p: -p.area)
            keep = [p for p in pieces if p.area >= SLIVER_M2]
            for p in keep:
                out.append(dict(f, geometry=p, area_m2=p.area))
            out.extend(levels)
            STATS["levels"] += len(levels)
            STATS["m2"] += sum(lv["geometry"].area for lv in levels)
    except Exception:
        return facets
    if changed:
        STATS["roofs"] += 1
        return out
    return facets
