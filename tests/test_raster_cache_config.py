"""Offline tests for filesystem-derived raster cache configuration."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.raster_cache_config import DAY, GIB, load_cache_config


class RasterCacheConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / "policy.json"

    def load(self, settings, total=100 * GIB):
        self.config.write_text(json.dumps(settings))
        with patch("src.raster_cache_config.shutil.disk_usage") as probe:
            probe.return_value = type("Usage", (), {"total": total})()
            result = load_cache_config(self.config)
        probe.assert_called_once_with(self.root)
        return result

    def test_defaults_follow_filesystem_and_report_sources(self):
        config, sources = self.load({"version": 1})
        self.assertEqual(config.directory, self.root / "raster_cache")
        self.assertEqual(config.max_bytes, 10 * GIB)
        self.assertEqual(config.min_free_bytes, 10 * GIB)
        self.assertEqual(config.high_water_bytes, 9 * GIB)
        self.assertEqual(config.target_bytes, 7 * GIB)
        self.assertEqual(config.unknown_asset_bytes, GIB)
        self.assertEqual(config.metadata_refresh_s, DAY)
        self.assertTrue(all(source == "default" for source in sources.values()))
        self.assertEqual(config.cache().policy.max_bytes, config.max_bytes)
        self.assertEqual(config.cleanup_policy().lookahead, 3)
        self.assertEqual(config.describe(sources)["values"]["directory"], str(config.directory))

    def test_explicit_overrides_drive_dependent_defaults(self):
        config, sources = self.load({"version": 1, "directory": "cache/deep",
                                     "max_bytes": 1000, "near_distance_m": 42})
        self.assertEqual(config.directory, self.root / "cache" / "deep")
        self.assertEqual((config.max_bytes, config.high_water_bytes, config.target_bytes),
                         (1000, 900, 700))
        self.assertEqual(config.unknown_asset_bytes, 1000)
        self.assertEqual(config.near_distance_m, 42)
        self.assertEqual(sources["max_bytes"], f"file:{self.config}")
        self.assertEqual(sources["high_water_bytes"], "default")

    def test_rejects_invalid_or_contradictory_settings(self):
        cases = [
            {"version": True},
            {"version": 1, "max_bytes": True},
            {"version": 1, "unknown": 1},
            {"version": 1, "queue_lookahead": 0},
            {"version": 1, "metadata_refresh_s": 0},
            {"version": 1, "partial_expiry_s": -1},
            {"version": 1, "max_bytes": 100, "target_bytes": 90, "high_water_bytes": 90},
            {"version": 1, "max_bytes": 100, "high_water_bytes": 101},
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(value)

    def test_invalid_file_is_reported(self):
        self.config.write_text("{")
        with self.assertRaisesRegex(ValueError, "Cannot read raster cache configuration"):
            load_cache_config(self.config)


if __name__ == "__main__":
    unittest.main()
