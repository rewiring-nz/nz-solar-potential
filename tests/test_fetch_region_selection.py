"""A fetch invocation uses one survey and footprint selection across passes."""

import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import fetch_pointcloud_regions, fetch_regions


class FetchRegionSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.area_dir = self.root / "regions" / "area"
        self.bbox = [168.0, -45.0, 168.1, -44.9]
        self.footprints = [[168.0, -45.0, 168.02, -44.98],
                           [168.08, -44.92, 168.1, -44.9]]
        self.survey = {
            "name": "first", "elevation": "lidar", "dsm_layer": 101,
            "imagery_layer": 202, "reference_imagery_layer": 303,
            "pointcloud_bulk_url": "https://example.test/first",
            "pointcloud_tile_year": "2021", "lidar_tile_index_layer": 404,
        }

    def run_fetch(self, survey_side_effect, *, imagery=True, plan_path="", wide_effect=None,
                  digest=None):
        from src.raster_plan_handoff import plan_digest

        if digest is None:
            digest = (plan_digest(json.loads(Path(plan_path).read_text())["raster_plan"])
                      if plan_path else "")
        patches = [
            patch.object(sys, "argv", ["fetch_regions.py", "area"]),
            patch.dict(os.environ, {"SOLAR_INCLUDE_IMAGERY": "1" if imagery else "0",
                                    "SOLAR_ACQUISITION_TIMINGS": str(self.root / "timings.json"),
                                    "SOLAR_RASTER_PLAN_JOB": str(plan_path),
                                    "SOLAR_RASTER_PLAN_SHA256": digest}),
            patch.object(fetch_regions, "REGIONS_DIR", self.root / "regions"),
            patch.object(fetch_regions, "DATA_DIR", self.root),
            patch.object(fetch_regions, "load_dotenv"),
            patch("src.project_secrets.linz_api_key", return_value="fixture"),
            patch.object(fetch_regions, "ensure_dem_wide", side_effect=wide_effect),
            patch.object(fetch_regions, "area_bbox_wgs84", return_value=self.bbox),
            patch.object(fetch_regions, "acquisition_footprints", return_value=self.footprints),
            patch.object(fetch_regions, "fetch_building_outlines", return_value={"features": []}),
            patch.object(fetch_regions, "survey_for", side_effect=survey_side_effect),
            patch.object(fetch_regions, "fetch_raster_footprints"),
            patch.object(fetch_pointcloud_regions, "POINTCLOUD_DIR", self.root / "pointcloud"),
            patch.object(fetch_pointcloud_regions, "load_dotenv"),
            patch.object(fetch_pointcloud_regions, "area_bbox_wgs84",
                  side_effect=AssertionError("point cloud resolved a different bbox")),
            patch.object(fetch_pointcloud_regions, "area_footprints_wgs84",
                  side_effect=AssertionError("point cloud resolved different footprints")),
            patch.object(fetch_pointcloud_regions, "survey_for",
                  side_effect=AssertionError("point cloud resolved a different survey")),
            patch.object(fetch_pointcloud_regions, "tiles_for_bbox_wgs84",
                  return_value=["CC11_1000_0712"]),
            patch.object(fetch_pointcloud_regions, "download_tile", return_value="fixture"),
            patch("src.region_build.area_paths", return_value={"dir": self.area_dir}),
            patch("src.synthesize_dsm.synthesize"),
            patch.object(fetch_regions, "planned_export", wraps=fetch_regions.planned_export),
            patch.object(fetch_regions, "fetch_s3_footprints"),
        ]
        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks = [stack.enter_context(item) for item in patches]
            fetch_regions.main()
            return mocks

    def test_survey_and_footprints_shared_with_pointcloud(self):
        mocks = self.run_fetch([self.survey, AssertionError("survey was resolved again")])
        survey, raster, index, download = mocks[10], mocks[11], mocks[17], mocks[18]
        self.assertEqual(survey.call_count, 1)
        self.assertEqual([call.args[2] for call in raster.call_args_list], [101, 202, 303])
        planned = mocks[21]
        self.assertEqual([call.args[2] for call in planned.call_args_list],
                         ["dsm", "imagery", "reference_imagery"])
        plans = [call.args[0] for call in planned.call_args_list]
        self.assertTrue(all(plan is plans[0] for plan in plans))
        self.assertEqual({entry["layer_id"] for entry in plans[0]["export_fallbacks"]},
                         {101, 202, 303})
        self.assertTrue(all(entry["footprints_wgs84"] == self.footprints
                            for entry in plans[0]["export_fallbacks"]))
        self.assertTrue(all(call.args[0] == self.footprints for call in raster.call_args_list))
        self.assertEqual([call.args[0] for call in index.call_args_list], self.footprints)
        self.assertTrue(all(call.args[2] == 404 for call in index.call_args_list))
        download.assert_called_once_with("CL2_CC11_2021_1000_0712.laz",
                                         "https://example.test/first")

    def test_failed_first_lookup_can_retry_and_the_success_is_shared(self):
        mocks = self.run_fetch([LookupError("catalog unavailable"), self.survey,
                                AssertionError("successful survey resolved again")])
        survey, raster, index = mocks[10], mocks[11], mocks[17]
        self.assertEqual(survey.call_count, 2)
        self.assertEqual([call.args[2] for call in raster.call_args_list], [202, 303])
        self.assertEqual(index.call_count, 2)

    def test_imagery_exclusion_keeps_dsm_and_pointcloud_selection(self):
        mocks = self.run_fetch([self.survey], imagery=False)
        self.assertEqual([call.args[2] for call in mocks[11].call_args_list], [101])
        self.assertEqual(mocks[17].call_count, 2)
        self.assertEqual([call.args[2] for call in mocks[21].call_args_list], ["dsm"])

    def test_explicit_raster_dsm_keeps_reference_survey_and_pointcloud(self):
        survey = {**self.survey, "raster_dsm_layer": 122082}
        mocks = self.run_fetch([survey])
        self.assertEqual([call.args[2] for call in mocks[11].call_args_list], [122082, 202, 303])
        self.assertEqual([call.args[2] for call in mocks[17].call_args_list], [404, 404])
        plan = mocks[21].call_args_list[0].args[0]
        dsm = next(item for item in plan["inputs"] if item["role"] == "dsm")
        self.assertEqual(dsm["layer_id"], 122082)
        self.assertIn("reference survey first", dsm["survey_name"])

    def test_synthetic_and_same_reference_do_not_fetch_dsm_or_reference(self):
        synthetic = {"name": "synthetic", "elevation": "synthetic",
                     "imagery_layer": 202, "reference_imagery_layer": 202}
        mocks = self.run_fetch([synthetic])
        self.assertEqual([call.args[2] for call in mocks[11].call_args_list], [202])
        mocks[20].assert_called_once_with("area")
        mocks[17].assert_not_called()

    def test_pointcloud_standalone_resolves_its_own_survey(self):
        with patch.object(fetch_pointcloud_regions, "POINTCLOUD_DIR", self.root / "pointcloud"), \
             patch.object(fetch_pointcloud_regions, "load_dotenv"), \
             patch("src.project_secrets.linz_api_key", return_value="fixture"), \
             patch.object(fetch_pointcloud_regions, "area_bbox_wgs84", return_value=self.bbox), \
             patch.object(fetch_pointcloud_regions, "survey_for", return_value=self.survey) as survey, \
             patch.object(fetch_pointcloud_regions, "area_footprints_wgs84",
                          return_value=self.footprints), \
             patch.object(fetch_pointcloud_regions, "tiles_for_bbox_wgs84",
                          return_value=["CC11_1000_0712"]) as index, \
             patch.object(fetch_pointcloud_regions, "download_tile", return_value="fixture"), \
             patch("src.region_build.area_paths", return_value={"dir": self.area_dir}):
            self.assertEqual(fetch_pointcloud_regions.main(["area"]), [])
        survey.assert_called_once_with(self.bbox, "area")
        self.assertEqual([call.args[0] for call in index.call_args_list], self.footprints)

    def test_planned_export_rejects_changed_footprints_and_missing_role(self):
        from src.raster_acquisition_plan import plan_batch
        from src.raster_source_selection import survey_requests

        selected = survey_requests("area", self.footprints, self.survey, mappings=[])
        plan = plan_batch(selected["requests"])
        self.assertEqual(fetch_regions.planned_export(
            plan, "area", "dsm", list(reversed(self.footprints)))["layer_id"], 101)
        with self.assertRaisesRegex(fetch_regions.RasterPlanError, "footprints changed"):
            fetch_regions.planned_export(plan, "area", "dsm", self.footprints[:1])
        with self.assertRaisesRegex(fetch_regions.RasterPlanError, "No unique planned export"):
            fetch_regions.planned_export(plan, "other", "dsm", self.footprints)
        with self.assertRaisesRegex(fetch_regions.RasterPlanError, "Invalid acquisition footprint"):
            fetch_regions.planned_export(plan, "area", "imagery", [[0, 0, 0, 1]])

    def approved_job(self, survey=None, *, imagery=True):
        from src.raster_acquisition_plan import plan_batch
        from src.raster_source_selection import survey_requests

        selected = survey_requests("area", self.footprints, survey or self.survey,
                                   mappings=[], include_imagery=imagery)
        plan = {"regions": [{"region": "area", "footprints_wgs84": self.footprints,
                             "inputs": selected["inputs"]}],
                "batch": plan_batch(selected["requests"]) if selected["requests"] else None}
        path = self.root / "job.json"
        path.write_text(json.dumps({"raster_plan": plan}))
        return path

    def test_fetch_consumes_approved_batch(self):
        path = self.approved_job()
        mocks = self.run_fetch([self.survey], plan_path=path)
        self.assertEqual([call.args[2] for call in mocks[11].call_args_list], [101, 202, 303])
        approved = json.loads(path.read_text())["raster_plan"]["batch"]
        self.assertEqual(mocks[21].call_args_list[0].args[0], approved)

    def test_validate_mapped_approved_snapshot_does_not_refresh_stac(self):
        from src.raster_acquisition_plan import plan_batch
        from src.raster_source_selection import LayerMapping, survey_requests

        url = "https://example.test/collection.json"
        selected = survey_requests(
            "area", self.footprints, self.survey,
            mappings=[LayerMapping(101, "dsm", url, "fixture", "data", "verified")])
        documents = {
            url: {"id": "fixture", "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "one", "bbox": self.bbox, "properties": {},
                "assets": {"data": {"href": "tile.tif", "type": "image/tiff"}}},
        }
        batch = plan_batch(selected["requests"], get_json=documents.__getitem__)
        approved = {"regions": [{"region": "area", "footprints_wgs84": self.footprints,
                                 "inputs": selected["inputs"]}], "batch": batch}
        with patch("src.raster_acquisition_plan.discover_assets",
                   side_effect=AssertionError("execution refreshed STAC")):
            self.assertIs(fetch_regions.validate_approved_plan(
                approved, "area", self.footprints, selected), batch)
            changed = survey_requests(
                "area", self.footprints, self.survey,
                mappings=[LayerMapping(101, "dsm", url, "fixture", "other", "verified")])
            with self.assertRaisesRegex(fetch_regions.RasterPlanError, "inputs changed"):
                fetch_regions.validate_approved_plan(approved, "area", self.footprints, changed)

    def test_changed_footprints_or_source_abort_before_wide_dem(self):
        path = self.approved_job()
        for kind in ("footprints", "source", "legacy"):
            with self.subTest(kind=kind):
                survey = dict(self.survey)
                if kind == "source":
                    survey["dsm_layer"] = 102
                if kind == "legacy":
                    survey["dsm_s3"] = "different-source"
                footprints = self.footprints[:1] if kind == "footprints" else self.footprints
                original = self.footprints
                self.footprints = footprints
                try:
                    with self.assertRaises(fetch_regions.RasterPlanError):
                        self.run_fetch([survey], plan_path=path,
                                       wide_effect=AssertionError("wide DEM started"))
                finally:
                    self.footprints = original

    def test_approved_synthetic_without_raster_inputs(self):
        survey = {"name": "synthetic", "elevation": "synthetic",
                  "dsm_layer": None, "imagery_layer": 202}
        path = self.approved_job(survey, imagery=False)
        mocks = self.run_fetch([survey], imagery=False, plan_path=path)
        mocks[11].assert_not_called()
        mocks[20].assert_called_once_with("area")

    def test_tampered_approved_batch_aborts_before_acquisition(self):
        path = self.approved_job()
        job = json.loads(path.read_text())
        job["raster_plan"]["batch"]["export_fallbacks"][0]["layer_id"] = 999
        path.write_text(json.dumps(job))
        with self.assertRaisesRegex(fetch_regions.RasterPlanError, "fallback does not match"):
            self.run_fetch([self.survey], plan_path=path,
                           wide_effect=AssertionError("wide DEM started"))

    def test_changed_approved_snapshot_digest_aborts_before_acquisition(self):
        path = self.approved_job()
        with self.assertRaisesRegex(fetch_regions.RasterPlanError, "plan changed"):
            self.run_fetch([self.survey], plan_path=path, digest="0" * 64,
                           wide_effect=AssertionError("wide DEM started"))

    def test_invalid_approved_snapshot_precedes_locality_acquisition(self):
        from src import localities
        from src.raster_acquisition_plan import RasterRequest, RasterSource, plan_batch
        from src.raster_plan_handoff import plan_digest

        region = "queenstown-lakes-district/queenstown/frankton"
        path = self.root / "invalid-job.json"
        source = RasterSource("https://example.test/collection.json", "data", "survey")
        documents = {
            source.collection_url: {"id": "survey", "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "tile", "bbox": [0, 0, 1, 1], "properties": {},
                "assets": {"data": {"href": "tile.tif", "type": "image/tiff", "file:size": 200}}},
        }
        mapped = {"regions": [{"region": region, "inputs": [
            {"role": "dsm", "state": "mapped", "layer_id": 101}]}],
            "batch": plan_batch([RasterRequest(region, "dsm", ((0, 0, 1, 1),), source, 101)],
                                get_json=documents.__getitem__)}
        cases = [
            ({"regions": [{"region": region, "inputs": []}], "batch": None},
             "0" * 64, "plan changed"),
            ({"regions": [{"region": region, "inputs": [{"role": "dsm",
                "state": "exports_fallback", "layer_id": 101}]}], "batch": None},
             None, "batch is missing selected inputs"),
            (mapped, None, "Mapped raster plans cannot be executed"),
        ]
        for plan, digest, error in cases:
            path.write_text(json.dumps({"raster_plan": plan}))
            with self.subTest(error=error), \
                    patch.object(sys, "argv", ["fetch_regions.py", region]), \
                    patch.dict(os.environ, {"SOLAR_RASTER_PLAN_JOB": str(path),
                               "SOLAR_RASTER_PLAN_SHA256": digest or plan_digest(plan)}), \
                    patch.object(fetch_regions, "load_dotenv"), \
                    patch("src.project_secrets.linz_api_key", return_value="fixture"), \
                    patch.object(localities, "prepare") as prepare, \
                    patch.object(fetch_regions, "ensure_dem_wide") as dem:
                with self.assertRaisesRegex(fetch_regions.RasterPlanError, error):
                    fetch_regions.main()
                prepare.assert_not_called()
                dem.assert_not_called()

    def test_explicit_legacy_s3_survey_keeps_existing_fetch_path(self):
        survey = {**self.survey, "dsm_s3": "dsm-source", "imagery_s3": "image-source",
                  "reference_imagery_s3": "reference-source"}
        mocks = self.run_fetch([survey])
        mocks[11].assert_not_called()
        mocks[21].assert_not_called()
        s3 = mocks[22]
        self.assertEqual([call.args[1] for call in s3.call_args_list],
                         ["dsm-source", "image-source", "reference-source"])
        self.assertEqual([call.kwargs.get("res") for call in s3.call_args_list],
                         [None, 0.1, 0.1])


if __name__ == "__main__":
    unittest.main()
