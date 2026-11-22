"""Planning CLI exposes fallbacks and cache inspection without raster transfers."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import raster_plan


class RasterPlanCliTests(unittest.TestCase):
    def test_planner_receives_mapping_and_inspects_cache(self):
        batch = {"assets": [], "inputs": [], "export_fallbacks": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mapping = root / "mappings.json"
            mapping.write_text('{"version": 1, "mappings": []}')
            output = io.StringIO()
            with patch.object(raster_plan, "plan_regions", return_value={"regions": [], "batch": batch}) as plan, \
                    contextlib.redirect_stdout(output):
                result = raster_plan.main(["area", "--mappings", str(mapping), "--no-imagery",
                                           "--cache-dir", str(root / "cache"),
                                           "--cache-max-bytes", "1000",
                                           "--unknown-asset-bytes", "100"])
            self.assertEqual(result, 0)
            plan.assert_called_once_with(["area"], mappings=[], include_imagery=False)
            self.assertEqual(json.loads(output.getvalue())["batch"]["cache"]["budget_bytes"], 1000)

    def test_mapping_failure_is_reported_without_resolving_regions(self):
        error = io.StringIO()
        with patch.object(raster_plan, "plan_regions") as plan, contextlib.redirect_stderr(error):
            self.assertEqual(raster_plan.main(["area", "--mappings", "/missing/mappings.json"]), 2)
        self.assertIn("Raster planning failed", error.getvalue())
        plan.assert_not_called()

    def test_cache_configuration_is_all_or_nothing(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            raster_plan.main(["area", "--cache-dir", "/tmp/cache"])
        self.assertEqual(exc.exception.code, 2)

    def test_policy_file_exposes_effective_settings_and_can_forecast(self):
        batch = {"assets": [], "inputs": [], "export_fallbacks": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy.json"
            policy.write_text('{"version": 1, "max_bytes": 1000, "min_free_bytes": 10}')
            output = io.StringIO()
            with patch.object(raster_plan, "plan_regions", return_value={"regions": [], "batch": batch}), \
                    patch("src.raster_disk_plan.shutil.disk_usage",
                          return_value=type("Usage", (), {"total": 10000, "free": 1000})()), \
                    contextlib.redirect_stdout(output):
                result = raster_plan.main(["area", "--cache-policy", str(policy),
                                           "--work-dir", str(root), "--output-dir", str(root),
                                           "--mosaic-bytes", "1", "--scratch-bytes", "2",
                                           "--output-bytes", "3"])
            self.assertEqual(result, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["cache_policy"]["values"]["max_bytes"], 1000)
            self.assertEqual(payload["cache_policy"]["sources"]["max_bytes"], f"file:{policy.resolve()}")
            self.assertEqual(payload["disk_forecast"]["filesystems"][0]["reserve_bytes"], 10)

    def test_policy_file_rejects_conflicting_cli_overrides(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            raster_plan.main(["area", "--cache-policy", "policy.json", "--min-free-bytes", "0"])
        self.assertEqual(exc.exception.code, 2)

    def test_invalid_policy_is_reported_without_resolving_regions(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            policy.write_text('{"version": 1, "queue_lookahead": 0}')
            error = io.StringIO()
            with patch.object(raster_plan, "plan_regions") as plan, contextlib.redirect_stderr(error):
                result = raster_plan.main(["area", "--cache-policy", str(policy)])
            self.assertEqual(result, 2)
            self.assertIn("lookahead must be positive", error.getvalue())
            plan.assert_not_called()

    def test_forecast_sums_supplied_bounds_and_reports_no_reservation(self):
        batch = {"assets": [], "inputs": [], "export_fallbacks": [{"region": "area"}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = io.StringIO()
            with patch.object(raster_plan, "plan_regions", return_value={"regions": [], "batch": batch}), \
                    patch("src.raster_disk_plan.shutil.disk_usage",
                          return_value=type("Usage", (), {"free": 1000})()), \
                    contextlib.redirect_stdout(output):
                result = raster_plan.main([
                    "area", "--cache-dir", str(root / "cache"), "--cache-max-bytes", "1000",
                    "--unknown-asset-bytes", "100", "--min-free-bytes", "50",
                    "--work-dir", str(root), "--output-dir", str(root),
                    "--mosaic-bytes", "100", "--scratch-bytes", "200", "--output-bytes", "300",
                    "--export-bytes", "400"])
            self.assertEqual(result, 0)
            forecast = json.loads(output.getvalue())["disk_forecast"]
            self.assertFalse(forecast["reservation"])
            self.assertFalse(forecast["sufficient_at_check"])
            filesystem, = forecast["filesystems"]
            self.assertEqual(filesystem["allocation_bytes"], 1000)
            self.assertEqual(filesystem["shortfall_bytes"], 50)

    def test_forecast_includes_extra_allocation(self):
        batch = {"assets": [], "inputs": [], "export_fallbacks": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temporary = root / "temporary"
            temporary.mkdir()
            output = io.StringIO()
            with patch.object(raster_plan, "plan_regions", return_value={"regions": [], "batch": batch}), \
                    patch("src.raster_disk_plan.shutil.disk_usage",
                          return_value=type("Usage", (), {"free": 1000})()), \
                    contextlib.redirect_stdout(output):
                result = raster_plan.main([
                    "area", "--cache-dir", str(root / "cache"), "--cache-max-bytes", "1000",
                    "--unknown-asset-bytes", "100", "--work-dir", str(root),
                    "--output-dir", str(root), "--mosaic-bytes", "100",
                    "--scratch-bytes", "200", "--output-bytes", "300",
                    "--extra-allocation", "temporary", str(temporary), "450"])
            self.assertEqual(result, 0)
            filesystem, = json.loads(output.getvalue())["disk_forecast"]["filesystems"]
            self.assertEqual(filesystem["allocations"]["temporary"], 450)
            self.assertEqual(filesystem["shortfall_bytes"], 50)

    def test_forecast_requires_complete_bounds_and_explicit_fallback_size(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            raster_plan.main(["area", "--work-dir", "/tmp"])
        self.assertEqual(exc.exception.code, 2)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            raster_plan.main(["area", "--extra-allocation", "temp", "/tmp", "1"])
        self.assertEqual(exc.exception.code, 2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = {"assets": [], "inputs": [], "export_fallbacks": [{"region": "area"}]}
            error = io.StringIO()
            with patch.object(raster_plan, "plan_regions", return_value={"regions": [], "batch": batch}), \
                    contextlib.redirect_stderr(error):
                result = raster_plan.main([
                    "area", "--cache-dir", str(root / "cache"), "--cache-max-bytes", "1000",
                    "--unknown-asset-bytes", "100", "--work-dir", str(root),
                    "--output-dir", str(root), "--mosaic-bytes", "0", "--scratch-bytes", "0",
                    "--output-bytes", "0"])
            self.assertEqual(result, 2)
            self.assertIn("requires an explicit positive size estimate", error.getvalue())


if __name__ == "__main__":
    unittest.main()
