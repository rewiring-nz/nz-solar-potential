"""A curved face gets one plane per section, not one plane for the whole curve.

WHY. A face is one plane, so a roof that curves in plan -- a D-shaped building
whose eaves follow an arc, its face fanning round like part of a cone -- is
read as the average of the curve. #5372566 (19 Camp St): its east face, cut in
thirds along its length, faces 100, 80 and 65 deg, each third fitting its
returns to 0.02-0.03 m; as one plane (7.9 deg / 77 deg) it fits to 0.11 m. The
single plane meets the face across the ridge 13 deg off the drawn ridge, so the
3D view steps from +0.9 to -1.25 m along the 36.5 m ridge, and panels are racked
and priced for one direction across 35 deg of real ones.

THE RULE. A face of at least CURVE_MIN_FACE_M2 and CURVE_MIN_SLOPE_DEG is cut
across its long axis into k equal sections (k = 2..CURVE_MAX_SECTIONS, the
smallest that passes). It is replaced by the sections only when
  * one plane misfits it by more than the noise (median |residual| over
    CURVE_MIN_RES_M),
  * every section fits at least CURVE_GAIN times better than the whole (the
    mean of the sections' median |residuals| against the whole's),
  * the sections' aspects turn steadily one way, by CURVE_MIN_TURN_DEG to
    CURVE_MAX_TURN_DEG end to end and no more than CURVE_MAX_STEP_DEG between
    neighbours, at pitches within CURVE_MAX_SLOPE_SPREAD_DEG -- a fan, not a
    face with a lump, a fold or a change of pitch in it, and
  * every section keeps CURVE_MIN_SECTION_M2 and CURVE_MIN_POINTS.
A planar face fails the first two tests whatever its size, so ordinary faces
are not cut.

A drawn face can be cut. The drawn lines stay the face's edges; the cuts are
inside a face the markup drew as one, and only change the planes panels are
racked and priced on. Off by default (SOLAR_CURVED_SPLIT=1) until judged.
"""
import math
import os

import numpy as np
import shapely
from shapely.affinity import rotate
from shapely.geometry import box

CURVED_SPLIT = os.environ.get("SOLAR_CURVED_SPLIT", "0") == "1"
CURVE_MIN_FACE_M2 = 60.0
CURVE_MIN_SLOPE_DEG = 3.0
CURVE_MAX_SECTIONS = 4
CURVE_MIN_RES_M = 0.06
CURVE_GAIN = 0.5
CURVE_MIN_TURN_DEG = 20.0
# A fan keeps its pitch and turns gradually. Without these, two sections facing
# opposite ways (a ridge inside one face, 24 deg at 315 and 137 deg on #4735237)
# or a pitch change (6 and 15 deg on #4725596) passed as a curve.
CURVE_MAX_TURN_DEG = 90.0
CURVE_MAX_STEP_DEG = 45.0
CURVE_MAX_SLOPE_SPREAD_DEG = 4.0
CURVE_MIN_SECTION_M2 = 15.0
CURVE_MIN_POINTS = 60


def _fit(pts):
    from src.roof_partition import _fit_plane_robust, _slope_aspect
    pl = _fit_plane_robust(pts)
    if pl is None:
        return None
    r = pts[:, 2] - (pl[0] * pts[:, 0] + pl[1] * pts[:, 1] + pl[2])
    slope, aspect = _slope_aspect(pl)
    return pl, float(np.median(np.abs(r - np.median(r)))), slope, aspect


def _turn(a, b):
    d = (b - a) % 360.0
    return d - 360.0 if d > 180.0 else d


def _sections(g, k):
    """g cut across its long axis into k equal-length pieces."""
    rect = g.minimum_rotated_rectangle
    c = list(rect.exterior.coords)
    e = max([(c[i], c[i + 1]) for i in range(4)], key=lambda e: math.dist(*e))
    ang = math.degrees(math.atan2(e[1][1] - e[0][1], e[1][0] - e[0][0]))
    origin = g.centroid
    gr = rotate(g, -ang, origin=origin)
    x0, y0, x1, y1 = gr.bounds
    out = []
    for i in range(k):
        cell = box(x0 + (x1 - x0) * i / k, y0 - 1, x0 + (x1 - x0) * (i + 1) / k, y1 + 1)
        p = gr.intersection(cell)
        if p.geom_type != "Polygon":
            return None
        out.append(rotate(p, ang, origin=origin))
    return out


def split_curved_faces(facets, pc_source):
    if not CURVED_SPLIT or not facets:
        return facets
    out = []
    for f in facets:
        g = f["geometry"]
        if (g.geom_type != "Polygon" or g.area < CURVE_MIN_FACE_M2
                or f.get("slope_deg", 0) < CURVE_MIN_SLOPE_DEG):
            out.append(f)
            continue
        pts = pc_source.points_in_bbox(*g.bounds, building_only=True)
        core = g.buffer(-0.45)
        if core.is_empty or len(pts) == 0:
            out.append(f)
            continue
        pts = pts[shapely.contains_xy(core, pts[:, 0], pts[:, 1])]
        whole = _fit(pts) if len(pts) >= CURVE_MIN_POINTS * 2 else None
        if whole is None or whole[1] <= CURVE_MIN_RES_M:
            out.append(f)
            continue
        done = None
        for k in range(2, CURVE_MAX_SECTIONS + 1):
            parts = _sections(g, k)
            if not parts or any(p.area < CURVE_MIN_SECTION_M2 for p in parts):
                break
            fits = []
            for p in parts:
                sub = pts[shapely.contains_xy(p, pts[:, 0], pts[:, 1])]
                fits.append(_fit(sub) if len(sub) >= CURVE_MIN_POINTS else None)
            if any(x is None for x in fits):
                break
            if np.mean([x[1] for x in fits]) > CURVE_GAIN * whole[1]:
                continue
            slopes = [x[2] for x in fits]
            if min(slopes) < CURVE_MIN_SLOPE_DEG or max(slopes) - min(slopes) > CURVE_MAX_SLOPE_SPREAD_DEG:
                continue
            turns = [_turn(fits[i][3], fits[i + 1][3]) for i in range(k - 1)]
            if not (all(t > 0 for t in turns) or all(t < 0 for t in turns)):
                continue
            if (abs(sum(turns)) < CURVE_MIN_TURN_DEG or abs(sum(turns)) > CURVE_MAX_TURN_DEG
                    or max(abs(t) for t in turns) > CURVE_MAX_STEP_DEG):
                continue
            done = (parts, fits)
            break
        if done is None:
            out.append(f)
            continue
        for p, (pl, _, slope, aspect) in zip(*done):
            sub = pts[shapely.contains_xy(p, pts[:, 0], pts[:, 1])]
            out.append({**f, "geometry": p, "plane_a": pl[0], "plane_b": pl[1], "plane_c": pl[2],
                        "slope_deg": slope, "aspect_deg": aspect, "area_m2": float(p.area),
                        "point_count": int(len(sub)), "curved_section": True})
    return out
