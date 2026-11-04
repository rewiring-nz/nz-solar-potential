"""A panel whose face overruns its ridge is dropped; one on its own slope stays.

Synthetic gable z = 5 - 0.5|x| (26.6 degrees each side), ridge along x = 0.
The west face's polygon overruns the ridge by 0.8 m, as 32 Manuka Crescent's
face overran the LiDAR crest. Ground returns 3 m below past the west eave
must not count against an eave panel.
"""
import os
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SOLAR_FOLD_PANEL"] = "1"

import importlib  # noqa: E402

import src.build_layout_geojson as B  # noqa: E402


class _PC:
    def __init__(self):
        xs, ys = np.meshgrid(np.arange(-7, 6, 0.25), np.arange(0, 10, 0.25))
        x, y = xs.ravel(), ys.ravel()
        z = 5 - 0.5 * np.abs(x)
        z[x < -6] = 0.0                     # ground past the west eave
        self.p = np.c_[x, y, z]

    def points_in_bbox(self, x0, y0, x1, y1, building_only=True):
        p = self.p
        return p[(p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1)]


def _faces():
    west = {"geometry": box(-6, 0, 0.8, 10), "plane_a": 0.5, "plane_b": 0.0, "plane_c": 5.0}
    east = {"geometry": box(0.8, 0, 6, 10), "plane_a": -0.5, "plane_b": 0.0, "plane_c": 5.0}
    return west, east


def test_panel_astride_ridge_is_dropped():
    importlib.reload(B)
    west, east = _faces()
    on_slope = {"geometry": box(-3, 2, -1, 3)}
    astride = {"geometry": box(-0.8, 2, 0.8, 3)}
    at_eave = {"geometry": box(-6.4, 5, -4.4, 6)}   # overhangs the eave by 0.4 m
    kept = B.drop_panels_across_folds(west, [on_slope, astride, at_eave], [west, east], _PC())
    assert on_slope in kept
    assert at_eave in kept
    assert astride not in kept


def test_drawn_face_is_exempt():
    importlib.reload(B)
    west, east = _faces()
    west["from_labels"] = True
    astride = {"geometry": box(-0.8, 2, 0.8, 3)}
    assert B.drop_panels_across_folds(west, [astride], [west, east], _PC()) == [astride]


if __name__ == "__main__":
    test_panel_astride_ridge_is_dropped()
    test_drawn_face_is_exempt()
    print("ok")
