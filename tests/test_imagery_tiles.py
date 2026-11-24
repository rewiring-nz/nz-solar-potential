"""Indexed imagery planning, pinning and web-job admission without network access."""

import hashlib
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import box

from src.imagery_tiles import intersecting_item_urls, tile_item_name, fetch_tile_index
from src.raster_acquisition_plan import RasterSource, discover_assets
from src.raster_freshness import verify_due_metadata
from src.raster_plan_handoff import plan_digest
from src.raster_source_selection import mappings_from_snapshot, survey_requests
from tools.pipeline_webui import estimate as E, geo_worker
from tools.pipeline_webui.jobs import automatic_raster_bounds, JobManager


class ImageryTilesTests(unittest.TestCase):
    def test_truncated_tile_name_uses_verified_components(self):
        fields = {"MAPSHEET": "BX24", "SCALE": "500", "TILE": "072050",
                  "TILENAME": "BX24_500_07205"}
        self.assertEqual(tile_item_name(fields, {"BX24_500_072050"}), "BX24_500_072050")
        with self.assertRaisesRegex(ValueError, "uniquely"):
            tile_item_name(fields, {"other"})

    def test_spatial_index_limits_metadata_to_intersecting_tiles(self):
        frame = gpd.GeoDataFrame(
            {"TILENAME": ["near", "far"], "geometry": [box(1570000, 5180000, 1570100, 5180100),
                                                       box(1580000, 5190000, 1580100, 5190100)]},
            crs=2193)
        bounds = list(frame.iloc[[0]].to_crs(4326).total_bounds)
        source = RasterSource("https://example.test/collection.json", "visual", "photos", 123)
        documents = {
            source.collection_url: {"id": "photos", "links": [
                {"rel": "item", "href": "near.json"}, {"rel": "item", "href": "far.json"}]},
            "https://example.test/near.json": {
                "id": "near", "bbox": bounds, "collection": "photos",
                "assets": {"visual": {"href": "near.tiff", "type": "image/tiff"}}},
        }
        calls = []

        def get_json(url):
            calls.append(url)
            return documents[url]

        with patch("src.imagery_tiles.load_tile_index", return_value=frame):
            assets = discover_assets(source, get_json=get_json, footprints=[bounds])
        self.assertEqual(len(assets), 1)
        self.assertNotIn("https://example.test/far.json", calls)
        self.assertEqual(assets[0]["item_id"], "near")
        documents["https://example.test/near.json"]["id"] = "wrong-tile"
        with patch("src.imagery_tiles.load_tile_index", return_value=frame):
            with self.assertRaisesRegex(ValueError, "identity does not match"):
                discover_assets(source, get_json=get_json, footprints=[bounds])

    def test_pagination_repetition_is_rejected_without_leaking_key(self):
        feature = {"id": "same"}
        response = unittest.mock.Mock()
        response.json.return_value = {"type": "FeatureCollection", "features": [feature],
                                      "crs": {"properties": {"name": "EPSG::2193"}}}
        with patch("src.imagery_tiles.PAGE_SIZE", 1), \
                patch("src.imagery_tiles.requests.get", return_value=response):
            with self.assertRaisesRegex(ValueError, "repeated"):
                fetch_tile_index(123, "secret-key")


class NativeImageryWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.bounds = [172.63, -43.55, 172.63005, -43.54995]
        geometry = box(*[172.62, -43.56, 172.65, -43.54])
        self.frame = gpd.GeoDataFrame({
            "imagery_survey_id": [1, 2], "name": ["Fine older", "Coarse newer"],
            "imagery_id": [101, 102], "index_id": [201, 202],
            "ground_sample_distance": [.075, .1],
            "flown_to": ["2024-01-01", "2025-01-01"],
            "s3": ["s3://nz-imagery/fine/rgb/2193/", "s3://nz-imagery/coarse/rgb/2193/"],
            "geometry": [geometry, geometry],
        }, crs=4326).to_crs(2193)
        self.survey = {"name": "test", "elevation": "synthetic",
                       "imagery_available": True, "reference_imagery_available": False,
                       "source_layers": {"dsm_layer": None, "imagery_layer": 102,
                                         "reference_imagery_layer": None}}
        self.docs = {}
        for name in ("fine", "coarse"):
            url = f"https://nz-imagery.s3-ap-southeast-2.amazonaws.com/{name}/rgb/2193/"
            self.docs[url + "collection.json"] = {
                "id": name, "links": [{"rel": "item", "href": "./tile.json"}]}
            self.docs[url + "tile.json"] = {
                "id": "tile", "bbox": [172.62, -43.56, 172.65, -43.54],
                "properties": {"updated": "1"}, "collection": name,
                "assets": {"visual": {"href": "./tile.tiff", "type": "image/tiff",
                                     "file:size": 100, "file:checksum": "1220" + "0" * 64}}}

    def estimate(self, choices=None):
        from src.raster_region_plan import plan_regions
        size = {"buildings": 10, "bbox": self.bounds, "bbox_km2": .01, "source": "fixture"}

        def worker(args, input_text=None, **kwargs):
            self.assertEqual(args, ["raster-plan"])
            return geo_worker._raster_plan(json.loads(input_text))

        def planner(*args, **kwargs):
            return plan_regions(*args, **kwargs, get_json=self.docs.__getitem__)

        with patch.object(E, "region_size", return_value=size), \
                patch.object(geo_worker, "run", side_effect=worker), \
                patch("src.imagery_survey_index.cached_index", return_value=self.frame), \
                patch("src.raster_acquisition_plan._read_json", side_effect=self.docs.__getitem__), \
                patch("src.imagery_tiles.intersecting_item_urls", side_effect=lambda _, urls, __: urls), \
                patch("src.raster_region_plan.plan_regions", side_effect=planner):
            return E.estimate(["area"], data=self.data, survey_fn=lambda *_: copy.deepcopy(self.survey),
                              web_native_default=True, imagery_choices=choices, disk_free=10**12)

    def test_recommended_choice_becomes_mapped_job_input_and_survives_revalidation(self):
        result = self.estimate()
        self.assertFalse(result["blockers"], result["blockers"])
        row = result["regions"][0]
        self.assertEqual(row["source_layers"]["imagery_layer"], 101)
        self.assertEqual(row["imagery_selected"]["imagery_id"], 101)
        self.assertEqual([x["imagery_id"] for x in row["imagery_candidates"]], [101, 102])
        plan = result["raster_plan"]
        batch = plan["batch"]
        self.assertEqual(batch["inputs"][0]["source"]["index_layer"], 201)
        self.assertEqual(batch["assets"][0]["uses"][0]["role"], "imagery")
        mappings = mappings_from_snapshot(plan, ["area"])
        selected = survey_requests("area", [self.bounds],
                                   {"name": "test", "elevation": "synthetic", **row["source_layers"]},
                                   mappings=mappings)
        self.assertEqual(selected["inputs"], plan["regions"][0]["inputs"])
        with patch("src.imagery_tiles.intersecting_item_urls", side_effect=lambda _, urls, __: urls):
            self.assertTrue(verify_due_metadata(batch, 1, now=batch["metadata_checked_at"] + 2,
                                               get_json=self.docs.__getitem__))
        bounds = automatic_raster_bounds(row, self.data, self.data.parent,
                                          plan_digest(plan), True, False)
        self.assertEqual(bounds["processing"], {"imagery": {"resolution": .1, "resampling": "average"}})
        manager = JobManager(self.data, self.data.parent)
        job = manager._prepare_job(result, False, False)
        self.assertEqual(job["survey_overrides"]["area"]["imagery_layer"], 101)
        self.assertEqual(job["raster_admission"]["processing"], bounds["processing"])

    def test_alternative_selection_changes_tiles_and_pinned_layer(self):
        result = self.estimate({"area": 102})
        self.assertFalse(result["blockers"], result["blockers"])
        row = result["regions"][0]
        self.assertEqual(row["imagery_selected"]["imagery_id"], 102)
        self.assertEqual(result["raster_plan"]["batch"]["assets"][0]["collection_id"], "coarse")
        self.assertNotEqual(plan_digest(result["raster_plan"]), plan_digest(self.estimate()["raster_plan"]))

    def test_selection_reestimates_distinct_reference_export(self):
        self.survey["source_layers"]["reference_imagery_layer"] = 102
        self.survey["reference_imagery_available"] = True
        self.frame.loc[1, "s3"] = None
        result = self.estimate()
        row = result["regions"][0]
        self.assertFalse(row["reference_same_as_imagery"])
        self.assertGreater(row["download_components_gb"]["reference_imagery"], 0)
        self.assertEqual(next(source["state"] for source in row["raster_sources"]
                              if source["role"] == "reference_imagery"), "exports_fallback")

    def test_partial_selection_is_pinned_and_warned(self):
        self.frame.geometry = gpd.GeoSeries([box(172.62, -43.56, 172.630025, -43.54)] * 2,
                                             crs=4326).to_crs(2193)
        result = self.estimate()
        self.assertFalse(result["blockers"], result["blockers"])
        row = result["regions"][0]
        self.assertTrue(row["source_layers"]["imagery_partial"])
        self.assertTrue(result["raster_plan"]["batch"]["inputs"][0]["allow_partial"])
        self.assertTrue(any("PARTIAL IMAGERY" in text for text in row["warnings"]))

    def test_selected_imagery_survives_real_survey_resolver_and_run_pinning(self):
        from src.surveys import survey_for
        from tools.pipeline import _pin_survey_for_run
        from tests.test_surveys import _metadata

        row = self.estimate()["regions"][0]
        metadata = _metadata(101, "Fine older Aerial Photos", [172.62, -43.56, 172.65, -43.54])
        selected = survey_for(
            self.bounds, "area", overrides=row["source_layers"], data_dir=self.data,
            get_json=lambda _: metadata)
        env = {}
        _pin_survey_for_run(env, "area", selected)
        with patch.dict("os.environ", env, clear=True):
            pinned = survey_for(self.bounds, "area", data_dir=self.data, get_json=lambda _: metadata)
        self.assertEqual(pinned["imagery_layer"], 101)
        self.assertEqual(pinned["imagery_index_layer"], 201)
        self.assertFalse(pinned["imagery_partial"])

    def test_invalid_imagery_choices_are_rejected_before_estimation(self):
        store = E.EstimateStore(self.data)
        for choices in ([], {"area": True}, {"area": 0}, {"area": "101"}, {"../bad": 101}):
            with self.subTest(choices=choices), self.assertRaises(E.EstimateError):
                store.start(["area"], imagery_choices=choices, background=False)
        for options in ({"include_imagery": False}, {"target": "developer"}):
            with self.subTest(options=options), self.assertRaises(E.EstimateError):
                store.start(["area"], imagery_choices={"area": 101}, background=False, **options)

    @unittest.skipUnless(shutil.which("gdalbuildvrt"), "GDAL CLI tools not installed")
    def test_selected_job_plan_acquires_native_imagery_and_reuses_it(self):
        import numpy as np
        import rasterio
        from rasterio.enums import ColorInterp
        from rasterio.transform import from_origin
        from rasterio.warp import transform_bounds
        from src.fetch_regions import prepare_mapped_fetch
        from src.raster_prepare import verify_planned_region

        x0, _, _, y1 = transform_bounds(4326, 2193, *self.bounds)
        tile = self.data / "native.tif"
        with rasterio.open(tile, "w", driver="GTiff", count=4, width=30, height=30,
                           dtype="uint8", crs=2193, transform=from_origin(x0 - 10, y1 + 10, 1, 1)) as source:
            data = np.full((4, 30, 30), 80, dtype="uint8")
            data[3] = 255
            source.write(data)
            source.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
        payload = tile.read_bytes()
        item = next(document for url, document in self.docs.items()
                    if url.endswith("/fine/rgb/2193/tile.json"))
        item["assets"]["visual"].update(
            href=tile.as_uri(), **{"file:size": len(payload),
                                  "file:checksum": "1220" + hashlib.sha256(payload).hexdigest()})
        directory = self.data / "regions" / "area"
        directory.mkdir(parents=True)
        output = directory / "imagery_mosaic.tif"
        output.write_bytes(b"previous photo")
        result = self.estimate()
        self.assertFalse(result["blockers"], result["blockers"])
        job = JobManager(self.data, self.data.parent)._prepare_job(result, False, False)
        verify_admission = unittest.mock.Mock()
        with patch("src.fetch_regions.REGIONS_DIR", self.data / "regions"):
            acquire = prepare_mapped_fetch(
                result["raster_plan"], job["raster_admission"], "area", verify_admission)
            self.assertEqual(acquire(("area", "imagery")), output.resolve())
            first = output.read_bytes()
            self.assertEqual(acquire(("area", "imagery")), output.resolve())
            self.assertEqual(first, output.read_bytes())
        manifest = verify_planned_region(result["raster_plan"]["batch"], "area", "imagery",
                                         output, res=.1, resampling="average")
        self.assertEqual(manifest["layer_id"], 101)
        self.assertEqual([path.read_bytes() for path in
                          (directory / "previous_imagery").glob("*/imagery_mosaic.tif")],
                         [b"previous photo"])
        self.assertGreaterEqual(verify_admission.call_count, 3)


if __name__ == "__main__":
    unittest.main()
