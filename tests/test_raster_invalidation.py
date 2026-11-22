"""Verified raster content participates in incremental build decisions."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import build_keys, run_stage
from src.raster_provenance import file_sha256, fingerprint


class RasterInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.region = self.data / "regions" / "area"
        self.region.mkdir(parents=True)
        self.raster = self.region / "dsm_mosaic.tif"
        self.raster.write_bytes(b"first mosaic")
        self.sidecar = self.raster.with_name(self.raster.name + ".source.json")
        self.patches = [
            patch.object(build_keys, "DATA", self.data),
            patch.object(build_keys, "reading_hash", return_value="reading"),
            patch.object(build_keys, "labels_hash", return_value="labels"),
            patch.object(build_keys, "outline_slide", return_value=None),
            patch.object(run_stage, "STATE_DIR", self.data / "state"),
            patch.object(run_stage, "_git_sha", return_value="test"),
            patch.object(run_stage, "_declared_inputs", return_value=[self.raster]),
            patch.object(run_stage, "stage_code_hash", return_value="code"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.content = {
            "schema_version": 1, "region": "area", "role": "dsm", "layer_id": 101,
            "footprint_wgs84": [1, 2, 3, 4], "processing": {"crs": "EPSG:2193"},
            "sources": [{"collection_url": "https://example.test/collection",
                         "collection_id": "collection", "item_id": "tile", "asset_key": "data",
                         "sha256": "a" * 64, "url": "https://example.test/data", "revision": "1"}],
            "output": {"name": self.raster.name, "sha256": file_sha256(self.raster)},
        }

    def write_sidecar(self):
        self.sidecar.write_text(json.dumps({**self.content, "fingerprint": fingerprint(self.content)}))

    def test_legacy_keys_and_markers_are_not_given_fabricated_provenance(self):
        build_keys.record_keys("area", [1], geometry_hash="code")
        self.assertEqual(build_keys.building_key(1, "code", "area"), "reading+labels+code")
        self.assertEqual(build_keys.stale_buildings("area", [1], "code"), [])
        run_stage.write_marker("build_layout_geojson", "area", 1)
        self.assertTrue(run_stage.is_done("build_layout_geojson", "area")[0])
        self.write_sidecar()
        self.assertEqual(build_keys.stale_buildings("area", [1], "code"), [1])
        self.assertEqual(run_stage.is_done("build_layout_geojson", "area"),
                         (False, "tracked raster inputs changed since the marker"))

    def test_source_content_change_invalidates_without_newer_mtime(self):
        self.write_sidecar()
        build_keys.record_keys("area", [1, 2], geometry_hash="code")
        run_stage.write_marker("build_layout_geojson", "area", 1)
        self.content["sources"][0]["sha256"] = "b" * 64
        self.write_sidecar()
        os.utime(self.raster, (1, 1))
        self.assertEqual(build_keys.stale_buildings("area", [1, 2], "code"), [1, 2])
        self.assertFalse(run_stage.is_done("build_layout_geojson", "area")[0])

    def test_multi_footprint_change_invalidates_keys_and_markers(self):
        del self.content["footprint_wgs84"]
        self.content["footprints_wgs84"] = [[1, 2, 3, 4], [5, 6, 7, 8]]
        self.write_sidecar()
        build_keys.record_keys("area", [1], geometry_hash="code")
        run_stage.write_marker("build_layout_geojson", "area", 1)
        self.content["footprints_wgs84"][1] = [5, 6, 7, 9]
        self.write_sidecar()
        self.assertEqual(build_keys.stale_buildings("area", [1], "code"), [1])
        self.assertFalse(run_stage.is_done("build_layout_geojson", "area")[0])

    def test_transfer_metadata_does_not_invalidate_scientific_identity(self):
        self.write_sidecar()
        build_keys.record_keys("area", [1], geometry_hash="code")
        run_stage.write_marker("build_layout_geojson", "area", 1)
        self.content["sources"][0]["url"] = "https://example.test/moved"
        self.content["sources"][0]["revision"] = "2"
        self.content["sources"][0]["license"] = "updated attribution"
        self.content["sources"][0]["providers"] = [{"name": "updated provider"}]
        self.content["mapping_evidence"] = "reviewed mapping"
        self.write_sidecar()
        self.assertEqual(build_keys.stale_buildings("area", [1], "code"), [])
        self.assertTrue(run_stage.is_done("build_layout_geojson", "area")[0])

    def test_tampering_and_wrong_region_fail_explicitly(self):
        self.write_sidecar()
        self.raster.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_keys.stale_buildings("area", [1], "code")
        self.content["output"]["sha256"] = file_sha256(self.raster)
        self.content["region"] = "another"
        self.write_sidecar()
        with self.assertRaisesRegex(ValueError, "does not match"):
            run_stage.write_marker("build_layout_geojson", "area", 1)

    def test_changed_during_build_does_not_record_keys_or_marker(self):
        self.write_sidecar()
        build_keys.record_keys("area", [1, 2], geometry_hash="code")
        build_keys.forget_keys("area", [1])
        snapshot = build_keys.raster_inputs("area")
        self.content["sources"][0]["sha256"] = "c" * 64
        self.write_sidecar()
        with self.assertRaisesRegex(RuntimeError, "changed during"):
            build_keys.record_keys("area", [1], geometry_hash="code", raster_identity=snapshot)
        self.assertEqual(list(build_keys.load_keys("area")), ["2"])
        self.assertIn(1, build_keys.stale_buildings("area", [1], "code"))
        with self.assertRaisesRegex(RuntimeError, "changed during"):
            run_stage.write_marker("build_layout_geojson", "area", 1, raster_inputs=snapshot)
        self.assertFalse(run_stage.marker_path("build_layout_geojson", "area").exists())

    def test_full_build_invalidates_all_previous_keys_before_writing(self):
        build_keys.record_keys("area", [1, 2], geometry_hash="code")
        build_keys.forget_keys("area")
        self.assertEqual(build_keys.stale_buildings("area", [1, 2], "code"), [1, 2])

    def test_failed_stage_removes_an_older_completion_marker(self):
        run_stage.write_marker("build_layout_geojson", "area", 1)
        with patch.object(sys, "argv", ["run_stage.py", "--force", "build_layout_geojson", "area"]), \
             patch("src.preflight.preflight"), \
             patch.object(run_stage.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(run_stage.main(), 1)
        self.assertFalse(run_stage.marker_path("build_layout_geojson", "area").exists())

    def test_removing_sidecar_makes_tracked_output_stale(self):
        self.write_sidecar()
        build_keys.record_keys("area", [1], geometry_hash="code")
        run_stage.write_marker("build_layout_geojson", "area", 1)
        self.sidecar.unlink()
        self.assertEqual(build_keys.stale_buildings("area", [1], "code"), [1])
        self.assertFalse(run_stage.is_done("build_layout_geojson", "area")[0])

    def test_bad_source_schema_does_not_count_as_verified(self):
        self.content["sources"][0].pop("item_id")
        self.write_sidecar()
        with self.assertRaisesRegex(ValueError, "Invalid source identity"):
            build_keys.raster_inputs("area")

    def test_reference_imagery_is_included_only_when_selected(self):
        self.raster.rename(self.region / "reference_imagery_mosaic.tif")
        self.raster = self.region / "reference_imagery_mosaic.tif"
        self.sidecar = self.raster.with_name(self.raster.name + ".source.json")
        self.content["role"] = "reference_imagery"
        self.content["output"]["name"] = self.raster.name
        self.write_sidecar()
        with patch.object(run_stage, "_declared_inputs", return_value=[self.raster]), \
             patch.dict(os.environ, {"SOLAR_INCLUDE_IMAGERY": "1"}):
            self.assertIn("reference_imagery", build_keys.raster_inputs("area"))
            self.assertIn("reference_imagery", run_stage._tracked_rasters("build_layout_geojson", "area"))
        with patch.object(run_stage, "_declared_inputs", return_value=[self.raster]), \
             patch.dict(os.environ, {"SOLAR_INCLUDE_IMAGERY": "0"}):
            self.assertEqual(build_keys.raster_inputs("area"), {})
            self.assertEqual(run_stage._tracked_rasters("build_layout_geojson", "area"), {})


if __name__ == "__main__":
    unittest.main()
