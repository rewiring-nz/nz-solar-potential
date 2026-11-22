"""Offline LERC dependency and strict mosaic checks."""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import s3_rasters
from tools.pipeline import _preflight


class RasterSupportTests(unittest.TestCase):
    def test_fixture_decodes_exact_values(self):
        with rasterio.io.MemoryFile(base64.b64decode(s3_rasters._LERC_PROBE)) as memory:
            with memory.open() as source:
                self.assertEqual(source.read(1).tolist(), [[1, 2], [3, 4]])
                self.assertEqual(source.crs.to_epsg(), 2193)

    def test_missing_tools_explain_native_dependencies(self):
        with patch("src.s3_rasters.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Missing GDAL commands.*requirements.txt"):
                s3_rasters.require_raster_support()

    def test_decode_failure_explains_native_install_and_does_not_download(self):
        with patch("src.s3_rasters.shutil.which", return_value="/bin/gdal"), \
             patch("src.s3_rasters.subprocess.run", return_value=subprocess.CompletedProcess(
                 [], 1, "", "missing codec LERC")), \
             patch("src.s3_rasters.tile_index") as index:
            with self.assertRaisesRegex(RuntimeError, "No available GDAL TIFF reader"):
                s3_rasters.require_raster_support()
            index.assert_not_called()

    def test_auto_falls_back_to_libertiff_without_environment_override(self):
        def decode(cmd, **kwargs):
            if "GTiff" in kwargs["env"].get("GDAL_SKIP", "").split():
                Path(cmd[-1]).write_text("1 2\n3 4\n")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 1, "", "GTiff lacks LERC")

        with patch.dict(os.environ, {}, clear=True), \
             patch("src.s3_rasters.shutil.which", return_value="/bin/gdal"), \
             patch("src.s3_rasters.subprocess.run", side_effect=decode):
            self.assertEqual(s3_rasters.require_raster_support(), "LIBERTIFF")

    def test_invalid_reader_is_rejected(self):
        with patch.dict(os.environ, {"SOLAR_TIFF_READER": "invalid"}):
            with self.assertRaisesRegex(RuntimeError, "must be auto, GTiff, or LIBERTIFF"):
                s3_rasters.require_raster_support()

    def test_reader_setting_is_scoped_and_preserves_environment(self):
        with patch.dict(os.environ, {"SOLAR_TIFF_READER": "LIBERTIFF",
                                     "GDAL_SKIP": "HDF5", "PATH": "/custom/bin"}):
            env = s3_rasters._reader_env("LIBERTIFF")
            self.assertEqual(env["GDAL_SKIP"], "HDF5 GTiff")
            self.assertEqual(env["PATH"], "/custom/bin")
            self.assertEqual(os.environ["GDAL_SKIP"], "HDF5")

    def test_pipeline_reports_decode_failure_before_credentials(self):
        with patch("tools.pipeline.subprocess.run", return_value=subprocess.CompletedProcess(
                [], 1, "", "No available GDAL TIFF reader can decode LERC")), \
             patch("tools.pipeline._key_available") as key:
            ok, details, _ = _preflight("pilot", Path(sys.executable))
            self.assertFalse(ok)
            self.assertIn("No available GDAL TIFF reader", details)
            key.assert_not_called()

    def test_pipeline_rejects_bad_raster_approval_before_preflight_work(self):
        from src.raster_plan_handoff import plan_digest

        region = "queenstown-lakes-district/queenstown/frankton"
        mapped = {"regions": [{"region": region, "inputs": [
            {"role": "dsm", "state": "mapped", "layer_id": 101}]}],
            "batch": {"inputs": [{"region": region, "role": "dsm", "layer_id": 101,
                                  "source": {"collection_url": "https://example.test"}}],
                      "export_fallbacks": [], "assets": [],
                      "footprints_without_intersecting_assets": [],
                      "footprint_bbox_coverage": []}}
        cases = [
            ({"regions": [{"region": region, "inputs": []}], "batch": None},
             "0" * 64, "plan changed"),
            ({"regions": [{"region": region, "inputs": [
                {"role": "dsm", "state": "exports_fallback", "layer_id": 101}]}],
              "batch": None}, None, "batch is missing selected inputs"),
            (mapped, None, "Mapped raster plans cannot be executed"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.json"
            for plan, digest, error in cases:
                path.write_text(json.dumps({"raster_plan": plan}))
                with self.subTest(error=error), \
                        patch.dict(os.environ, {"SOLAR_RASTER_PLAN_JOB": str(path),
                                   "SOLAR_RASTER_PLAN_SHA256": digest or plan_digest(plan)}), \
                        patch("tools.pipeline.subprocess.run") as run, \
                        patch("tools.pipeline._key_available") as key:
                    ok, details, _ = _preflight(region, Path(sys.executable))
                    self.assertFalse(ok)
                    self.assertIn(error, details)
                    run.assert_not_called()
                    key.assert_not_called()

    def test_pipeline_preflight_accepts_approved_and_unplanned_locality(self):
        from src.raster_plan_handoff import plan_digest

        region = "queenstown-lakes-district/queenstown/frankton"
        plan = {"regions": [{"region": region, "inputs": []}], "batch": None}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "job.json"
            path.write_text(json.dumps({"raster_plan": plan}))
            area_dir = root / "regions" / region
            area_dir.mkdir(parents=True)
            (area_dir / "locality.json").write_text(json.dumps({"bbox": [168, -45, 169, -44]}))
            for env in ({}, {"SOLAR_RASTER_PLAN_JOB": str(path),
                             "SOLAR_RASTER_PLAN_SHA256": plan_digest(plan)}):
                with self.subTest(approved=bool(env)), \
                        patch.dict(os.environ, env, clear=True), \
                        patch("tools.pipeline.DATA", root), \
                        patch("tools.pipeline._key_available", return_value=True), \
                        patch("tools.pipeline.shutil.which", return_value="/tool"), \
                        patch("tools.pipeline.subprocess.run",
                              return_value=subprocess.CompletedProcess([], 0, "GTiff", "")) as run, \
                        patch("src.surveys.survey_for", return_value={"name": "fixture",
                              "elevation": "synthetic"}):
                    ok, details, _ = _preflight(region, Path(sys.executable))
                    self.assertTrue(ok, details)
                    self.assertEqual(len(run.call_args_list), 3)
                    self.assertEqual(run.call_args_list[-1].args[0][1:],
                                     ["src/localities.py", "prepare", region])

    def test_pipeline_rejects_incomplete_approval_environment(self):
        for env in ({"SOLAR_RASTER_PLAN_JOB": "/job.json"},
                    {"SOLAR_RASTER_PLAN_SHA256": "0" * 64}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), \
                    patch("tools.pipeline.subprocess.run") as run:
                ok, details, _ = _preflight("pilot", Path(sys.executable))
                self.assertFalse(ok)
                self.assertIn("path and digest must both be supplied", details)
                run.assert_not_called()

    def test_successful_cli_probe_also_checks_python_decoder(self):
        def decode(cmd, **kwargs):
            Path(cmd[-1]).write_text("1 2\n3 4\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("src.s3_rasters.shutil.which", return_value="/bin/gdal"), \
             patch("src.s3_rasters.subprocess.run", side_effect=decode), \
             patch("rasterio.open", side_effect=rasterio.errors.RasterioIOError("missing codec")):
            with self.assertRaisesRegex(RuntimeError, "selected Python's Rasterio"):
                s3_rasters.require_raster_support()

    def test_cli_success_without_pixels_is_rejected(self):
        with patch("src.s3_rasters.shutil.which", return_value="/bin/gdal"), \
             patch("src.s3_rasters.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, "", "")):
            with self.assertRaisesRegex(RuntimeError, "No available GDAL TIFF reader"):
                s3_rasters.require_raster_support()

    def test_failed_build_preserves_previous_vrt(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "terrain.vrt"
            target.write_text("previous valid mosaic")
            with patch("src.s3_rasters.subprocess.run",
                       side_effect=subprocess.CalledProcessError(1, ["gdalbuildvrt"])) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    s3_rasters._build_vrt(["missing.tif"], target, "LIBERTIFF")
            self.assertIn("-strict", run.call_args.args[0])
            self.assertEqual(target.read_text(), "previous valid mosaic")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    @unittest.skipUnless(shutil.which("gdalbuildvrt") and shutil.which("gdal_translate"),
                         "GDAL CLI tools not installed")
    def test_alternative_crop_matches_cli_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory).resolve()
            source = directory / "source.tif"
            with rasterio.open(
                    source, "w", driver="GTiff", count=1, width=8, height=8,
                    dtype="float32", crs="EPSG:2193",
                    transform=rasterio.transform.from_origin(1200000, 5000000, 8, 8),
                    compress="deflate") as dataset:
                dataset.write(np.arange(64, dtype="float32").reshape(8, 8), 1)
            bounds = (1200001, 4999950, 1200050, 4999999)
            for resolution in (None, 16):
                with self.subTest(resolution=resolution):
                    expected = directory / "expected.tif"
                    actual = directory / "actual.tif"
                    cmd = ["gdal_translate", "-q", "-projwin", str(bounds[0]), str(bounds[3]),
                           str(bounds[2]), str(bounds[1])]
                    if resolution:
                        cmd += ["-tr", str(resolution), str(resolution), "-r", "average"]
                    subprocess.run([*cmd, str(source), str(expected)], check=True)
                    s3_rasters._rasterio_crop(source, actual, bounds, resolution, "average")
                    with rasterio.open(actual) as a, rasterio.open(expected) as e:
                        self.assertEqual(a.shape, e.shape)
                        self.assertEqual(a.transform, e.transform)
                        np.testing.assert_allclose(a.read(), e.read(), atol=1e-5)

    @unittest.skipUnless(shutil.which("gdalbuildvrt") and shutil.which("gdal_translate"),
                         "GDAL CLI tools not installed")
    def test_real_libertiff_probe_mosaic_and_crop(self):
        drivers = subprocess.run(["gdal_translate", "--formats"],
                                 capture_output=True, text=True, check=True).stdout
        if "LIBERTIFF" not in drivers:
            self.skipTest("LIBERTIFF requires GDAL >=3.11")
        with patch.dict(os.environ, {"SOLAR_TIFF_READER": "auto"}):
            reader = s3_rasters.require_raster_support()
            self.assertIn(reader, ("GTiff", "LIBERTIFF"))
            with tempfile.TemporaryDirectory() as directory:
                directory = Path(directory).resolve()
                tile = directory / "input.tif"
                tile.write_bytes(base64.b64decode(s3_rasters._LERC_PROBE))
                vrt = directory / "terrain.vrt"
                s3_rasters._build_vrt([str(tile)], vrt, reader)
                output = directory / "cropped.tif"
                with rasterio.open(tile) as source:
                    bounds = source.bounds
                s3_rasters._rasterio_crop(vrt, output, bounds, None, "average")
                with rasterio.open(output) as result:
                    np.testing.assert_array_equal(result.read(1), [[1, 2], [3, 4]])
                    self.assertEqual(result.crs.to_epsg(), 2193)
                s3_rasters._rasterio_crop(vrt, output, bounds, 16, "average")
                with rasterio.open(output) as result:
                    self.assertEqual(result.shape, (1, 1))
                    self.assertAlmostEqual(result.read(1)[0, 0], 2.5)
                build_vrt = s3_rasters._build_vrt
                with patch("src.s3_rasters.tiles_touching", return_value=["offline-tile"]), \
                     patch("src.s3_rasters._nztm_bounds", return_value=tuple(bounds)), \
                     patch("src.s3_rasters._build_vrt",
                           side_effect=lambda paths, target, reader=None:
                           build_vrt([str(tile)], target, reader)):
                    fetched = directory / "fetched.tif"
                    s3_rasters.fetch("offline-collection", [], fetched)
                    with rasterio.open(fetched) as result:
                        np.testing.assert_array_equal(result.read(1), [[1, 2], [3, 4]])
                    self.assertFalse(fetched.with_suffix(".src.vrt").exists())
                previous = vrt.read_bytes()
                with self.assertRaises(subprocess.CalledProcessError):
                    s3_rasters._build_vrt([str(tile), str(directory / "missing.tif")], vrt)
                self.assertEqual(vrt.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
