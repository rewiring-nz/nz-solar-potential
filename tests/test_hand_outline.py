"""A building outline drawn in the labelling tool replaces the surveyed and
the fitted outline wherever the build reads outlines (fitted_outlines)."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import geopandas as gpd  # noqa: E402
from shapely.geometry import box  # noqa: E402

from src import outline_fit, roof_line_source as rls  # noqa: E402

X, Y = 1258000.0, 5004000.0
DRAWN = [[X + 1, Y + 1], [X + 13, Y + 1], [X + 13, Y + 9], [X + 1, Y + 9], [X + 1, Y + 1]]


def _with_labels(tmp_path, monkeypatch, buildings, hide=""):
    p = tmp_path / "roof_labels.json"
    p.write_text(json.dumps({"buildings": buildings}))
    monkeypatch.setattr(rls, "LABELS_PATH", p)
    monkeypatch.setattr(rls, "_LABELS_CACHE", [None])
    monkeypatch.setenv("SOLAR_MARKUP_FRAME", "photo")
    if hide:
        monkeypatch.setenv("SOLAR_HIDE_LABELS", hide)
    else:
        monkeypatch.delenv("SOLAR_HIDE_LABELS", raising=False)
    monkeypatch.setattr(outline_fit, "_marked", lambda: {int(k) for k in buildings})


def _gdf():
    return gpd.GeoDataFrame({"building_id": [1, 2]},
                            geometry=[box(X, Y, X + 10, Y + 8), box(X + 30, Y, X + 40, Y + 8)],
                            crs="EPSG:2193")


def test_drawn_outline_replaces_surveyed(tmp_path, monkeypatch):
    _with_labels(tmp_path, monkeypatch, {"1": {"lines": [], "outline": DRAWN}})
    g = outline_fit.fitted_outlines(_gdf(), tmp_path, use=False)
    assert abs(g.geometry.iloc[0].area - 96.0) < 1e-6        # 12 x 8 drawn
    assert abs(g.geometry.iloc[1].area - 80.0) < 1e-6        # untouched


def test_hidden_labels_hide_the_outline(tmp_path, monkeypatch):
    _with_labels(tmp_path, monkeypatch, {"1": {"lines": [], "outline": DRAWN}}, hide="1")
    g = outline_fit.fitted_outlines(_gdf(), tmp_path, use=False)
    assert abs(g.geometry.iloc[0].area - 80.0) < 1e-6


def test_void_flag_ignores_outline(tmp_path, monkeypatch):
    _with_labels(tmp_path, monkeypatch,
                 {"1": {"lines": [], "outline": DRAWN, "problem": "absent"}})
    assert rls.hand_outline(1) is None
