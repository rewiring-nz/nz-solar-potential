"""
An "obstruction" whose returns lie on a roof plane is roof.

The detector marks a patch when its colour or height stands out from the
face. Two things that are not objects pass that test: a patch of the face
itself that photographs differently (a lighter run of iron, a shadow line),
and -- where a seam is drawn in the wrong place -- the NEIGHBOUR's slope,
which stands well off this face's plane because it is a different plane
(src/plane_seams.py explains the mechanism). On 19 Oct, 18-23% of pitched
faces in an Arrowtown and a CBD sample were more than 10% "obstructed", and
the large patches were plain roof in the photo: smooth blobs hugging a hip
on 4719798, across a whole slope on 4746235, along a seam on 4735299. They
are two of Josh's complaint types, "obstructions drawn over clear space" and
"clear roof left unfilled".

So each detected patch of at least CHECK_MIN_M2 is tested against the
LiDAR: if ON_PLANE_SHARE of its returns lie within ON_PLANE_M of this face's
plane, or of a neighbouring face's plane, AND fewer than RAISED_MAX_SHARE
stand more than RAISED_M above that plane, it is roof and is dropped. A real
object -- chimney, dormer, plant, a water heater on legs -- sticks UP out of
the roof and stays; roof noise scatters both ways. (The first cut asked 85%
on-plane and nothing else; real patches of plain iron measured 68-92% with
a 2 cm median offset, so most survived.) Small patches (vents, skylights) are left to the
detector; a patch with too few returns to judge stays; drawn obstructions
and edge drops are never touched. SOLAR_OBS_PLANE_CHECK=0 turns it off.
"""

import math
import os

import numpy as np

CHECK_MIN_M2 = 2.0
ON_PLANE_M = 0.10
ON_PLANE_SHARE = 0.60
RAISED_M = 0.25
RAISED_MAX_SHARE = 0.10
MIN_POINTS = 15
NEIGHBOUR_M = 3.0
# A RAISED LEVEL IS ROOF TOO. Where two flat roof levels share one face, the
# upper level stands clear of the face's plane and the detector marks the
# whole of it: 4735304 lost a 160 m2 section whose 921 returns sit a steady
# 0.6 m up. A real object is small or ragged; a level is big and lies on a
# plane of its own. So a patch of at least LEVEL_MIN_M2 whose own fitted
# plane holds LEVEL_ON_PLANE_SHARE of its returns within ON_PLANE_M, tilted
# within LEVEL_MAX_TILT_DEG of the face, is roof -- or two or three such levels
# (see _is_level; SOLAR_OBS_LEVEL_CHECK=0 off).
LEVEL_MIN_M2 = 15.0
LEVEL_MIN_POINTS = 60
LEVEL_ON_PLANE_SHARE = 0.80
LEVEL_MAX_TILT_DEG = 10.0
LEVEL_MAX_PLANES = 3
LEVEL_RANSAC_TRIES = 300


def _normal(a, b):
    n = np.array([-a, -b, 1.0])
    return n / np.linalg.norm(n)


def _parallel(a, b, f):
    cosang = float(np.clip(_normal(a, b) @ _normal(f["plane_a"], f["plane_b"]), -1.0, 1.0))
    return math.degrees(math.acos(cosang)) <= LEVEL_MAX_TILT_DEG


def _is_level(pts, f):
    """True when up to LEVEL_MAX_PLANES planes parallel to the face hold
    LEVEL_ON_PLANE_SHARE of the patch's returns between them: one raised level
    or a few (4735304's 160 m2 "obstruction" is two flat levels at the face's
    5 degrees, 50% and 37% of its returns, 0.4 m apart). Plant and ducts sit at
    every height and no few planes hold them."""
    if len(pts) < LEVEL_MIN_POINTS:
        return False
    rng = np.random.default_rng(0)
    left = np.ones(len(pts), dtype=bool)
    held = 0
    for _k in range(LEVEL_MAX_PLANES):
        idx = np.nonzero(left)[0]
        if len(idx) < max(12, int(0.1 * len(pts))):
            break
        best = None
        for _ in range(LEVEL_RANSAC_TRIES):
            s = pts[rng.choice(idx, 3, replace=False)]
            try:
                a, b, c = np.linalg.solve(np.c_[s[:, 0], s[:, 1], np.ones(3)], s[:, 2])
            except np.linalg.LinAlgError:
                continue
            if not _parallel(a, b, f):
                continue
            inl = idx[np.abs(pts[idx, 2] - (a * pts[idx, 0] + b * pts[idx, 1] + c)) < ON_PLANE_M]
            if best is None or len(inl) > len(best):
                best = inl
        if best is None or len(best) < 0.1 * len(pts):
            break
        held += len(best)
        left[best] = False
        if held >= LEVEL_ON_PLANE_SHARE * len(pts):
            return True
    return False


