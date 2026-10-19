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
plane, or of a neighbouring face's plane, it is roof and is dropped. A real
object -- chimney, dormer, plant, a water heater on legs -- stands off every
roof plane and stays. Small patches (vents, skylights) are left to the
detector; a patch with too few returns to judge stays; drawn obstructions
and edge drops are never touched. SOLAR_OBS_PLANE_CHECK=0 turns it off.
"""

import os

import numpy as np

CHECK_MIN_M2 = 2.0
ON_PLANE_M = 0.10
ON_PLANE_SHARE = 0.85
MIN_POINTS = 15
NEIGHBOUR_M = 3.0


def _on_plane(pts, f):
    r = pts[:, 2] - (f["plane_a"] * pts[:, 0] + f["plane_b"] * pts[:, 1] + f["plane_c"])
    return float((np.abs(r) < ON_PLANE_M).mean())


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
        if any(_on_plane(pts, f) >= ON_PLANE_SHARE for f in near):
            continue
        keep.append(o)
    return keep
