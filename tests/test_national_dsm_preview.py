"""Offline tests for the advisory national DSM map preview."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.raster_cache_config import display_cache_config
from tools.pipeline_webui import geo_worker
from tools.pipeline_webui.estimate import EstimateError, EstimateStore
from tools.pipeline_webui.common import write_json_atomic


class NationalDsmPreviewTests(unittest.TestCase):
    def test_cache_path_precedence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            data = Path(tmp).resolve() / "data"
            self.assertEqual(display_cache_config(data)[0].directory, data / "raster_cache")
            os.environ["SOLAR_RASTER_CACHE_DIR"] = str(Path(tmp).resolve() / "display")
            self.assertEqual(display_cache_config(data)[0].directory, Path(tmp).resolve() / "display")
            policy = Path(tmp).resolve() / "policy.json"
            policy.write_text(json.dumps({"version": 1, "directory": "actual"}))
            os.environ["SOLAR_RASTER_CACHE_POLICY"] = str(policy)
            self.assertEqual(display_cache_config(data)[0].directory, Path(tmp).resolve() / "actual")

    def test_worker_plans_all_lidar_areas_together_and_excludes_synthetic(self):
        rows = [
            {"region": "pilot", "bbox": [170, -45, 171, -44], "elevation": "lidar"},
            {"region": "no-lidar", "bbox": [170, -45, 171, -44], "elevation": "synthetic"},
        ]
        batch = {"assets": [{"item_id": "tile", "collection_id": "dsm", "asset_key": "visual",
                             "bbox_wgs84": [170, -45, 171, -44], "size_bytes": 100,
                             "regions": ["pilot"], "cache_state": "recovery_required"}],
                 "capture_coverage": [{"region": "pilot", "state": "covered", "source_url": "private"}],
                 "footprint_bbox_coverage": [], "metadata_checked_at": 1}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), \
                patch("src.raster_region_plan.plan_regions", return_value={"batch": batch}) as plan, \
                patch("src.raster_cache.RasterCache.inspect_plan", return_value=batch):
            result = geo_worker._national_dsm_preview({"data": tmp, "regions": rows})
        self.assertEqual(plan.call_args.args[0], ["pilot"])
        self.assertEqual(result["synthetic"], ["no-lidar"])
        self.assertEqual(result["assets"][0]["cache_state"], "recovery_required")
        self.assertNotIn("source_url", result["capture_coverage"][0])

    def test_real_planner_deduplicates_and_inspects_missing_tile_without_download(self):
        from src.raster_region_plan import plan_regions
        from src.raster_source_selection import load_layer_mappings

        mapping = load_layer_mappings(geo_worker.ROOT / "tools" / "national-dsm-mapping.json")[0]
        polygon = {"type": "Polygon", "coordinates": [[
            [167, -46], [169, -46], [169, -44], [167, -44], [167, -46]]]}
        documents = {
            mapping.collection_url: {
                "id": mapping.collection_id, "links": [{"rel": "item", "href": "tile.json"}],
                "assets": {"capture_area": {"href": "capture-area.geojson"},
                           "capture_dates": {"href": "capture-dates.geojson"}}},
            mapping.collection_url.replace("collection.json", "tile.json"): {
                "id": "tile", "collection": mapping.collection_id,
                "bbox": [167, -46, 169, -44],
                "assets": {"visual": {"href": "tile.tiff", "type": "image/tiff",
                                       "file:checksum": "1220" + "a" * 64}}},
            mapping.collection_url.replace("collection.json", "capture-area.geojson"): {
                "type": "Feature", "geometry": polygon},
            mapping.collection_url.replace("collection.json", "capture-dates.geojson"): {
                "type": "FeatureCollection", "features": [{
                    "type": "Feature", "geometry": polygon,
                    "properties": {"id": "survey", "title": "capture", "source": "survey"}}]},
        }
        rows = [{"region": region, "bbox": [168, -45, 168.1, -44.9], "elevation": "lidar"}
                for region in ("area1", "area2")]

        def offline_plan(*args, **kwargs):
            return plan_regions(*args, **kwargs, get_json=documents.__getitem__)

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), \
                patch("src.raster_region_plan.plan_regions", side_effect=offline_plan), \
                patch("src.raster_cache.RasterCache.lease", side_effect=AssertionError("raster download")):
            result = geo_worker._national_dsm_preview({"data": tmp, "regions": rows})
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["cache_state"], "missing")
        self.assertEqual(set(result["assets"][0]["regions"]), {"area1", "area2"})
        self.assertEqual([item["state"] for item in result["capture_coverage"]], ["covered", "covered"])

    def test_estimates_are_combined_and_preview_is_not_build_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EstimateStore(Path(tmp))
            for identifier, region in [("a" * 16, "pilot"), ("b" * 16, "second")]:
                write_json_atomic(store._path(identifier), {
                    "estimate_id": identifier, "target": "local", "status": "ready",
                    "expires_at": time.time() + 60,
                    "regions": [{"region": region, "bbox": [170, -45, 171, -44],
                                 "elevation": "lidar"}],
                })
            with patch.object(geo_worker, "run", return_value={"assets": []}) as run:
                preview_id = store.start_dsm_preview(["a" * 16, "b" * 16])
                for _ in range(100):
                    result = store.get(preview_id)
                    if result["status"] != "pending":
                        break
                    time.sleep(.01)
                self.assertEqual(result["status"], "ready")
                request = json.loads(run.call_args.kwargs["input_text"])
                self.assertEqual([row["region"] for row in request["regions"]], ["pilot", "second"])
                with self.assertRaises(EstimateError):
                    store.usable(preview_id)
            with self.assertRaises(EstimateError):
                store.start_dsm_preview(["a" * 16, "a" * 16])
            with self.assertRaises(EstimateError):
                store.start_dsm_preview([{}])

    def test_failed_worker_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EstimateStore(Path(tmp))
            identifier = "c" * 16
            write_json_atomic(store._path(identifier), {
                "status": "ready", "target": "local", "expires_at": time.time() + 60,
                "regions": [{"region": "pilot"}],
            })
            with patch.object(geo_worker, "run", side_effect=LookupError("metadata unavailable")):
                preview_id = store.start_dsm_preview([identifier])
                for _ in range(100):
                    result = store.get(preview_id)
                    if result["status"] != "pending":
                        break
                    time.sleep(.01)
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error"], "metadata unavailable")

    def test_view_index_reused_refreshed_and_batched(self):
        assets = [
            {"collection_url": "https://example.test/collection", "collection_id": "dsm",
             "item_id": f"tile{i:03d}", "asset_key": "visual", "bbox_wgs84": [168, -45, 169, -44],
             "url": "https://example.test/tile", "revision": "1", "checksum": None, "size_bytes": 100}
            for i in range(10)
        ]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), \
                patch("src.raster_acquisition_plan.discover_assets", return_value=assets) as discover, \
                patch("src.raster_cache.RasterCache.lease", side_effect=AssertionError("raster download")):
            request = {"data": tmp, "bbox": [168, -45, 169, -44], "offset": 0}
            first = geo_worker._national_dsm_view(request)
            self.assertEqual(first["total"], 10)
            self.assertEqual(len(first["assets"]), 8)
            self.assertTrue(all(asset["cache_state"] == "missing" for asset in first["assets"]))
            second = geo_worker._national_dsm_view({**request, "offset": 8})
            self.assertEqual(len(second["assets"]), 2)
            self.assertEqual(discover.call_count, 1)
            outside = geo_worker._national_dsm_view({**request, "bbox": [170, -45, 171, -44]})
            self.assertEqual(outside["total"], 0)
            geo_worker._national_dsm_view({**request, "refresh": True})
            self.assertEqual(discover.call_count, 2)
            discover.return_value = assets * 16
            too_many = geo_worker._national_dsm_view({**request, "refresh": True})
            self.assertEqual(too_many["total"], 160)
            self.assertEqual(too_many["assets"], [])

    def test_view_input_validation_and_single_active_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EstimateStore(Path(tmp))
            for bbox in (None, [1, 2, 1, 4], [True, 1, 2, 3], [0, 0, float("nan"), 1]):
                with self.assertRaises(EstimateError):
                    store.start_dsm_view(bbox)
            with self.assertRaises(EstimateError):
                store.start_dsm_view([1, 1, 2, 2], offset=150)
            store._dsm_view_lock.acquire()
            try:
                with self.assertRaises(EstimateError):
                    store.start_dsm_view([1, 1, 2, 2])
            finally:
                store._dsm_view_lock.release()

    def test_view_distinguishes_valid_corrupt_and_recovering_cache(self):
        import base64
        import hashlib
        from src.raster_cache import _key
        from src.s3_rasters import _LERC_PROBE

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            payload = base64.b64decode(_LERC_PROBE)
            source = root / "source.tif"
            source.write_bytes(payload)
            asset = {
                "collection_url": "https://example.test/collection", "collection_id": "dsm",
                "item_id": "tile", "asset_key": "visual", "bbox_wgs84": [168, -45, 169, -44],
                "url": source.as_uri(), "revision": "1", "size_bytes": len(payload),
                "checksum": "1220" + hashlib.sha256(payload).hexdigest(),
            }
            cache = display_cache_config(root)[0].cache()
            with cache.lease(asset) as target:
                pass
            request = {"data": str(root), "bbox": [168, -45, 169, -44], "offset": 0}
            with patch("src.raster_acquisition_plan.discover_assets", return_value=[asset]), \
                    patch("src.raster_cache.RasterCache.lease", side_effect=AssertionError("unexpected transfer")):
                self.assertEqual(geo_worker._national_dsm_view(request)["assets"][0]["cache_state"], "cached")
                target.write_bytes(b"x" * len(payload))
                self.assertEqual(geo_worker._national_dsm_view(request)["assets"][0]["cache_state"], "invalid")
                (cache.directory / "partial" / f"{_key(asset)}.previous").touch()
                self.assertEqual(geo_worker._national_dsm_view(request)["assets"][0]["cache_state"], "recovery_required")


if __name__ == "__main__":
    unittest.main()
