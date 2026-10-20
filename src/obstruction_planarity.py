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
# within LEVEL_MAX_TILT_DEG of the face, is roof (SOLAR_OBS_LEVEL_CHECK=0 off).
LEVEL_MIN_M2 = 15.0
LEVEL_MIN_POINTS = 60
LEVEL_ON_PLANE_SHARE = 0.80
LEVEL_MAX_TILT_DEG = 10.0


def _normal(a, b):
    n = np.array([-a, -b, 1.0])
    return n / np.linalg.norm(n)


def _is_level(pts, f):
    if len(pts) < LEVEL_MIN_POINTS:
        return False
    A = np.c_[pts[:, 0], pts[:, 1], np.ones(len(pts))]
    (a, b, c), *_ = np.linalg.lstsq(A, pts[:, 2], rcond=None)
    if float((np.abs(pts[:, 2] - A @ np.array([a, b, c])) < ON_PLANE_M).mean()) < LEVEL_ON_PLANE_SHARE:
        return False
    cosang = float(np.clip(_normal(a, b) @ _normal(f["plane_a"], f["plane_b"]), -1.0, 1.0))
    return math.degrees(math.acos(cosang)) <= LEVEL_MAX_TILT_DEG


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
                and _is_level(pts, face)):
            continue
        keep.append(o)
    return keep
