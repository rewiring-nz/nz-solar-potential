"""Installer rules Josh asked for on 24 Oct (29 York Street): panels on the
sunny side only, the hillside ground test, and no lone panel left behind by
the panel gate."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SOLAR_RACKING"] = "1"
import numpy as np  # noqa: E402
import pyproj  # noqa: E402
from shapely.geometry import box, mapping  # noqa: E402
from shapely.ops import transform  # noqa: E402

from src import gate_panels as gp  # noqa: E402
from src.build_layout_geojson import away_from_sun  # noqa: E402


class _Sun:
    """Sunshine by pitch and direction as SolarModel gives it at Queenstown
    (1.0 = the best direction), measured 24 Oct."""
    SLOPES = [0, 5, 10, 15, 20, 25, 30, 35]
    DEVS = [0, 45, 90, 112, 135, 157, 180]
    T = np.array([[.88] * 7,
                  [.91, .90, .88, .87, .85, .84, .84],
                  [.94, .92, .87, .85, .82, .81, .80],
                  [.96, .94, .87, .83, .79, .77, .76],
                  [.98, .95, .86, .81, .75, .73, .72],
                  [.99, .96, .84, .78, .71, .68, .67],
                  [1.0, .96, .82, .76, .67, .64, .63],
                  [1.0, .96, .80, .73, .64, .60, .58]])

    def annual_poa_kwh_per_m2(self, slope, aspect):
        dev = min(aspect % 360, 360 - aspect % 360)
        col = [np.interp(dev, self.DEVS, row) for row in self.T]
        return 1000 * float(np.interp(min(slope, 35), self.SLOPES, col))


def test_shady_side_bare_sunny_east_west_and_flat_kept():
    m = _Sun()
    best = 0.99                                    # the roof has a north face
    assert not away_from_sun(m, 30, 0, best)       # north
    assert not away_from_sun(m, 25, 90, best)      # east
    assert not away_from_sun(m, 35, 270, best)     # west, steep
    assert not away_from_sun(m, 44, 267, best)     # 5370578's 44-degree west face
    assert not away_from_sun(m, 3, 180, best)      # nearly flat, facing south
    assert away_from_sun(m, 24, 226, best)         # 29 York St's south-west face
    assert away_from_sun(m, 30, 180, best)         # steep south


def test_a_roof_with_no_sunny_side_keeps_its_low_south_faces():
    m = _Sun()
    best = 0.77                                    # 4751212: only south faces, ~15 degrees
    assert not away_from_sun(m, 15, 202, best)
    assert not away_from_sun(m, 14, 173, best)
    assert away_from_sun(m, 30, 180, best)         # but never a steep south face


def test_hillside_ground_is_the_terrain_under_the_panel():
    # ground rising 0.3 m per metre to the north; the roof at y=0 sits 3 m
    # above the ground directly beneath it but below the neighbourhood median
    rng = np.random.default_rng(1)
    x, y = rng.uniform(-20, 20, 400), rng.uniform(-20, 20, 400)
    ground = np.column_stack([x, y, 100 + 0.3 * y + rng.normal(0, 0.05, 400)])
    ground[:200, 1] = np.abs(ground[:200, 1])                  # more uphill samples
    ground[:200, 2] = 100 + 0.3 * ground[:200, 1]
    z = gp._ground_at(ground, 0.0, 0.0)
    assert abs(z - 100.0) < 0.3
    assert np.percentile(ground[:, 2], 50) > 101.0             # the old reading


def _feat(geom_nztm, **props):
    to_wgs = pyproj.Transformer.from_crs(2193, 4326, always_xy=True).transform
    return {"type": "Feature", "geometry": mapping(transform(to_wgs, geom_nztm)), "properties": props}


def test_gate_leaving_a_lone_panel_reracks_that_face_only():
    x0, y0 = 1_265_000.0, 5_005_000.0
    face = dict(building_id=1, plane_aspect_deg=0.0, plane_slope_deg=25.0)
    other = dict(building_id=1, plane_aspect_deg=90.0, plane_slope_deg=25.0)
    feats = [_feat(box(x0, y0, x0 + 12, y0 + 8), kind="facet", panel_count=4, **face),
             _feat(box(x0 + 20, y0, x0 + 32, y0 + 8), kind="facet", panel_count=3, **other)]
    # the gated face: three of its four panels survived, but one stands alone
    for k in (0, 1):
        feats.append(_feat(box(x0 + k * 1.02, y0, x0 + k * 1.02 + 1, y0 + 1.7), kind="panel", **face))
    feats.append(_feat(box(x0 + 8, y0 + 5, x0 + 9, y0 + 6.7), kind="panel", **face))
    # the untouched face keeps its three, whatever their layout
    for k in (0, 1, 2):
        feats.append(_feat(box(x0 + 20 + k * 3, y0, x0 + 21 + k * 3, y0 + 1.7), kind="panel", **other))
    out, n = gp.rerack(feats)
    panels = [f for f in out if f["properties"]["kind"] == "panel"]
    assert n == 3   # the face had 2 + a lone panel: under three in all, so it empties
    assert len(panels) == 3 and all(f["properties"]["plane_aspect_deg"] == 90.0 for f in panels)


class _Cloud:
    """Building returns on z = 0.4 x (a 22-degree face falling west), plus
    whatever is added."""

    def __init__(self, extra=()):
        xs, ys = np.meshgrid(np.arange(0, 12, 0.25), np.arange(0, 8, 0.25))
        self.p = np.column_stack([xs.ravel(), ys.ravel(), 0.4 * xs.ravel()])
        if len(extra):
            self.p = np.vstack([self.p, np.asarray(extra)])

    def points_in_bbox(self, x0, y0, x1, y1, building_only=False):
        m = (self.p[:, 0] >= x0) & (self.p[:, 0] <= x1) & (self.p[:, 1] >= y0) & (self.p[:, 1] <= y1)
        return self.p[m]


def test_a_blob_along_the_edge_goes_a_chimney_at_the_edge_stays():
    from src.obstruction_planarity import drop_edge_hugging
    face = {"geometry": box(0, 0, 12, 8), "plane_a": 0.4, "plane_b": 0.0, "plane_c": 0.0}
    gutter = box(0.05, 2, 0.45, 6)                 # a streak along the eave
    vent = box(5, 3.5, 6, 4.5)                     # inside the face
    chimney = box(11.4, 3, 11.95, 3.6)             # at the edge, standing up
    tall = [(x, y, 0.4 * x + 1.2) for x in np.arange(11.4, 11.95, 0.1) for y in np.arange(3, 3.6, 0.1)]
    kept = drop_edge_hugging([gutter, vent, chimney], face, _Cloud(tall))
    assert vent in kept and chimney in kept and gutter not in kept


def test_a_strip_on_the_slope_joins_the_slope():
    from src.face_straighten import absorb_slivers
    # one 22-degree plane, cut into a 10 x 6 face and a 1.2 m strip beside it
    # whose fitted plane came out steeper
    slope = {"geometry": box(0, 0, 10, 6), "plane_a": 0.0, "plane_b": -0.4, "plane_c": 3.2,
             "slope_deg": 22.0, "aspect_deg": 180.0}
    strip = {"geometry": box(0, 6, 10, 7.2), "plane_a": 0.0, "plane_b": -0.6, "plane_c": 4.4,
             "slope_deg": 31.0, "aspect_deg": 182.0}

    class C(_Cloud):
        def __init__(self):
            xs, ys = np.meshgrid(np.arange(0, 10, 0.25), np.arange(0, 7.2, 0.25))
            self.p = np.column_stack([xs.ravel(), ys.ravel(), 3.2 - 0.4 * ys.ravel()])
    out = absorb_slivers([slope, strip], C())
    assert len(out) == 1 and abs(out[0]["geometry"].area - 72.0) < 0.5
    assert out[0]["plane_b"] == -0.4
