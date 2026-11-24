"""Offline survey-index caching and selection tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import box, mapping

from src.imagery_survey_index import load_index, rank_surveys


def feature(identifier, resolution, flown_to, geometry):
    return {
        "type": "Feature", "geometry": mapping(geometry),
        "properties": {
            "imagery_survey_id": identifier, "imagery_id": identifier,
            "index_id": identifier + 1, "name": f"survey {identifier}",
            "ground_sample_distance": resolution, "flown_to": flown_to,
            "s3": "s3://nz-imagery/example/rgb/2193/",
        },
    }


class ImagerySurveyIndexTests(unittest.TestCase):
    def setUp(self):
        self.features = [
            feature(1, .1, "2024-01-01Z", box(0, 0, 10, 10)),
            feature(2, .075, "2023-01-01Z", box(0, 0, 10, 10)),
            feature(3, .075, "2025-01-01Z", box(0, 0, 10, 10)),
            feature(4, .01, "2025-01-01Z", box(0, 0, 5, 5)),
        ]
        self.frame = gpd.GeoDataFrame.from_features(self.features, crs="EPSG:2193")

    def test_full_coverage_then_detail_then_recency(self):
        result = rank_surveys(self.frame, box(0, 0, 10, 10))
        self.assertEqual([x["imagery_id"] for x in result], [3, 2, 1, 4])
        self.assertFalse(result[-1]["fully_covered"])
        self.assertEqual(result[-1]["coverage_fraction"], .25)

    def test_partial_only_returns_ranked_selection(self):
        result = rank_surveys(self.frame.iloc[[3]], box(0, 0, 10, 10))
        self.assertEqual(result[0]["imagery_id"], 4)
        self.assertFalse(result[0]["fully_covered"])

    def test_float_ids_from_nullable_index_columns_are_accepted(self):
        self.frame.loc[0, "imagery_id"] = float("nan")
        result = rank_surveys(self.frame, box(0, 0, 10, 10))
        self.assertEqual([x["imagery_id"] for x in result], [3, 2, 4])

    def test_cached_index_is_reused_and_refresh_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "survey.gpkg"
            with patch("src.imagery_survey_index.fetch_index",
                       return_value={"features": self.features}) as fetch:
                self.assertEqual(len(load_index(path, "test")), 4)
                self.assertEqual(len(load_index(path)), 4)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(len(load_index(path, "test", refresh=True)), 4)
                self.assertEqual(fetch.call_count, 2)

    def test_failed_refresh_leaves_prior_cache_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "survey.gpkg"
            with patch("src.imagery_survey_index.fetch_index",
                       return_value={"features": self.features}):
                load_index(path, "test")
            with patch("src.imagery_survey_index.fetch_index", side_effect=RuntimeError("offline")):
                with self.assertRaisesRegex(RuntimeError, "offline"):
                    load_index(path, "test", refresh=True)
            self.assertEqual(len(load_index(path)), 4)


if __name__ == "__main__":
    unittest.main()
