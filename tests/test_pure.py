"""
Unit tests for the pure functions -- the arithmetic you cannot see.

Why these functions and not others: every regression this project has caught
so far was caught by LOOKING at something. That works for geometry, which is
visible, and fails completely for coordinate conventions and unit conversions,
which are not. The irradiance bias found on 31 Aug lived undetected for as
long as it existed because every internal check agreed with every other
internal check. The functions below are the ones where a silent sign flip or
a factor-of-two would change published kWh figures with nothing on screen
looking wrong.

They are all pure -- no I/O, no rasters, no network -- so they are fast and
deterministic, which is the whole reason to start here.

Run:  .venv/bin/python tests/test_pure.py
      (or `pytest tests/` if pytest is ever added to the venv -- the
      test_* naming and bare asserts work under both.)
"""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
from src.building_horizon import N_BINS, AZ_STEP, decode_horizon, encode_horizon
from src.solar_model import _nearest_bin
from src.terrain_horizon import horizon_angle_at
from src.validate_against_pvgis import our_aspect_to_pvgis


# --------------------------------------------------------------------------
# Aspect convention: ours is a compass bearing (0 = north, 90 = east).
# PVGIS uses 0 = SOUTH, negative = east, positive = west, in both hemispheres.
# Getting this backwards would make the whole external validation agree with
# itself while comparing north-facing roofs against south-facing ones -- which
# in New Zealand is the difference between the best and worst roof on a house.
# --------------------------------------------------------------------------

def test_aspect_cardinals_map_to_pvgis():
    assert our_aspect_to_pvgis(180) == 0.0      # south -> PVGIS zero
    assert our_aspect_to_pvgis(90) == -90.0     # east  -> negative
    assert our_aspect_to_pvgis(270) == 90.0     # west  -> positive
    assert abs(our_aspect_to_pvgis(0)) == 180.0 # north -> the far side


def test_aspect_always_within_pvgis_range():
    for a in range(0, 360, 5):
        v = our_aspect_to_pvgis(a)
        assert -180.0 <= v <= 180.0, f"aspect {a} -> {v} outside PVGIS range"


def test_aspect_is_a_rotation_not_a_reflection():
    """A 10 deg step east of north must stay a 10 deg step in PVGIS terms.
    A reflection would preserve the cardinals above but silently mirror
    everything between them."""
    for a in range(0, 350, 10):
        d = our_aspect_to_pvgis(a + 10) - our_aspect_to_pvgis(a)
        d = (d + 180) % 360 - 180          # shortest way round
        assert abs(d - 10.0) < 1e-9, f"{a} -> {a+10} moved {d}, not 10"


# --------------------------------------------------------------------------
# Horizon encoding: 72 bins, uint8 = elevation degrees x 2, base64.
# This is baked onto every building and decoded by BOTH the frontend chart
# and the model's beam masking. If the two ever disagreed about the sky, the
# horizon tab would draw one thing and the economics would use another.
# --------------------------------------------------------------------------

def test_horizon_round_trip_preserves_half_degrees():
    profile = {i * AZ_STEP: (i % 40) * 0.5 for i in range(N_BINS)}
    back = decode_horizon(encode_horizon(profile))
    assert len(back) == N_BINS
    for az, v in profile.items():
        assert abs(back[az] - v) < 1e-9, f"bin {az}: {v} -> {back[az]}"


def test_horizon_quantises_to_the_nearest_half_degree():
    """0.5 deg is the storage resolution; a value between steps must land on
    the nearer one, not truncate downward."""
    back = decode_horizon(encode_horizon({0.0: 10.24, AZ_STEP: 10.26}))
    assert back[0.0] == 10.0
    assert back[AZ_STEP] == 10.5


def test_horizon_missing_bins_are_open_sky_not_dropped():
    """A sparse profile must encode as 72 bins with the gaps meaning 'no
    obstruction'. Dropping them would shift every later bin's azimuth."""
    back = decode_horizon(encode_horizon({0.0: 12.0, 180.0: 30.0}))
    assert len(back) == N_BINS
    assert back[0.0] == 12.0 and back[180.0] == 30.0
    assert back[90.0] == 0.0


