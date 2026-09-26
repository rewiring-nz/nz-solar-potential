"""A face that is plainly a staircase of narrow floors is not roof."""
import os
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import roof_segmentation as rs


class _PC:
    def __init__(self, p):
        self.p = p

    def points_in_bbox(self, minx, miny, maxx, maxy, building_only=True):
        p = self.p
        m = (p[:, 0] >= minx) & (p[:, 0] <= maxx) & (p[:, 1] >= miny) & (p[:, 1] <= maxy)
        return p[m]


def _stairs(depth=2.0, rise=3.0, steps=3):
    """steps floors, each `depth` m deep across y, 20 m long in x, `rise` apart."""
    rng = np.random.default_rng(2)
    xy = rng.uniform([0, 0], [20, depth * steps], size=(3000, 2))
    z = 100 - rise * np.floor(xy[:, 1] / depth) + rng.normal(0, 0.03, len(xy))
    return _PC(np.column_stack([xy, z])), box(0, 0, 20, depth * steps)


def _face(geom, pts):
    from src.roof_partition import _fit_plane
    a, b, c = _fit_plane(pts)
    return dict(geometry=geom, plane_a=a, plane_b=b, plane_c=c, building_id=1, area_m2=geom.area)


def test_balcony_staircase_is_not_roof():
    pc, g = _stairs()
    f = _face(g, pc.p)
    assert rs._is_unrepairable_staircase(f, pc)


def test_a_pitched_face_is_not_a_staircase():
    rng = np.random.default_rng(3)
    xy = rng.uniform([0, 0], [20, 6], size=(3000, 2))
    pts = np.column_stack([xy, 100 - 0.6 * xy[:, 1] + rng.normal(0, 0.03, len(xy))])
    assert not rs._is_unrepairable_staircase(_face(box(0, 0, 20, 6), pts), _PC(pts))


def test_drawn_faces_are_never_dropped():
    pc, g = _stairs()
    f = dict(_face(g, pc.p), from_labels=True)
    assert not rs._is_unrepairable_staircase(f, pc)


def test_never_most_of_the_roof():
    pc, g = _stairs()
    f = _face(g, pc.p)
    assert rs._drop_staircases([f], [False]) == [f]
    big = dict(geometry=box(0, 20, 40, 40))
    assert rs._drop_staircases([big, f], [True, False]) == [big]


def test_switch_turns_it_off():
    pc, g = _stairs()
    os.environ["SOLAR_STAIRCASE_DROP"] = "0"
    try:
        assert not rs._is_unrepairable_staircase(_face(g, pc.p), pc)
    finally:
        del os.environ["SOLAR_STAIRCASE_DROP"]


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
