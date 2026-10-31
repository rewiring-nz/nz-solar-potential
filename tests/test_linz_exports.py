"""Offline tests for the per-council LINZ GeoPackage outline source and the
locality-id defaults. No network: the GeoPackages here are written locally."""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src import build_keys, linz_exports, localities, region_ids
from tools import pipeline


def _write_gpkg(path: Path, rows: list[dict]) -> None:
    import geopandas as gpd
    from shapely.geometry import MultiPolygon, box
    geoms = [MultiPolygon([box(*r.pop("box"))]) if not r.get("two_parts") else
             MultiPolygon([box(0, 0, 1, 1), box(5, 5, 6, 6)]) for r in rows]
    for r in rows:
        r.pop("two_parts", None)
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:2193")
    gdf.to_file(path, driver="GPKG", engine="pyogrio")


def _row(bid, suburb, town, ta, **extra):
    row = {"building_id": bid, "suburb_locality": suburb, "town_city": town,
           "territorial_authority": ta, "last_modified": dt.datetime(2024, 3, 1),
           "box": (1_260_000 + bid, 5_000_000, 1_260_005 + bid, 5_000_005)}
    row.update(extra)
    return row


class OutlineWhereTests(unittest.TestCase):
    def test_matches_wfs_filter_and_quotes(self) -> None:
        props = {"name": "O'Neill Bay", "major_name": "Queenstown",
                 "territorial_authority": "Queenstown-Lakes District"}
        where = linz_exports.outline_where(
            props["name"], props["major_name"], localities.territorial_authorities(props))
        self.assertEqual(where, localities.outline_filter(props))
        self.assertIn("'O''Neill Bay'", where)

    def test_no_town(self) -> None:
        where = linz_exports.outline_where("Closeburn", None, ["Queenstown-Lakes District"])
        self.assertIn("(town_city IS NULL OR town_city = '')", where)


class ReadOutlinesTests(unittest.TestCase):
    def test_selects_unwraps_formats_and_dedups_across_councils(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.gpkg", Path(d) / "b.gpkg"
            _write_gpkg(a, [
                _row(1, "Sunshine Bay", "Queenstown", "Queenstown-Lakes District"),
                _row(2, "Sunshine Bay", "Queenstown", "Queenstown-Lakes District", two_parts=True),
                _row(3, "Fernhill", "Queenstown", "Queenstown-Lakes District"),
            ])
            _write_gpkg(b, [
                _row(1, "Sunshine Bay", "Queenstown", "Queenstown-Lakes District"),
                _row(4, "Sunshine Bay", "Queenstown", "Central Otago District"),
            ])
            where = linz_exports.outline_where(
                "Sunshine Bay", "Queenstown",
                ["Queenstown-Lakes District", "Central Otago District"])
            feats = linz_exports.read_outlines([a, b], where)
        self.assertEqual([f["properties"]["building_id"] for f in feats], [1, 2, 4])
        by_id = {f["properties"]["building_id"]: f for f in feats}
        self.assertEqual(by_id[1]["geometry"]["type"], "Polygon")
        self.assertEqual(by_id[2]["geometry"]["type"], "MultiPolygon")
        self.assertEqual(by_id[1]["id"], "layer-101290.1")
        self.assertEqual(by_id[1]["properties"]["last_modified"], "2024-03-01Z")


class LoadOutlinesTests(unittest.TestCase):
    PROPS = {"name": "Sunshine Bay", "major_name": "Queenstown",
             "territorial_authority": "Queenstown-Lakes District"}

    def test_falls_back_to_wfs_with_reason(self) -> None:
        wfs = {"type": "FeatureCollection", "features": []}
        with patch.object(localities, "outlines_from_councils",
                          side_effect=PermissionError("no export permission")), \
             patch.object(localities, "fetch_outlines", return_value=wfs), \
             patch.dict("os.environ", {"SOLAR_OUTLINE_SOURCE": "geopackage"}):
            got, source = localities.load_outlines("k", self.PROPS)
        self.assertIs(got, wfs)
        self.assertEqual(source["method"], "wfs")
        self.assertIn("no export permission", source["fallback_reason"])

    def test_wfs_on_request(self) -> None:
        with patch.object(localities, "outlines_from_councils",
                          side_effect=AssertionError("must not export")), \
             patch.object(localities, "fetch_outlines", return_value={}), \
             patch.dict("os.environ", {"SOLAR_OUTLINE_SOURCE": "wfs"}):
            _, source = localities.load_outlines("k", self.PROPS)
        self.assertEqual(source, {"method": "wfs"})


class DefaultRegionTests(unittest.TestCase):
    def test_default_is_a_locality_id(self) -> None:
        region = pipeline._default_region(config)
        self.assertTrue(region_ids.is_locality_id(region))
        self.assertEqual(region, config.CLOUD_SHARED_BUILD["local_region"])

    def test_falls_back_to_pipeline_region(self) -> None:
        class Legacy:
            CLOUD_SHARED_BUILD = {}
            PIPELINE_REGION = "town_gorge_north"
        self.assertEqual(pipeline._default_region(Legacy), "town_gorge_north")

    def test_legacy_names_warn_and_localities_do_not(self) -> None:
        self.assertIsNone(pipeline._legacy_region_warning(
            "queenstown-lakes-district/queenstown/sunshine-bay", config.REGIONS))
        msg = pipeline._legacy_region_warning("town_gorge_north", config.REGIONS)
        self.assertIn("deprecated", msg)
        self.assertIn("town_gorge_north", msg)

    def test_defaults_live_in_the_block_build_keys_leave_out(self) -> None:
        # Moving the default must not change every stage's code hash (a full
        # rebuild of every published region): the default sits inside
        # CLOUD_SHARED_BUILD, which build_keys strips before hashing.
        hashed = build_keys._hashed_bytes(ROOT / "config.py").decode("utf-8")
        self.assertNotIn("local_region", hashed)
        self.assertNotIn("sunshine-bay", hashed)


class CloudFetchTests(unittest.TestCase):
    def test_locality_regions_fetch_on_the_vm(self) -> None:
        cfg = dict(pipeline._load_shared_cloud_config(), preview_user="alice")
        captured = {}

        class Done(Exception):
            pass

        def fake_popen(command, **kwargs):
            captured["command"] = command
            raise Done

        with patch.object(pipeline.subprocess, "Popen", side_effect=fake_popen):
            try:
                pipeline._run_shared_cloud_build(
                    cfg, "queenstown-lakes-district/queenstown/sunshine-bay",
                    pipeline.CloudQuickstartLog())
            except Done:
                pass
        self.assertIn("--fetch", captured.get("command", []))


if __name__ == "__main__":
    unittest.main()
