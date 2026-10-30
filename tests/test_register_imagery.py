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


def test_a_robust_match_keeps_its_own_lean():
    # 13 Douglas Ave: sharp, the same at a wider window, but further than its
    # neighbours (a taller roof leans more) -- its own answer stands
    raw = {i: (10.0 * i, 0.0, 0.0, 0.8, 9.0) for i in range(6)}
    raw[6] = (35.0, 5.0, -0.2, 1.6, 8.2)
    raw[7] = (25.0, 5.0, -2.0, 2.0, 1.5)     # weak; the wider look is sharp
    looks = {6: (-0.3, 1.7, 6.8), 7: (0.1, 1.0, 6.0)}
    out = ri.reconcile(raw, lambda b: looks.get(b))
    assert out["6"] == [-0.2, 1.6, 8.2]
    assert out["7"] == [0.1, 1.0, 6.0]


def test_an_unsteady_outlier_still_borrows():
    raw = {i: (10.0 * i, 0.0, 0.0, 0.8, 9.0) for i in range(6)}
    raw[6] = (35.0, 5.0, 3.5, 3.5, 9.0)      # wider window says something else
    out = ri.reconcile(raw, lambda b: (0.2, 0.9, 7.0))
    assert out["6"][:2] == [0.0, 0.8] and out["6"][2] == 0.0


def test_a_second_look_onto_the_next_roof_is_not_believed():
    raw = {i: (10.0 * i, 0.0, 0.0, 0.8, 9.0) for i in range(6)}
    raw[6] = (35.0, 5.0, -3.0, 3.9, 9.0)
    out = ri.reconcile(raw, lambda b: (-3.0, 3.9, 9.0))
    assert out["6"][:2] == [0.0, 0.8]


def test_no_trusted_match_means_no_shift():
    assert ri.reconcile({1: (0.0, 0.0, 1.0, 1.0, 2.0)}) == {}


def test_markup_traced_on_the_map_photo_moves_onto_the_lidar():
    import os
    import src.roof_line_source as rls
    b = {"7": {"lines": [{"points": [[1266000.0, 5007000.0], [1266010.0, 5007000.0]]}],
               "faces": [{"ring": [[1266000.0, 5007000.0], [1266001.0, 5007000.0], [1266000.0, 5007001.0]], "m2": 0.5}],
               "area": 12.5},
         "8": {"lines": [{"points": [[1266100.0, 5007100.0], [1266110.0, 5007100.0]]}]}}
    real = ri.markup_shifts
    ri.markup_shifts = lambda d: {"7": [0.6, -0.8, 7.0]}
    os.environ["SOLAR_MARKUP_FRAME"] = "lidar"
    try:
        rls._to_lidar_frame(b)
    finally:
        ri.markup_shifts = real
        del os.environ["SOLAR_MARKUP_FRAME"]
    assert b["7"]["lines"][0]["points"][0] == [1265999.4, 5007000.8]
    assert b["7"]["faces"][0]["ring"][1] == [1266000.4, 5007000.8]
    assert b["7"]["area"] == 12.5 and b["7"]["faces"][0]["m2"] == 0.5
    assert b["8"]["lines"][0]["points"][0] == [1266100.0, 5007100.0]


def test_markup_frame_is_off_by_default():
    import src.roof_line_source as rls
    b = {"7": {"lines": [{"points": [[1266000.0, 5007000.0]]}]}}
    rls._to_lidar_frame(b)
    assert b["7"]["lines"][0]["points"][0] == [1266000.0, 5007000.0]


def test_a_weak_match_takes_the_roof_alone_before_borrowing():
    # 24 Beach St: weak on the whole window, borrowed the neighbours' lean;
    # the roof alone is sharp and within reach of them
    raw = {i: (10.0 * i, 0.0, 1.8, -1.1, 9.0) for i in range(6)}
    raw[6] = (35.0, 5.0, 2.5, -1.5, 4.6)
    out = ri.reconcile(raw, lambda b: (2.0, -1.1, 4.5), lambda b: (3.1, -1.8, 5.3))
    assert out["6"] == [3.1, -1.8, 5.3]


def test_the_roof_alone_is_not_believed_weak_or_far():
    raw = {i: (10.0 * i, 0.0, 1.8, -1.1, 9.0) for i in range(6)}
    raw[6] = (35.0, 5.0, 2.5, -1.5, 4.6)
    weak = ri.reconcile(raw, None, lambda b: (3.1, -1.8, 4.0))
    far = ri.reconcile(raw, None, lambda b: (-2.0, 2.0, 9.0))
    assert weak["6"] == [1.8, -1.1, 0.0] and far["6"] == [1.8, -1.1, 0.0]


def test_a_trusted_match_never_asks_the_roof():
    raw = {i: (10.0 * i, 0.0, 1.8, -1.1, 9.0) for i in range(6)}
    asked = []
    ri.reconcile(raw, None, lambda b: asked.append(b))
    assert asked == []


class _RoofOnly:
    """The map's photo where only the roof leans; the ground stays put."""
    def __init__(self, east, north):
        self.e, self.n = east, north

    def window(self, b, h, w):
        cols = np.arange(w)[None, :] + int(round((b[0] - X0) / RES))
        rows = np.arange(h)[:, None] + int(round((Y1 - b[3]) / RES))
        ground = P[np.clip(rows, 0, N - 1), np.clip(cols, 0, N - 1)].copy()
        sc = np.clip(cols - int(round(self.e / RES)), 0, N - 1)
        sr = np.clip(rows + int(round(self.n / RES)), 0, N - 1)
        moved = P[sr, sc]
        roof = (sr >= 200) & (sr < 400) & (sc >= 150) & (sc < 420)
        ground[roof] = moved[roof]
        return np.stack([ground] * 3)


def test_the_roof_alone_measures_the_roof_not_the_street():
    dx, dy, _ = ri.measure(ROOF, _reference(), _RoofOnly(2.0, -1.2), roof_only=True)
    assert abs(dx - 2.0) < 0.15 and abs(dy + 1.2) < 0.15, (dx, dy)


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
