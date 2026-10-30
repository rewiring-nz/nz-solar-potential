"""The three seam fixes, each behind its own switch:
  * a drawn end within SOLAR_DANGLE_JOIN_M of another drawn line is joined to it
    (src/roof_line_source.drawn_network);
  * a face that curves in plan gets a plane per section, a planar face does not
    (src/curved_faces.py, SOLAR_CURVED_SPLIT);
  * a face with no panels whose tilt misfits its returns is refitted
    (src/build_layout_geojson._refit_empty_face, SOLAR_EMPTY_FACE_REFIT)."""
import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Polygon, box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.curved_faces as CF
import src.roof_line_source as R


class _Points:
    """A point source over fixed returns, as PointCloudSource serves them."""
    def __init__(self, pts):
        self.pts = pts

    def points_in_bbox(self, x0, y0, x1, y1, building_only=False):
        p = self.pts
        return p[(p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1)]


def _grid(x0, y0, x1, y1, step=0.45):
    xs, ys = np.meshgrid(np.arange(x0, x1, step), np.arange(y0, y1, step))
    return xs.ravel(), ys.ravel()


def test_join_end_short_of_a_line():
    # a ridge whose end stops 0.62 m short of a cliff line, on flat returns (no
    # fold for the evidence gate to find)
    cliff = LineString([(0, 0), (20, 0)])
    ridge = LineString([(10, 10), (10, 0.62)])
    fp = box(-5, -10, 25, 15)
    xs, ys = _grid(-5, -10, 25, 15, 0.5)
    pts = np.c_[xs, ys, np.full(len(xs), 10.0)]
    R.drawn_polylines = lambda bid: [cliff, ridge]
    R.DANGLE_JOIN_M = 0.0
    joined = []
    off = R.drawn_network(1, fp, pts, joined=joined)
    assert off[1].length == ridge.length and not joined
    R.DANGLE_JOIN_M = 1.0
    on = R.drawn_network(1, fp, pts, joined=joined)
    assert on[1].coords[-1][1] < 0, on[1].coords[-1]   # run 0.35 m past the cliff
    assert len(joined) == 1


def test_curved_face_split_planar_not():
    CF.CURVED_SPLIT = True
    # a fan: the face's direction turns from 100 to 60 deg along y, 8 deg pitch
    face = Polygon([(0, 0), (12, 0), (12, 36), (0, 36)])
    xs, ys = _grid(0, 0, 12, 36)
    asp = np.radians(100 - 40 * ys / 36)
    t = math.tan(math.radians(8))
    # height falls along the local downhill direction, measured from the face's
    # west edge, so each strip across y is a plane facing asp
    z = 20 - t * (np.sin(asp) * xs + np.cos(asp) * (ys - 18))
    pts = np.c_[xs, ys, z]
    f = {"geometry": face, "slope_deg": 8.0, "aspect_deg": 80.0, "plane_a": 0, "plane_b": 0, "plane_c": 0}
    out = CF.split_curved_faces([f], _Points(pts))
    assert len(out) >= 2, len(out)
    a = [o["aspect_deg"] for o in out]
    assert max(a) - min(a) >= 20, a
    # one plane: never cut
    zp = 20 - t * (np.sin(np.radians(80)) * xs + np.cos(np.radians(80)) * ys)
    noise = np.random.default_rng(1).normal(0, 0.04, len(xs))
    out = CF.split_curved_faces([f], _Points(np.c_[xs, ys, zp + noise]))
    assert len(out) == 1


def test_empty_face_refit():
    import src.build_layout_geojson as B
    # a flat deck (z = 10) cluttered with units 1 m tall over a fifth of it,
    # carrying a 4.4 deg tilt
    xs, ys = _grid(0, 0, 14, 12)
    rng = np.random.default_rng(2)
    z = 10 + np.where(rng.random(len(xs)) < 0.2, 1.0, 0.0) + rng.normal(0, 0.03, len(xs))
    t = math.tan(math.radians(4.4)); A = math.radians(200)
    f = {"geometry": box(0, 0, 14, 12), "plane_a": -t * math.sin(A), "plane_b": -t * math.cos(A),
         "plane_c": 10.0, "slope_deg": 4.4, "aspect_deg": 200.0}
    pf = {"poa": 0.0, "shading_factor": 1.0}

    class _Model:
        def annual_poa_kwh_per_m2(self, s, a):
            return 1000.0 + s
    B._refit_empty_face(f, pf, _Points(np.c_[xs, ys, z]), _Model())
    assert f.get("empty_face_refit") and f["slope_deg"] < 1.0, f["slope_deg"]
    assert pf["poa"] == 1000.0 + f["slope_deg"]
    # a face that fits its tilt is left alone
    zt = 10 + f["plane_a"] * 0 + (-t * math.sin(A)) * xs + (-t * math.cos(A)) * ys
    g = {"geometry": box(0, 0, 14, 12), "plane_a": -t * math.sin(A), "plane_b": -t * math.cos(A),
         "plane_c": 10.0, "slope_deg": 4.4, "aspect_deg": 200.0}
    B._refit_empty_face(g, dict(pf), _Points(np.c_[xs, ys, zt]), _Model())
    assert not g.get("empty_face_refit") and g["slope_deg"] == 4.4


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
