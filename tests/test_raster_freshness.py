"""Offline tests for approved-plan metadata refresh."""

import copy
import unittest
from unittest.mock import Mock, patch

from src.raster_acquisition_plan import RasterRequest, RasterSource, plan_batch
from src.raster_freshness import verify_due_metadata
from src.raster_plan_handoff import plan_for_regions


COLLECTION = "https://example.test/collection.json"


def documents():
    return {
        COLLECTION: {"id": "survey", "links": [{"rel": "item", "href": "item.json"}]},
        "https://example.test/item.json": {
            "id": "tile", "bbox": [0, 0, 1, 1], "properties": {"updated": "one"},
            "assets": {"data": {"href": "tile.tif", "type": "image/tiff", "file:size": 100}},
        },
    }


class RasterFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.docs = documents()
        request = RasterRequest("area", "dsm", ((0.1, 0.1, 0.9, 0.9),),
                                RasterSource(COLLECTION, "data", "survey"), 101,
                                mapping_evidence="verified")
        self.plan = plan_batch([request], get_json=self.docs.__getitem__)
        self.plan["metadata_checked_at"] = 100
        self.plan["assets"][0]["cache_state"] = "cached"
        self.plan["assets"][0]["cache_key"] = "ignored"

    def test_recent_plan_does_not_contact_stac(self):
        lookup = Mock(side_effect=AssertionError("unexpected network request"))
        self.assertFalse(verify_due_metadata(self.plan, 10, get_json=lookup, now=109))
        lookup.assert_not_called()

    def test_due_and_legacy_plans_recheck_without_changing_approval(self):
        original = copy.deepcopy(self.plan)
        lookup = Mock(side_effect=self.docs.__getitem__)
        self.assertTrue(verify_due_metadata(self.plan, 10, get_json=lookup, now=110))
        self.assertEqual(self.plan, original)
        self.assertEqual(lookup.call_count, 2)
        del self.plan["metadata_checked_at"]
        self.assertTrue(verify_due_metadata(self.plan, 10, get_json=lookup, now=110))

    def test_due_head_size_is_rechecked_and_change_requires_new_estimate(self):
        del self.docs["https://example.test/item.json"]["assets"]["data"]["file:size"]
        self.plan["assets"][0]["size_bytes"] = 1198655802
        self.plan["assets"][0]["size_source"] = "http-head"
        def resolve(items):
            for item in items:
                item["size_bytes"] = 1198655802
                item["size_source"] = "http-head"
        with patch("src.raster_freshness.resolve_asset_sizes", side_effect=resolve) as probe:
            self.assertTrue(verify_due_metadata(self.plan, 10, get_json=self.docs.__getitem__, now=110))
            self.assertEqual(len(probe.call_args.args[0]), 1)
        def changed(items):
            for item in items:
                item["size_bytes"] = 1198655803
        with patch("src.raster_freshness.resolve_asset_sizes", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed; estimate"):
                verify_due_metadata(self.plan, 10, get_json=self.docs.__getitem__, now=110)

    def test_changed_metadata_or_new_intersecting_item_requires_approval(self):
        self.docs["https://example.test/item.json"]["properties"]["updated"] = "two"
        with self.assertRaisesRegex(ValueError, "changed; estimate and approve"):
            verify_due_metadata(self.plan, 10, get_json=self.docs.__getitem__, now=110)
        self.docs = documents()
        self.docs[COLLECTION]["links"].append({"rel": "item", "href": "other.json"})
        self.docs["https://example.test/other.json"] = {
            **copy.deepcopy(self.docs["https://example.test/item.json"]), "id": "other"}
        with self.assertRaisesRegex(ValueError, "changed; estimate and approve"):
            verify_due_metadata(self.plan, 10, get_json=self.docs.__getitem__, now=110)

    def test_failure_is_visible_and_invalid_time_is_rejected(self):
        lookup = Mock(side_effect=OSError("offline"))
        with self.assertRaisesRegex(OSError, "offline"):
            verify_due_metadata(self.plan, 10, get_json=lookup, now=110)
        for value in (float("nan"), -1, 111, True):
            self.plan["metadata_checked_at"] = value
            with self.assertRaisesRegex(ValueError, "check time"):
                verify_due_metadata(self.plan, 10, get_json=lookup, now=110)
        self.assertFalse(verify_due_metadata({"inputs": []}, 10, get_json=lookup, now=110))

    def test_sliced_batch_rechecks_only_its_selected_uses(self):
        requests = [
            RasterRequest(region, "dsm", ((0.1, 0.1, 0.9, 0.9),),
                          RasterSource(COLLECTION, "data", "survey"), 101,
                          mapping_evidence="verified")
            for region in ("one", "two")
        ]
        batch = plan_batch(requests, get_json=self.docs.__getitem__)
        plan = {"regions": [
            {"region": region, "inputs": [{"role": "dsm", "state": "mapped",
                                            "layer_id": 101, "mapping_evidence": "verified"}]}
            for region in ("one", "two")], "batch": batch}
        selected = plan_for_regions(plan, ["one"])["batch"]
        self.assertTrue(verify_due_metadata(selected, 10, get_json=self.docs.__getitem__, now=110))


if __name__ == "__main__":
    unittest.main()
