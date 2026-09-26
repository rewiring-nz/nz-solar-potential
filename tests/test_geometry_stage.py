"""A face-refining stage may improve a roof but never lose one."""
import sys
from pathlib import Path

from shapely.geometry import Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.build_layout_geojson as blg


def _faces():
    return [dict(geometry=box(0, 0, 5, 5), area_m2=25.0), dict(geometry=box(5, 0, 10, 5), area_m2=25.0)]


def test_empty_face_is_dropped():
    def stage(facets):
        return [facets[0], dict(facets[1], geometry=Polygon())]
    out = blg._geometry_stage(1, stage, _faces())
    assert len(out) == 1 and out[0]["geometry"].area == 25.0


def test_raising_stage_leaves_faces_alone():
    def stage(facets):
        raise ValueError("TopologyException")
    faces = _faces()
    assert blg._geometry_stage(1, stage, faces) is faces


def test_invalid_face_is_repaired():
    bowtie = Polygon([(0, 0), (4, 4), (4, 0), (0, 4)])
    out = blg._geometry_stage(1, lambda f: [dict(f[0], geometry=bowtie)], _faces())
    assert len(out) == 1 and out[0]["geometry"].is_valid and out[0]["geometry"].area > 0
    assert abs(out[0]["area_m2"] - out[0]["geometry"].area) < 1e-9


def test_all_empty_keeps_the_input():
    faces = _faces()
    assert blg._geometry_stage(1, lambda f: [dict(x, geometry=Polygon()) for x in f], faces) is faces


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
