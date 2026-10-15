"""structured_roof: a hip roof read as two faces is replaced by the hip its
returns describe; a roof the partition explains better is left alone."""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SOLAR_STRUCTURED_ROOFS"] = "1"
from src import structured_roof as sr  # noqa: E402


class Points:
    def __init__(self, pts):
        self.pts = pts

    def points_in_bbox(self, x0, y0, x1, y1, building_only=True):
        p = self.pts
        return p[(p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1)]


def _hip_points(w=16.0, h=10.0, pitch=22.0, step=0.35):
    xs, ys = np.meshgrid(np.arange(0.2, w, step), np.arange(0.2, h, step))
    d = np.minimum.reduce([xs, w - xs, ys, h - ys])          # distance to the nearest wall
    z = 103.0 + d * np.tan(np.radians(pitch))
    return np.c_[xs.ravel(), ys.ravel(), z.ravel()]


def _facet(poly, plane):
    from src.roof_partition import _slope_aspect
    s, a = _slope_aspect(plane)
    return {"building_id": 1, "geometry": poly, "plane_a": plane[0], "plane_b": plane[1],
            "plane_c": plane[2], "slope_deg": s, "aspect_deg": a}


def test_two_face_reading_of_a_hip_becomes_the_hip():
    from shapely.geometry import box
    fp = box(0, 0, 16, 10)
    pts = _hip_points()
    north, south = box(0, 5, 16, 10), box(0, 0, 16, 5)
    fit = lambda poly: sr._fit(pts[[poly.contains(__import__("shapely").geometry.Point(x, y)) for x, y, _ in pts]])
    facets = [_facet(north, fit(north)), _facet(south, fit(south))]
    out = sr.maybe_structured(facets, fp, Points(pts), 1)
    assert len(out) == 4 and all(f.get("structured") == "hip" for f in out), [f.get("structured") for f in out]
    assert all(abs(f["slope_deg"] - 22) < 3 for f in out)


def test_explained_roof_is_left_alone():
    from shapely.geometry import box
    fp = box(0, 0, 16, 10)
    xs, ys = np.meshgrid(np.arange(0.2, 16, 0.35), np.arange(0.2, 10, 0.35))
    z = 103 + 0.4 * (ys - 5).clip(min=0) + 0.4 * (5 - ys).clip(min=0) * 0 + 0.0 * xs   # a mono-ish odd roof
    pts = np.c_[xs.ravel(), ys.ravel(), z.ravel()]
    facets = [_facet(box(0, 5, 16, 10), (0.0, 0.4, 101.0)), _facet(box(0, 0, 16, 5), (0.0, 0.0, 103.0))]
    out = sr.maybe_structured(facets, fp, Points(pts), 1)
    assert out is facets


if __name__ == "__main__":
    test_two_face_reading_of_a_hip_becomes_the_hip()
    test_explained_roof_is_left_alone()
    print("structured roof: ok")
