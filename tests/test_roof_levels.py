"""Lower roof levels become faces; decks, walls and plain roofs are left alone."""
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import roof_levels
from src.roof_levels import split_lower_levels


class _Cloud:
    def __init__(self, pts):
        self.pts = pts

    def points_in_bbox(self, minx, miny, maxx, maxy, building_only=False):
        p = self.pts
        m = (p[:, 0] >= minx) & (p[:, 0] <= maxx) & (p[:, 1] >= miny) & (p[:, 1] <= maxy)
        return p[m]


def _flat_roof(lower=None, drop=0.8, size=(30.0, 20.0), density=4.0, seed=0, noise=0.03):
    """A flat roof at z=10 over `size`; returns inside `lower` (a box) sit `drop` lower."""
    rng = np.random.default_rng(seed)
    w, h = size
    n = int(w * h * density)
    x, y = rng.uniform(0, w, n), rng.uniform(0, h, n)
    z = 10.0 + rng.normal(0, noise, n)
    if lower is not None:
        x0, y0, x1, y1 = lower
        m = (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
        z[m] -= drop
    return np.column_stack([x, y, z])


def _facet(size=(30.0, 20.0)):
    return [{"geometry": box(0, 0, *size), "plane_a": 0.0, "plane_b": 0.0, "plane_c": 10.0,
             "slope_deg": 0.0, "aspect_deg": 0.0, "area_m2": size[0] * size[1]}]


def test_lower_level_becomes_a_face():
    lower = (0, 0, 10, 8)                      # 10 x 8 m, 0.8 m down
    out = split_lower_levels(_facet(), _Cloud(_flat_roof(lower)))
    assert len(out) == 2, [f["geometry"].area for f in out]
    lv = [f for f in out if f.get("lower_level")]
    assert len(lv) == 1
    g = lv[0]["geometry"]
    assert abs(g.area - 80) < 12, g.area
    assert abs(lv[0]["plane_c"] - 9.2) < 0.1, lv[0]["plane_c"]
    host = [f for f in out if not f.get("lower_level")][0]["geometry"]
    assert host.intersection(g).area < 1.0
    assert abs(host.area + g.area - 600) < 2.0


def test_narrow_deck_stays_an_obstruction():
    deck = (0, 0, 30, 3)                       # a 3 m strip along the facade
    out = split_lower_levels(_facet(), _Cloud(_flat_roof(deck)))
    assert len(out) == 1 and not out[0].get("lower_level")


def test_plain_roof_is_untouched():
    f = _facet()
    assert split_lower_levels(f, _Cloud(_flat_roof())) is f


def test_labelled_roof_is_untouched():
    f = _facet()
    f[0]["from_labels"] = True
    assert split_lower_levels(f, _Cloud(_flat_roof((0, 0, 10, 8)))) is f


def test_cluttered_patch_is_not_a_level():
    # returns scattered over a metre of height: plant, not a roof surface
    pts = _flat_roof()
    rng = np.random.default_rng(3)
    m = (pts[:, 0] <= 10) & (pts[:, 1] <= 8)
    pts[m, 2] = 10.0 - rng.uniform(0.6, 2.0, m.sum())
    out = split_lower_levels(_facet(), _Cloud(pts))
    assert len(out) == 1 and not out[0].get("lower_level")


def test_switch_turns_it_off():
    import os
    os.environ["SOLAR_LEVEL_SPLIT"] = "0"
    try:
        f = _facet()
        assert split_lower_levels(f, _Cloud(_flat_roof((0, 0, 10, 8)))) is f
    finally:
        del os.environ["SOLAR_LEVEL_SPLIT"]


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            roof_levels.STATS.update({"roofs": 0, "levels": 0, "m2": 0.0})
            try:
                fn()
                print("  pass ", name)
            except AssertionError as e:
                fails += 1
                print("  FAIL ", name, e)
    sys.exit(1 if fails else 0)
