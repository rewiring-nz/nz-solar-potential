"""Local-tile preparation is compared with the existing GDAL crop grid."""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds
from rasterio.enums import Resampling

from src import s3_rasters
from src.raster_acquisition_plan import plan_batch
from src.raster_cache import CachePolicy, RasterCache
from src.raster_disk_plan import DiskRequirements
from src.raster_execution import (
    RasterProcessing, admitted_raster_inputs, prepare_admitted_mapped_inputs,
)
from src.raster_prepare import (
    _planned_assets, ensure_planned_region, prepare_cached_mosaic, prepare_planned_mosaic,
    prepare_planned_region, verify_planned_region, preserve_previous_imagery,
)
from src.raster_provenance import fingerprint, tracked_raster_inputs, verify_mosaic
from src.raster_region_plan import plan_regions
from src.raster_reservations import DiskReservationError, DiskReservations, QueueCleanupPolicy
from src.raster_source_selection import LayerMapping, survey_requests


class CaptureCoverageTests(unittest.TestCase):
    def test_completely_outside_capture_geometry_is_rejected_before_fetch(self):
        bounds = [168.0, -45.0, 168.1, -44.9]
        source = {"collection_url": "https://example.test/collection.json",
                  "asset_key": "data", "expected_collection_id": "capture"}
        plan = {
            "inputs": [{"region": "area", "role": "dsm", "layer_id": 101,
                        "footprints_wgs84": [bounds], "source": source}],
            "assets": [{"collection_url": source["collection_url"], "collection_id": "capture",
                        "asset_key": "data", "bbox_wgs84": bounds,
                        "uses": [{"region": "area", "role": "dsm", "layer_id": 101}]}],
            "capture_coverage": [{"region": "area", "role": "dsm",
                                  "footprint_wgs84": bounds, "state": "outside"}],
        }
        with self.assertRaisesRegex(ValueError, "outside published capture geometry"):
            _planned_assets(plan, "area", "dsm", bounds)
        plan["capture_coverage"][0]["state"] = "intersects"
        self.assertEqual(len(_planned_assets(plan, "area", "dsm", bounds)[1]), 1)


@unittest.skipUnless(shutil.which("gdalbuildvrt") and shutil.which("gdal_translate"),
                     "GDAL CLI tools not installed")
class RasterPrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = RasterCache(self.root / "cache", CachePolicy(10000000, 0, 1000000))
        self.tile = self.root / "tile.tif"
        with rasterio.open(self.tile, "w", driver="GTiff", count=1, width=8, height=8,
                           dtype="float32", crs="EPSG:2193", nodata=-9999,
                           transform=from_origin(1200000, 5000000, 8, 8)) as dataset:
            data = np.arange(64, dtype="float32").reshape(8, 8)
            data[1, 1] = -9999
            dataset.write(data, 1)
            self.bounds = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds,
                                           densify_pts=21)
        payload = self.tile.read_bytes()
        self.asset = {
            "collection_url": "https://example.test/collection.json",
            "collection_id": "capture", "item_id": "tile", "asset_key": "data",
            "url": self.tile.as_uri(), "size_bytes": len(payload),
            "checksum": "1220" + hashlib.sha256(payload).hexdigest(),
            "bbox_wgs84": self.bounds, "revision": "1",
        }

    def rgba_plan(self, footprints=None, partial=False):
        tile = self.root / "rgba.tif"
        from rasterio.enums import ColorInterp
        with rasterio.open(tile, "w", driver="GTiff", count=4, width=20, height=8,
                           dtype="uint8", crs=2193,
                           transform=from_origin(1200000, 5000000, 1, 1)) as source:
            data = np.full((4, 8, 20), 100, dtype="uint8")
            data[3] = 255
            data[:3, 3, 2] = 0  # Black is valid when the alpha band is nonzero.
            data[3, 4, 2] = 0
            source.write(data)
            source.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
            bounds = list(transform_bounds(2193, 4326, *source.bounds, densify_pts=21))
        payload = tile.read_bytes()
        asset = {**self.asset, "item_id": "rgba", "url": tile.as_uri(),
                 "size_bytes": len(payload), "checksum": "1220" + hashlib.sha256(payload).hexdigest(),
                 "bbox_wgs84": bounds,
                 "uses": [{"region": "area", "role": "imagery", "layer_id": 202}]}
        return {"inputs": [{"region": "area", "role": "imagery", "layer_id": 202,
                            "footprints_wgs84": footprints or [bounds],
                            "allow_partial": partial,
                            "source": {"collection_url": asset["collection_url"],
                                       "asset_key": asset["asset_key"],
                                       "expected_collection_id": asset["collection_id"],
                                       "index_layer": 203}}],
                "assets": [asset]}

    def test_rgba_imagery_preserves_alpha_black_pixels_and_provenance(self):
        plan = self.rgba_plan()
        output = self.root / "rgba-mosaic.tif"
        prepare_planned_region(self.cache, plan, "area", "imagery", output, res=1, resampling="nearest")
        manifest = verify_planned_region(plan, "area", "imagery", output, res=1, resampling="nearest")
        self.assertEqual(manifest["layer_id"], 202)
        with rasterio.open(output) as raster:
            self.assertEqual(raster.count, 4)
            values, mask = raster.read(), raster.dataset_mask()
            self.assertTrue(np.any((values[:3] == 0).all(axis=0) & (mask == 255)))
            self.assertTrue(np.any(mask == 0))

    def test_disjoint_rgba_imagery_preserves_transparent_gap(self):
        footprints = [list(transform_bounds(2193, 4326, 1200001, 4999993, 1200005, 4999999)),
                      list(transform_bounds(2193, 4326, 1200015, 4999993, 1200019, 4999999))]
        plan = self.rgba_plan(footprints)
        output = self.root / "rgba-parts.tif"
        prepare_planned_region(self.cache, plan, "area", "imagery", output, res=1)
        verify_planned_region(plan, "area", "imagery", output, res=1)
        with rasterio.open(output) as raster:
            self.assertEqual(raster.count, 4)
            mask = raster.dataset_mask()
            self.assertTrue(np.any(mask == 255))
            self.assertTrue(np.all(mask[:, mask.shape[1] // 2] == 0))

    def test_partial_rgba_preparation_keeps_uncovered_pixels_invalid(self):
        footprint = list(transform_bounds(2193, 4326, 1200010, 4999993, 1200030, 4999999))
        plan = self.rgba_plan([footprint], partial=True)
        output = self.root / "rgba-partial.tif"
        prepare_planned_region(self.cache, plan, "area", "imagery", output, res=1)
        with rasterio.open(output) as raster:
            self.assertTrue(np.any(raster.dataset_mask() == 255))
            self.assertTrue(np.all(raster.dataset_mask()[:, -5:] == 0))
        plan["inputs"][0]["allow_partial"] = False
        with self.assertRaisesRegex(ValueError, "do not cover"):
            _planned_assets(plan, "area", "imagery", footprint)

    def test_partial_disjoint_imagery_keeps_completely_uncovered_footprint(self):
        footprints = [list(transform_bounds(2193, 4326, 1200001, 4999993, 1200005, 4999999)),
                      list(transform_bounds(2193, 4326, 1200030, 4999993, 1200035, 4999999))]
        plan = self.rgba_plan(footprints, partial=True)
        output = self.root / "rgba-uncovered.tif"
        prepare_planned_region(self.cache, plan, "area", "imagery", output, res=1)
        manifest = verify_planned_region(plan, "area", "imagery", output, res=1)
        self.assertEqual(len(manifest["footprints_wgs84"]), 2)
        with rasterio.open(output) as raster:
            self.assertGreaterEqual(raster.bounds.right, 1200035)
            self.assertTrue(np.any(raster.dataset_mask() == 255))
            self.assertTrue(np.all(raster.dataset_mask()[:, -5:] == 0))

    def test_selected_imagery_preserves_legacy_mosaic_before_replacement(self):
        plan = self.rgba_plan()
        output = self.root / "imagery_mosaic.tif"
        output.write_bytes(b"legacy imagery")
        preserve_previous_imagery(plan, "area", "imagery", output, res=1, resampling="average")
        self.assertFalse(output.exists())
        self.assertEqual([path.read_bytes() for path in
                          (self.root / "previous_imagery").glob("*/imagery_mosaic.tif")],
                         [b"legacy imagery"])
        ensure_planned_region(self.cache, plan, "area", "imagery", output, res=1)
        preserve_previous_imagery(plan, "area", "imagery", output, res=1, resampling="average")
        self.assertTrue(output.exists())
        output.write_bytes(b"corrupted")
        with self.assertRaises(ValueError):
            preserve_previous_imagery(plan, "area", "imagery", output, res=1, resampling="average")

    def test_local_output_matches_existing_gdal_grid_values_and_mask(self):
        for resolution in (None, 16):
            with self.subTest(resolution=resolution):
                expected = self.root / f"expected-{resolution}.tif"
                output = self.root / f"output-{resolution}.tif"
                x0, y0, x1, y1 = s3_rasters._nztm_bounds(self.bounds)
                command = ["gdal_translate", "-q", "-projwin", str(x0), str(y1),
                           str(x1), str(y0)]
                if resolution is not None:
                    command.extend(["-tr", str(resolution), str(resolution), "-r", "average"])
                subprocess.run([*command, str(self.tile), str(expected)], check=True)
                prepare_cached_mosaic(self.cache, [self.asset], self.bounds, output,
                                      res=resolution)
                with rasterio.open(output) as actual, rasterio.open(expected) as reference:
                    self.assertEqual(actual.crs, reference.crs)
                    self.assertEqual(actual.transform, reference.transform)
                    self.assertEqual(actual.shape, reference.shape)
                    self.assertEqual(actual.nodata, reference.nodata)
                    np.testing.assert_array_equal(actual.read_masks(), reference.read_masks())
                    np.testing.assert_allclose(actual.read(), reference.read(), atol=1e-5)

    def test_native_wgs84_tiles_reproject_to_aligned_nztm_grid(self):
        tile = self.root / "wgs84.tif"
        values = np.arange(64, dtype="float32").reshape(8, 8)
        values[2, 2] = -9999
        with rasterio.open(tile, "w", driver="GTiff", count=1, width=8, height=8,
                           dtype="float32", crs="EPSG:4326", nodata=-9999,
                           transform=from_origin(168.6, -45.0, 0.0004, 0.0004)) as source:
            source.write(values, 1)
            footprint = list(source.bounds)
        payload = tile.read_bytes()
        asset = {**self.asset, "item_id": "wgs84", "url": tile.as_uri(),
                 "size_bytes": len(payload),
                 "checksum": "1220" + hashlib.sha256(payload).hexdigest(),
                 "bbox_wgs84": footprint, "native_crs": "EPSG:4326"}
        destination = self.root / "reprojected.tif"
        prepare_cached_mosaic(self.cache, [asset], footprint, destination,
                              res=30, resampling="nearest",
                              provenance={"region": "area", "role": "dsm", "layer_id": 101})
        with rasterio.open(tile) as source, rasterio.open(destination) as actual:
            self.assertEqual(actual.crs.to_epsg(), 2193)
            self.assertEqual(actual.res, (30, 30))
            self.assertAlmostEqual(actual.transform.c / 30, round(actual.transform.c / 30))
            self.assertAlmostEqual(actual.transform.f / 30, round(actual.transform.f / 30))
            self.assertEqual(actual.nodata, -9999)
            expected = np.full(actual.shape, -9999, dtype="float32")
            reproject(rasterio.band(source, 1), expected, src_transform=source.transform,
                      src_crs=source.crs, src_nodata=source.nodata,
                      dst_transform=actual.transform, dst_crs=actual.crs,
                      dst_nodata=actual.nodata, resampling=Resampling.nearest)
            np.testing.assert_array_equal(actual.read(1), expected)
            np.testing.assert_array_equal(actual.read_masks(1), np.where(expected == -9999, 0, 255))
        manifest = verify_mosaic(destination, destination.with_name(destination.name + ".source.json"))
        self.assertEqual(manifest["sources"][0]["native_crs"], "EPSG:4326")
        self.assertEqual(manifest["processing"]["crs"], "EPSG:2193")

    def test_planned_native_crs_must_match_raster_header(self):
        for native_crs in ("EPSG:4326", "not-a-crs", True):
            with self.subTest(native_crs=native_crs):
                destination = self.root / f"bad-crs-{native_crs}.tif"
                with self.assertRaisesRegex(ValueError, "native CRS"):
                    prepare_cached_mosaic(self.cache, [{**self.asset, "native_crs": native_crs}],
                                          self.bounds, destination)
                self.assertFalse(destination.exists())
        destination = self.root / "matching-crs.tif"
        prepare_cached_mosaic(self.cache, [{**self.asset, "native_crs": 2193}],
                              self.bounds, destination)
        self.assertTrue(destination.is_file())

    def test_tile_header_must_overlap_acquisition_footprint(self):
        destination = self.root / "wrong-extent.tif"
        false_footprint = [170, -45, 170.001, -44.999]
        asset = {**self.asset, "bbox_wgs84": false_footprint, "native_crs": "EPSG:2193"}
        with self.assertRaisesRegex(ValueError, "headers do not overlap"):
            prepare_cached_mosaic(self.cache, [asset], false_footprint, destination)
        self.assertFalse(destination.exists())

    def test_reprojection_requires_resolution_and_supported_nodata(self):
        for nodata, res, message in ((-9999, None, "explicit"), (None, 30, "common nodata")):
            with self.subTest(nodata=nodata, res=res):
                tile = self.root / f"wgs84-{nodata}.tif"
                with rasterio.open(tile, "w", driver="GTiff", count=1, width=4, height=4,
                                   dtype="float32", crs="EPSG:4326", nodata=nodata,
                                   transform=from_origin(168.6, -45, 0.001, 0.001)) as source:
                    source.write(np.ones((4, 4), dtype="float32"), 1)
                    footprint = list(source.bounds)
                payload = tile.read_bytes()
                asset = {**self.asset, "item_id": f"wgs84-{nodata}", "url": tile.as_uri(),
                         "size_bytes": len(payload),
                         "checksum": "1220" + hashlib.sha256(payload).hexdigest(),
                         "bbox_wgs84": footprint}
                destination = self.root / f"unsupported-{nodata}.tif"
                with self.assertRaisesRegex(ValueError, message):
                    prepare_cached_mosaic(self.cache, [asset], footprint, destination, res=res)
                self.assertFalse(destination.exists())

    def test_reprojection_rejects_internal_dataset_masks_and_alpha(self):
        for mode in ("mask", "alpha"):
            with self.subTest(mode=mode):
                tile = self.root / f"{mode}.tif"
                with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
                    with rasterio.open(tile, "w", driver="GTiff", count=4 if mode == "alpha" else 1,
                                       width=4, height=4, dtype="uint8", crs="EPSG:4326",
                                       nodata=0, transform=from_origin(168.6, -45, 0.001, 0.001)) as source:
                        source.write(np.ones((source.count, 4, 4), dtype="uint8"))
                        if mode == "mask":
                            source.write_mask(np.array([[0, 255, 255, 255]] * 4, dtype="uint8"))
                        else:
                            source.colorinterp = (rasterio.enums.ColorInterp.red,
                                                  rasterio.enums.ColorInterp.green,
                                                  rasterio.enums.ColorInterp.blue,
                                                  rasterio.enums.ColorInterp.alpha)
                        footprint = list(source.bounds)
                payload = tile.read_bytes()
                asset = {**self.asset, "item_id": mode, "url": tile.as_uri(),
                         "size_bytes": len(payload),
                         "checksum": "1220" + hashlib.sha256(payload).hexdigest(),
                         "bbox_wgs84": footprint}
                destination = self.root / f"rejected-{mode}.tif"
                with self.assertRaisesRegex(ValueError, "without alpha or per-dataset masks"):
                    prepare_cached_mosaic(self.cache, [asset], footprint, destination, res=30)
                self.assertFalse(destination.exists())

    def test_reprojected_disjoint_footprints_share_grid_and_preserve_gap(self):
        tile = self.root / "wgs84-parts.tif"
        with rasterio.open(tile, "w", driver="GTiff", count=1, width=20, height=10,
                           dtype="float32", crs="EPSG:4326", nodata=-9999,
                           transform=from_origin(168.6, -45.0, 0.0004, 0.0004)) as source:
            source.write(np.ones((10, 20), dtype="float32"), 1)
            tile_bounds = list(source.bounds)
        payload = tile.read_bytes()
        asset = {**self.asset, "item_id": "wgs84-parts", "url": tile.as_uri(),
                 "size_bytes": len(payload),
                 "checksum": "1220" + hashlib.sha256(payload).hexdigest(),
                 "bbox_wgs84": tile_bounds, "native_crs": "EPSG:4326",
                 "uses": [{"region": "area", "role": "dsm", "layer_id": 101}]}
        footprints = [[168.6, -45.004, 168.6016, -45.0],
                      [168.6064, -45.004, 168.608, -45.0]]
        plan = {"inputs": [{"region": "area", "role": "dsm", "layer_id": 101,
                            "footprints_wgs84": footprints,
                            "source": {"collection_url": asset["collection_url"],
                                       "asset_key": asset["asset_key"],
                                       "expected_collection_id": asset["collection_id"]}}],
                "assets": [asset]}
        output = self.root / "parts.tif"
        prepare_planned_region(self.cache, plan, "area", "dsm", output,
                               res=30, resampling="nearest")
        with rasterio.open(output) as raster:
            self.assertEqual(raster.crs.to_epsg(), 2193)
            self.assertEqual(raster.res, (30, 30))
            data = raster.read(1)
            self.assertTrue(np.any(data == 1))
            self.assertTrue(np.all(data[:, data.shape[1] // 2] == -9999))
        manifest = verify_mosaic(output, output.with_name(output.name + ".source.json"))
        self.assertEqual(manifest["footprints_wgs84"], footprints)

    def test_plan_execution_reserves_through_consumer_and_reuses_cached_tile(self):
        source = self.asset["collection_url"]
        documents = {
            source: {"id": "capture", "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "tile", "collection": "capture", "bbox": self.bounds,
                "properties": {"updated": "1"},
                "assets": {"data": {"href": self.tile.as_uri(), "type": "image/tiff",
                                    "file:size": self.asset["size_bytes"],
                                    "file:checksum": self.asset["checksum"]}}},
        }
        selected = survey_requests(
            "area", [self.bounds], {"elevation": "lidar", "dsm_layer": 101,
                                    "imagery_layer": 202},
            mappings=[LayerMapping(101, "dsm", source, "capture", "data", "fixture")])
        plan = plan_batch(selected["requests"], get_json=documents.__getitem__)
        destinations = {("area", "dsm"): self.root / "dsm.tif",
                        ("area", "imagery"): self.root / "imagery.tif"}
        requirements = DiskRequirements(self.cache.directory, self.root, self.root,
                                        20000, 20000, 20000, 0, 10000)
        reservations = DiskReservations(self.root / "reservations",
                                        probe=lambda path: (1, 120000))
        calls = []

        def export(fallback, path):
            calls.append(fallback)
            path.write_bytes(b"fixture export")

        with admitted_raster_inputs(plan, self.cache, reservations, requirements,
                                    destinations, export) as execution:
            self.assertTrue(execution["forecast"]["reservation"])
            self.assertEqual(calls[0]["layer_id"], 202)
            self.assertTrue(destinations[("area", "dsm")].is_file())
            self.assertEqual(len(list((self.root / "reservations").glob("*.json"))), 1)
            with self.assertRaisesRegex(DiskReservationError, "Insufficient disk reservation"):
                with reservations.reserve(lambda: self.cache.inspect_plan(plan), requirements):
                    self.fail("overlapping job exceeded available space")
            verify_mosaic(destinations[("area", "dsm")],
                          self.root / "dsm.tif.source.json")
        self.assertEqual(list((self.root / "reservations").glob("*.json")), [])
        with patch("src.raster_cache.urllib.request.urlopen",
                   side_effect=AssertionError("warm cache transferred bytes")):
            with admitted_raster_inputs(plan, self.cache, reservations, requirements,
                                        destinations, export):
                pass

    def test_external_admission_is_rechecked_before_mapped_acquisition(self):
        source = self.asset["collection_url"]
        documents = {
            source: {"id": "capture", "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "tile", "collection": "capture", "bbox": self.bounds,
                "properties": {"updated": "1"},
                "assets": {"data": {"href": self.tile.as_uri(), "type": "image/tiff",
                                    "file:size": self.asset["size_bytes"],
                                    "file:checksum": self.asset["checksum"]}}},
        }
        selected = survey_requests(
            "area", [self.bounds], {"elevation": "lidar", "dsm_layer": 101,
                                    "imagery_layer": 202},
            mappings=[LayerMapping(101, "dsm", source, "capture", "data", "fixture")])
        plan = plan_batch(selected["requests"], get_json=documents.__getitem__)
        destinations = {("area", "dsm"): self.root / "dsm.tif",
                        ("area", "imagery"): self.root / "imagery.tif"}
        checks = unittest.mock.Mock()
        acquire = prepare_admitted_mapped_inputs(
            plan, self.cache, destinations, {("area", "dsm"): RasterProcessing()}, checks)
        checks.assert_called_once()
        with self.assertRaisesRegex(ValueError, "Not a mapped"):
            acquire(("area", "imagery"))
        checks.side_effect = RuntimeError("reservation lost")
        with self.assertRaisesRegex(RuntimeError, "reservation lost"):
            acquire(("area", "dsm"))
        self.assertFalse(destinations[("area", "dsm")].exists())
        checks.side_effect = None
        self.assertEqual(acquire(("area", "dsm")), destinations[("area", "dsm")])
        verify_mosaic(destinations[("area", "dsm")], self.root / "dsm.tif.source.json")

    def test_partial_item_bounds_are_rejected_before_transfer(self):
        source = {"collection_url": self.asset["collection_url"],
                  "asset_key": self.asset["asset_key"],
                  "expected_collection_id": self.asset["collection_id"]}
        west, south, east, north = self.bounds
        partial = [west, south, (west + east) / 2, north]
        asset = {**self.asset, "bbox_wgs84": partial,
                 "uses": [{"region": "area", "role": "dsm", "layer_id": 101}]}
        plan = {"inputs": [{"region": "area", "role": "dsm", "layer_id": 101,
                            "footprints_wgs84": [self.bounds], "source": source}],
                "assets": [asset]}
        output = self.root / "partial-bounds.tif"
        with patch("src.raster_cache.urllib.request.urlopen",
                   side_effect=AssertionError("partial coverage transferred a tile")):
            with self.assertRaisesRegex(ValueError, "item bounds do not cover"):
                prepare_planned_region(self.cache, plan, "area", "dsm", output)
        self.assertFalse(output.exists())

        plan["inputs"].insert(0, {"region": "area", "role": "imagery", "layer_id": 202,
                                  "footprints_wgs84": [self.bounds], "source": None})
        plan["export_fallbacks"] = [{"region": "area", "role": "imagery", "layer_id": 202,
                                     "footprints_wgs84": [self.bounds]}]
        requirements = DiskRequirements(self.cache.directory, self.root, self.root,
                                        0, 0, 0, 0, 10000)
        reservations = DiskReservations(self.root / "reservations",
                                        probe=lambda path: (1, 1000000))
        export = unittest.mock.Mock()
        with self.assertRaisesRegex(ValueError, "item bounds do not cover"):
            with admitted_raster_inputs(plan, self.cache, reservations, requirements,
                                        {("area", "dsm"): output,
                                         ("area", "imagery"): self.root / "imagery.tif"}, export):
                self.fail("partial coverage was admitted")
        export.assert_not_called()
        self.assertFalse(self.root.joinpath("reservations").exists())

    def test_plan_execution_passes_explicit_processing_for_reprojection_and_reuse(self):
        tile = self.root / "execution-wgs84.tif"
        with rasterio.open(tile, "w", driver="GTiff", count=1, width=8, height=8,
                           dtype="float32", crs="EPSG:4326", nodata=-9999,
                           transform=from_origin(168.6, -45.0, 0.0004, 0.0004)) as source:
            source.write(np.ones((8, 8), dtype="float32"), 1)
            footprint = list(source.bounds)
        payload = tile.read_bytes()
        asset = {**self.asset, "item_id": "execution-wgs84", "url": tile.as_uri(),
                 "size_bytes": len(payload),
                 "checksum": "1220" + hashlib.sha256(payload).hexdigest(),
                 "bbox_wgs84": footprint,
                 "uses": [{"region": "area", "role": "dsm", "layer_id": 101}]}
        plan = {"inputs": [{"region": "area", "role": "dsm", "layer_id": 101,
                            "footprints_wgs84": [footprint],
                            "source": {"collection_url": asset["collection_url"],
                                       "asset_key": asset["asset_key"],
                                       "expected_collection_id": asset["collection_id"]}}],
                "assets": [asset], "export_fallbacks": []}
        requirements = DiskRequirements(self.cache.directory, self.root, self.root,
                                        20000, 20000, 20000, 0)
        reservations = DiskReservations(self.root / "reservations",
                                        probe=lambda path: (1, 1000000))
        destinations = {("area", "dsm"): self.root / "execution-reprojected.tif"}
        processing = {("area", "dsm"): RasterProcessing(30, "nearest")}
        with admitted_raster_inputs(plan, self.cache, reservations, requirements,
                                    destinations, lambda fallback, path: None,
                                    processing=processing):
            with rasterio.open(destinations[("area", "dsm")]) as raster:
                self.assertEqual(raster.crs.to_epsg(), 2193)
                self.assertEqual(raster.res, (30, 30))
            self.assertEqual(len(list((self.root / "reservations").glob("*.json"))), 1)
        with admitted_raster_inputs(plan, self.cache, reservations, requirements,
                                    destinations, lambda fallback, path: None,
                                    processing=processing):
            pass
        with self.assertRaisesRegex(ValueError, "processing does not match"):
            with admitted_raster_inputs(plan, self.cache, reservations, requirements,
                                        destinations, lambda fallback, path: None,
                                        processing={("area", "dsm"): RasterProcessing(60, "nearest")}):
                self.fail("changed output grid was reused")
        self.assertEqual(list((self.root / "reservations").glob("*.json")), [])
        with self.assertRaisesRegex(ValueError, "positive finite"):
            with admitted_raster_inputs(plan, self.cache, reservations, requirements,
                                        destinations, lambda fallback, path: None,
                                        processing={("area", "dsm"): RasterProcessing(0)}):
                self.fail("invalid output grid was accepted")

    def test_execution_reserves_replacement_for_same_unversioned_tile_across_inputs(self):
        source = {"collection_url": self.asset["collection_url"],
                  "asset_key": self.asset["asset_key"],
                  "expected_collection_id": self.asset["collection_id"]}
        uses = [{"region": "area", "role": role, "layer_id": layer}
                for role, layer in (("dsm", 101), ("imagery", 202))]
        asset = {**self.asset, "revision": None, "checksum": None, "uses": uses}
        plan = {"inputs": [{**use, "footprints_wgs84": [self.bounds], "source": source}
                           for use in uses], "assets": [asset], "export_fallbacks": []}
        destinations = {(use["region"], use["role"]): self.root / f"{use['role']}-shared.tif"
                        for use in uses}
        requirements = DiskRequirements(self.cache.directory, self.root, self.root, 0, 0, 0, 0)
        size = asset["size_bytes"]
        limited = DiskReservations(self.root / "reservations", probe=lambda path: (1, 2 * size - 1))
        with self.assertRaisesRegex(DiskReservationError, "device 1: 1 bytes"):
            with admitted_raster_inputs(plan, self.cache, limited, requirements,
                                        destinations, lambda fallback, path: None):
                self.fail("repeated unversioned transfer was under-reserved")
        self.assertTrue(all(not path.exists() for path in destinations.values()))

        enough = DiskReservations(self.root / "reservations", probe=lambda path: (1, 2 * size))
        import urllib.request
        with patch("src.raster_cache.urllib.request.urlopen",
                   wraps=urllib.request.urlopen) as transfer:
            with admitted_raster_inputs(plan, self.cache, enough, requirements,
                                        destinations, lambda fallback, path: None) as execution:
                self.assertEqual(execution["forecast"]["shared_unversioned_replacement_bytes"], size)
                self.assertTrue(all(path.is_file() for path in destinations.values()))
        self.assertEqual(transfer.call_count, 2)

    def test_plan_execution_refuses_before_writing_when_disk_is_insufficient(self):
        plan = plan_batch(survey_requests(
            "area", [self.bounds], {"elevation": "lidar", "dsm_layer": 101},
            mappings=[], include_imagery=False)["requests"])
        requirements = DiskRequirements(self.cache.directory, self.root, self.root,
                                        20000, 0, 0, 0, 10000)
        reservations = DiskReservations(self.root / "reservations", probe=lambda path: (1, 1))
        destination = self.root / "dsm.tif"
        export = unittest.mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "Insufficient disk reservation"):
            with admitted_raster_inputs(plan, self.cache, reservations, requirements,
                                        {("area", "dsm"): destination}, export):
                self.fail("insufficient disk was admitted")
        export.assert_not_called()
        self.assertFalse(destination.exists())

    def test_execution_retains_reservation_if_consumers_cannot_be_confirmed_stopped(self):
        plan = plan_batch(survey_requests(
            "area", [self.bounds], {"elevation": "lidar", "dsm_layer": 101},
            mappings=[], include_imagery=False)["requests"])
        requirements = DiskRequirements(self.cache.directory, self.root, self.root, 0, 0, 0, 0, 1000)
        reservations = DiskReservations(self.root / "reservations", probe=lambda _: (1, 100000))
        destination = self.root / "export.tif"

        def export(_, path):
            path.write_bytes(b"fixture")

        with self.assertRaisesRegex(DiskReservationError, "Consumers may still be running"):
            with admitted_raster_inputs(
                plan, self.cache, reservations, requirements, {("area", "dsm"): destination},
                export, consumers_stopped=lambda: False,
            ):
                self.assertTrue(destination.is_file())
        record, = (self.root / "reservations").glob("*.json")
        reservations.recover_orphan(record.stem, consumers_stopped=True)

    def test_execution_can_reclaim_cache_before_preparing_planned_input(self):
        size = self.asset["size_bytes"]
        cache = RasterCache(self.root / "cache", CachePolicy(size, 0, size))
        old = {**self.asset, "item_id": "unneeded"}
        with cache.lease(old) as old_path:
            pass
        use = {"region": "area", "role": "dsm", "layer_id": 101}
        source = {"collection_url": self.asset["collection_url"],
                  "asset_key": self.asset["asset_key"],
                  "expected_collection_id": self.asset["collection_id"]}
        plan = {"inputs": [{**use, "footprints_wgs84": [self.bounds], "source": source}],
                "assets": [{**self.asset, "uses": [use]}], "export_fallbacks": []}
        requirements = DiskRequirements(cache.directory, self.root, self.root, 0, 0, 0, 0)
        reservations = DiskReservations(self.root / "reservations", probe=lambda _: (1, 1000000))
        destination = self.root / "reclaimed-input.tif"
        with admitted_raster_inputs(
            plan, cache, reservations, requirements, {("area", "dsm"): destination},
            lambda fallback, path: self.fail("unexpected export"),
            cleanup_policy=QueueCleanupPolicy(1, 0, 0, 3600),
        ) as result:
            self.assertFalse(old_path.exists())
            self.assertTrue(destination.is_file())
            self.assertEqual(len(result["forecast"]["cleanup"]["assets"]["removed"]), 1)
            self.assertEqual(len(list((self.root / "reservations").glob("*.json"))), 1)
        self.assertEqual(list((self.root / "reservations").glob("*.json")), [])

    def test_plan_execution_rejects_mismatched_fallback_and_releases_on_failure(self):
        plan = plan_batch(survey_requests(
            "area", [self.bounds], {"elevation": "lidar", "dsm_layer": 101},
            mappings=[], include_imagery=False)["requests"])
        requirements = DiskRequirements(self.cache.directory, self.root, self.root,
                                        20000, 0, 0, 0, 10000)
        reservations = DiskReservations(self.root / "reservations",
                                        probe=lambda path: (1, 1000000))
        destinations = {("area", "dsm"): self.root / "dsm.tif"}
        invalid = copy.deepcopy(plan)
        invalid["export_fallbacks"][0]["layer_id"] = 102
        export = unittest.mock.Mock()
        with self.assertRaisesRegex(ValueError, "does not match"):
            with admitted_raster_inputs(invalid, self.cache, reservations, requirements,
                                        destinations, export):
                self.fail("mismatched plan was admitted")
        export.assert_not_called()
        export.side_effect = OSError("export failed")
        with self.assertRaisesRegex(OSError, "export failed"):
            with admitted_raster_inputs(plan, self.cache, reservations, requirements,
                                        destinations, export):
                self.fail("failed export was accepted")
        self.assertEqual(list((self.root / "reservations").glob("*.json")), [])
        with self.assertRaisesRegex(RuntimeError, "did not create"):
            with admitted_raster_inputs(plan, self.cache, reservations, requirements,
                                        destinations, lambda fallback, path: None):
                self.fail("missing export was accepted")
        self.assertEqual(list((self.root / "reservations").glob("*.json")), [])

    def test_existing_mosaic_is_preserved_and_live_tiles_are_leased(self):
        destination = self.root / "mosaic.tif"
        destination.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            prepare_cached_mosaic(self.cache, [self.asset], self.bounds, destination)
        self.assertEqual(destination.read_bytes(), b"existing")
        destination.unlink()
        build_vrt = s3_rasters._build_vrt

        def verify_lease(paths, vrt, reader):
            self.assertFalse(self.cache.cleanup(0)["target_reached"])
            return build_vrt(paths, vrt, reader)

        with patch("src.s3_rasters._build_vrt", side_effect=verify_lease):
            prepare_cached_mosaic(self.cache, [self.asset], self.bounds, destination)
        self.assertTrue(destination.is_file())
        self.assertTrue(self.cache.cleanup(0)["target_reached"])

    def test_internal_mask_is_preserved(self):
        tile = self.root / "masked.tif"
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
            with rasterio.open(tile, "w", driver="GTiff", count=1, width=8, height=8,
                               dtype="uint8", crs="EPSG:2193",
                               transform=from_origin(1200000, 5000000, 8, 8)) as dataset:
                dataset.write(np.ones((8, 8), dtype="uint8"), 1)
                mask = np.full((8, 8), 255, dtype="uint8")
                mask[1, 1] = 0
                dataset.write_mask(mask)
        payload = tile.read_bytes()
        asset = {**self.asset, "item_id": "masked", "url": tile.as_uri(),
                 "size_bytes": len(payload), "checksum": "1220" + hashlib.sha256(payload).hexdigest()}
        expected, output = self.root / "mask-expected.tif", self.root / "mask-actual.tif"
        x0, y0, x1, y1 = s3_rasters._nztm_bounds(self.bounds)
        subprocess.run(["gdal_translate", "-q", "-projwin", str(x0), str(y1), str(x1),
                        str(y0), str(tile), str(expected)], check=True)
        with patch("src.s3_rasters.require_raster_support", return_value="GTiff"):
            prepare_cached_mosaic(self.cache, [asset], self.bounds, output)
        with rasterio.open(output) as actual, rasterio.open(expected) as reference:
            np.testing.assert_array_equal(actual.read_masks(), reference.read_masks())
        output.unlink()
        with patch("src.s3_rasters.require_raster_support", return_value="LIBERTIFF"):
            with self.assertRaisesRegex(ValueError, "cannot preserve.*mask"):
                prepare_cached_mosaic(self.cache, [asset], self.bounds, output)
        self.assertFalse(output.exists())

    def test_rgba_bands_and_alpha_match_gdal(self):
        tile = self.root / "rgba.tif"
        with rasterio.open(tile, "w", driver="GTiff", count=4, width=8, height=8,
                           dtype="uint8", crs="EPSG:2193",
                           transform=from_origin(1200000, 5000000, 8, 8)) as dataset:
            data = np.full((4, 8, 8), 128, dtype="uint8")
            data[3] = 255
            data[3, 1, 1] = 0
            dataset.write(data)
            dataset.colorinterp = (rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                                   rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha)
        payload = tile.read_bytes()
        asset = {**self.asset, "item_id": "rgba", "url": tile.as_uri(),
                 "size_bytes": len(payload), "checksum": "1220" + hashlib.sha256(payload).hexdigest()}
        expected, output = self.root / "rgba-expected.tif", self.root / "rgba-actual.tif"
        x0, y0, x1, y1 = s3_rasters._nztm_bounds(self.bounds)
        subprocess.run(["gdal_translate", "-q", "-projwin", str(x0), str(y1), str(x1),
                        str(y0), str(tile), str(expected)], check=True)
        with patch("src.s3_rasters.require_raster_support", return_value="GTiff"):
            prepare_cached_mosaic(self.cache, [asset], self.bounds, output)
        with rasterio.open(output) as actual, rasterio.open(expected) as reference:
            self.assertEqual(actual.colorinterp, reference.colorinterp)
            np.testing.assert_array_equal(actual.read_masks(), reference.read_masks())
            actual_data, reference_data = actual.read(), reference.read()
            np.testing.assert_array_equal(actual_data[3], reference_data[3])
            valid = reference_data[3] != 0
            np.testing.assert_array_equal(actual_data[:3, valid], reference_data[:3, valid])
        output.unlink()
        with patch("src.s3_rasters.require_raster_support", return_value="LIBERTIFF"):
            prepare_cached_mosaic(self.cache, [asset], self.bounds, output)
        with rasterio.open(output) as actual, rasterio.open(expected) as reference:
            self.assertEqual(actual.colorinterp, reference.colorinterp)
            np.testing.assert_array_equal(actual.read_masks(), reference.read_masks())
            np.testing.assert_array_equal(actual.read(4), reference.read(4))

    def _second_tile(self, *, resolution=8, nodata=-9999, x=1200064):
        tile = self.root / f"tile-{resolution}-{nodata}-{x}.tif"
        with rasterio.open(tile, "w", driver="GTiff", count=1, width=8, height=8,
                           dtype="float32", crs="EPSG:2193", nodata=nodata,
                           transform=from_origin(x, 5000000, resolution, resolution)) as dataset:
            dataset.write(np.full((8, 8), 42, dtype="float32"), 1)
        payload = tile.read_bytes()
        return {**self.asset, "item_id": tile.stem, "url": tile.as_uri(),
                "size_bytes": len(payload), "checksum": "1220" + hashlib.sha256(payload).hexdigest()}

    def test_planned_region_merges_disjoint_footprints_without_selecting_gap_assets(self):
        tile = self.root / "wide.tif"
        with rasterio.open(tile, "w", driver="GTiff", count=1, width=32, height=8,
                           dtype="float32", crs="EPSG:2193", nodata=-9999,
                           transform=from_origin(1200000, 5000000, 8, 8)) as dataset:
            dataset.write(np.ones((8, 32), dtype="float32"), 1)
            tile_bounds = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds,
                                           densify_pts=21)
        payload = tile.read_bytes()
        footprints = [
            transform_bounds("EPSG:2193", "EPSG:4326", x0, 4999944, x1, 4999992,
                             densify_pts=21)
            for x0, x1 in ((1200008, 1200040), (1200216, 1200248))
        ]
        gap = transform_bounds("EPSG:2193", "EPSG:4326", 1200100, 4999950,
                               1200140, 4999980, densify_pts=21)
        asset = {**self.asset, "item_id": "wide", "url": tile.as_uri(),
                 "size_bytes": len(payload), "checksum": "1220" + hashlib.sha256(payload).hexdigest(),
                 "bbox_wgs84": tile_bounds, "uses": [{"region": "area", "role": "dsm", "layer_id": 101}]}
        gap_asset = {**asset, "item_id": "gap", "url": "file:///nonexistent-raster.tif",
                     "bbox_wgs84": gap}
        plan = {
            "inputs": [{"region": "area", "role": "dsm", "layer_id": 101,
                        "footprints_wgs84": footprints, "survey_name": "fixture",
                        "mapping_evidence": "fixture",
                        "source": {"collection_url": asset["collection_url"],
                                   "asset_key": "data", "expected_collection_id": "capture"}}],
            "assets": [asset, gap_asset],
            "capture_coverage": [
                {"region": "area", "role": "dsm", "footprint_wgs84": list(bounds),
                 "state": "covered"} for bounds in footprints],
            "capture_surveys": [
                {"region": "area", "role": "dsm", "footprint_wgs84": list(bounds),
                 "surveys": [{"id": str(index)}]}
                for index, bounds in enumerate(footprints)],
        }
        output = self.root / "region.tif"
        prepare_planned_region(self.cache, plan, "area", "dsm", output)
        with rasterio.open(output) as raster:
            self.assertEqual(list(raster.sample([(1200024, 4999970)]))[0][0], 1)
            self.assertEqual(list(raster.sample([(1200232, 4999970)]))[0][0], 1)
            self.assertEqual(list(raster.sample([(1200120, 4999970)]))[0][0], -9999)
        manifest = verify_mosaic(output, output.with_name(output.name + ".source.json"))
        self.assertEqual(manifest["footprints_wgs84"], [list(bounds) for bounds in footprints])
        self.assertEqual(manifest["capture_coverage"], plan["capture_coverage"])
        self.assertEqual(manifest["capture_surveys"], plan["capture_surveys"])
        self.assertEqual([source["item_id"] for source in manifest["sources"]], ["wide"])
        self.assertIn("dsm", tracked_raster_inputs({"dsm": output}, region="area"))
        with patch("src.raster_cache.urllib.request.urlopen",
                   side_effect=AssertionError("verified mosaic reuse transferred bytes")):
            self.assertEqual(ensure_planned_region(self.cache, plan, "area", "dsm", output), output)
        changed = copy.deepcopy(plan)
        changed["inputs"][0]["footprints_wgs84"].reverse()
        with self.assertRaisesRegex(ValueError, "footprints do not match"):
            ensure_planned_region(self.cache, changed, "area", "dsm", output)
        with self.assertRaisesRegex(ValueError, "grid alignment"):
            prepare_planned_region(self.cache, plan, "area", "dsm",
                                   self.root / "misaligned.tif", res=10)
        self.assertFalse((self.root / "misaligned.tif").exists())
        self.assertFalse((self.root / "misaligned.tif.source.json").exists())

        other = self._second_tile(x=1200192)
        plan["assets"] = [
            {**asset, "bbox_wgs84": footprints[0]},
            {**other, "bbox_wgs84": footprints[1], "uses": asset["uses"]},
            gap_asset,
        ]
        build_vrt = s3_rasters._build_vrt

        def verify_all_assets_leased(paths, vrt, reader):
            self.assertFalse(self.cache.cleanup(0)["target_reached"])
            return build_vrt(paths, vrt, reader)

        combined = self.root / "two-sources.tif"
        with patch("src.s3_rasters._build_vrt", side_effect=verify_all_assets_leased):
            prepare_planned_region(self.cache, plan, "area", "dsm", combined)
        with rasterio.open(combined) as raster:
            self.assertEqual(list(raster.sample([(1200024, 4999970)]))[0][0], 1)
            self.assertEqual(list(raster.sample([(1200232, 4999970)]))[0][0], 42)
            self.assertEqual(list(raster.sample([(1200120, 4999970)]))[0][0], -9999)
        manifest = verify_mosaic(combined, combined.with_name(combined.name + ".source.json"))
        self.assertEqual({source["item_id"] for source in manifest["sources"]},
                         {"wide", other["item_id"]})
        self.assertTrue(self.cache.cleanup(0)["target_reached"])

        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True), rasterio.open(tile, "r+") as dataset:
            mask = np.full((8, 32), 255, dtype="uint8")
            mask[2, 2] = 0
            dataset.write_mask(mask)
        payload = tile.read_bytes()
        plan["assets"] = [{**asset, "size_bytes": len(payload),
                           "checksum": "1220" + hashlib.sha256(payload).hexdigest()}]
        rejected = self.root / "masked-region.tif"
        with patch("src.s3_rasters.require_raster_support", return_value="GTiff"), \
                self.assertRaisesRegex(ValueError, "per-dataset masks"):
            prepare_planned_region(self.cache, plan, "area", "dsm", rejected)
        self.assertFalse(rejected.exists())
        self.assertFalse(rejected.with_name(rejected.name + ".source.json").exists())

    def test_planned_region_preserves_multiband_imagery(self):
        tile = self.root / "rgb.tif"
        with rasterio.open(tile, "w", driver="GTiff", count=3, width=32, height=8,
                           dtype="uint8", crs="EPSG:2193", nodata=0,
                           transform=from_origin(1200000, 5000000, 8, 8)) as dataset:
            for band, value in enumerate((10, 20, 30), start=1):
                dataset.write(np.full((8, 32), value, dtype="uint8"), band)
            tile_bounds = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds,
                                           densify_pts=21)
            colors = dataset.colorinterp
        payload = tile.read_bytes()
        footprints = [
            transform_bounds("EPSG:2193", "EPSG:4326", x0, 4999944, x1, 4999992,
                             densify_pts=21)
            for x0, x1 in ((1200008, 1200040), (1200216, 1200248))
        ]
        asset = {**self.asset, "item_id": "rgb", "url": tile.as_uri(),
                 "size_bytes": len(payload), "checksum": "1220" + hashlib.sha256(payload).hexdigest(),
                 "bbox_wgs84": tile_bounds,
                 "uses": [{"region": "area", "role": "imagery", "layer_id": 102}]}
        plan = {
            "inputs": [{"region": "area", "role": "imagery", "layer_id": 102,
                        "footprints_wgs84": footprints,
                        "source": {"collection_url": asset["collection_url"],
                                   "asset_key": "data", "expected_collection_id": "capture"}}],
            "assets": [asset],
        }
        output = self.root / "rgb-region.tif"
        prepare_planned_region(self.cache, plan, "area", "imagery", output)
        with rasterio.open(output) as raster:
            self.assertEqual(raster.colorinterp, colors)
            np.testing.assert_array_equal(list(raster.sample([(1200024, 4999970)]))[0], [10, 20, 30])
            np.testing.assert_array_equal(list(raster.sample([(1200120, 4999970)]))[0], [0, 0, 0])
            np.testing.assert_array_equal(list(raster.sample([(1200232, 4999970)]))[0], [10, 20, 30])

    def test_adjacent_tiles_preserve_grid_and_values(self):
        other = self._second_tile()
        bounds = transform_bounds("EPSG:2193", "EPSG:4326",
                                  1200000, 4999936, 1200128, 5000000, densify_pts=21)
        reference_vrt = self.root / "reference.vrt"
        reference = self.root / "reference.tif"
        subprocess.run(["gdalbuildvrt", "-q", "-strict", str(reference_vrt),
                        str(self.tile), str(self.root / f"{other['item_id']}.tif")], check=True)
        x0, y0, x1, y1 = s3_rasters._nztm_bounds(bounds)
        subprocess.run(["gdal_translate", "-q", "-projwin", str(x0), str(y1), str(x1),
                        str(y0), str(reference_vrt), str(reference)], check=True)
        output = self.root / "adjacent.tif"
        with patch("src.s3_rasters.require_raster_support", return_value="GTiff"):
            prepare_cached_mosaic(self.cache, [self.asset, other], bounds, output)
        with rasterio.open(output) as actual, rasterio.open(reference) as expected:
            self.assertEqual(actual.transform, expected.transform)
            self.assertEqual(actual.shape, expected.shape)
            np.testing.assert_array_equal(actual.read(), expected.read())
            np.testing.assert_array_equal(actual.read_masks(), expected.read_masks())

    def test_rejects_mixed_sources_and_incompatible_native_grids(self):
        incompatible = [
            {**self._second_tile(), "collection_id": "different-capture"},
            self._second_tile(resolution=4),
            self._second_tile(nodata=-32768),
            self._second_tile(x=1200066),
        ]
        for index, asset in enumerate(incompatible):
            output = self.root / f"bad-{index}.tif"
            with self.subTest(asset=asset["item_id"]), \
                    self.assertRaisesRegex(ValueError, "source|grid|nodata"):
                prepare_cached_mosaic(self.cache, [self.asset, asset], self.bounds, output)
            self.assertFalse(output.exists())

    def test_rejects_uncovered_area_when_missing_pixels_have_no_mask(self):
        tile = self.root / "unmasked.tif"
        with rasterio.open(tile, "w", driver="GTiff", count=1, width=8, height=8,
                           dtype="uint8", crs="EPSG:2193",
                           transform=from_origin(1200000, 5000000, 8, 8)) as dataset:
            dataset.write(np.ones((8, 8), dtype="uint8"), 1)
        payload = tile.read_bytes()
        asset = {**self.asset, "item_id": "unmasked", "url": tile.as_uri(),
                 "size_bytes": len(payload), "checksum": "1220" + hashlib.sha256(payload).hexdigest()}
        output = self.root / "uncovered.tif"
        with self.assertRaisesRegex(ValueError, "[Uu]ncovered.*mask|mask.*uncovered"):
            prepare_cached_mosaic(self.cache, [asset], self.bounds, output)
        self.assertFalse(output.exists())

    def test_survey_plan_reservation_and_warm_mosaic_end_to_end(self):
        source = self.asset["collection_url"]
        documents = {
            source: {"id": "capture", "license": "CC-BY-4.0",
                     "providers": [{"name": "LINZ", "roles": ["producer"]}],
                     "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "tile", "collection": "capture", "bbox": self.bounds,
                "properties": {"start_datetime": "2021-01-01T00:00:00Z",
                               "end_datetime": "2021-12-31T23:59:59Z",
                               "proj:code": "EPSG:2193", "gsd": 1},
                "assets": {"data": {"href": self.tile.as_uri(), "type": "image/tiff",
                                    "file:size": self.asset["size_bytes"],
                                    "file:checksum": self.asset["checksum"]}}},
        }
        survey = {"name": "fixture", "elevation": "lidar", "dsm_layer": 101}
        mapping = LayerMapping(101, "dsm", source, "capture", "data", "offline fixture")
        selection = plan_regions(
            ["area", "other"], mappings=[mapping], include_imagery=False,
            bbox_for=lambda region: self.bounds,
            footprints_for=lambda region, bbox: [bbox],
            survey_for=lambda bbox, region: survey, get_json=documents.__getitem__)
        plan = selection["batch"]
        self.assertEqual(len(plan["assets"]), 1)
        self.cache._prepare()
        requirements = DiskRequirements(self.cache.directory, self.root, self.root,
                                        20000, 20000, 20000, 1000)
        reservations = DiskReservations(self.root / "reservations",
                                        probe=lambda path: (1, 10000000))
        first = self.root / "cold.tif"
        with reservations.reserve(lambda: self.cache.inspect_plan(plan), requirements):
            prepare_planned_mosaic(self.cache, plan, "area", "dsm", self.bounds, first)
        second = self.root / "warm.tif"
        with patch("src.raster_cache.urllib.request.urlopen",
                   side_effect=AssertionError("warm cache attempted network transfer")):
            with reservations.reserve(lambda: self.cache.inspect_plan(plan), requirements) as forecast:
                self.assertEqual(forecast["cached_recovery_bytes"], self.asset["size_bytes"])
                self.assertEqual(forecast["filesystems"][0]["allocations"]["cache_transfer"],
                                 self.asset["size_bytes"])
                ensure_planned_region(self.cache, plan, "other", "dsm", second)
        with rasterio.open(first) as cold, rasterio.open(second) as warm:
            self.assertEqual(cold.transform, warm.transform)
            np.testing.assert_array_equal(cold.read(), warm.read())
            np.testing.assert_array_equal(cold.read_masks(), warm.read_masks())
        manifest = verify_mosaic(first, first.with_name(first.name + ".source.json"))
        self.assertEqual((manifest["region"], manifest["role"], manifest["layer_id"]),
                         ("area", "dsm", 101))
        self.assertEqual(manifest["survey_name"], "fixture")
        self.assertEqual(manifest["mapping_evidence"], "offline fixture")
        self.assertEqual(manifest["sources"][0]["license"], "CC-BY-4.0")
        self.assertEqual(manifest["sources"][0]["providers"][0]["name"], "LINZ")
        self.assertEqual(manifest["sources"][0]["start_datetime"], "2021-01-01T00:00:00Z")
        self.assertEqual(manifest["sources"][0]["native_crs"], "EPSG:2193")
        self.assertEqual(manifest["sources"][0]["sha256"], hashlib.sha256(self.tile.read_bytes()).hexdigest())
        second_manifest = verify_mosaic(second, second.with_name(second.name + ".source.json"))
        self.assertEqual(second_manifest["region"], "other")
        self.assertEqual(manifest["sources"], second_manifest["sources"])
        self.assertTrue(self.cache.cleanup(0)["target_reached"])
        with patch("src.raster_cache.urllib.request.urlopen",
                   side_effect=AssertionError("verified mosaic reuse transferred bytes")):
            self.assertEqual(ensure_planned_region(self.cache, plan, "area", "dsm", first), first)

        changed_crs = copy.deepcopy(plan)
        changed_crs["assets"][0]["native_crs"] = "EPSG:4326"
        with self.assertRaisesRegex(ValueError, "native CRS"):
            ensure_planned_region(self.cache, changed_crs, "area", "dsm", first)
        sidecar = first.with_name(first.name + ".source.json")
        legacy_manifest = copy.deepcopy(manifest)
        del legacy_manifest["sources"][0]["native_crs_actual"]
        legacy_manifest["fingerprint"] = fingerprint(
            {key: value for key, value in legacy_manifest.items() if key != "fingerprint"})
        sidecar.write_text(json.dumps(legacy_manifest))
        with self.assertRaisesRegex(ValueError, "native CRS"):
            ensure_planned_region(self.cache, plan, "area", "dsm", first)
        sidecar.write_text(json.dumps(manifest))

        for field, value in (("revision", "replacement"), ("url", "https://example.test/other"),
                             ("checksum", "1220" + "0" * 64), ("size_bytes", 1)):
            changed = copy.deepcopy(plan)
            changed["assets"][0][field] = value
            with self.subTest(field=field), \
                    self.assertRaisesRegex(ValueError, "source version"):
                ensure_planned_region(self.cache, changed, "area", "dsm", first)
        changed = copy.deepcopy(plan)
        changed["assets"][0]["revision"] = None
        changed["assets"][0]["checksum"] = None
        with self.assertRaisesRegex(ValueError, "unversioned"):
            ensure_planned_region(self.cache, changed, "area", "dsm", first)
        changed["assets"][0]["revision"] = {"updated": "invalid"}
        with self.assertRaisesRegex(ValueError, "unversioned"):
            ensure_planned_region(self.cache, changed, "area", "dsm", first)
        with self.assertRaisesRegex(ValueError, "processing"):
            verify_planned_region(plan, "area", "dsm", first, res=2)
        with self.assertRaisesRegex(ValueError, "Resolution"):
            verify_planned_region(plan, "area", "dsm", first, res=True)
        changed = copy.deepcopy(plan)
        changed["inputs"][0]["footprints_wgs84"] = [[-10, -10, -9, -9]]
        with self.assertRaisesRegex(ValueError, "No planned raster asset intersects"):
            verify_planned_region(changed, "area", "dsm", first)
        legacy = self.root / "untracked.tif"
        legacy.write_bytes(first.read_bytes())
        with self.assertRaisesRegex(ValueError, "Cannot read raster provenance"):
            ensure_planned_region(self.cache, plan, "area", "dsm", legacy)
        self.assertEqual(legacy.read_bytes(), first.read_bytes())
        orphan = self.root / "orphan.tif"
        orphan.with_name(orphan.name + ".source.json").write_text(
            first.with_name(first.name + ".source.json").read_text())
        with self.assertRaisesRegex(ValueError, "output provenance"):
            ensure_planned_region(self.cache, plan, "area", "dsm", orphan)
        self.assertFalse(orphan.exists())
        first.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "does not match provenance"):
            ensure_planned_region(self.cache, plan, "area", "dsm", first)

    def test_planned_reuse_rejects_exports_fallback(self):
        destination = self.root / "legacy.tif"
        destination.write_bytes(b"untracked")
        plan = {"inputs": [{"region": "area", "role": "dsm",
                            "footprints_wgs84": [self.bounds], "source": None}],
                "assets": []}
        with self.assertRaisesRegex(ValueError, "exports fallback"):
            ensure_planned_region(self.cache, plan, "area", "dsm", destination)
        self.assertEqual(destination.read_bytes(), b"untracked")

    def test_planned_execution_enforces_area_role_layer_and_footprint(self):
        source = self.asset["collection_url"]
        documents = {
            source: {"id": "capture", "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "tile", "collection": "capture", "bbox": self.bounds,
                "assets": {"data": {"href": self.tile.as_uri(), "type": "image/tiff",
                                    "file:size": self.asset["size_bytes"],
                                    "file:checksum": self.asset["checksum"]}}},
        }
        selected = survey_requests(
            "area", [self.bounds], {"elevation": "lidar", "dsm_layer": 101},
            mappings=[LayerMapping(101, "dsm", source, "capture", "data", "fixture")],
            include_imagery=False)
        plan = plan_batch(selected["requests"], get_json=documents.__getitem__)
        destination = self.root / "plan.tif"
        with patch("src.raster_prepare.prepare_cached_mosaic") as prepare:
            with self.assertRaisesRegex(ValueError, "one planned input"):
                prepare_planned_mosaic(self.cache, plan, "other", "dsm", self.bounds, destination)
            with self.assertRaisesRegex(ValueError, "not planned"):
                prepare_planned_mosaic(self.cache, plan, "area", "dsm",
                                       (0, 0, 1, 1), destination)
            changed = {**plan, "assets": [{**plan["assets"][0], "collection_id": "wrong"}]}
            with self.assertRaisesRegex(ValueError, "source does not match"):
                prepare_planned_mosaic(self.cache, changed, "area", "dsm", self.bounds, destination)
            changed = {**plan, "assets": [{**plan["assets"][0],
                       "uses": [{"region": "area", "role": "dsm", "layer_id": 102}]}]}
            with self.assertRaisesRegex(ValueError, "layer does not match"):
                prepare_planned_mosaic(self.cache, changed, "area", "dsm", self.bounds, destination)
            fallback = {**plan, "inputs": [{**plan["inputs"][0], "source": None}]}
            with self.assertRaisesRegex(ValueError, "exports fallback"):
                prepare_planned_mosaic(self.cache, fallback, "area", "dsm", self.bounds, destination)
            prepare.assert_not_called()

    def test_planned_execution_only_uses_tiles_for_the_selected_footprint(self):
        source = self.asset["collection_url"]
        first = self.bounds
        second = (first[0] + 1, first[1], first[2] + 1, first[3])
        selected = survey_requests(
            "area", [first, second], {"elevation": "lidar", "dsm_layer": 101},
            mappings=[LayerMapping(101, "dsm", source, "capture", "data", "fixture")],
            include_imagery=False)
        documents = {
            source: {"id": "capture", "links": [
                {"rel": "item", "href": "a.json"}, {"rel": "item", "href": "b.json"}]},
            "https://example.test/a.json": {"id": "a", "bbox": first,
                "assets": {"data": {"href": self.tile.as_uri(), "type": "image/tiff"}}},
            "https://example.test/b.json": {"id": "b", "bbox": second,
                "assets": {"data": {"href": self.tile.as_uri(), "type": "image/tiff"}}},
        }
        plan = plan_batch(selected["requests"], get_json=documents.__getitem__)
        with patch("src.raster_prepare.prepare_cached_mosaic") as prepare:
            prepare_planned_mosaic(self.cache, plan, "area", "dsm", first, self.root / "first.tif")
            self.assertEqual([asset["item_id"] for asset in prepare.call_args.args[1]], ["a"])

    def test_prepared_provenance_verifies_output_after_cache_eviction(self):
        destination = self.root / "provenance.tif"
        sidecar = self.root / "provenance.tif.source.json"
        prepare_cached_mosaic(self.cache, [self.asset], self.bounds, destination,
                              provenance={"region": "area", "role": "dsm", "layer_id": 101})
        manifest = verify_mosaic(destination, sidecar)
        self.assertEqual(manifest["sources"][0]["sha256"], hashlib.sha256(self.tile.read_bytes()).hexdigest())
        self.assertEqual(manifest["sources"][0]["source_checksum"], self.asset["checksum"])
        self.assertEqual(manifest["region"], "area")
        self.assertEqual(manifest["processing"]["crs"], "EPSG:2193")
        self.assertTrue(self.cache.cleanup(0)["target_reached"])
        self.assertEqual(verify_mosaic(destination, sidecar), manifest)
        destination.write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "does not match"):
            verify_mosaic(destination, sidecar)

    def test_provenance_detects_tampering_and_missing_output(self):
        destination = self.root / "provenance.tif"
        sidecar = self.root / "provenance.tif.source.json"
        prepare_cached_mosaic(self.cache, [self.asset], self.bounds, destination,
                              provenance={"region": "area", "role": "dsm", "layer_id": 101})
        original = sidecar.read_text()
        sidecar.write_text(original.replace('"region": "area"', '"region": "another"'))
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            verify_mosaic(destination, sidecar)
        sidecar.write_text(original)
        destination.unlink()
        with self.assertRaisesRegex(ValueError, "Cannot read provenance-tracked mosaic"):
            verify_mosaic(destination, sidecar)

    def test_provenance_failure_does_not_replace_existing_files(self):
        destination = self.root / "provenance.tif"
        sidecar = self.root / "provenance.tif.source.json"
        sidecar.write_text("existing")
        with self.assertRaisesRegex(FileExistsError, "existing raster provenance"):
            prepare_cached_mosaic(self.cache, [self.asset], self.bounds, destination,
                                  provenance={"region": "area", "role": "dsm", "layer_id": 101})
        self.assertEqual(sidecar.read_text(), "existing")
        self.assertFalse(destination.exists())
        sidecar.unlink()
        real_link = os.link

        def link(source, target):
            if target == destination:
                raise OSError("fixture publication failure")
            return real_link(source, target)

        with patch("src.raster_prepare.os.link", side_effect=link):
            with self.assertRaisesRegex(OSError, "fixture publication failure"):
                prepare_cached_mosaic(self.cache, [self.asset], self.bounds, destination,
                                      provenance={"region": "area", "role": "dsm", "layer_id": 101})
        self.assertFalse(destination.exists())
        self.assertFalse(sidecar.exists())

    def test_provenance_records_unverified_upstream_without_fabricated_checksum(self):
        destination = self.root / "no-upstream-checksum.tif"
        asset = {**self.asset, "checksum": None}
        prepare_cached_mosaic(self.cache, [asset], self.bounds, destination,
                              provenance={"region": "area", "role": "dsm", "layer_id": 101})
        manifest = verify_mosaic(destination, self.root / "no-upstream-checksum.tif.source.json")
        self.assertIsNone(manifest["sources"][0]["source_checksum"])
        self.assertEqual(manifest["sources"][0]["sha256"], hashlib.sha256(self.tile.read_bytes()).hexdigest())

    def test_provenance_publication_rejects_conflicting_destination_race(self):
        destination = self.root / "race.tif"
        sidecar = self.root / "race.tif.source.json"
        real_link = os.link

        def link(source, target):
            if target == destination:
                destination.write_bytes(b"other writer")
            return real_link(source, target)

        with patch("src.raster_prepare.os.link", side_effect=link):
            with self.assertRaises(FileExistsError):
                prepare_cached_mosaic(self.cache, [self.asset], self.bounds, destination,
                                      provenance={"region": "area", "role": "dsm", "layer_id": 101})
        self.assertEqual(destination.read_bytes(), b"other writer")
        self.assertFalse(sidecar.exists())

    def test_failed_preparation_leaves_no_output(self):
        destination = self.root / "mosaic.tif"
        with patch("src.s3_rasters._build_vrt", side_effect=RuntimeError("fixture failure")):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                prepare_cached_mosaic(self.cache, [self.asset], self.bounds, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob(".raster-prepare-*")))
        self.assertTrue(self.cache.cleanup(0)["target_reached"])


if __name__ == "__main__":
    unittest.main()
