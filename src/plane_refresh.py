"""Refit a face's plane when a robust fit explains its returns far better.

WHAT WAS WRONG. Nothing after segmentation checks that a face's plane fits
the face. 17 Church St (#4726056) is a flat roof at 324.0-324.75 m with plant
up to 3 m above it; segmentation handed its 626 m2 face a plane tilted 2.1
degrees, on which 27% of the face's returns lie within 0.3 m. A robust fit of
the same returns (roof_partition._fit_plane_robust) lies at 0.3 degrees and
explains 83%. The bad plane withheld the whole roof (confidence 0.43, below
MIN_ROOF_CONFIDENCE), and any plane that misfits by a metre at one end also
makes the sunken detector carve good roof there.

WHAT THIS DOES. For every machine face with enough returns, fit the robust
plane and keep it when it puts at least MIN_GAIN more of the face's returns
within INLIER_BAND_M. A face whose plane already fits is untouched. Faces from
the markup are skipped: a small drawn face deliberately borrows a
neighbour's plane (build_layout_geojson, "A ROOF JOSH DREW").
SOLAR_PLANE_REFRESH=0 turns this off.
"""

import os

import numpy as np

INLIER_BAND_M = 0.30        # roof_segmentation.PLANARITY_INLIER_BAND_M
MIN_GAIN = 0.15
MIN_POINTS = 30
STATS = {"faces": 0, "refit": 0}


def _inliers(pts, plane):
    r = pts[:, 2] - (plane[0] * pts[:, 0] + plane[1] * pts[:, 1] + plane[2])
    return float((np.abs(r - np.median(r)) < INLIER_BAND_M).mean())


def refresh_planes(facets, pc_source, dsm=None):
    """Return facets with badly fitting machine planes replaced."""
    if os.environ.get("SOLAR_PLANE_REFRESH", "1") == "0" or not facets or pc_source is None:
        return facets
    import config
    from src.roof_partition import _fit_plane_robust, _slope_aspect
    from src.roof_segmentation import _facet_points
    out, changed = [], False
    for f in facets:
        if f.get("from_labels") or any(k not in f for k in ("plane_a", "plane_b", "plane_c")):
            out.append(f)
            continue
        STATS["faces"] += 1
        pts = _facet_points(pc_source, f["geometry"])
        if len(pts) < MIN_POINTS:
            out.append(f)
            continue
        old = (f["plane_a"], f["plane_b"], f["plane_c"])
        new = _fit_plane_robust(pts)
        if new is None:
            out.append(f)
            continue
        slope, aspect = _slope_aspect(new)
        if slope > config.MAX_ROOF_SLOPE_DEG or _inliers(pts, new) - _inliers(pts, old) < MIN_GAIN:
            out.append(f)
            continue
        STATS["refit"] += 1
        changed = True
        out.append(dict(f, plane_a=float(new[0]), plane_b=float(new[1]), plane_c=float(new[2]),
                        slope_deg=float(slope), aspect_deg=float(aspect)))
    return out if changed else facets
