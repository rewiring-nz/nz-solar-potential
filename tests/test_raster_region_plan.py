"""The experimental planner resolves the same regional selection inputs."""

import unittest
from unittest.mock import Mock, patch

from src.raster_region_plan import plan_regions
from src.raster_source_selection import LayerMapping


class RasterRegionPlanTests(unittest.TestCase):
    def setUp(self):
        self.bbox = [168.0, -45.0, 168.1, -44.9]
        self.parts = [[168.0, -45.0, 168.02, -44.98],
                      [168.08, -44.92, 168.1, -44.9]]
        self.survey = {"name": "survey", "elevation": "lidar", "dsm_layer": 101,
                       "imagery_layer": 202, "reference_imagery_layer": 202}
        self.mapping = LayerMapping(101, "dsm", "https://example.test/collection.json",
                                    "capture", "data", "reviewed mapping")
        self.documents = {
            "https://example.test/collection.json": {
                "id": "capture", "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "tile", "collection": "capture", "bbox": [167, -46, 169, -44],
                "assets": {"data": {"href": "tile.tif", "type": "image/tiff"}}},
        }

    def test_batch_deduplicates_assets_and_resolves_each_survey_once(self):
        bbox_for = Mock(return_value=self.bbox)
        footprints_for = Mock(return_value=self.parts)
        survey_for = Mock(return_value=self.survey)
        result = plan_regions(["area1", "area2"], mappings=[self.mapping],
                              bbox_for=bbox_for, footprints_for=footprints_for,
                              survey_for=survey_for, get_json=self.documents.__getitem__)
        self.assertEqual(survey_for.call_count, 2)
        self.assertEqual(len(result["batch"]["assets"]), 1)
        self.assertEqual({use["region"] for use in result["batch"]["assets"][0]["uses"]},
                         {"area1", "area2"})
        self.assertEqual(result["batch"]["inputs"][0]["footprints_wgs84"], self.parts)
        self.assertEqual(result["regions"][0]["footprints_wgs84"], self.parts)
        self.assertEqual(len(result["batch"]["export_fallbacks"]), 2)
        self.assertEqual(result["regions"][0]["inputs"][2]["state"], "same_as_imagery")
        bbox_for.assert_any_call("area1")
        footprints_for.assert_any_call("area1", self.bbox)
        survey_for.assert_any_call(self.bbox, "area1")

    def test_uses_live_resolvers_by_default(self):
        with patch("src.region_build.area_bbox_wgs84", return_value=self.bbox), \
             patch("src.fetch_regions.acquisition_footprints", return_value=self.parts), \
             patch("src.surveys.survey_for", return_value=self.survey):
            result = plan_regions(["area"], mappings=[], include_imagery=False)
        self.assertEqual(result["batch"]["export_fallbacks"][0]["layer_id"], 101)
        self.assertEqual(result["batch"]["export_fallbacks"][0]["footprints_wgs84"], self.parts)

    def test_planner_snapshot_can_be_sliced_and_revalidated_by_fetcher(self):
        from src.fetch_regions import validate_approved_plan
        from src.raster_plan_handoff import plan_for_regions
        from src.raster_source_selection import survey_requests

        result = plan_regions(["area", "other"], mappings=[],
                              bbox_for=lambda _: self.bbox,
                              footprints_for=lambda name, bbox: self.parts,
                              survey_for=lambda bbox, name: self.survey)
        snapshot = plan_for_regions(result, ["area"])
        current = survey_requests("area", self.parts, self.survey, mappings=[])
        self.assertIs(validate_approved_plan(snapshot, "area", self.parts, current),
                      snapshot["batch"])

    def test_synthetic_without_imagery_has_no_raster_plan(self):
        result = plan_regions(["area"], mappings=[], include_imagery=False,
                              bbox_for=lambda _: self.bbox,
                              footprints_for=lambda name, bbox: self.parts,
                              survey_for=lambda bbox, name: {
                                  "name": "synthetic", "elevation": "synthetic"})
        self.assertIsNone(result["batch"])
        self.assertEqual(result["regions"][0]["inputs"][0]["state"], "synthetic")

    def test_rejects_legacy_s3_without_reinterpreting_selection(self):
        for field in ("dsm_s3", "imagery_s3", "reference_imagery_s3"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "legacy S3"):
                plan_regions(["area"], mappings=[self.mapping],
                             bbox_for=lambda _: self.bbox,
                             footprints_for=lambda name, bbox: self.parts,
                             survey_for=lambda bbox, name: {**self.survey, field: "selected"},
                             get_json=lambda url: self.fail("unexpected network"))
        result = plan_regions(["area"], mappings=[], include_imagery=False,
                              bbox_for=lambda _: self.bbox,
                              footprints_for=lambda name, bbox: self.parts,
                              survey_for=lambda bbox, name: {**self.survey, "imagery_s3": "ignored"})
        self.assertEqual(len(result["batch"]["export_fallbacks"]), 1)

    def test_invalid_or_duplicate_regions_fail_before_resolution(self):
        resolve = Mock()
        for regions in ([], ["../escape"], ["area", "area"]):
            with self.subTest(regions=regions), self.assertRaises(ValueError):
                plan_regions(regions, mappings=[], bbox_for=resolve)
        resolve.assert_not_called()

    def test_invalid_resolved_bbox_fails_before_survey_lookup(self):
        survey_for = Mock()
        with self.assertRaisesRegex(ValueError, "bounding box"):
            plan_regions(["area"], mappings=[], bbox_for=lambda _: [0, 0, 0, 1],
                         footprints_for=lambda name, bbox: self.parts, survey_for=survey_for)
        survey_for.assert_not_called()


if __name__ == "__main__":
    unittest.main()