def test_horizon_clamps_instead_of_wrapping():
    """uint8 caps at 127.5 deg. Wrapping would turn a bad value into a
    plausible small one -- a vertical wall becoming open sky."""
    back = decode_horizon(encode_horizon({0.0: 200.0, AZ_STEP: -5.0}))
    assert back[0.0] == 127.5
    assert back[AZ_STEP] == 0.0


# --------------------------------------------------------------------------
# Horizon lookup, called with the full 8760-hour array.
# --------------------------------------------------------------------------

def test_horizon_angle_interpolates_between_samples():
    profile = {0.0: 0.0, 90.0: 10.0, 180.0: 0.0, 270.0: 0.0}
    assert abs(horizon_angle_at(profile, 45.0) - 5.0) < 1e-9


def test_horizon_angle_wraps_past_north():
    """359 deg must interpolate across the 360/0 seam, not clamp to an end."""
    profile = {0.0: 10.0, 90.0: 0.0, 180.0: 0.0, 270.0: 10.0}
    assert horizon_angle_at(profile, 359.0) > 9.0
    assert abs(horizon_angle_at(profile, 720.0) - horizon_angle_at(profile, 0.0)) < 1e-9


def test_horizon_angle_accepts_arrays():
    """The hourly path passes all 8760 at once; scalar and array must agree."""
    profile = {i * AZ_STEP: float(i % 10) for i in range(N_BINS)}
    azs = np.array([0.0, 37.5, 123.0, 359.9])
    got = horizon_angle_at(profile, azs)
    assert isinstance(got, np.ndarray) and got.shape == azs.shape
    for i, a in enumerate(azs):
        assert abs(got[i] - horizon_angle_at(profile, float(a))) < 1e-9


# --------------------------------------------------------------------------
# Lookup-table binning.
# --------------------------------------------------------------------------

def test_nearest_bin_wraps_azimuth():
    assert _nearest_bin(359.0, 5.0) == 0        # 360 wraps to 0, not 360
    assert _nearest_bin(87.0, 5.0) == 85
    assert _nearest_bin(88.0, 5.0) == 90


def test_nearest_bin_clamps_slope():
    assert _nearest_bin(88.0, 5.0, max_value=60) == 60
    assert _nearest_bin(22.0, 5.0, max_value=60) == 20


# --------------------------------------------------------------------------
# Published assumptions. These are numbers the public reads.
# --------------------------------------------------------------------------

def test_total_losses_are_fourteen_percent_including_the_inverter():
    """Josh set the TOTAL at 14% including the inverter, so the thing to pin is
    the product, not either factor alone. Losses compound multiplicatively --
    3% inverter plus 11% everything-else is NOT 14% -- which is why the derate
    is 11.34 and not a round number.

    This test has now caught three deliberate changes to a published figure
    (0.81 -> 0.85 -> 0.86). That is what it is for."""
    pv = config.PV_ASSUMPTIONS
    factor = (pv["inverter_efficiency_pct"] / 100.0) * (1 - pv["system_derate_pct"] / 100.0)
    assert abs(factor - 0.86) < 5e-5, f"DC->AC {factor:.5f}, total loss {100*(1-factor):.2f}%"


def test_derate_stays_in_a_defensible_band():
    """A change that puts this outside 0.72-0.90 is either a typo or a decision
    that needs the public assumptions text updated with it -- either way it
    should stop the build. The upper bound was 0.85 and had to move when the
    total was set to 14%; it is a sanity rail, not a target."""
    pv = config.PV_ASSUMPTIONS
    factor = (pv["inverter_efficiency_pct"] / 100.0) * (1 - pv["system_derate_pct"] / 100.0)
    assert 0.72 <= factor <= 0.90, f"DC->AC factor {factor:.3f} outside sane band"


