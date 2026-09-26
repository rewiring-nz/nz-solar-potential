"""The plant-deck rule drops raised clutter, never most of a roof."""
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import roof_segmentation as rs


class _PC:
    def __init__(self, parts):
        rng = np.random.default_rng(4)
        pts = []
        for (x0, y0, x1, y1), z, clutter in parts:
            n = int((x1 - x0) * (y1 - y0) * 8)
            xy = rng.uniform([x0, y0], [x1, y1], size=(n, 2))
            zz = z + rng.normal(0, 0.03, n)
            k = rng.random(n) < clutter
            zz[k] += rng.uniform(0.4, 1.0, k.sum())
            pts.append(np.column_stack([xy, zz]))
        self.p = np.vstack(pts)

    def points_in_bbox(self, minx, miny, maxx, maxy, building_only=True):
        p = self.p
        m = (p[:, 0] >= minx) & (p[:, 0] <= maxx) & (p[:, 1] >= miny) & (p[:, 1] <= maxy)
        return p[m]


def _face(b, z):
    return dict(geometry=box(*b), plane_a=0.0, plane_b=0.0, plane_c=z)


def test_a_small_cluttered_deck_is_dropped():
    roof, deck = (0, 0, 20, 20), (5, 5, 10, 10)
    pc = _PC([((0, 0, 20, 5), 10.0, 0), ((0, 10, 20, 20), 10.0, 0), (deck, 10.6, 0.3)])
    faces = [_face((0, 0, 20, 5), 10.0), _face((0, 10, 20, 20), 10.0), _face(deck, 10.6)]
    out = rs.drop_plant_decks(faces, pc)
    assert len(out) == 2


def test_never_most_of_the_roof():
    low = [(0, 0, 13, 10), (13, 0, 26, 10)]            # 260 m2 low band
    high = (0, 10, 24, 20)                               # 240 m2 raised, cluttered
    pc = _PC([(low[0], 10.0, 0), (low[1], 10.0, 0), (high, 10.6, 0.3)])
    faces = [_face(low[0], 10.0), _face(low[1], 10.0), _face(high, 10.6)]
    assert len(rs.drop_plant_decks(faces, pc)) == 3


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
