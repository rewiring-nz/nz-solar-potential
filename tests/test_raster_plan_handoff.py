"""Job-specific raster snapshots preserve approved shared assets."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from src.raster_acquisition_plan import RasterRequest, RasterSource, plan_batch
from src.raster_plan_handoff import (
    RasterPlanError, optional_approved_raster_plan, plan_digest, plan_for_regions,
)


class RasterPlanHandoffTests(unittest.TestCase):
    def setUp(self):
        source = RasterSource("https://example.test/collection.json", "data", "survey")
        boxes = ((0, 0, 1, 1),)
        requests = [
            RasterRequest("alpha", "dsm", boxes, source, 101),
            RasterRequest("beta", "dsm", boxes, source, 101),
            RasterRequest("beta", "imagery", boxes, None, 202, "unmapped"),
        ]
        documents = {
            source.collection_url: {"id": "survey", "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "tile", "bbox": [0, 0, 1, 1], "properties": {},
                "assets": {"data": {"href": "tile.tif", "type": "image/tiff", "file:size": 200}}},
        }
        self.plan = {
            "regions": [
                {"region": "alpha", "inputs": [{"role": "dsm", "state": "mapped",
                                                  "layer_id": 101}]},
                {"region": "beta", "inputs": [{"role": "dsm", "state": "mapped", "layer_id": 101},
                                               {"role": "imagery", "state": "exports_fallback",
                                                "layer_id": 202}]},
                {"region": "synthetic", "inputs": [{"role": "dsm", "state": "synthetic"}]},
            ],
            "batch": plan_batch(requests, get_json=documents.__getitem__),
        }

    def test_subsets_shared_assets_and_totals_without_mutating_snapshot(self):
        original = copy.deepcopy(self.plan)
        alpha = plan_for_regions(self.plan, ["alpha"])
        beta = plan_for_regions(self.plan, ["beta"])
        self.assertEqual(self.plan, original)
        for region, result in (("alpha", alpha), ("beta", beta)):
            self.assertEqual([item["region"] for item in result["regions"]], [region])
            self.assertEqual(result["batch"]["known_asset_bytes"], 200)
            asset, = result["batch"]["assets"]
            self.assertEqual(asset["regions"], [region])
            self.assertEqual([use["region"] for use in asset["uses"]], [region])
            self.assertEqual(asset["url"], "https://example.test/tile.tif")
            self.assertTrue(all(entry["region"] == region
                                for entry in result["batch"]["footprint_bbox_coverage"]))
        self.assertEqual(alpha["batch"]["unknown_export_size_count"], 0)
        self.assertEqual(beta["batch"]["unknown_export_size_count"], 1)
        alpha["batch"]["assets"][0]["uses"].clear()
        self.assertEqual(len(self.plan["batch"]["assets"][0]["uses"]), 2)

    def test_synthetic_region_has_no_batch(self):
        self.assertIsNone(plan_for_regions(self.plan, ["synthetic"])["batch"])

    def test_partial_approval_environment_is_rejected(self):
        for environment in ({"SOLAR_RASTER_PLAN_JOB": "job.json"},
                            {"SOLAR_RASTER_PLAN_SHA256": "0" * 64}):
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(RasterPlanError, "path and digest"):
                    optional_approved_raster_plan(environment, ["synthetic"])
        self.assertIsNone(optional_approved_raster_plan(
            {"SOLAR_RASTER_PLAN_JOB": "", "SOLAR_RASTER_PLAN_SHA256": ""}, ["synthetic"]))

    def test_mapped_snapshot_requires_explicit_caller_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.json"
            path.write_text(json.dumps({"raster_plan": self.plan}))
            env = {"SOLAR_RASTER_PLAN_JOB": str(path),
                   "SOLAR_RASTER_PLAN_SHA256": plan_digest(self.plan)}
            with self.assertRaisesRegex(RasterPlanError, "Mapped raster plans"):
                optional_approved_raster_plan(env, ["alpha"])
            self.assertEqual(plan_digest(optional_approved_raster_plan(
                env, ["alpha"], allow_mapped=True)), plan_digest(self.plan))
            env["SOLAR_RASTER_PLAN_SHA256"] = "0" * 64
            with self.assertRaisesRegex(RasterPlanError, "changed"):
                optional_approved_raster_plan(env, ["alpha"], allow_mapped=True)

    def test_missing_or_inconsistent_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            plan_for_regions(self.plan, ["missing"])
        broken = copy.deepcopy(self.plan)
        broken["batch"]["inputs"] = [entry for entry in broken["batch"]["inputs"]
                                      if entry["region"] != "alpha"]
        with self.assertRaisesRegex(ValueError, "does not match"):
            plan_for_regions(broken, ["alpha"])
        broken["batch"] = None
        with self.assertRaisesRegex(ValueError, "missing selected inputs"):
            plan_for_regions(broken, ["alpha"])
        broken = copy.deepcopy(self.plan)
        broken["batch"]["export_fallbacks"].clear()
        with self.assertRaisesRegex(ValueError, "missing selected fallbacks"):
            plan_for_regions(broken, ["beta"])
        broken = copy.deepcopy(self.plan)
        broken["regions"][0]["inputs"][0]["layer_id"] = 999
        with self.assertRaisesRegex(ValueError, "does not match selected source"):
            plan_for_regions(broken, ["alpha"])
        broken = copy.deepcopy(self.plan)
        broken["regions"][0]["inputs"][0]["state"] = "typo"
        with self.assertRaisesRegex(ValueError, "Invalid raster source selection"):
            plan_for_regions(broken, ["alpha"])

    def test_subset_recomputes_cache_inspection_totals(self):
        plan = copy.deepcopy(self.plan)
        plan["batch"]["assets"][0]["cache_state"] = "missing"
        plan["batch"]["cache"] = {"usage_bytes": 1000, "cached_count": 0, "missing_count": 5,
                                  "known_missing_bytes": 2000, "unknown_missing_count": 4}
        selected = plan_for_regions(plan, ["alpha"])["batch"]["cache"]
        self.assertEqual(selected, {"usage_bytes": 1000, "cached_count": 0, "missing_count": 1,
                                    "known_missing_bytes": 200, "unknown_missing_count": 0})
        self.assertEqual(plan["batch"]["cache"]["missing_count"], 5)


if __name__ == "__main__":
    unittest.main()
