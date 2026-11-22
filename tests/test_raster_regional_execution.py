"""Revalidate and acquire regional snapshots using local raster fixtures."""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from src import fetch_regions, s3_rasters
from src.raster_cache import CachePolicy, RasterCache, RasterCacheError, RasterTransferError
from src.raster_disk_plan import DiskRequirements
from src.raster_execution import RasterProcessing
from src.raster_plan_handoff import plan_digest
from src.raster_provenance import verify_mosaic
from src.raster_region_plan import plan_regions
from src.raster_regional_execution import admitted_regional_rasters
from src.raster_regional_job import run_regional_command
from src.raster_reservations import (
    DiskCapacityError, DiskReservationError, DiskReservations, QueueCleanupPolicy,
)
from src.raster_source_selection import LayerMapping, load_layer_mappings


@unittest.skipUnless(shutil.which("gdalbuildvrt") and shutil.which("gdal_translate"),
                     "GDAL CLI tools not installed")
class RegionalRasterExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.regions = self.root / "regions"
        (self.regions / "area").mkdir(parents=True)
        tile = self.root / "source.tif"
        with rasterio.open(tile, "w", driver="GTiff", count=1, width=8, height=8,
                           dtype="float32", crs="EPSG:2193", nodata=-9999,
                           transform=from_origin(1200000, 5000000, 8, 8)) as dataset:
            dataset.write(np.ones((8, 8), dtype="float32"), 1)
            self.bbox = list(transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds,
                                              densify_pts=21))
        payload = tile.read_bytes()
        self.survey = {"name": "fixture", "elevation": "lidar",
                       "dsm_layer": 101, "imagery_layer": 202}
        self.mappings = [LayerMapping(101, "dsm", "https://example.test/collection.json",
                                      "capture", "data", "fixture evidence")]
        self.docs = {
            "https://example.test/collection.json": {
                "id": "capture", "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "tile", "collection": "capture", "bbox": self.bbox, "properties": {},
                "assets": {"data": {"href": tile.as_uri(), "type": "image/tiff",
                                    "file:size": len(payload),
                                    "file:checksum": "1220" + hashlib.sha256(payload).hexdigest()}}},
        }
        self.snapshot = plan_regions(
            ["area"], mappings=self.mappings, bbox_for=lambda _: self.bbox,
            footprints_for=lambda *_: [self.bbox], survey_for=lambda *_: self.survey,
            get_json=self.docs.__getitem__)
        self.cache = RasterCache(self.root / "cache", CachePolicy(10000000, 0, 1000000))
        self.reservations = DiskReservations(self.root / "registry", probe=lambda _: (1, 100000000))
        self.requirements = DiskRequirements(self.cache.directory, self.root, self.root,
                                             100000, 100000, 100000, 0, 100000)
        self.processing = {("area", "dsm"): RasterProcessing()}

    def execute(self, *, snapshot=None, mappings=None, processing=None, imagery=True):
        return admitted_regional_rasters(
            self.snapshot if snapshot is None else snapshot, ["area"],
            self.mappings if mappings is None else mappings,
            regions_dir=self.regions, api_key="fixture", include_imagery=imagery,
            cache=self.cache, reservations=self.reservations, requirements=self.requirements,
            processing=self.processing if processing is None else processing)

    def run_job(self, command, digest=None):
        return run_regional_command(
            self.snapshot, plan_digest(self.snapshot) if digest is None else digest,
            ["area"], self.mappings, command, cwd=self.root, env=os.environ,
            regions_dir=self.regions, api_key="fixture", include_imagery=True,
            cache=self.cache, reservations=self.reservations, requirements=self.requirements,
            processing=self.processing)

    def export_fixture(self, footprints, key, layer, name, directory, format_key):
        (directory / f"{name}_mosaic.tif").write_bytes(b"fixture export")

    def test_acquires_approved_mapped_and_fallback_inputs_inside_reservation(self):
        def export(footprints, key, layer, name, directory, format_key):
            self.assertEqual((footprints, key, layer, name, format_key),
                             ([self.bbox], "fixture", 202, "imagery", "raster"))
            self.assertEqual(len(list(self.reservations.directory.glob("*.json"))), 1)
            (directory / "imagery_mosaic.tif").write_bytes(b"fixture export")

        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch("src.raster_regional_execution.fetch_raster_footprints", side_effect=export):
            with self.execute() as result:
                self.assertTrue(result["forecast"]["reservation"])
                dsm = result["destinations"][("area", "dsm")]
                self.assertEqual(verify_mosaic(dsm, dsm.with_name(dsm.name + ".source.json"))["layer_id"], 101)
                self.assertEqual(len(list(self.reservations.directory.glob("*.json"))), 1)
            self.assertEqual(list(self.reservations.directory.glob("*.json")), [])
            with self.execute():
                pass

    def test_snapshot_mappings_can_revalidate_and_acquire_without_mapping_file(self):
        with (patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox),
              patch("src.raster_regional_execution.survey_for", return_value=self.survey),
              patch("src.raster_regional_execution.fetch_raster_footprints",
                    side_effect=self.export_fixture)):
            with admitted_regional_rasters(
                self.snapshot, ["area"], None, regions_dir=self.regions, api_key="fixture",
                include_imagery=True, cache=self.cache, reservations=self.reservations,
                requirements=self.requirements, processing=self.processing) as result:
                self.assertTrue(result["destinations"][("area", "dsm")].is_file())

    def test_fetch_mapped_executor_uses_existing_admission_and_checks_all_destinations(self):
        bounds = {
            "cache": {"directory": str(self.cache.directory), "max_bytes": 10000000,
                      "min_free_bytes": 0, "unknown_asset_bytes": 1000000},
            "work_dir": str(self.root), "output_dir": str(self.root),
            "mosaic_bytes": 100000, "scratch_bytes": 100000, "output_bytes": 100000,
            "export_bytes": 100000, "extra_allocations": [],
            "processing": {"dsm": {"resolution": None, "resampling": "average"}},
        }
        verify = unittest.mock.Mock()
        with patch.object(fetch_regions, "REGIONS_DIR", self.regions):
            acquire = fetch_regions.prepare_mapped_fetch(self.snapshot, bounds, "area", verify)
            self.assertEqual(acquire(("area", "dsm")),
                             (self.regions / "area" / "dsm_mosaic.tif").resolve())
            verify_mosaic(self.regions / "area" / "dsm_mosaic.tif",
                          self.regions / "area" / "dsm_mosaic.tif.source.json")
            self.assertEqual(verify.call_count, 2)
            self.assertFalse((self.regions / "area" / "imagery_mosaic.tif").exists())

    def test_fetch_mapped_executor_rejects_unverified_existing_mosaic(self):
        bounds = {
            "cache": {"directory": str(self.cache.directory), "max_bytes": 10000000,
                      "min_free_bytes": 0, "unknown_asset_bytes": 1000000},
            "work_dir": str(self.root), "output_dir": str(self.root),
            "mosaic_bytes": 100000, "scratch_bytes": 100000, "output_bytes": 100000,
            "export_bytes": 100000, "extra_allocations": [],
            "processing": {"dsm": {"resolution": None, "resampling": "average"}},
        }
        (self.regions / "area" / "dsm_mosaic.tif").write_bytes(b"legacy")
        with patch.object(fetch_regions, "REGIONS_DIR", self.regions):
            with self.assertRaises((ValueError, RuntimeError)):
                fetch_regions.prepare_mapped_fetch(self.snapshot, bounds, "area",
                                                  unittest.mock.Mock())
        self.assertFalse(self.cache.directory.exists())

    def test_live_fetch_uses_approved_mapped_and_export_inputs_under_active_reservation(self):
        from src.raster_job_admission import ADMISSION_ENV, admission_binding
        from src.raster_job_bounds import job_resources

        snapshot_path = self.root / "job.json"
        snapshot_path.write_text(json.dumps({"raster_plan": self.snapshot}))
        digest = plan_digest(self.snapshot)
        bounds = {
            "version": 1, "region": "area", "workspace": str(self.root.parent.resolve()),
            "plan_sha256": digest, "include_imagery": True, "paid_referee": False,
            "evidence": "fixture", "cache_policy": str(self.root / "policy.json"),
            "work_dir": str(self.root), "output_dir": str(self.root),
            "mosaic_bytes": 100000, "scratch_bytes": 100000, "output_bytes": 100000,
            "export_bytes": 100000, "extra_allocations": [],
            "cache": {"directory": str(self.cache.directory), "max_bytes": 10000000,
                      "min_free_bytes": 0, "unknown_asset_bytes": 1000000},
            "processing": {"dsm": {"resolution": None, "resampling": "average"}},
        }
        cache, requirements = job_resources(bounds)
        registry = DiskReservations(cache.directory / "reservations")
        with registry.reserve(lambda: cache.inspect_plan(self.snapshot["batch"]), requirements,
                              binding=admission_binding(bounds)) as forecast:
            env = {"SOLAR_RASTER_PLAN_JOB": str(snapshot_path),
                   "SOLAR_RASTER_PLAN_SHA256": digest, "SOLAR_INCLUDE_IMAGERY": "1",
                   "SOLAR_DEMOLISHED_REFEREE": "0",
                   ADMISSION_ENV: json.dumps({"bounds": bounds,
                                               "reservation_id": forecast["reservation_id"]})}
            with (patch.dict(os.environ, env),
                  patch.object(sys, "argv", ["fetch_regions.py", "area"]),
                  patch.object(fetch_regions, "DATA_DIR", self.root),
                  patch.object(fetch_regions, "REGIONS_DIR", self.regions),
                  patch.object(fetch_regions, "load_dotenv"),
                  patch("src.project_secrets.linz_api_key", return_value="fixture"),
                  patch.object(fetch_regions, "ensure_dem_wide"),
                  patch.object(fetch_regions, "area_bbox_wgs84", return_value=self.bbox),
                  patch.object(fetch_regions, "acquisition_footprints", return_value=[self.bbox]),
                  patch.object(fetch_regions, "survey_for", return_value=self.survey),
                  patch.object(fetch_regions, "fetch_building_outlines", return_value={"features": []}),
                  patch.object(fetch_regions, "fetch_raster_footprints",
                               side_effect=self.export_fixture) as exports,
                  patch("src.fetch_pointcloud_regions.main", return_value=[])):
                fetch_regions.main()
                self.assertEqual([call.args[2] for call in exports.call_args_list], [202])
                self.assertTrue((self.regions / "area" / "imagery_mosaic.tif").exists())
                verify_mosaic(self.regions / "area" / "dsm_mosaic.tif",
                              self.regions / "area" / "dsm_mosaic.tif.source.json")
                self.assertEqual(len(list(registry.directory.glob("*.json"))), 1)

    def test_live_mapped_imagery_degrades_on_network_failure_not_integrity_failure(self):
        from src.raster_job_admission import ADMISSION_ENV, admission_binding
        from src.raster_job_bounds import job_resources
        from src.raster_prepare import ensure_planned_region

        imagery_mapping = LayerMapping(202, "imagery", self.mappings[0].collection_url,
                                       "capture", "data", "fixture imagery evidence")
        snapshot = plan_regions(
            ["area"], mappings=[*self.mappings, imagery_mapping],
            bbox_for=lambda _: self.bbox, footprints_for=lambda *_: [self.bbox],
            survey_for=lambda *_: self.survey, get_json=self.docs.__getitem__)
        job = self.root / "imagery-job.json"
        job.write_text(json.dumps({"raster_plan": snapshot}))
        digest = plan_digest(snapshot)
        bounds = {
            "version": 1, "region": "area", "workspace": str(self.root.parent.resolve()),
            "plan_sha256": digest, "include_imagery": True, "paid_referee": False,
            "evidence": "fixture", "cache_policy": str(self.root / "policy.json"),
            "work_dir": str(self.root), "output_dir": str(self.root),
            "mosaic_bytes": 100000, "scratch_bytes": 100000, "output_bytes": 100000,
            "export_bytes": 100000, "extra_allocations": [],
            "cache": {"directory": str(self.cache.directory), "max_bytes": 10000000,
                      "min_free_bytes": 0, "unknown_asset_bytes": 1000000},
            "processing": {role: {"resolution": None, "resampling": "average"}
                           for role in ("dsm", "imagery")},
        }
        cache, requirements = job_resources(bounds)
        registry = DiskReservations(cache.directory / "reservations")
        with registry.reserve(lambda: cache.inspect_plan(snapshot["batch"]), requirements,
                              binding=admission_binding(bounds)) as forecast:
            env = {"SOLAR_RASTER_PLAN_JOB": str(job), "SOLAR_RASTER_PLAN_SHA256": digest,
                   "SOLAR_INCLUDE_IMAGERY": "1", "SOLAR_DEMOLISHED_REFEREE": "0",
                   ADMISSION_ENV: json.dumps({"bounds": bounds,
                                               "reservation_id": forecast["reservation_id"]})}
            with (patch.dict(os.environ, env),
                  patch.object(sys, "argv", ["fetch_regions.py", "area"]),
                  patch.object(fetch_regions, "DATA_DIR", self.root),
                  patch.object(fetch_regions, "REGIONS_DIR", self.regions),
                  patch.object(fetch_regions, "load_dotenv"),
                  patch("src.project_secrets.linz_api_key", return_value="fixture"),
                  patch.object(fetch_regions, "ensure_dem_wide"),
                  patch.object(fetch_regions, "area_bbox_wgs84", return_value=self.bbox),
                  patch.object(fetch_regions, "acquisition_footprints", return_value=[self.bbox]),
                  patch.object(fetch_regions, "survey_for", return_value=self.survey),
                  patch.object(fetch_regions, "fetch_building_outlines", return_value={"features": []}),
                  patch("src.fetch_pointcloud_regions.main", return_value=[])):
                for error, should_raise in ((RasterTransferError("offline"), False),
                                            (RasterCacheError("bad checksum"), True)):
                    def acquire(cache, plan, region, role, path, **kwargs):
                        if role == "imagery":
                            raise error
                        return ensure_planned_region(cache, plan, region, role, path, **kwargs)

                    with patch("src.raster_execution.ensure_planned_region", side_effect=acquire):
                        if should_raise:
                            with self.assertRaisesRegex(RasterCacheError, "bad checksum"):
                                fetch_regions.main()
                        else:
                            fetch_regions.main()
                            self.assertFalse((self.regions / "area" / "imagery_mosaic.tif").exists())

    def test_changed_selection_is_refused_before_any_acquisition(self):
        cases = [
            ({"area_bbox_wgs84": self.bbox, "survey_for": {**self.survey, "dsm_layer": 999}},
             self.mappings, self.processing),
            ({"area_bbox_wgs84": [168, -45, 169, -44], "survey_for": self.survey},
             self.mappings, self.processing),
            ({"area_bbox_wgs84": self.bbox, "survey_for": self.survey}, [], self.processing),
            ({"area_bbox_wgs84": self.bbox, "survey_for": self.survey}, self.mappings, {}),
        ]
        for values, mappings, processing in cases:
            with self.subTest(values=values, mappings=mappings, processing=processing), \
                    patch("src.raster_regional_execution.area_bbox_wgs84",
                          return_value=values["area_bbox_wgs84"]), \
                    patch("src.raster_regional_execution.survey_for",
                          return_value=values["survey_for"]), \
                    patch("src.raster_regional_execution.fetch_raster_footprints") as export:
                with self.assertRaises((ValueError, RuntimeError)):
                    with self.execute(mappings=mappings, processing=processing):
                        self.fail("changed selection admitted")
                export.assert_not_called()
                self.assertFalse((self.regions / "area" / "dsm_mosaic.tif").exists())

    def test_malformed_snapshot_fails_before_download(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["batch"]["export_fallbacks"].clear()
        with self.assertRaisesRegex(ValueError, "missing selected fallbacks"):
            with self.execute(snapshot=snapshot):
                self.fail("incomplete plan admitted")

    def test_region_on_unreserved_filesystem_is_refused(self):
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch("src.raster_regional_execution._device", side_effect=[1, 2]), \
                patch("src.raster_regional_execution.fetch_raster_footprints") as export:
            with self.assertRaisesRegex(ValueError, "reserved work filesystem"):
                with self.execute():
                    self.fail("unreserved filesystem admitted")
            export.assert_not_called()

    def test_existing_unverified_mapped_mosaic_is_not_overwritten(self):
        destination = self.regions / "area" / "dsm_mosaic.tif"
        destination.write_bytes(b"legacy")
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch("src.raster_regional_execution.fetch_raster_footprints") as export:
            with self.assertRaises(ValueError):
                with self.execute():
                    self.fail("unverified raster adopted")
            export.assert_not_called()
        self.assertEqual(destination.read_bytes(), b"legacy")
        self.assertEqual(list(self.reservations.directory.glob("*.json")), [])

    def test_existing_unverified_mapped_imagery_fails_before_dsm_export(self):
        mappings = [LayerMapping(202, "imagery", "https://example.test/collection.json",
                                 "capture", "data", "fixture evidence")]
        snapshot = plan_regions(
            ["area"], mappings=mappings, bbox_for=lambda _: self.bbox,
            footprints_for=lambda *_: [self.bbox], survey_for=lambda *_: self.survey,
            get_json=self.docs.__getitem__)
        destination = self.regions / "area" / "imagery_mosaic.tif"
        destination.write_bytes(b"legacy")
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch("src.raster_regional_execution.fetch_raster_footprints",
                      side_effect=self.export_fixture) as export, \
                patch.object(self.cache, "_download", wraps=self.cache._download) as download:
            with self.assertRaises(ValueError):
                with self.execute(snapshot=snapshot, mappings=mappings,
                                  processing={("area", "imagery"): RasterProcessing()}):
                    self.fail("unverified raster adopted")
            export.assert_not_called()
            download.assert_not_called()
        self.assertEqual(destination.read_bytes(), b"legacy")
        self.assertFalse((self.regions / "area" / "dsm_mosaic.tif").exists())
        self.assertEqual(list(self.reservations.directory.glob("*.json")), [])

    def test_export_failure_is_reported_and_reservation_released(self):
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch("src.raster_regional_execution.fetch_raster_footprints",
                      side_effect=RuntimeError("export failed")):
            with self.assertRaisesRegex(RuntimeError, "export failed"):
                with self.execute():
                    self.fail("failed export accepted")
        self.assertEqual(list(self.reservations.directory.glob("*.json")), [])

    def test_capacity_failure_precedes_transfers(self):
        self.reservations = DiskReservations(self.root / "registry", probe=lambda _: (1, 0))
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch("src.raster_regional_execution.fetch_raster_footprints") as export, \
                patch.object(self.cache, "_download", wraps=self.cache._download) as download:
            with self.assertRaises(DiskCapacityError):
                with self.execute():
                    self.fail("insufficient space admitted")
            export.assert_not_called()
            download.assert_not_called()

    def test_imagery_exclusion_needs_no_exports_key(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["regions"][0]["inputs"][1] = {
            "role": "imagery", "state": "excluded", "layer_id": 202}
        snapshot["regions"][0]["inputs"][2] = {
            "role": "reference_imagery", "state": "excluded", "layer_id": None}
        snapshot["batch"]["inputs"] = [
            item for item in snapshot["batch"]["inputs"] if item["role"] == "dsm"]
        snapshot["batch"]["export_fallbacks"] = []
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch("src.raster_regional_execution.fetch_raster_footprints") as export:
            with admitted_regional_rasters(
                snapshot, ["area"], self.mappings, regions_dir=self.regions, api_key="",
                include_imagery=False, cache=self.cache, reservations=self.reservations,
                requirements=self.requirements, processing=self.processing) as result:
                self.assertEqual(set(result["destinations"]), {("area", "dsm")})
            export.assert_not_called()

    def test_synthetic_without_imagery_still_holds_whole_job_reservation(self):
        survey = {"name": "synthetic", "elevation": "synthetic", "dsm_layer": None,
                  "imagery_layer": None}
        snapshot = plan_regions(
            ["area"], mappings=[], include_imagery=False,
            bbox_for=lambda _: self.bbox, footprints_for=lambda *_: [self.bbox],
            survey_for=lambda *_: survey)
        self.assertIsNone(snapshot["batch"])
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=survey), \
                patch("src.raster_regional_execution.fetch_raster_footprints") as export:
            with admitted_regional_rasters(
                snapshot, ["area"], [], regions_dir=self.regions, api_key="",
                include_imagery=False, cache=self.cache, reservations=self.reservations,
                requirements=self.requirements, processing={}) as result:
                self.assertEqual(result["destinations"], {})
                self.assertTrue(result["forecast"]["reservation"])
                self.assertEqual(len(list(self.reservations.directory.glob("*.json"))), 1)
            export.assert_not_called()
        self.assertEqual(list(self.reservations.directory.glob("*.json")), [])

    def test_synthetic_without_rasters_supports_queue_cleanup(self):
        survey = {"name": "synthetic", "elevation": "synthetic", "dsm_layer": None,
                  "imagery_layer": None}
        snapshot = plan_regions(
            ["area"], mappings=[], include_imagery=False,
            bbox_for=lambda _: self.bbox, footprints_for=lambda *_: [self.bbox],
            survey_for=lambda *_: survey)
        with self.cache.lease(self.snapshot["batch"]["assets"][0]) as path:
            cached = path
        policy = QueueCleanupPolicy(1, 0, 0, 3600, high_water_bytes=1, target_bytes=0)
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=survey):
            with admitted_regional_rasters(
                snapshot, ["area"], [], regions_dir=self.regions, api_key="",
                include_imagery=False, cache=self.cache, reservations=self.reservations,
                requirements=self.requirements, processing={}, cleanup_policy=policy) as result:
                self.assertTrue(result["forecast"]["reservation"])
                self.assertTrue(result["forecast"]["cleanup"]["proactive"]["target_reached"])
                self.assertFalse(cached.exists())

    def test_two_regions_share_one_native_download(self):
        (self.regions / "other").mkdir()
        snapshot = plan_regions(
            ["area", "other"], mappings=self.mappings, include_imagery=False,
            bbox_for=lambda _: self.bbox, footprints_for=lambda *_: [self.bbox],
            survey_for=lambda *_: self.survey, get_json=self.docs.__getitem__)
        self.assertEqual(len(snapshot["batch"]["assets"]), 1)
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch.object(self.cache, "_download", wraps=self.cache._download) as download:
            with admitted_regional_rasters(
                snapshot, ["area", "other"], self.mappings,
                regions_dir=self.regions, api_key="", include_imagery=False,
                cache=self.cache, reservations=self.reservations, requirements=self.requirements,
                processing={("area", "dsm"): RasterProcessing(),
                            ("other", "dsm"): RasterProcessing()}) as result:
                self.assertEqual(set(result["destinations"]),
                                 {("area", "dsm"), ("other", "dsm")})
                for destination in result["destinations"].values():
                    self.assertTrue(destination.is_file())
            self.assertEqual(download.call_count, 1)

    def test_national_dsm_two_areas_plan_acquire_and_reuse_payload(self):
        (self.regions / "other").mkdir()
        mapping_path = Path(__file__).resolve().parents[1] / "tools" / "national-dsm-mapping.json"
        mappings = load_layer_mappings(mapping_path)
        mapping = mappings[0]
        west, south, east, north = self.bbox
        dx, dy = east - west, north - south
        boxes = {
            "area": [west + dx * .1, south + dy * .1, east - dx * .2, north - dy * .2],
            "other": [west + dx * .2, south + dy * .2, east - dx * .1, north - dy * .1],
        }
        polygon = {"type": "Polygon", "coordinates": [[
            [west, south], [east, south], [east, north], [west, north], [west, south]]]}
        survey = {**self.survey, "raster_dsm_layer": 122082}
        tile = self.root / "source.tif"
        with rasterio.open(tile, "r+") as dataset:
            values = np.arange(64, dtype="float32").reshape(8, 8)
            values[4, 4] = -9999
            dataset.write(values, 1)
        payload = tile.read_bytes()
        base = mapping.collection_url.rsplit("/", 1)[0] + "/"
        documents = {
            mapping.collection_url: {
                "id": mapping.collection_id, "license": "CC-BY-4.0",
                "providers": [{"name": "LINZ", "roles": ["producer"]}],
                "links": [{"rel": "item", "href": "tile.json"}],
                "assets": {"capture_area": {"href": "capture-area.geojson"},
                           "capture_dates": {"href": "capture-dates.geojson"}}},
            base + "tile.json": {
                "id": "tile", "collection": mapping.collection_id, "bbox": self.bbox,
                "properties": {"updated": "one", "proj:code": "EPSG:2193"},
                "assets": {"visual": {"href": tile.as_uri(), "type": "image/tiff",
                                       "file:size": len(payload),
                                       "file:checksum": "1220" + hashlib.sha256(payload).hexdigest()}}},
            base + "capture-area.geojson": {"type": "Feature", "geometry": polygon},
            base + "capture-dates.geojson": {
                "type": "FeatureCollection", "features": [{
                    "type": "Feature", "geometry": polygon,
                    "properties": {"id": "capture", "title": "fixture survey",
                                   "source": "s3://example.test/fixture",
                                   "flown_from": "2021-01-01", "flown_to": "2021-01-02"}}]},
        }
        snapshot = plan_regions(
            ["area", "other"], mappings=mappings, include_imagery=False,
            bbox_for=boxes.__getitem__, footprints_for=lambda _, bbox: [bbox],
            survey_for=lambda *_: survey, get_json=documents.__getitem__)
        self.assertEqual(len(snapshot["batch"]["assets"]), 1)
        self.assertEqual(snapshot["batch"]["assets"][0]["regions"], ["area", "other"])
        transfers = []
        original_open = urllib.request.urlopen

        @contextmanager
        def count_payload(url, **kwargs):
            counter = {"url": url, "bytes": 0}
            transfers.append(counter)
            with original_open(url, **kwargs) as response:
                class Reader:
                    def read(self, size):
                        data = response.read(size)
                        counter["bytes"] += len(data)
                        return data
                yield Reader()

        with (patch("src.raster_regional_execution.area_bbox_wgs84", side_effect=boxes.__getitem__),
              patch("src.raster_regional_execution.survey_for", return_value=survey),
              patch("src.raster_cache.urllib.request.urlopen", side_effect=count_payload)):
            for region in ("area", "other"):
                with admitted_regional_rasters(
                    snapshot, [region], mappings, regions_dir=self.regions, api_key="",
                    include_imagery=False, cache=self.cache, reservations=self.reservations,
                    requirements=self.requirements,
                    processing={(region, "dsm"): RasterProcessing()}) as result:
                    mosaic = result["destinations"][(region, "dsm")]
                    manifest = verify_mosaic(mosaic, mosaic.with_name(mosaic.name + ".source.json"))
                    self.assertEqual(manifest["layer_id"], 122082)
                    self.assertEqual(manifest["sources"][0]["license"], "CC-BY-4.0")
                    self.assertEqual(manifest["capture_surveys"][0]["surveys"][0]["flown_from"],
                                     "2021-01-01")
                    self.assertEqual(manifest["capture_coverage"][0]["region"], region)
                    x0, y0, x1, y1 = s3_rasters._nztm_bounds(boxes[region])
                    reference = self.root / f"{region}-reference.tif"
                    subprocess.run(["gdal_translate", "-q", "-projwin", str(x0), str(y1),
                                    str(x1), str(y0), str(tile), str(reference)], check=True)
                    with rasterio.open(mosaic) as raster, rasterio.open(reference) as expected:
                        self.assertEqual(raster.crs.to_epsg(), 2193)
                        self.assertEqual(raster.transform, expected.transform)
                        self.assertEqual(raster.shape, expected.shape)
                        self.assertEqual(raster.nodata, expected.nodata)
                        np.testing.assert_array_equal(raster.read(), expected.read())
                        np.testing.assert_array_equal(raster.read_masks(), expected.read_masks())
                        self.assertTrue(np.any(raster.read_masks() == 0))
                        self.assertTrue(np.any(raster.read_masks() == 255))
                self.assertEqual(sum(item["bytes"] for item in transfers), len(payload))
                self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0]["url"], tile.as_uri())

    def test_supervised_command_holds_reservation_after_acquisition(self):
        script = (
            "import pathlib,sys\n"
            f"assert len(list(pathlib.Path({str(self.reservations.directory)!r}).glob('*.json'))) == 1\n"
            f"assert pathlib.Path({str(self.regions / 'area' / 'dsm_mosaic.tif')!r}).is_file()\n"
            f"assert pathlib.Path({str(self.regions / 'area' / 'imagery_mosaic.tif')!r}).is_file()\n"
            "raise SystemExit(int(sys.argv[1]))\n"
        )
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch("src.raster_regional_execution.fetch_raster_footprints",
                      side_effect=self.export_fixture):
            for code in (0, 7):
                result = self.run_job([sys.executable, "-c", script, str(code)])
                self.assertEqual(result["exit_code"], code)
                self.assertTrue(result["forecast"]["reservation"])
                self.assertEqual(list(self.reservations.directory.glob("*.json")), [])

    def test_supervised_job_retains_record_on_surviving_child(self):
        marker = self.root / "stopped"
        script = ("import os,pathlib,time\n"
                  "if os.fork() == 0:\n"
                  " time.sleep(0.7)\n"
                  f" pathlib.Path({str(marker)!r}).touch()\n"
                  " os._exit(0)\n")
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch("src.raster_regional_execution.fetch_raster_footprints",
                      side_effect=self.export_fixture):
            with self.assertRaisesRegex(DiskReservationError, "Consumers may still be running"):
                self.run_job([sys.executable, "-c", script])
            record, = self.reservations.directory.glob("*.json")
            with self.assertRaisesRegex(DiskReservationError, "Orphaned disk reservation"):
                self.run_job([sys.executable, "-c", "pass"])
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists())
            self.reservations.recover_orphan(record.stem, consumers_stopped=True)

    def test_supervised_job_stops_command_on_interruption(self):
        script = ("import os,signal,time\n"
                  "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                  "time.sleep(0.2)\n"
                  "os.kill(os.getppid(), signal.SIGINT)\n"
                  "time.sleep(60)\n")
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch("src.raster_regional_execution.fetch_raster_footprints",
                      side_effect=self.export_fixture), \
                patch("src.raster_job_supervisor._STOP_TIMEOUT_S", 0.2):
            with self.assertRaises(KeyboardInterrupt):
                self.run_job([sys.executable, "-c", script])
        self.assertEqual(list(self.reservations.directory.glob("*.json")), [])

    def test_supervised_job_retains_record_if_acquisition_fails(self):
        with patch("src.raster_regional_execution.area_bbox_wgs84", return_value=self.bbox), \
                patch("src.raster_regional_execution.survey_for", return_value=self.survey), \
                patch("src.raster_regional_execution.fetch_raster_footprints",
                      side_effect=RuntimeError("export failed")), \
                patch("src.raster_regional_job._ProcessGroup.run") as run:
            with self.assertRaisesRegex(DiskReservationError, "Consumers may still be running") as error:
                self.run_job([sys.executable, "-c", "pass"])
            self.assertIn("export failed", str(error.exception.__context__))
            run.assert_not_called()
        record, = self.reservations.directory.glob("*.json")
        self.reservations.recover_orphan(record.stem, consumers_stopped=True)

    def test_supervised_job_rejects_changed_approval_before_work(self):
        with patch("src.raster_regional_execution.survey_for") as survey:
            with self.assertRaisesRegex(ValueError, "plan changed"):
                self.run_job([sys.executable, "-c", "pass"], digest="0" * 64)
            survey.assert_not_called()
            self.assertFalse(self.reservations.directory.exists())


if __name__ == "__main__":
    unittest.main()
