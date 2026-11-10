"""A structure the LiDAR saw but no outline covers is pressed flat in the 3D
ground; a hillside, a tree crown and an outlined building are not."""
import sys
from pathlib import Path

import numpy as np
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_terrain_tiles as btt

N = 120
TR = from_origin(0.0, float(N), 1.0, 1.0)     # 1 m cells
DECK = (slice(40, 70), slice(30, 75))        # a 30 x 45 m deck


def _ground(slope=0.0, seed=1):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:N, 0:N]
    return (100.0 + slope * xx + rng.normal(0, 0.05, (N, N))).astype("float32")


def _run(z, covered=None, green=None):
    covered = np.zeros(z.shape, bool) if covered is None else covered
    green = np.zeros(z.shape, bool) if green is None else green
    return btt.unoutlined_structures(z, TR, covered, np.ones(z.shape, bool), green)


def _deck(z):
    z = z.copy()
    z[DECK] += 12.0
    rng = np.random.default_rng(7)
    # scaffold and stacked materials: rough at 1 m
    rough = rng.random(z[DECK].shape) < 0.3
    z[DECK][rough] += rng.uniform(0.5, 2.0, rough.sum())
    z[52, 50] += 45.0                         # a crane
    return z


def test_an_unoutlined_deck_is_found_and_pressed_to_its_ground():
    z = _deck(_ground())
    found = _run(z)
    assert len(found) == 1, len(found)
    r0, c0, m, g = found[0]
    full = np.zeros(z.shape, bool)
    full[r0:r0 + m.shape[0], c0:c0 + m.shape[1]] = m
    assert full[DECK].mean() > 0.9, full[DECK].mean()
    assert abs(g - 100.0) < 0.5, g


def test_an_outlined_building_is_left_to_its_outline():
    z = _deck(_ground())
    cov = np.zeros(z.shape, bool)
    cov[38:72, 28:77] = True
    assert _run(z, covered=cov) == []


def test_a_green_crown_is_not_a_building():
    z = _deck(_ground())
    green = np.zeros(z.shape, bool)
    green[DECK] = True
    assert _run(z, green=green) == []


def test_a_hillside_has_no_walls():
    assert _run(_ground(slope=0.4)) == []


def test_without_a_photo_nothing_is_found():
    z = _deck(_ground())
    assert btt.unoutlined_structures(z, TR, np.zeros(z.shape, bool),
                                     np.ones(z.shape, bool), None) == []


def test_green_cells_reads_excess_green():
    rgb = np.array([[[60, 200]], [[140, 200]], [[50, 200]]], dtype="float32")
    assert btt.green_cells(rgb).tolist() == [[True, False]]


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
