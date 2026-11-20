"""face_weld: a narrow gap between two faces is closed to one line and a lapped
edge is cut; a wide gap, a notch in the outer edge and drawn faces are left."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shapely.geometry import Polygon, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402
from src import face_weld as fw  # noqa: E402


def test_wedge_gap_becomes_one_line():
    a = box(0, 0, 10, 4)
    b = Polygon([(0, 4.1), (10, 4.4), (10, 8), (0, 8)])     # 0.1 to 0.4 m off a's edge
    na, nb = fw.weld_polygons([a, b])
    assert unary_union([na, nb]).geom_type == "Polygon"
    assert abs(na.area + nb.area - 80) < 0.3 and na.intersection(nb).area < 0.01


def test_lapped_edge_is_cut():
    a, b = box(0, 0, 10, 4.3), box(0, 4, 10, 7)
    na, nb = fw.weld_polygons([a, b])
    assert na.intersection(nb).area < 0.01 and abs(na.area - 43) < 0.01


def test_wide_gap_is_left():
    a, b = box(0, 0, 10, 4), box(0, 5.5, 10, 9)
    na, nb = fw.weld_polygons([a, b])
    assert abs(na.area - 40) < 0.01 and abs(nb.area - 35) < 0.01


def test_outer_notch_is_left():
    a = Polygon([(0, 0), (10, 0), (10, 4), (6, 4), (6, 3.5), (5.5, 3.5), (5.5, 4), (0, 4)])
    b = box(0, 4, 5, 8)
    na, _ = fw.weld_polygons([a, b])
    assert abs(na.area - a.area) < 0.01


def test_drawn_faces_untouched():
    f = [dict(geometry=box(0, 0, 10, 4), from_labels=True), dict(geometry=box(0, 4.2, 10, 8), from_labels=True)]
    fw.weld_faces(f)
    assert f[1]["geometry"].equals(box(0, 4.2, 10, 8))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  pass ", name)
