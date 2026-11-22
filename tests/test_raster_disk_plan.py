"""Offline tests for the experimental filesystem forecast."""

import tempfile
import unittest
from pathlib import Path

from src.raster_disk_plan import DiskAllocation, DiskRequirements, forecast_disk


class RasterDiskPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.cache = root / "cache"
        self.work = root / "work"
        self.output = root / "output"
        for path in (self.cache, self.work, self.output):
            path.mkdir()
        self.requirements = DiskRequirements(self.cache, self.work, self.output,
                                             100, 200, 300, 50)
        self.asset = {
            "collection_url": "https://example.test/collection.json",
            "collection_id": "survey", "item_id": "tile", "asset_key": "data",
            "size_bytes": 400, "cache_state": "missing",
        }
        self.plan = {"assets": [self.asset], "cache": {"unknown_asset_estimate_bytes": 500,
                                                       "usage_bytes": 0, "budget_bytes": 5000,
                                                       "directory": str(self.cache)},
                     "export_fallbacks": []}

    def test_same_filesystem_sums_all_allocations_and_one_reserve(self):
        result = forecast_disk(self.plan, self.requirements, probe=lambda path: (1, 1000))
        filesystem = result["filesystems"][0]
        self.assertEqual(filesystem["allocation_bytes"], 1000)
        self.assertEqual(filesystem["shortfall_bytes"], 50)
        self.assertFalse(result["sufficient_at_check"])
        self.assertFalse(result["reservation"])

    def test_separate_filesystems_have_independent_reserves(self):
        devices = {self.cache: (1, 600), self.work: (2, 400), self.output: (3, 500)}
        result = forecast_disk(self.plan, self.requirements, probe=devices.__getitem__)
        self.assertTrue(result["sufficient_at_check"])
        self.assertEqual([x["allocation_bytes"] for x in result["filesystems"]], [400, 300, 300])
        self.assertEqual([x["reserve_bytes"] for x in result["filesystems"]], [50, 50, 50])

    def test_additional_allocations_use_their_own_filesystems(self):
        temporary = self.work / "temporary"
        pointcloud = self.work / "pointcloud"
        temporary.mkdir()
        pointcloud.mkdir()
        requirements = DiskRequirements(
            self.cache, self.work, self.output, 100, 200, 300, 50,
            extra_allocations=(DiskAllocation("temporary", temporary, 600),
                               DiskAllocation("pointcloud", pointcloud, 700)))
        devices = {self.cache: (1, 2000), self.work: (2, 2000),
                   self.output: (3, 2000), temporary: (4, 600), pointcloud: (2, 2000)}
        result = forecast_disk(self.plan, requirements, probe=devices.__getitem__)
        by_device = {item["device"]: item for item in result["filesystems"]}
        self.assertEqual(by_device[2]["allocation_bytes"], 1000)
        self.assertEqual(by_device[2]["allocations"]["pointcloud"], 700)
        self.assertEqual(by_device[4]["shortfall_bytes"], 50)
        self.assertFalse(result["sufficient_at_check"])

    def test_invalid_or_duplicate_additional_allocations_are_rejected(self):
        for allocations in (
            (DiskAllocation("outputs", self.work, 1),),
            (DiskAllocation("other", self.work, 1), DiskAllocation("other", self.output, 1)),
            (DiskAllocation("", self.work, 1),),
            (DiskAllocation("other", self.work, -1),),
            (DiskAllocation("other", self.work, True),),
        ):
            requirements = DiskRequirements(
                self.cache, self.work, self.output, 0, 0, 0, 0,
                extra_allocations=allocations)
            with self.subTest(allocations=allocations), self.assertRaises(ValueError):
                forecast_disk(self.plan, requirements, probe=lambda _: (1, 10000))

    def test_cached_unknown_and_superseded_assets(self):
        self.plan["assets"] = [
            {**self.asset, "cache_state": "cached"},
            {**self.asset, "item_id": "unknown", "size_bytes": None},
            {**self.asset, "item_id": "replacement", "cache_state": "superseded"},
        ]
        result = forecast_disk(self.plan, self.requirements, probe=lambda path: (1, 2000))
        self.assertEqual(result["unknown_asset_count"], 1)
        self.assertEqual(result["filesystems"][0]["allocations"]["cache_transfer"], 900)

    def test_fallback_requires_explicit_estimate(self):
        self.plan["export_fallbacks"] = [{"region": "alpha"}, {"region": "beta"}]
        with self.assertRaisesRegex(ValueError, "exports fallback"):
            forecast_disk(self.plan, self.requirements)
        req = DiskRequirements(self.cache, self.work, self.output, 100, 200, 300, 50, 700)
        result = forecast_disk(self.plan, req, probe=lambda path: (1, 5000))
        self.assertEqual(result["unknown_export_count"], 2)
        self.assertEqual(result["filesystems"][0]["allocations"]["exports"], 1400)

    def test_rejects_recovery_and_duplicate_assets(self):
        self.plan["assets"] = [{**self.asset, "cache_state": "recovery_required"}]
        with self.assertRaisesRegex(ValueError, "inspection or recovery"):
            forecast_disk(self.plan, self.requirements)
        self.plan["assets"] = [self.asset, self.asset]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            forecast_disk(self.plan, self.requirements)

    def test_uses_smaller_free_value_for_same_device(self):
        free = {self.cache: 1200, self.work: 1050, self.output: 1100}
        result = forecast_disk(self.plan, self.requirements, probe=lambda path: (1, free[path]))
        self.assertEqual(result["filesystems"][0]["free_bytes"], 1050)
        self.assertTrue(result["sufficient_at_check"])

    def test_existing_cache_counts_towards_budget_even_for_replacement(self):
        self.plan["assets"][0]["cache_state"] = "superseded"
        self.plan["cache"].update(usage_bytes=700, budget_bytes=1000)
        result = forecast_disk(self.plan, self.requirements, probe=lambda path: (1, 10000))
        self.assertEqual(result["cache_budget_shortfall_bytes"], 100)
        self.assertFalse(result["sufficient_at_check"])

    def test_actual_probe_rejects_missing_directory(self):
        self.cache.rmdir()
        with self.assertRaisesRegex(ValueError, "does not exist"):
            forecast_disk(self.plan, self.requirements)

    def test_rejects_inspection_of_a_different_cache(self):
        self.plan["cache"]["directory"] = str(self.work)
        with self.assertRaisesRegex(ValueError, "does not match"):
            forecast_disk(self.plan, self.requirements)


if __name__ == "__main__":
    unittest.main()
