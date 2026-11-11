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


def test_the_rough_rim_and_crane_go_with_the_deck_but_a_tree_beside_it_stays():
    z = _ground()
    z[DECK] += 12.0
    rng = np.random.default_rng(3)
    rim = np.zeros(z.shape, bool)
    rim[36:74, 26:79] = True
    rim[DECK] = False
    z[rim] = 100.0 + rng.uniform(4.0, 14.0, rim.sum())   # scaffold: high, rough
    z[38, 50] = 160.0                                     # crane on the rim
    tree = (slice(40, 52), slice(80, 90))
    z[tree] = 108.0
    green = np.zeros(z.shape, bool)
    green[tree] = True
    found = _run(z, green=green)
    assert len(found) == 1, len(found)
    r0, c0, m, g = found[0]
    full = np.zeros(z.shape, bool)
    full[r0:r0 + m.shape[0], c0:c0 + m.shape[1]] = m
    assert full[rim].mean() > 0.95, full[rim].mean()
    assert full[38, 50]
    assert full[tree].mean() < 0.2, full[tree].mean()
    assert abs(g - 100.0) < 0.5, g


def _mask(found, shape):
    full = np.zeros(shape, bool)
    for r0, c0, m, g in found:
        full[r0:r0 + m.shape[0], c0:c0 + m.shape[1]] |= m
    return full


def test_a_rough_wing_far_past_the_flat_part_goes_with_it():
    z = _ground()
    z[40:70, 15:50] += 12.0
    rng = np.random.default_rng(5)
    wing = (slice(40, 70), slice(50, 75))                 # 25 m, never flat
    z[wing] = 100.0 + rng.uniform(6.0, 20.0, z[wing].shape)
    found = _run(z)
    assert len(found) == 1, len(found)
    assert _mask(found, z.shape)[wing].mean() > 0.95


def test_growth_up_a_hillside_stops_at_the_first_step():
    z = _ground()
    yy, xx = np.mgrid[0:N, 0:N]
    hill = xx >= 50
    z[hill] += 0.4 * (xx[hill] - 50)                      # the slope rises from flat ground
    z[40:70, 20:60] = 112.0                               # a deck cut into its foot
    full = _mask(_run(z), z.shape)
    assert full[40:70, 20:55].mean() > 0.9
    assert not full[:, 60 + 12 + 3 + 2:].any(), np.where(full)[1].max()   # 12 m step + 3 m pad

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
