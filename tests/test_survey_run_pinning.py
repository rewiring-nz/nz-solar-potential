"""The selected preflight survey is preserved in child processes."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.surveys import environment_overrides, survey_for
from tests.test_surveys import CHRISTCHURCH_CENTRAL, DSM_ID, IMAGERY_ID, _client, _metadata
from tools.pipeline import _pin_survey_for_run


class SurveyRunPinningTests(unittest.TestCase):
    def test_preflight_selection_is_passed_to_later_resolver(self):
        layers = {"name": "selected", "dsm_layer": DSM_ID, "dem_layer": 123193,
                  "lidar_tile_index_layer": 123195, "imagery_layer": IMAGERY_ID,
                  "reference_imagery_layer": IMAGERY_ID, "pointcloud_bulk_url": None,
                  "pointcloud_tile_year": "2025"}
        previous = {"other": {"dsm_layer": 1}, "area": {"dsm_layer": 2}}
        encoded = base64.b64encode(json.dumps(previous).encode()).decode()
        env = {"SOLAR_SURVEY_OVERRIDES_B64": encoded,
               "SOLAR_SURVEY_OVERRIDES": '{"ignored": {}}'}
        result = _pin_survey_for_run(env, "area", layers)
        self.assertNotIn("SOLAR_SURVEY_OVERRIDES_B64", env)
        self.assertEqual(result["other"], {"dsm_layer": 1})
        self.assertEqual(result["area"]["dsm_layer"], DSM_ID)
        self.assertNotIn("name", result["area"])
        get_json, calls = _client()
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", env, clear=True):
            selected = survey_for(CHRISTCHURCH_CENTRAL, "area", data_dir=Path(directory),
                                  get_json=get_json, now=100)
        self.assertEqual(selected["dsm_layer"], DSM_ID)
        self.assertEqual(selected["imagery_layer"], IMAGERY_ID)
        self.assertFalse(any("/layers/?" in url for url in calls))

    def test_synthetic_and_unavailable_imagery_are_pinned(self):
        env = {}
        _pin_survey_for_run(env, "area", {"name": "no survey", "dsm_layer": None,
                                          "imagery_layer": None,
                                          "reference_imagery_layer": None})
        get_json, calls = _client()
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", env, clear=True):
            selected = survey_for(CHRISTCHURCH_CENTRAL, "area", data_dir=Path(directory),
                                  get_json=get_json, now=100)
        self.assertEqual(selected["elevation"], "synthetic")
        self.assertIsNone(selected["imagery_layer"])
        self.assertEqual(calls, [])

    def test_invalid_prior_override_is_not_silently_discarded(self):
        with self.assertRaisesRegex(LookupError, "not valid JSON"):
            _pin_survey_for_run({"SOLAR_SURVEY_OVERRIDES": "not-json"}, "area", {})
        self.assertEqual(environment_overrides({}), {})
        for invalid in ([], "", 0, False):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    LookupError, "pinned source layers must be an object"):
                survey_for(CHRISTCHURCH_CENTRAL, "area", overrides=invalid)

    def test_raster_dsm_override_preserves_reference_survey_and_is_pinned(self):
        national_id = 122082
        get_json, _ = _client()
        national = _metadata(national_id, "New Zealand LiDAR 1m DSM",
                             [172.38, -43.67, 172.82, -43.38])

        def client(url):
            return national if url.endswith(f"/layers/{national_id}/") else get_json(url)

        with tempfile.TemporaryDirectory() as directory:
            selected = survey_for(CHRISTCHURCH_CENTRAL, "area", data_dir=Path(directory),
                                  get_json=client, now=100,
                                  overrides={"raster_dsm_layer": national_id})
            self.assertEqual(selected["dsm_layer"], DSM_ID)
            self.assertEqual(selected["raster_dsm_layer"], national_id)
            self.assertEqual(selected["reference_imagery_layer"], IMAGERY_ID)
            self.assertEqual(selected["lidar_tile_index_layer"], 123195)
            env = {}
            _pin_survey_for_run(env, "area", selected)
            with patch.dict("os.environ", env, clear=True):
                pinned = survey_for(CHRISTCHURCH_CENTRAL, "area", data_dir=Path(directory),
                                    get_json=client, now=100)
            for key in ("dsm_layer", "raster_dsm_layer", "imagery_layer",
                        "reference_imagery_layer", "lidar_tile_index_layer", "pointcloud_tile_year"):
                self.assertEqual(pinned[key], selected[key])

    def test_raster_dsm_override_rejects_synthetic_and_noncovering_sources(self):
        get_json, _ = _client()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LookupError, "cannot replace synthetic"):
                survey_for(CHRISTCHURCH_CENTRAL, "area", data_dir=Path(directory),
                           get_json=get_json, now=100,
                           overrides={"dsm_layer": None, "raster_dsm_layer": 122082})
            bad = _metadata(122082, "New Zealand LiDAR 1m DSM", [0, 0, 1, 1])
            def client(url):
                return bad if url.endswith("/layers/122082/") else get_json(url)
            with self.assertRaisesRegex(LookupError, "no longer covers"):
                survey_for(CHRISTCHURCH_CENTRAL, "area", data_dir=Path(directory),
                           get_json=client, now=100, overrides={"raster_dsm_layer": 122082})

    def test_national_preference_is_pinned_and_explicit_disable_wins(self):
        get_json, _ = _client()
        national = _metadata(122082, "New Zealand LiDAR 1m DSM",
                             [172.38, -43.67, 172.82, -43.38])
        def client(url):
            return national if url.endswith("/layers/122082/") else get_json(url)

        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("os.environ", {"SOLAR_PREFER_NATIONAL_DSM": "1"}, clear=True):
            kwargs = {"data_dir": Path(directory), "get_json": client, "now": 100}
            selected = survey_for(CHRISTCHURCH_CENTRAL, "area", **kwargs)
            self.assertEqual(selected["raster_dsm_layer"], 122082)
            self.assertEqual(selected["dsm_layer"], DSM_ID)
            self.assertEqual(selected["reference_imagery_layer"], IMAGERY_ID)
            pinned_env = {"SOLAR_PREFER_NATIONAL_DSM": "0"}
            _pin_survey_for_run(pinned_env, "area", selected)
            with patch.dict("os.environ", pinned_env, clear=True):
                pinned = survey_for(CHRISTCHURCH_CENTRAL, "area", **kwargs)
            self.assertEqual(pinned["raster_dsm_layer"], 122082)
            disabled = survey_for(CHRISTCHURCH_CENTRAL, "area",
                                  overrides={"raster_dsm_layer": None}, **kwargs)
            self.assertIsNone(disabled["raster_dsm_layer"])
            synthetic = survey_for(CHRISTCHURCH_CENTRAL, "area",
                                   overrides={"dsm_layer": None, "imagery_layer": None}, **kwargs)
            self.assertEqual(synthetic["elevation"], "synthetic")
            self.assertIsNone(synthetic["raster_dsm_layer"])
        with patch.dict("os.environ", {"SOLAR_PREFER_NATIONAL_DSM": "yes"}, clear=True), \
                self.assertRaisesRegex(LookupError, "must be 0 or 1"):
            survey_for(CHRISTCHURCH_CENTRAL, "area", get_json=client)


if __name__ == "__main__":
    unittest.main()
