"""The no-LiDAR surface: hip roofs at the assumed pitch, flat big roofs."""
import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import synthesize_dsm as sd


def test_small_footprints_are_hipped_big_ones_flat():
    assert sd.roof_model(150)[0] == "hip" and sd.roof_model(150)[1] == sd.PITCH_DEG
    assert sd.roof_model(sd.FLAT_MIN_M2)[0] == "flat"


def test_hip_roof_rises_at_the_pitch_from_every_wall():
    house = box(0, 0, 12, 8)
    xs, ys = np.array([0.5, 4.0, 6.0]), np.array([4.0, 4.0, 4.0])
    z = sd.roof_heights(house, xs, ys, 100.0, "hip", 20.0, 3.0)
    t = math.tan(math.radians(20.0))
    assert np.allclose(z, [103 + 0.5 * t, 103 + 4 * t, 103 + 4 * t])   # ridge at 4 m in


def test_a_hip_roof_reads_as_four_planes_of_the_pitch():
    """Every point of a rectangle's roof lies on one of four planes whose
    slope is the assumed pitch -- what the face reader will find."""
    house = box(0, 0, 12, 8)
    gx, gy = np.meshgrid(np.arange(0.5, 12, 1.0), np.arange(0.5, 8, 1.0))
    z = sd.roof_heights(house, gx.ravel(), gy.ravel(), 0.0, "hip", 20.0, 3.0)
    x, y = gx.ravel(), gy.ravel()
    south = y < np.minimum.reduce([x, 12 - x, 8 - y])     # the south wall is nearest
    slope = np.polyfit(y[south], z[south], 1)[0]
    assert abs(math.degrees(math.atan(slope)) - 20.0) < 0.5


def test_flat_roofs_are_level():
    z = sd.roof_heights(box(0, 0, 30, 20), np.array([1.0, 15.0]), np.array([1.0, 10.0]),
                        200.0, "flat", 0.0, 5.0)
    assert np.allclose(z, 205.0)


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