def _is_structure(pts, f, area_m2):
    """A flat lid a storey up is a rooftop structure, not a level: see
    obstruction_detection.STRUCTURE_MIN_M (48 Beach St's plant room, 3.9 m
    up, is a level to _is_level). The median is used, not every return, as the
    merged patch carries a rim of deck around the box."""
    from src.obstruction_detection import RAISED_STRUCTURES, STRUCTURE_MIN_M, STRUCTURE_MAX_M2
    if not RAISED_STRUCTURES or area_m2 > STRUCTURE_MAX_M2:
        return False
    r = pts[:, 2] - (f["plane_a"] * pts[:, 0] + f["plane_b"] * pts[:, 1] + f["plane_c"])
    return float(np.median(r)) >= STRUCTURE_MIN_M


def _is_roof(pts, f):
    r = pts[:, 2] - (f["plane_a"] * pts[:, 0] + f["plane_b"] * pts[:, 1] + f["plane_c"])
    return (float((np.abs(r) < ON_PLANE_M).mean()) >= ON_PLANE_SHARE
            and float((r > RAISED_M).mean()) < RAISED_MAX_SHARE)


def drop_roof_like(obstructions, face, facets, pc_source):
    if os.environ.get("SOLAR_OBS_PLANE_CHECK", "1") == "0" or not obstructions:
        return obstructions
    if face.get("plane_a") is None or pc_source is None:
        return obstructions
    from src.roof_segmentation import _facet_points
    near = [face] + [g for g in facets if g is not face and g.get("plane_a") is not None
                     and g["geometry"].distance(face["geometry"]) < NEIGHBOUR_M]
    keep = []
    for o in obstructions:
        if o is None or o.is_empty or o.area < CHECK_MIN_M2:
            keep.append(o)
            continue
        try:
            pts = _facet_points(pc_source, o)
        except Exception:
            keep.append(o)
            continue
        if len(pts) < MIN_POINTS:
            keep.append(o)
            continue
        if any(_is_roof(pts, f) for f in near):
            continue
        if (o.area >= LEVEL_MIN_M2 and os.environ.get("SOLAR_OBS_LEVEL_CHECK", "1") != "0"
                and not _is_structure(pts, face, o.area)
                and _is_level(pts, face)):
            continue
        keep.append(o)
    return keep


# AN EDGE IS NOT AN OBSTRUCTION. Built blind on Josh's 119 roofs marked
# complete (tools/markup_benchmark.py, 24 Oct), 1,749 of the 2,987 m2 of
# detected obstruction was nowhere near anything he drew -- and rendered, the
# false blobs hug the face's edges: the gutter line, the wall or ground seen
# past the eave, the shadow along a hip, the jagged band where one roof level
# steps down to the next. His obstructions sit inside faces: skylights, vents,
# plant. A blob with EDGE_SHARE of its area within EDGE_BAND_M of its face's
# boundary goes, unless the returns under it stand up off the plane (its
# EDGE_RAISED_Q quantile more than EDGE_RAISED_M above): a chimney on a ridge
# or a parapet at an eave stays. SOLAR_OBS_EDGE_CHECK=0 turns it off.
EDGE_BAND_M = 0.5
EDGE_SHARE = 0.6
EDGE_RAISED_M = 0.25
EDGE_RAISED_Q = 75


def drop_edge_hugging(obstructions, face, pc_source):
    if os.environ.get("SOLAR_OBS_EDGE_CHECK", "1") == "0" or not obstructions:
        return obstructions
    if face.get("plane_a") is None:
        return obstructions
    g = face["geometry"]
    band = g.boundary.buffer(EDGE_BAND_M)
    keep = []
    for o in obstructions:
        if o is None or o.is_empty or o.area <= 0:
            continue
        if o.intersection(band).area < EDGE_SHARE * o.area:
            keep.append(o)
            continue
        pts = None
        if pc_source is not None:
            try:
                from src.roof_segmentation import _facet_points
                pts = _facet_points(pc_source, o)
            except Exception:
                pts = None
        if pts is not None and len(pts) >= 5:
            r = pts[:, 2] - (face["plane_a"] * pts[:, 0] + face["plane_b"] * pts[:, 1] + face["plane_c"])
            if float(np.percentile(r, EDGE_RAISED_Q)) > EDGE_RAISED_M:
                keep.append(o)
    return keep
