"""face_steps (on by default): a face over two roof levels a step apart is cut along the step;
a plain plane, a ridge (with steps only) and drawn faces are left alone; with
folds on, a face over a ridge is cut along the ridge."""
import os
os.environ["SOLAR_STEP_SPLIT"] = "1"
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shapely.geometry import box  # noqa: E402
from src import face_steps as fs  # noqa: E402


class Cloud:
    def __init__(self, z_of, bounds=(0, 0, 12, 8), density=4.0, seed=1):
        rng = np.random.default_rng(seed)
        minx, miny, maxx, maxy = bounds
        n = int((maxx - minx) * (maxy - miny) * density)
        x = rng.uniform(minx, maxx, n); y = rng.uniform(miny, maxy, n)
        self.p = np.c_[x, y, z_of(x, y) + rng.normal(0, 0.03, n)]

    def points_in_bbox(self, a, b, c, d, building_only=True):
        p = self.p
        return p[(p[:, 0] >= a) & (p[:, 0] <= c) & (p[:, 1] >= b) & (p[:, 1] <= d)]


def _face(poly, **kw):
    return dict(geometry=poly, area_m2=poly.area, plane_a=0.0, plane_b=0.0, plane_c=0.0,
                slope_deg=0.0, aspect_deg=0.0, **kw)


T = math.tan(math.radians(20))


def two_levels(x, y):
    # an upper mono-pitch at x < 7, a lower one 1.2 m down beyond
    return 10 + T * y - np.where(x < 7, 0.0, 1.2)


def test_step_between_levels_is_cut_along_it():
    out = fs.split_steps([_face(box(0, 0, 12, 8))], Cloud(two_levels))
    assert len(out) == 2
    xs = sorted(f["geometry"].centroid.x for f in out)
    assert xs[0] < 7 < xs[1]
    seam = out[0]["geometry"].intersection(out[1]["geometry"].buffer(0.05))
    assert all(abs(x - 7) < 0.6 for x, _ in seam.boundary.coords) if seam.geom_type == "LineString" else True
    for f in out:
        assert abs(f["slope_deg"] - 20) < 2
        assert f["split_by"] == "step"


def test_three_levels_split_twice():
    z = lambda x, y: 10 + T * y - np.where(x < 4, 0.0, np.where(x < 8, 1.0, 2.0))
    out = fs.split_steps([_face(box(0, 0, 12, 8))], Cloud(z))
    assert len(out) == 3
    # the pieces cover the face: no level is dropped
    from shapely.ops import unary_union
    assert abs(unary_union([f["geometry"] for f in out]).area - 96) < 1.0
    assert sorted(round(f["geometry"].centroid.x) for f in out) == [2, 6, 10]


def test_one_plane_is_left_alone():
    faces = [_face(box(0, 0, 12, 8))]
    assert fs.split_steps(faces, Cloud(lambda x, y: 10 + T * y)) is faces


def test_ridge_is_not_a_step():
    ridge = lambda x, y: 12 - T * np.abs(y - 4)
    faces = [_face(box(0, 0, 12, 8))]
    assert fs.split_steps(faces, Cloud(ridge)) is faces


def test_folds_on_cut_a_ridge():
    os.environ["SOLAR_STEP_SPLIT"] = "2"
    try:
        out = fs.split_steps([_face(box(0, 0, 12, 8))], Cloud(lambda x, y: 12 - T * np.abs(y - 4)))
        assert len(out) == 2 and {f["split_by"] for f in out} == {"fold"}
        ys = sorted(f["geometry"].centroid.y for f in out)
        assert ys[0] < 4 < ys[1]
    finally:
        os.environ["SOLAR_STEP_SPLIT"] = "1"


def test_small_level_is_not_a_face():
    # a 1 m x 1.5 m drop (a skylight well or plant) is an obstruction, not a level
    z = lambda x, y: 10 + T * y - np.where((x > 5) & (x < 6) & (y > 3) & (y < 4.5), 1.0, 0.0)
    faces = [_face(box(0, 0, 12, 8))]
    assert fs.split_steps(faces, Cloud(z)) is faces


def test_on_by_default():
    os.environ.pop("SOLAR_STEP_SPLIT")
    try:
        assert fs.mode() == 1
    finally:
        os.environ["SOLAR_STEP_SPLIT"] = "1"


def test_drawn_faces_and_off():
    faces = [_face(box(0, 0, 12, 8), from_labels=True)]
    assert fs.split_steps(faces, Cloud(two_levels)) is faces
    os.environ["SOLAR_STEP_SPLIT"] = "0"
    try:
        faces = [_face(box(0, 0, 12, 8))]
        assert fs.split_steps(faces, Cloud(two_levels)) is faces
    finally:
        os.environ["SOLAR_STEP_SPLIT"] = "1"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  pass ", name)
