"""A face's plane is refit when a robust fit explains its returns far better."""
import os
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import plane_refresh as pr


class _PC:
    """A flat roof at z=10 over a 30 x 20 m box, with plant 2 m up in one corner."""
    def __init__(self):
        rng = np.random.default_rng(0)
        xy = rng.uniform([0, 0], [30, 20], size=(6000, 2))
        z = 10 + rng.normal(0, 0.05, len(xy))
        plant = (xy[:, 0] > 24) & (xy[:, 1] > 14)
        z[plant] += 2.0
        self.p = np.column_stack([xy, z])

    def points_in_bbox(self, minx, miny, maxx, maxy, building_only=True):
        p = self.p
        m = (p[:, 0] >= minx) & (p[:, 0] <= maxx) & (p[:, 1] >= miny) & (p[:, 1] <= maxy)
        return p[m]


def _face(a, b, c, **kw):
    return dict(geometry=box(0, 0, 30, 20), plane_a=a, plane_b=b, plane_c=c,
                slope_deg=0.0, aspect_deg=0.0, **kw)


def test_tilted_plane_on_a_flat_roof_is_refit():
    tilt = np.tan(np.radians(2.1))
    out = pr.refresh_planes([_face(tilt, 0.0, 10 - 15 * tilt)], _PC())
    f = out[0]
    assert abs(f["plane_a"]) < 0.01 and abs(f["plane_b"]) < 0.01, f
    assert f["slope_deg"] < 0.5


def test_a_plane_that_fits_is_untouched():
    faces = [_face(0.0, 0.0, 10.0)]
    assert pr.refresh_planes(faces, _PC()) is faces


def test_drawn_faces_keep_their_plane():
    tilt = np.tan(np.radians(2.1))
    faces = [_face(tilt, 0.0, 10.0, from_labels=True)]
    assert pr.refresh_planes(faces, _PC()) is faces


def test_switch_turns_it_off():
    tilt = np.tan(np.radians(2.1))
    faces = [_face(tilt, 0.0, 10 - 15 * tilt)]
    os.environ["SOLAR_PLANE_REFRESH"] = "0"
    try:
        assert pr.refresh_planes(faces, _PC()) is faces
    finally:
        del os.environ["SOLAR_PLANE_REFRESH"]


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("  pass ", name)
            except AssertionError as e:
                fails += 1
                print("  FAIL ", name, e)
    sys.exit(1 if fails else 0)
