"""Sunken regions need most of a cell's returns low, and the region low as a whole."""
import os
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import obstruction_detection as od


class _PC:
    def __init__(self, hole=False, stray=False):
        rng = np.random.default_rng(1)
        xy = rng.uniform([0, 0], [20, 12], size=(4000, 2))
        z = 10 + rng.normal(0, 0.04, len(xy))
        if hole:                                    # a 4 x 3 m deck 1.5 m down
            m = (xy[:, 0] > 8) & (xy[:, 0] < 12) & (xy[:, 1] > 4) & (xy[:, 1] < 7)
            z[m] -= 1.5
        if stray:                                   # one low return in every 4th cell along an edge
            edge = np.nonzero(xy[:, 1] < 1.2)[0][::4]
            z[edge] -= 1.0
        self.p = np.column_stack([xy, z])

    def points_in_bbox(self, minx, miny, maxx, maxy, building_only=True):
        p = self.p
        m = (p[:, 0] >= minx) & (p[:, 0] <= maxx) & (p[:, 1] >= miny) & (p[:, 1] <= maxy)
        return p[m]


ROOF = box(0, 0, 20, 12)
FLAT = (0.0, 0.0, 10.0)


def test_stray_low_returns_are_not_a_strip():
    assert od._sunken_regions(_PC(stray=True), ROOF, FLAT) == []


def test_a_real_deck_is_found():
    regs = od._sunken_regions(_PC(hole=True), ROOF, FLAT)
    assert len(regs) == 1 and 8 < regs[0].area < 20, [r.area for r in regs]


def test_switch_restores_the_old_rule():
    os.environ["SOLAR_SUNKEN_STRICT"] = "0"
    try:
        assert od._sunken_regions(_PC(stray=True), ROOF, FLAT) != []
    finally:
        del os.environ["SOLAR_SUNKEN_STRICT"]


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
