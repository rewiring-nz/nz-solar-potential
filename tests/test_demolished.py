"""A building gone from the map's photo is screened and withheld; a re-roofed one is not."""
import sys
from pathlib import Path

import numpy as np
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from scipy import ndimage
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.demolished as dm

RES = 0.1
X0, Y1, N = 1000.0, 2000.0, 600          # 60 m square
ROOF = box(X0 + 20, Y1 - 40, X0 + 40, Y1 - 25)
R0, R1, C0, C1 = 250, 400, 200, 400      # ROOF in pixels


def _ground(seed):
    """Gravel, grass and dirt: uneven at every scale, no straight edges."""
    rng = np.random.default_rng(seed)
    return (ndimage.gaussian_filter(rng.random((N, N)), 1.5) * 500 - 120).clip(0, 255)


def _scene(roof_value, seed=3):
    a = _ground(seed)
    if roof_value is not None:
        a[R0:R1, C0:C1] = roof_value     # a smooth roof with crisp walls
    return a.astype(np.uint8)


def _reference(a):
    mem = MemoryFile()
    ds = mem.open(driver="GTiff", width=N, height=N, count=3, dtype="uint8",
                  transform=from_origin(X0, Y1, RES, RES), crs="EPSG:2193")
    ds.write(np.stack([a] * 3))
    ds.close()
    return mem.open()


class _Display:
    """The map's photo, read the way register_imagery's tiles are."""
    def __init__(self, a):
        self.a = a

    def window(self, b, h, w):
        cols = np.arange(w)[None, :] + int(round((b[0] - X0) / RES))
        rows = np.arange(h)[:, None] + int(round((Y1 - b[3]) / RES))
        return np.stack([self.a[np.clip(rows, 0, N - 1), np.clip(cols, 0, N - 1)]] * 3)


SURVEY = _reference(_scene(210))


def test_a_cleared_site_is_screened():
    ev = dm.evidence(ROOF, SURVEY, _Display(_scene(None, seed=9)), [0, 0, 0])
    assert ev["edgefrac_cur"] < dm.SCREEN_EDGE_FRAC < ev["edgefrac_ref"], ev
    assert ev["tex_cur"] > dm.SCREEN_TEXTURE, ev
    assert dm.screened(ev), ev


def test_a_reroofed_building_is_not():
    # a new roof in a similar tone on the same walls: the walls still show
    ev = dm.evidence(ROOF, SURVEY, _Display(_scene(170, seed=9)), [0, 0, 0])
    assert ev["edgefrac_cur"] > dm.SCREEN_EDGE_FRAC, ev
    assert not dm.screened(ev), ev


def test_a_road_over_the_site_is_screened_by_its_brightness():
    # lane markings along the outline give it an edge; the tone changed outright
    a = _scene(None, seed=9)
    a[:] = ndimage.gaussian_filter(a.astype(float), 6) * 0.3
    a[R0 - 2:R0 + 2, :] = 255
    a[R1 - 2:R1 + 2, :] = 255
    ev = dm.evidence(ROOF, SURVEY, _Display(a.astype(np.uint8)), [0, 0, 0])
    assert abs(ev["lum_ref"] - ev["lum_cur"]) > dm.SCREEN_LUM, ev
    assert dm.screened(ev), ev


def test_an_unchanged_building_is_not():
    assert not dm.screened(dm.evidence(ROOF, SURVEY, _Display(_scene(210)), [0, 0, 0]))


def test_only_a_confident_gone_or_replaced_withholds():
    assert dm.withheld_by({"answer": "gone", "confidence": 0.9})
    assert dm.withheld_by({"answer": "replaced", "confidence": 0.7})
    assert not dm.withheld_by({"answer": "gone", "confidence": 0.5})
    assert not dm.withheld_by({"answer": "unclear", "confidence": 0.95})
    assert not dm.withheld_by({"answer": "standing", "confidence": 0.99})
    assert not dm.withheld_by(None)


def test_withholding_replaces_the_layout_with_a_reason():
    feats = [{"type": "Feature", "geometry": None, "properties": {"kind": k, "building_id": b}}
             for b in (1, 2) for k in ("facet", "panel", "panel", "obstruction")]
    out = dm.withhold(feats, {2}, {2: {"type": "Polygon", "coordinates": []}}, "gone")
    assert [f["properties"]["kind"] for f in out if f["properties"]["building_id"] == 1] == \
        ["facet", "panel", "panel", "obstruction"]
    mine = [f["properties"] for f in out if f["properties"]["building_id"] == 2]
    assert mine == [{"kind": "no_estimate", "building_id": 2,
                     "no_estimate_reason": "demolished", "no_estimate_text": "gone"}]


def test_the_cache_key_follows_outline_and_lean():
    k = dm.input_key(ROOF, [0.5, -0.2, 6.0])
    assert k == dm.input_key(ROOF, [0.5, -0.2, 3.0])          # match quality is not an input
    assert k != dm.input_key(ROOF, [0.5, -0.3, 6.0])
    assert k != dm.input_key(ROOF.buffer(0.5), [0.5, -0.2, 6.0])


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
