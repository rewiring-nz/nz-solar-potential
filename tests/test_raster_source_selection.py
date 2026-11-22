"""Offline survey-to-planner selection tests."""

import copy
import unittest
import tempfile
import json
from pathlib import Path

from src.raster_acquisition_plan import plan_batch
from src.raster_region_plan import plan_regions
from src.raster_source_selection import (
    LayerMapping, load_layer_mappings, mappings_from_snapshot, survey_requests,
)
from src.surveys import survey_for
from tests.test_surveys import CHRISTCHURCH_CENTRAL, DSM_ID, IMAGERY_ID, _client


class RasterSourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.footprints = [[168.0, -45.0, 168.1, -44.9]]
        self.survey = {"name": "selected capture", "elevation": "lidar",
                       "dsm_layer": 101, "imagery_layer": 202,
                       "reference_imagery_layer": 303}
        self.mapping = LayerMapping(
            101, "dsm", "https://example.test/collection.json",
            "capture-id", "data", "Reviewed LDS layer and collection metadata")
        self.documents = {
            "https://example.test/collection.json": {
                "id": "capture-id", "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "tile", "collection": "capture-id", "bbox": [167, -46, 169, -44],
                "assets": {"data": {"href": "tile.tif", "type": "image/tiff"}}},
        }

    def test_selected_layer_mapping_and_unmapped_imagery_fallbacks(self):
        result = survey_requests("area", self.footprints, self.survey, mappings=[self.mapping])
        self.assertEqual([x["state"] for x in result["inputs"]],
                         ["mapped", "exports_fallback", "exports_fallback"])
        self.assertEqual(result["inputs"][0]["mapping_evidence"], self.mapping.evidence)
        plan = plan_batch(result["requests"], get_json=self.documents.__getitem__)
        self.assertEqual(plan["inputs"][0]["survey_name"], "selected capture")
        self.assertEqual(plan["inputs"][0]["mapping_evidence"], self.mapping.evidence)
        self.assertEqual(plan["assets"][0]["uses"][0]["layer_id"], 101)
        self.assertEqual({x["layer_id"] for x in plan["export_fallbacks"]}, {202, 303})
        self.assertEqual(plan["unknown_export_size_count"], 2)

    def test_snapshot_mappings_reconstruct_only_approved_sources(self):
        snapshot = plan_regions(
            ["area", "other"], mappings=[self.mapping],
            bbox_for=lambda _: self.footprints[0],
            footprints_for=lambda *_: self.footprints,
            survey_for=lambda *_: self.survey, get_json=self.documents.__getitem__)
        self.assertEqual(mappings_from_snapshot(snapshot, ["area", "other"]), [self.mapping])
        selected = survey_requests("area", self.footprints, self.survey,
                                   mappings=mappings_from_snapshot(snapshot, ["area"]))
        self.assertEqual(selected["inputs"], snapshot["regions"][0]["inputs"])
        broken = copy.deepcopy(snapshot)
        next(item for item in broken["batch"]["inputs"]
             if item["region"] == "other" and item["role"] == "dsm")["source"]["asset_key"] = "other"
        with self.assertRaisesRegex(ValueError, "Conflicting snapshot mappings"):
            mappings_from_snapshot(broken, ["area", "other"])
        broken = copy.deepcopy(snapshot)
        for item in broken["batch"]["inputs"]:
            if item["role"] == "dsm":
                item["source"]["expected_collection_id"] = None
        with self.assertRaisesRegex(ValueError, "Mapping requires"):
            mappings_from_snapshot(broken, ["area"])

    def test_snapshot_without_mapped_inputs_has_no_mappings(self):
        synthetic = {**self.survey, "elevation": "synthetic", "dsm_layer": None}
        snapshot = plan_regions(
            ["area"], mappings=[], include_imagery=False,
            bbox_for=lambda _: self.footprints[0],
            footprints_for=lambda *_: self.footprints,
            survey_for=lambda *_: synthetic)
        self.assertEqual(mappings_from_snapshot(snapshot, ["area"]), [])

    def test_mapping_is_keyed_by_role_and_layer_not_similar_title(self):
        wrong_layer = LayerMapping(999, "dsm", self.mapping.collection_url,
                                   "capture-id", "data", "reviewed different layer")
        wrong_role = LayerMapping(101, "imagery", self.mapping.collection_url,
                                  "capture-id", "data", "reviewed different product")
        result = survey_requests("area", self.footprints, self.survey,
                                 mappings=[wrong_layer, wrong_role])
        self.assertEqual(result["inputs"][0]["state"], "exports_fallback")
        self.assertIsNone(result["requests"][0].source)

    def test_mapped_collection_identity_is_checked(self):
        self.documents[self.mapping.collection_url]["id"] = "different-capture"
        result = survey_requests("area", self.footprints, self.survey, mappings=[self.mapping])
        with self.assertRaisesRegex(ValueError, "does not match configured mapping"):
            plan_batch(result["requests"], get_json=self.documents.__getitem__)

    def test_synthetic_and_imagery_selection_match_existing_fetch_behavior(self):
        survey = {**self.survey, "elevation": "synthetic", "dsm_layer": None,
                  "reference_imagery_layer": 202}
        result = survey_requests("area", self.footprints, survey, mappings=[])
        self.assertEqual([x["state"] for x in result["inputs"]],
                         ["synthetic", "exports_fallback", "same_as_imagery"])
        self.assertEqual([x.role for x in result["requests"]], ["imagery"])
        excluded = survey_requests("area", self.footprints, survey, mappings=[],
                                   include_imagery=False)
        self.assertEqual([x["state"] for x in excluded["inputs"]],
                         ["synthetic", "excluded", "excluded"])
        self.assertEqual(excluded["requests"], [])

    def test_missing_optional_imagery_and_invalid_lidar_survey(self):
        survey = {**self.survey, "imagery_layer": None, "reference_imagery_layer": None}
        result = survey_requests("area", self.footprints, survey, mappings=[])
        self.assertEqual([x["state"] for x in result["inputs"]],
                         ["exports_fallback", "unavailable", "unavailable"])
        with self.assertRaisesRegex(ValueError, "requires a DSM layer"):
            survey_requests("area", self.footprints, {**survey, "dsm_layer": None}, mappings=[])
        with self.assertRaisesRegex(ValueError, "Synthetic elevation"):
            survey_requests("area", self.footprints,
                            {**survey, "elevation": "synthetic"}, mappings=[])

    def test_ambiguous_or_unsubstantiated_mappings_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            survey_requests("area", self.footprints, self.survey,
                            mappings=[self.mapping, self.mapping])
        for invalid in (
            LayerMapping(101, "dsm", self.mapping.collection_url, "capture-id", "data", ""),
            LayerMapping(101, "dsm", "http://example.test/c", "capture-id", "data", "reviewed"),
            LayerMapping(101, "dem", self.mapping.collection_url, "capture-id", "data", "reviewed"),
        ):
            with self.subTest(mapping=invalid), self.assertRaises(ValueError):
                survey_requests("area", self.footprints, self.survey, mappings=[invalid])

    def test_existing_survey_resolver_pins_flow_to_explicit_fallbacks(self):
        get_json, _ = _client()
        with tempfile.TemporaryDirectory() as directory:
            survey = survey_for(
                CHRISTCHURCH_CENTRAL, "christchurch-central", data_dir=Path(directory),
                get_json=get_json, now=100,
                overrides={"dsm_layer": DSM_ID, "imagery_layer": IMAGERY_ID,
                           "reference_imagery_layer": IMAGERY_ID})
            selected = survey_requests("christchurch-central", [CHRISTCHURCH_CENTRAL],
                                       survey, mappings=[])
        plan = plan_batch(selected["requests"])
        self.assertEqual([(x["role"], x["layer_id"]) for x in plan["export_fallbacks"]],
                         [("dsm", DSM_ID), ("imagery", IMAGERY_ID)])
        self.assertEqual(selected["inputs"][2]["state"], "same_as_imagery")
        self.assertEqual(plan["assets"], [])

    def test_conflicting_collection_assertions_fail_before_network(self):
        other = LayerMapping(202, "imagery", self.mapping.collection_url,
                             "different-id", "data", "reviewed imagery")
        mapped = survey_requests("area", self.footprints, self.survey,
                                 mappings=[self.mapping, other])
        with self.assertRaisesRegex(ValueError, "Conflicting collection identity"):
            plan_batch(mapped["requests"], get_json=lambda url: self.fail("unexpected network"))

    def test_explicit_mapping_file_is_validated_before_planning(self):
        record = {"layer_id": 101, "role": "dsm",
                  "collection_url": self.mapping.collection_url,
                  "collection_id": "capture-id", "asset_key": "data",
                  "evidence": "Direct source cross-reference"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.json"
            path.write_text(json.dumps({"version": 1, "mappings": [record]}))
            mappings = load_layer_mappings(path)
            self.assertEqual(mappings[0].layer_id, 101)
            self.assertEqual(survey_requests("area", self.footprints, self.survey,
                                             mappings=mappings)["inputs"][0]["state"], "mapped")
            for value in ({"version": True, "mappings": []},
                          {"version": 1, "mappings": [record, record]},
                          {"version": 1, "mappings": [{**record, "extra": 1}]},
                          {"version": 1, "mappings": [{**record, "evidence": ""}]}):
                path.write_text(json.dumps(value))
                with self.subTest(value=value), self.assertRaises(ValueError):
                    load_layer_mappings(path)
            path.write_text("{")
            with self.assertRaisesRegex(ValueError, "Cannot read"):
                load_layer_mappings(path)

    def test_national_dsm_mapping_is_explicit_and_plan_preserves_selection(self):
        path = Path(__file__).resolve().parents[1] / "tools" / "national-dsm-mapping.json"
        mappings = load_layer_mappings(path)
        self.assertEqual(len(mappings), 1)
        mapping = mappings[0]
        self.assertEqual((mapping.layer_id, mapping.role, mapping.asset_key),
                         (122082, "dsm", "visual"))
        documents = {
            mapping.collection_url: {
                "id": mapping.collection_id,
                "links": [{"rel": "item", "href": "./tile.json"}],
                "assets": {"capture_area": {"href": "./capture-area.geojson",
                                            "file:checksum": "1220" + "b" * 64},
                           "capture_dates": {"href": "./capture-dates.geojson",
                                             "file:checksum": "1220" + "c" * 64}},
                "license": "CC-BY-4.0"},
            mapping.collection_url.replace("collection.json", "tile.json"): {
                "id": "tile", "collection": mapping.collection_id,
                "bbox": [167, -46, 169, -44],
                "properties": {"start_datetime": "2021-01-01T00:00:00Z",
                               "end_datetime": "2021-12-31T00:00:00Z"},
                "assets": {"visual": {"href": "./tile.tiff",
                                       "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                                       "file:checksum": "1220" + "a" * 64}}},
            mapping.collection_url.replace("collection.json", "capture-area.geojson"): {
                "type": "Feature", "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [167, -46], [168.05, -46], [168.05, -44], [167, -44], [167, -46]]]}},
            mapping.collection_url.replace("collection.json", "capture-dates.geojson"): {
                "type": "FeatureCollection", "features": [{
                    "type": "Feature",
                    "properties": {"id": "survey-id", "title": "capture",
                                   "source": "s3://example/survey", "flown_from": "2021-01-01",
                                   "flown_to": "2021-01-02"},
                    "geometry": {"type": "Polygon", "coordinates": [[
                        [167, -46], [169, -46], [169, -44], [167, -44], [167, -46]]]}}]},
        }
        national = {**self.survey, "name": "New Zealand LiDAR 1m DSM", "dsm_layer": 122082}
        selected = survey_requests("area", self.footprints, national, mappings=mappings,
                                   include_imagery=False)
        plan = plan_batch(selected["requests"], get_json=documents.__getitem__)
        self.assertEqual(selected["inputs"][0]["state"], "mapped")
        self.assertEqual(plan["inputs"][0]["layer_id"], 122082)
        self.assertEqual(plan["assets"][0]["asset_key"], "visual")
        self.assertEqual(plan["assets"][0]["start_datetime"], "2021-01-01T00:00:00Z")
        self.assertEqual(plan["assets"][0]["checksum"], "1220" + "a" * 64)
        self.assertEqual(plan["assets"][0]["license"], "CC-BY-4.0")
        self.assertEqual(plan["capture_coverage"][0]["state"], "intersects")
        self.assertEqual(plan["capture_coverage"][0]["checksum"], "1220" + "b" * 64)
        self.assertEqual(plan["capture_surveys"][0]["surveys"][0]["id"], "survey-id")
        from src.raster_freshness import verify_due_metadata
        self.assertTrue(verify_due_metadata(plan, 1, now=10, get_json=documents.__getitem__))
        capture = documents[mapping.collection_url.replace("collection.json", "capture-area.geojson")]
        capture["geometry"]["coordinates"] = [[
            [167, -46], [169, -46], [169, -44], [167, -44], [167, -46]]]
        with self.assertRaisesRegex(ValueError, "capture coverage changed"):
            verify_due_metadata(plan, 1, now=10, get_json=documents.__getitem__)
        capture["geometry"]["coordinates"] = [[
            [167, -46], [168.05, -46], [168.05, -44], [167, -44], [167, -46]]]
        dates = documents[mapping.collection_url.replace("collection.json", "capture-dates.geojson")]
        dates["features"][0]["properties"]["flown_to"] = "2021-02-01"
        with self.assertRaisesRegex(ValueError, "contributing surveys changed"):
            verify_due_metadata(plan, 1, now=10, get_json=documents.__getitem__)
        regional = survey_requests("area", self.footprints, self.survey, mappings=mappings,
                                   include_imagery=False)
        self.assertEqual(regional["inputs"][0]["state"], "exports_fallback")
        self.assertEqual(regional["inputs"][0]["layer_id"], 101)

    def test_explicit_raster_override_selects_national_without_changing_imagery(self):
        mappings = load_layer_mappings(
            Path(__file__).resolve().parents[1] / "tools" / "national-dsm-mapping.json")
        survey = {**self.survey, "raster_dsm_layer": 122082}
        selected = survey_requests("area", self.footprints, survey, mappings=mappings)
        self.assertEqual([(item["role"], item["layer_id"]) for item in selected["inputs"]],
                         [("dsm", 122082), ("imagery", 202), ("reference_imagery", 303)])
        self.assertEqual(selected["inputs"][0]["state"], "mapped")
        self.assertIn("reference survey selected capture", selected["requests"][0].survey_name)
        with self.assertRaisesRegex(ValueError, "Synthetic elevation"):
            survey_requests("area", self.footprints,
                            {**survey, "elevation": "synthetic", "dsm_layer": None},
                            mappings=mappings)


if __name__ == "__main__":
    unittest.main()
