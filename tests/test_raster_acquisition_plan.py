"""Offline tests for STAC discovery and union-of-footprints planning."""

import unittest
import threading
from unittest.mock import Mock

from src.raster_acquisition_plan import (
    RasterRequest, RasterSource, _read_items, describe_requests, discover_assets, plan_assets,
    plan_batch, region_manifest,
)


COLLECTION = "https://example.test/one/collection.json"
SOURCE = RasterSource(COLLECTION, "data")


def fixture():
    def item(name, bounds, href, size=None):
        asset = {"href": href, "type": "image/tiff; application=geotiff"}
        if size is not None:
            asset["file:size"] = size
        return {
            "id": name, "bbox": bounds,
            "properties": {"proj:code": "EPSG:2193", "gsd": 1},
            "assets": {"thumbnail": {"href": "preview.jpg", "type": "image/jpeg"},
                       "data": asset},
        }

    return {
        COLLECTION: {"id": "survey-one", "links": [
            {"rel": "item", "href": "items/a.json"},
            {"rel": "item", "href": "items/b.json"},
            {"rel": "item", "href": "items/middle.json"},
        ]},
        "https://example.test/one/items/a.json": item(
            "a", [0, 0, 1, 1], "../tiles/a.tif", 100),
        "https://example.test/one/items/b.json": item(
            "b", [9, 0, 10, 1], "https://other.test/b.tif"),
        "https://example.test/one/items/middle.json": item(
            "middle", [4, 0, 5, 1], "../tiles/middle.tif", 1000),
    }