def test_inverter_loss_is_not_double_counted():
    """PVGIS folds inverter losses into its single loss number; we keep them
    separate. The comparison in validate_against_pvgis only holds if the
    system derate excludes them -- see the config breakdown."""
    pv = config.PV_ASSUMPTIONS
    assert pv["inverter_efficiency_pct"] < 100.0
    assert pv["system_derate_pct"] < 30.0, "derate looks like it swallowed the inverter"


# --------------------------------------------------------------------------
# Export cleanup. This one deletes files, so its refusal cases matter more
# than its happy path: a bug here either fills the disk (what happened) or
# throws away the only copy of a half-fetched multi-GB download.
# --------------------------------------------------------------------------

def _export_fixture(tmp, mosaic_bytes=b"x" * 1000):
    from pathlib import Path
    d = Path(tmp)
    z = d / "imagery_export.zip"
    z.write_bytes(b"z" * 5000)
    ex = d / "imagery"
    ex.mkdir()
    (ex / "a.tif").write_bytes(b"t" * 3000)
    m = d / "imagery_mosaic.tif"
    if mosaic_bytes is not None:
        m.write_bytes(mosaic_bytes)
    return z, ex, m


def test_export_cleanup_removes_intermediates_but_keeps_the_mosaic():
    import tempfile
    from src.fetch_data import reclaim_export_intermediates as reclaim
    with tempfile.TemporaryDirectory() as tmp:
        z, ex, m = _export_fixture(tmp)
        freed = reclaim(z, ex, m)
        assert freed == 8000, freed
        assert not z.exists() and not ex.exists()
        assert m.exists(), "the mosaic must never be deleted"


def test_export_cleanup_keeps_everything_when_the_mosaic_is_missing():
    """A failed merge must leave the multi-GB download in place to retry from."""
    import tempfile
    from src.fetch_data import reclaim_export_intermediates as reclaim
    with tempfile.TemporaryDirectory() as tmp:
        z, ex, m = _export_fixture(tmp, mosaic_bytes=None)
        assert reclaim(z, ex, m) == 0
        assert z.exists() and ex.exists()


def test_export_cleanup_keeps_everything_when_the_mosaic_is_empty():
    """A zero-byte mosaic means the merge failed, even though the file exists."""
    import tempfile
    from src.fetch_data import reclaim_export_intermediates as reclaim
    with tempfile.TemporaryDirectory() as tmp:
        z, ex, m = _export_fixture(tmp, mosaic_bytes=b"")
        assert reclaim(z, ex, m) == 0
        assert z.exists() and ex.exists()


def test_export_cleanup_can_be_opted_out_for_debugging():
    import tempfile
    from src.fetch_data import reclaim_export_intermediates as reclaim
    with tempfile.TemporaryDirectory() as tmp:
        z, ex, m = _export_fixture(tmp)
        assert reclaim(z, ex, m, keep=True) == 0
        assert z.exists() and ex.exists()


def test_export_cleanup_never_raises():
    """Cleanup must not fail a fetch: a full disk is recoverable, a
    half-fetched region is not."""
    import tempfile
    from pathlib import Path
    from src.fetch_data import reclaim_export_intermediates as reclaim
    with tempfile.TemporaryDirectory() as tmp:
        _, _, m = _export_fixture(tmp)
        assert reclaim(Path("/nonexistent/x.zip"), Path("/nonexistent/d"), m) == 0


def test_wide_dem_bbox_has_requested_metric_buffer():
    import pyproj
    from src.fetch_dem_wide import wide_dem_bbox_wgs84
    to_nztm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True)
    bbox = wide_dem_bbox_wgs84()
    min_x, min_y = to_nztm.transform(bbox[0], bbox[1])
    max_x, max_y = to_nztm.transform(bbox[2], bbox[3])
    district = [config.PILOT_BBOX, *config.REGIONS.values()]
    points = [to_nztm.transform(lon, lat)
              for item in district
              for lon, lat in ((item[0], item[1]), (item[2], item[3]))]
    assert min_x <= min(point[0] for point in points) - 29_999
    assert min_y <= min(point[1] for point in points) - 29_999
    assert max_x >= max(point[0] for point in points) + 29_999
    assert max_y >= max(point[1] for point in points) + 29_999


