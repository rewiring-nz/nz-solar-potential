"""The photo lean has the right sign, and weak or outlying matches borrow from neighbours."""
import sys
from pathlib import Path

import numpy as np
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from scipy import ndimage
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.register_imagery as ri

RES = 0.1
X0, Y1, N = 1000.0, 2000.0, 600          # 60 m square of synthetic roofs


def _pattern():
    rng = np.random.default_rng(3)
    a = ndimage.gaussian_filter(rng.random((N, N)), 4) * 255
    a[200:400, 150:420] = 230            # a bright roof with crisp edges
    a[260:300, 200:260] = 40             # a dark vent on it
    return a.astype(np.uint8)


P = _pattern()


def _reference():
    mem = MemoryFile()
    ds = mem.open(driver="GTiff", width=N, height=N, count=3, dtype="uint8",
                  transform=from_origin(X0, Y1, RES, RES), crs="EPSG:2193")
    ds.write(np.stack([P] * 3))
    ds.close()
    return mem.open()


class _Display:
    """The map's photo: the same scene with every roof moved east/north."""
    def __init__(self, east, north):
        self.e, self.n = east, north

    def window(self, b, h, w):
        cols = np.arange(w)[None, :] + int(round((b[0] - X0 - self.e) / RES))
        rows = np.arange(h)[:, None] + int(round((Y1 - b[3] + self.n) / RES))
        a = P[np.clip(rows, 0, N - 1), np.clip(cols, 0, N - 1)]
        return np.stack([a] * 3)


ROOF = box(X0 + 16, Y1 - 39, X0 + 41, Y1 - 21)


def test_lean_east_is_positive_east():
    dx, dy, sharp = ri.measure(ROOF, _reference(), _Display(1.0, 0.0))
    assert abs(dx - 1.0) < 0.15 and abs(dy) < 0.15, (dx, dy)
    assert sharp >= ri.SHARP_MIN, sharp


def test_lean_north_is_positive_north():
    dx, dy, _ = ri.measure(ROOF, _reference(), _Display(-0.5, 1.3))
    assert abs(dx + 0.5) < 0.15 and abs(dy - 1.3) < 0.15, (dx, dy)


def test_outlier_and_weak_matches_borrow():
    raw = {i: (10.0 * i, 0.0, 0.6, -0.8, 9.0) for i in range(6)}
    raw[6] = (35.0, 5.0, 3.5, 3.5, 9.0)      # sharp but disagrees with every neighbour
    raw[7] = (25.0, 5.0, -2.0, 2.0, 1.5)     # weak match
    out = ri.reconcile(raw)
    assert out["6"][:2] == [0.6, -0.8] and out["6"][2] == 0.0
    assert out["7"][:2] == [0.6, -0.8] and out["7"][2] == 0.0
    assert out["0"] == [0.6, -0.8, 9.0]


def test_no_trusted_match_means_no_shift():
    assert ri.reconcile({1: (0.0, 0.0, 1.0, 1.0, 2.0)}) == {}


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