class RasterAcquisitionPlanTests(unittest.TestCase):
    def test_public_metadata_reads_are_bounded_parallel_and_ordered(self):
        from unittest.mock import patch
        import src.raster_acquisition_plan as planner

        barrier = threading.Barrier(2, timeout=5)
        def read(url):
            barrier.wait()
            return {"url": url}

        with patch.object(planner, "_read_json", read):
            self.assertEqual(_read_items(["first", "second"], read),
                             [{"url": "first"}, {"url": "second"}])

    def test_injected_metadata_reader_is_sequential_and_errors_propagate(self):
        calls = []
        def read(url):
            calls.append(url)
            if url == "bad":
                raise OSError("metadata unavailable")
            return {"url": url}

        with self.assertRaisesRegex(OSError, "metadata unavailable"):
            _read_items(["first", "bad", "last"], read)
        self.assertEqual(calls, ["first", "bad"])

    def test_selects_explicit_asset_and_resolves_links_relative_to_item(self):
        documents = fixture()
        get_json = Mock(side_effect=documents.__getitem__)
        assets = discover_assets(SOURCE, get_json=get_json)
        self.assertEqual(assets[0]["url"], "https://example.test/one/tiles/a.tif")
        self.assertEqual(assets[1]["url"], "https://other.test/b.tif")
        self.assertEqual(assets[0]["native_crs"], "EPSG:2193")
        self.assertEqual(get_json.call_count, 4)
        self.assertTrue(all(call.args[0].endswith(".json") for call in get_json.call_args_list))

    def test_deduplicates_overlap_without_using_enclosing_bbox(self):
        documents = fixture()
        plan = plan_assets(SOURCE, {
            "one": [(0.1, 0.1, 0.8, 0.8)],
            "overlap": [(0.2, 0.2, 0.9, 0.9)],
            "distant": [(9.1, 0.1, 9.9, 0.9)],
            "uncovered": [(15, 0, 16, 1)],
        }, get_json=documents.__getitem__)
        self.assertEqual([a["item_id"] for a in plan["assets"]], ["a", "b"])
        self.assertEqual(plan["assets"][0]["regions"], ["one", "overlap"])
        self.assertEqual(plan["known_asset_bytes"], 100)
        self.assertEqual(plan["unknown_size_count"], 1)
        self.assertEqual(plan["regions_without_intersecting_assets"], ["uncovered"])
        self.assertEqual(plan["footprints_wgs84"]["one"], [[0.1, 0.1, 0.8, 0.8]])
        self.assertEqual(plan["footprints_without_intersecting_assets"]["uncovered"], [[15, 0, 16, 1]])
        self.assertEqual(plan["assets"][0]["cache_state"], "unknown")
        self.assertIn("valid-data coverage not checked", plan["coverage"])

    def test_partial_region_footprints_are_reported(self):
        documents = fixture()
        plan = plan_assets(SOURCE, {"area": [(0.1, 0.1, 0.8, 0.8), (15, 0, 16, 1)]},
                           get_json=documents.__getitem__)
        self.assertEqual(plan["regions_without_intersecting_assets"], [])
        self.assertEqual(plan["footprints_without_intersecting_assets"]["area"], [[15, 0, 16, 1]])

    def test_does_not_guess_asset_or_accept_non_tiff(self):
        documents = fixture()
        with self.assertRaisesRegex(ValueError, "lacks asset"):
            discover_assets(RasterSource(COLLECTION, "missing"), get_json=documents.__getitem__)
        with self.assertRaisesRegex(ValueError, "not a GeoTIFF"):
            discover_assets(RasterSource(COLLECTION, "thumbnail"), get_json=documents.__getitem__)

    def test_invalid_footprint_fails_before_network(self):
        get_json = Mock()
        with self.assertRaisesRegex(ValueError, "footprint"):
            plan_assets(SOURCE, {"area": [(0, 0, 0, 1)]}, get_json=get_json)
        get_json.assert_not_called()

    def test_duplicate_item_identity_is_rejected(self):
        documents = fixture()
        documents["https://example.test/one/items/b.json"]["id"] = "a"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            discover_assets(SOURCE, get_json=documents.__getitem__)

    def test_collection_and_asset_identity_remain_distinct(self):
        documents = fixture()
        other_url = "https://example.test/two/collection.json"
        other_item = "https://example.test/two/a.json"
        documents[other_url] = {"id": "survey-two", "links": [{"rel": "item", "href": "a.json"}]}
        documents[other_item] = {**documents["https://example.test/one/items/a.json"],
                                 "collection": "survey-two"}
        first = discover_assets(SOURCE, get_json=documents.__getitem__)[0]
        second = discover_assets(RasterSource(other_url, "data"), get_json=documents.__getitem__)[0]
        self.assertEqual(first["item_id"], second["item_id"])
        self.assertNotEqual(first["collection_url"], second["collection_url"])
        self.assertNotEqual(first["collection_id"], second["collection_id"])

    def test_rejects_mismatched_collection_and_untyped_asset(self):
        documents = fixture()
        item = documents["https://example.test/one/items/a.json"]
        item["collection"] = "other-survey"
        with self.assertRaisesRegex(ValueError, "different collection"):
            discover_assets(SOURCE, get_json=documents.__getitem__)
        del item["collection"]
        del item["assets"]["data"]["type"]
        with self.assertRaisesRegex(ValueError, "not a GeoTIFF"):
            discover_assets(SOURCE, get_json=documents.__getitem__)

    def test_batch_deduplicates_sources_and_preserves_area_roles_and_fallbacks(self):
        documents = fixture()
        other_url = "https://example.test/two/collection.json"
        other_item = "https://example.test/two/a.json"
        documents[other_url] = {"id": "survey-two", "links": [{"rel": "item", "href": "a.json"}]}
        documents[other_item] = {**documents["https://example.test/one/items/a.json"],
                                 "collection": "survey-two"}
        first = ((0.1, 0.1, 0.8, 0.8),)
        distant = ((9.1, 0.1, 9.9, 0.9),)
        requests = [
            RasterRequest("alpha", "dsm", first, SOURCE, 123),
            RasterRequest("beta", "dsm", first, SOURCE, 123),
            RasterRequest("alpha", "reference_imagery", first, SOURCE, 456),
            RasterRequest("gamma", "dsm", first, RasterSource(other_url, "data"), 789),
            RasterRequest("delta", "dsm", distant, SOURCE, 123),
            RasterRequest("epsilon", "imagery", first, None, 999, "STAC mapping unverified"),
        ]
        get_json = Mock(side_effect=documents.__getitem__)
        plan = plan_batch(requests, get_json=get_json)
        self.assertEqual(len(plan["assets"]), 3)
        self.assertEqual(plan["known_asset_bytes"], 200)
        self.assertEqual(plan["unknown_size_count"], 1)
        self.assertEqual(plan["unknown_export_size_count"], 1)
        self.assertEqual(plan["assets"][0]["uses"], [
            {"region": "alpha", "role": "dsm", "layer_id": 123},
            {"region": "alpha", "role": "reference_imagery", "layer_id": 456},
            {"region": "beta", "role": "dsm", "layer_id": 123},
        ])
        self.assertEqual(plan["assets"][2]["collection_id"], "survey-two")
        self.assertEqual(plan["export_fallbacks"][0]["reason"], "STAC mapping unverified")
        self.assertIsNone(plan["export_fallbacks"][0]["estimated_bytes"])
        self.assertEqual(get_json.call_count, 6)
        self.assertEqual(describe_requests(requests), {
            "inputs": plan["inputs"], "export_fallbacks": plan["export_fallbacks"]})

    def test_batch_reports_missing_footprints_by_role(self):
        requests = [
            RasterRequest("alpha", "dsm",
                          ((0.1, 0.1, 0.8, 0.8), (15, 0, 16, 1)), SOURCE, 123),
            RasterRequest("alpha", "imagery", ((15, 0, 16, 1),), SOURCE, 456),
        ]
        plan = plan_batch(requests, get_json=fixture().__getitem__)
        self.assertEqual(plan["footprints_without_intersecting_assets"], [
            {"region": "alpha", "role": "dsm", "footprint_wgs84": [15, 0, 16, 1]},
            {"region": "alpha", "role": "imagery", "footprint_wgs84": [15, 0, 16, 1]},
        ])

    def test_batch_distinguishes_partial_and_fully_bounded_footprints(self):
        requests = [
            RasterRequest("alpha", "dsm",
                          ((0, 0, 1, 1), (0, 0, 1.5, 1), (15, 0, 16, 1)),
                          SOURCE, 123),
        ]
        plan = plan_batch(requests, get_json=fixture().__getitem__)
        self.assertEqual([item["bbox_coverage"] for item in plan["footprint_bbox_coverage"]],
                         ["fully_bounded", "partial", "none"])
        self.assertIn("valid-data coverage not checked", plan["coverage"])

    def test_batch_validates_all_requests_before_network(self):
        good = RasterRequest("alpha", "dsm", ((0, 0, 1, 1),), SOURCE, 123)
        get_json = Mock()
        invalid = [
            RasterRequest("beta", "imagery", ((0, 0, 1, 1),), None, 1),
            RasterRequest("beta", "dsm", ((0, 0, 1, 1),), None, None, "no mapping"),
            RasterRequest("alpha", "dsm", ((0, 0, 1, 1),), SOURCE, 123),
            RasterRequest("beta", "unknown", ((0, 0, 1, 1),), SOURCE, 123),
            RasterRequest("beta", "dsm", ((0, 0, 0, 1),), SOURCE, 123),
            RasterRequest("beta", "dsm", ((0, 0, 1, 1),), RasterSource("", "data"), 123),
            RasterRequest("beta", "dsm", ((0, 0, 1, 1),), None, 1, " "),
        ]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                plan_batch([good, request], get_json=get_json)
        get_json.assert_not_called()

    def test_region_manifest_fingerprint_tracks_sources_and_processing_not_cache(self):
        requests = [
            RasterRequest("alpha", "dsm", ((0.1, 0.1, 0.8, 0.8),), SOURCE, 123),
            RasterRequest("beta", "imagery", ((0.1, 0.1, 0.8, 0.8),), SOURCE, 456),
            RasterRequest("alpha", "imagery", ((0.1, 0.1, 0.8, 0.8),),
                          None, 999, "STAC mapping unverified"),
        ]
        plan = plan_batch(requests, get_json=fixture().__getitem__)
        first = region_manifest(plan, "alpha", {"crs": "EPSG:2193"})
        self.assertFalse(first["content_verified"])
        self.assertEqual(len(first["assets"]), 1)
        self.assertEqual(first["assets"][0]["roles"], ["dsm"])
        plan["assets"][0]["cache_state"] = "warm"
        self.assertEqual(first["fingerprint"],
                         region_manifest(plan, "alpha", {"crs": "EPSG:2193"})["fingerprint"])
        self.assertNotEqual(first["fingerprint"],
                            region_manifest(plan, "alpha", {"crs": "EPSG:4326"})["fingerprint"])
        plan["assets"][0]["revision"] = "changed"
        self.assertNotEqual(first["fingerprint"],
                            region_manifest(plan, "alpha", {"crs": "EPSG:2193"})["fingerprint"])
        with self.assertRaisesRegex(ValueError, "No raster inputs"):
            region_manifest(plan, "missing", {})
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            region_manifest(plan, "alpha", {"bad": float("nan")})

    def test_batch_and_manifest_are_independent_of_request_order(self):
        boxes = ((9.1, 0.1, 9.9, 0.9), (0.1, 0.1, 0.8, 0.8))
        a = RasterRequest("alpha", "dsm", boxes, SOURCE, 123)
        b = RasterRequest("beta", "imagery", (boxes[0],), SOURCE, 456)
        first = plan_batch([a, b], get_json=fixture().__getitem__)
        second = plan_batch([b, RasterRequest("alpha", "dsm", boxes[::-1], SOURCE, 123)],
                            get_json=fixture().__getitem__)
        self.assertEqual(first, second)
        self.assertEqual(region_manifest(first, "alpha", {}),
                         region_manifest(second, "alpha", {}))


if __name__ == "__main__":
    unittest.main()