def test_wide_dem_fetch_skips_existing_mosaic():
    import tempfile
    from pathlib import Path
    from src.fetch_dem_wide import ensure_dem_wide
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "dem_wide_mosaic.tif"
        path.write_bytes(b"existing")
        assert ensure_dem_wide("unused", tmp) == path


# ---------------------------------------------------------------- quickstart

def _my_area(tmp, **fields):
    import json
    from pathlib import Path
    p = Path(tmp) / "my_area.json"
    p.write_text(json.dumps({"name": "qs_test", **fields}))
    return config._load_my_area(str(p))


def test_my_area_inside_a_survey_inherits_it_and_overrides_only_what_it_sets():
    """my_area.json's layer ids used to be written over the module constants,
    which survey_for never reads once SURVEYS exists -- so they were ignored."""
    import tempfile
    from src import surveys
    with tempfile.TemporaryDirectory() as tmp:
        bbox = [168.66, -45.033, 168.665, -45.029]
        ma = _my_area(tmp, bbox=bbox, dsm_layer=111)
        saved = list(config.SURVEYS)
        try:
            config.SURVEYS.append(ma["survey"])
            sv = surveys.survey_for(bbox, "qs_test")
            assert sv["dsm_layer"] == 111
            assert sv["imagery_layer"] == config.LINZ_IMAGERY_LAYER
            # ...and it speaks for that area only
            assert surveys.survey_for(bbox, "someone_else")["dsm_layer"] == config.LINZ_DSM_LAYER
        finally:
            config.SURVEYS[:] = saved


def test_my_area_outside_every_survey_needs_layers_and_inherits_nothing():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        wgtn = [174.775, -41.295, 174.78, -41.29]
        try:
            _my_area(tmp, bbox=wgtn)
            raise AssertionError("an unknown survey with no dsm_layer loaded")
        except ValueError as e:
            assert "dsm_layer" in str(e)
        sv = _my_area(tmp, bbox=wgtn, dsm_layer=5)["survey"]
        assert sv["dsm_layer"] == 5
        assert sv["imagery_layer"] is None            # not Queenstown's
        assert sv["pointcloud_bulk_url"] is None


def test_quickstart_area_wide_dem_is_its_own_not_the_district():
    """Folding a Wellington street into the district extent asked LINZ for a
    ~290,000 km2 DEM. An area's own is its bbox plus 30 km."""
    import pyproj
    from src.fetch_dem_wide import wide_dem_bbox_wgs84
    t = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True)
    w, s, e, n = wide_dem_bbox_wgs84([[174.775, -41.295, 174.78, -41.29]])
    x0, y0 = t.transform(w, s)
    x1, y1 = t.transform(e, n)
    assert (x1 - x0) * (y1 - y0) / 1e6 < 4_500


def test_build_does_not_require_the_optional_vision_precompute():
    from src.preflight import REQUIRED
    spec = REQUIRED["build_layout_geojson"]
    assert "selected_faces" not in spec.get("root", [])
    assert "selected_faces" in spec.get("optional_root", [])


def test_empty_pointcloud_directory_is_an_empty_source():
    import tempfile
    from src.pointcloud_source import PointCloudSource
    with tempfile.TemporaryDirectory() as tmp:
        pc = PointCloudSource(tmp)
        assert pc.points_in_bbox(0, 0, 1, 1).shape == (0, 3)
        assert pc.ground_points_in_bbox(0, 0, 1, 1).shape == (0, 3)


# --------------------------------------------------------------------------

def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  pass  {name}")
        except AssertionError as e:
            failed.append((name, str(e) or "assertion failed"))
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
