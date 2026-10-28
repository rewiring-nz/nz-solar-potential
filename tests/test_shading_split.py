"""Shading splits direct and diffuse light (building_shading.facet_sun,
SolarModel.kept_sun), a hole in the scan is not a pit, and stations are
picked by real distance.

Run:  python tests/test_shading_split.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import building_shading as BS
from src import solar_model as SM
from src.terrain_horizon import compute_horizon_profile_from_array

T = from_origin(0, 200, 1, 1)          # 200 x 200 m, 1 m cells


def _hourly():
    t = pd.date_range("2023-01-01", periods=24 * 365, freq="1h", tz="Pacific/Auckland")
    h = (t.hour.to_numpy() - 12) / 6.0
    el = np.clip(60 * np.cos(np.clip(h, -1.5, 1.5)), -5, None) - 10
    return {"dni": pd.Series(np.where(el > 0, 800.0, 0.0), index=t),
            "solar_azimuth": pd.Series(np.full(len(t), 0.0), index=t),   # sun due north
            "solar_elevation": pd.Series(el, index=t)}


def test_a_wall_to_the_north_cuts_direct_sun_more_than_sky():
    dsm = np.zeros((200, 200), dtype="float32")
    dsm[60:62, 80:120] = 30.0                    # a 30 m wall 39 m north of the point
    hourly = _hourly()
    ctx = BS.facet_sun_context(hourly, 20, 0)
    fs = BS.facet_sun(dsm, T, -9999, 100.5, 99.5, hourly, ctx)
    assert 0 < fs["beam"] < 1, fs
    assert fs["beam"] < fs["sky"] < 1, fs


def test_open_ground_keeps_everything():
    dsm = np.zeros((200, 200), dtype="float32")
    hourly = _hourly()
    fs = BS.facet_sun(dsm, T, -9999, 100.5, 99.5, hourly, BS.facet_sun_context(hourly, 20, 0))
    assert fs["beam"] == 1.0 and fs["sky"] == 1.0, fs


def test_a_hole_under_the_point_is_not_a_pit():
    dsm = np.full((200, 200), 5.0, dtype="float32")
    dsm[100, 100] = -9999                        # nodata at the observer
    prof = compute_horizon_profile_from_array(dsm, T, -9999, 100.5, 99.5, max_distance_km=0.05,
                                              sample_step_m=2.0, azimuth_step_deg=10)
    assert max(prof.values()) < 1.0, max(prof.values())


def test_kept_sun_weights_direct_and_diffuse():
    m = SM.SolarModel.__new__(SM.SolarModel)
    m.lookup = {(30, 0): 1000.0}
    m.hourly = {"beam_lookup": {(30, 0): 600.0}}
    assert abs(m.kept_sun(30, 0, 0.5, 1.0) - (0.6 * 0.5 + 0.4)) < 1e-9


def test_wanaka_is_nearer_queenstown_than_clyde():
    st = {n: (la, lo) for n, la, lo, _ in SM.NIWA_MEASURED_GHI_STATIONS}
    d = lambda s: SM._deg_dist(-44.70, 169.13, *st[s])
    assert d("Queenstown") < d("Clyde"), (d("Queenstown"), d("Clyde"))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  pass ", name)
