"""obstruction_planarity: a detected patch whose returns lie on a roof plane
is roof; a chimney, a small vent, or a patch with no evidence stays."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shapely.geometry import box  # noqa: E402
from src import obstruction_planarity as op  # noqa: E402


class Points:
    def __init__(self, pts):
        self.pts = pts

    def points_in_bbox(self, x0, y0, x1, y1, building_only=True):
        p = self.pts
        return p[(p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1)]


def _grid(x0, y0, x1, y1, z, step=0.35):
    xs, ys = np.meshgrid(np.arange(x0 + 0.1, x1, step), np.arange(y0 + 0.1, y1, step))
    return np.c_[xs.ravel(), ys.ravel(), z(xs.ravel(), ys.ravel())]


# a 10 x 8 face rising 0.4 m per metre north, and its neighbour falling the other way from y = 8
FACE = dict(geometry=box(0, 0, 10, 8), plane_a=0.0, plane_b=0.4, plane_c=100.0)
NEXT = dict(geometry=box(0, 8, 10, 12), plane_a=0.0, plane_b=-0.4, plane_c=106.4)
roof = lambda x, y: 100.0 + 0.4 * y
rng = np.random.default_rng(1)


def _pc(*extra):
    pts = _grid(0, 0, 10, 8, roof)
    pts[:, 2] += rng.normal(0, 0.03, len(pts))
    return Points(np.vstack([pts, *extra]) if extra else pts)


def test_patch_lying_on_the_face_is_dropped():
    patch = box(2, 2, 4, 4)                      # 4 m2 of plain roof that photographed differently
    assert op.drop_roof_like([patch], FACE, [FACE, NEXT], _pc()) == []


def test_chimney_stays():
    chimney = box(5, 5, 6.5, 6.5)
    top = _grid(5, 5, 6.5, 6.5, lambda x, y: 100.0 + 0.4 * y + 1.2)
    pc = Points(np.vstack([p for p in _pc().pts if not (5 <= p[0] <= 6.5 and 5 <= p[1] <= 6.5)] + [top]))
    assert op.drop_roof_like([chimney], FACE, [FACE, NEXT], pc) == [chimney]


def test_neighbours_slope_read_as_an_object_is_dropped():
    # the seam was drawn at y = 9 instead of 8: the strip 8-9 inside this face is the neighbour's roof
    face = dict(FACE, geometry=box(0, 0, 10, 9))
    strip_pts = _grid(0, 8, 10, 9, lambda x, y: 106.4 - 0.4 * y)
    pc = Points(np.vstack([_pc().pts, strip_pts]))
    strip = box(0, 8, 10, 9)
    nxt = dict(NEXT, geometry=box(0, 9, 10, 12))
    assert op.drop_roof_like([strip], face, [face, nxt], pc) == []


def test_low_box_on_part_of_the_patch_stays():
    # a 3 x 2 m patch, a third of it a plant box standing 0.6 m proud
    patch = box(2, 2, 5, 4)
    pts = [p for p in _pc().pts if not (2 <= p[0] <= 3 and 2 <= p[1] <= 4)]
    box_top = _grid(2, 2, 3, 4, lambda x, y: 100.0 + 0.4 * y + 0.6)
    assert op.drop_roof_like([patch], FACE, [FACE], Points(np.vstack([pts, box_top]))) == [patch]


def _replace(x0, y0, x1, y1, top):
    pts = [p for p in _pc().pts if not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1)]
    return Points(np.vstack([pts, _grid(x0, y0, x1, y1, top)]))


def test_raised_roof_level_is_roof():
    # 4735304: a 20 m2 section a steady 0.6 m up, parallel to the face
    level = box(1, 1, 6, 5)
    pc = _replace(1, 1, 6, 5, lambda x, y: 100.0 + 0.4 * y + 0.6)
    assert op.drop_roof_like([level], FACE, [FACE], pc) == []


def test_raised_level_outline_overlapping_the_roof_below_is_roof():
    # the outline takes in a 1 m strip of the face beside the level
    level = box(1, 1, 7, 5)
    pc = _replace(1, 1, 6, 5, lambda x, y: 100.0 + 0.4 * y + 0.6)
    assert op.drop_roof_like([level], FACE, [FACE], pc) == []


def test_big_ragged_plant_area_stays():
    # 20 m2 of plant and ducts at all heights: big, but on no plane of its own
    plant = box(1, 1, 6, 5)
    pc = _replace(1, 1, 6, 5, lambda x, y: 100.0 + 0.4 * y + rng.uniform(0.3, 1.8, len(x)))
    assert op.drop_roof_like([plant], FACE, [FACE], pc) == [plant]


def test_small_patches_and_patches_without_returns_stay():
    vent = box(1, 1, 2, 1.8)                      # 0.8 m2: left to the detector
    blind = box(20, 20, 23, 23)                   # no returns there at all
    assert op.drop_roof_like([vent, blind], FACE, [FACE], _pc()) == [vent, blind]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  pass ", name)
