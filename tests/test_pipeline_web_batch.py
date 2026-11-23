"""Each selected region or grid cell becomes a separate sequential queue job."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_pipeline_web_jobs import _manager, _estimate, _wait
from test_pipeline_web_server import _serve
from test_pipeline_web_cloud import fake_cloud
from tools.pipeline_webui import estimate as E
from tools.pipeline_webui import geo_worker
from tools.pipeline_webui.common import write_json_atomic
from tools.pipeline_webui.jobs import JobError
from src.grid_selection import selection_id

PARENT = "queenstown-lakes-district/queenstown/sunshine-bay"


class BatchQueueTests(unittest.TestCase):
    def test_jobs_are_separate_ordered_and_failure_does_not_stop_later_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            commands = []
            original = manager.command_builder

            def capture(*args):
                command = original(*args)
                commands.append(command)
                return command

            manager.command_builder = capture
            estimate = _estimate("a", "bad", "b")
            for index, row in enumerate(estimate["regions"]):
                row.update(total_s=10 + index, buildings=5, source_layers={"dsm_layer": index})
            estimate["options"]["include_imagery"] = False
            ids = manager.start_batch(estimate)
            self.assertEqual(len(set(ids)), 3)
            jobs = [_wait(manager, identifier) for identifier in ids]
            self.assertEqual([job["regions"] for job in jobs], [["a"], ["bad"], ["b"]])
            self.assertEqual([job["status"] for job in jobs], ["succeeded", "failed", "succeeded"])
            for index, job in enumerate(jobs):
                self.assertEqual(len(job["runs"]), 1)
                self.assertEqual(job["estimated_total_s"], 10 + index)
                self.assertEqual(job["survey_overrides"], {job["regions"][0]: {"dsm_layer": index}})
                self.assertFalse(job["include_imagery"])
                self.assertIn("--no-imagery", commands[index])
                if index:
                    self.assertGreaterEqual(job["started"], jobs[index - 1]["finished"])
            self.assertEqual(jobs[-1]["final_combine"]["regions"], ["a", "b"])
            self.assertEqual(jobs[-1]["final_combine"]["status"], "succeeded")

    def test_cancel_one_queued_job_keeps_siblings_and_their_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            ids = manager.start_batch(_estimate("slow", "a", "b"))
            for _ in range(200):
                if manager.load(ids[0])["status"] == "running":
                    break
                time.sleep(.01)
            manager.cancel(ids[1])
            self.assertEqual(manager.load(ids[1])["status"], "cancelled")
            self.assertEqual(manager.load(ids[2])["status"], "queued")
            manager.cancel(ids[0])
            self.assertEqual(_wait(manager, ids[2])["status"], "succeeded")
            _wait(manager, ids[0])

    def test_batch_validation_does_not_enqueue_a_partial_submission(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            estimate = _estimate("a", "b")
            estimate["regions"][1]["selection_count"] = 2
            with self.assertRaisesRegex(JobError, "estimate again"):
                manager.start_batch(estimate)
            self.assertEqual(manager.list(), [])
            estimate["regions"][1]["selection_count"] = 1
            estimate["options"]["include_imagery"] = "false"
            with self.assertRaisesRegex(JobError, "source choices"):
                manager.start_batch(estimate)
            self.assertEqual(manager.list(), [])

    def test_bad_raster_snapshot_in_later_job_does_not_enqueue_earlier_job(self):
        from src.raster_acquisition_plan import plan_batch
        from src.raster_source_selection import survey_requests

        footprints = [[168, -45, 168.1, -44.9]]
        selections = {
            region: survey_requests(region, footprints,
                                    {"elevation": "lidar", "dsm_layer": layer}, mappings=[])
            for region, layer in (("a", 101), ("b", 102))
        }
        plan = {
            "regions": [{"region": region, "inputs": selections[region]["inputs"],
                         "footprints_wgs84": footprints} for region in ("a", "b")],
            "batch": plan_batch([request for selection in selections.values()
                                 for request in selection["requests"]]),
        }
        plan["batch"]["export_fallbacks"] = [
            item for item in plan["batch"]["export_fallbacks"] if item["region"] != "b"]
        with tempfile.TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            estimate = _estimate("a", "b")
            estimate["raster_plan"] = plan
            with self.assertRaisesRegex(JobError, "missing selected fallbacks"):
                manager.start_batch(estimate)
            self.assertEqual(manager.list(), [])

    def test_mapped_preview_cannot_bypass_estimate_blocker(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            estimate = _estimate("a")
            estimate["raster_plan"] = {
                "regions": [{"region": "a", "inputs": [{"role": "dsm", "state": "mapped"}]}],
                "batch": None,
            }
            with self.assertRaisesRegex(JobError, "preview-only"):
                manager.start(estimate)
            self.assertEqual(manager.list(), [])

    def test_mapped_job_freezes_valid_operator_bounds(self):
        from src.raster_job_bounds import load_frozen_job_bounds
        from src.raster_plan_handoff import plan_digest, plan_for_regions
        from src.raster_region_plan import plan_regions
        from src.raster_source_selection import LayerMapping

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            manager = _manager(root)
            bbox = [168, -45, 168.1, -44.9]
            mapping = LayerMapping(101, "dsm", "https://example.test/collection.json",
                                   "capture", "data", "reviewed")
            docs = {
                mapping.collection_url: {"id": "capture", "links": [{"rel": "item", "href": "item.json"}]},
                "https://example.test/item.json": {
                    "id": "tile", "bbox": bbox, "properties": {},
                    "assets": {"data": {"href": "tile.tif", "type": "image/tiff",
                                        "file:size": 100}}}}
            plan = plan_regions(
                ["a"], mappings=[mapping], bbox_for=lambda _: bbox,
                footprints_for=lambda *_: [bbox],
                survey_for=lambda *_: {"elevation": "lidar", "dsm_layer": 101},
                get_json=docs.__getitem__)
            digest = plan_digest(plan_for_regions(plan, ["a"]))
            policy = root / "policy.json"
            policy.write_text(json.dumps({"version": 1, "directory": str(root / "cache"),
                                          "max_bytes": 1000000, "min_free_bytes": 0}))
            bounds_file = root / "bounds.json"
            bounds_file.write_text(json.dumps({
                "version": 1, "region": "a", "workspace": str(ROOT.resolve()),
                "plan_sha256": digest, "include_imagery": True, "paid_referee": False,
                "evidence": "fixture", "cache_policy": str(policy), "work_dir": str(root),
                "output_dir": str(root), "mosaic_bytes": 1, "scratch_bytes": 1,
                "output_bytes": 1, "export_bytes": 1, "extra_allocations": [],
                "processing": {"dsm": {"resolution": 1, "resampling": "average"}}}))
            estimate = _estimate("a")
            estimate["raster_plan"] = plan
            with patch.dict(os.environ, {"SOLAR_RASTER_JOB_BOUNDS": str(bounds_file)}):
                for missing in ("max_bytes", "min_free_bytes"):
                    settings = {"version": 1, "directory": str(root / "cache"),
                                "max_bytes": 1000000, "min_free_bytes": 0}
                    del settings[missing]
                    policy.write_text(json.dumps(settings))
                    with self.assertRaisesRegex(JobError, "explicitly set max_bytes and min_free_bytes"):
                        manager.start_batch(estimate)
                policy.write_text(json.dumps({"version": 1, "directory": str(root / "cache"),
                                              "max_bytes": 1000000, "min_free_bytes": 0}))
                job_id = manager.start_batch(estimate)[0]
                _wait(manager, job_id)
            saved = manager.load(job_id)
            self.assertEqual(saved["raster_plan_sha256"], digest)
            self.assertEqual(saved["raster_admission"]["processing"]["dsm"]["resolution"], 1)
            bounds_file.write_text("{}")
            policy.write_text("{}")
            job_path = manager._job_dir(job_id) / "job.json"
            frozen = load_frozen_job_bounds(job_path, region="a", workspace=ROOT,
                                            plan_digest=digest, include_imagery=True,
                                            paid_referee=False)
            self.assertEqual(frozen["cache"]["max_bytes"], 1000000)

    def test_web_auto_estimate_freezes_admission_without_ack(self):
        from src.raster_cache_config import display_cache_config
        from src.raster_job_bounds import load_frozen_job_bounds
        from src.raster_region_plan import plan_regions
        from src.raster_source_selection import LayerMapping

        with tempfile.TemporaryDirectory() as temporary, patch.dict(
                os.environ, {"SOLAR_RASTER_JOB_BOUNDS": ""}):
            root = Path(temporary).resolve()
            manager = _manager(root)
            bbox = [168, -45, 168.1, -44.9]
            mapping = LayerMapping(101, "dsm", "https://example.test/collection.json",
                                   "capture", "data", "reviewed")
            docs = {
                mapping.collection_url: {"id": "capture", "links": [{"rel": "item", "href": "item.json"}]},
                "https://example.test/item.json": {
                    "id": "tile", "bbox": bbox, "properties": {},
                    "assets": {"data": {"href": "tile.tif", "type": "image/tiff", "file:size": 100}}}}
            plan = plan_regions(
                ["a"], mappings=[mapping], bbox_for=lambda _: bbox,
                footprints_for=lambda *_: [bbox],
                survey_for=lambda *_: {"elevation": "lidar", "dsm_layer": 101},
                get_json=docs.__getitem__)
            config, sources = display_cache_config(manager.data)
            row = {"region": "a", "bbox_km2": 1, "buildings": 10,
                   "download_components_gb": {"imagery": 0.1, "reference_imagery": 0, "lidar": 0}}
            row["raster_auto_estimate"] = E.automatic_raster_estimate(
                row, {"policy": config.describe(sources)}, manager.data)
            estimate = _estimate("a")
            estimate["regions"] = [row]
            estimate["raster_plan"] = plan
            job_id = manager.start_batch(estimate)[0]
            _wait(manager, job_id)
            saved = manager.load(job_id)
            bounds = load_frozen_job_bounds(
                manager._job_dir(job_id) / "job.json", region="a", workspace=ROOT,
                plan_digest=saved["raster_plan_sha256"], include_imagery=True,
                paid_referee=False)
            self.assertEqual(bounds["cache"]["directory"], str(config.directory))
            self.assertEqual(bounds["mosaic_bytes"], row["raster_auto_estimate"]["mosaic_bytes"])
            self.assertEqual(bounds["processing"]["dsm"], {"resolution": 1, "resampling": "average"})

    def test_submission_recovers_stopped_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            lock = manager.data / "pipeline-workspace.lock"
            lock.mkdir()
            registry = manager.data / "raster_cache/reservations"
            registry.mkdir(parents=True)
            reservation = registry / (("a" * 32) + ".json")
            reservation.write_text("{}")
            write_json_atomic(lock / "owner.json", {
                "reservation_id": "a" * 32, "reservation_registry": str(registry),
                "pid": 99999999, "process_group": 99999998})
            job_id = manager.start(_estimate("a"))
            self.assertEqual(_wait(manager, job_id)["status"], "succeeded")
            self.assertFalse(lock.exists())
            self.assertFalse(reservation.exists())

    def test_batch_inputs_cannot_hide_mapped_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            estimate = _estimate("a")
            estimate["raster_plan"] = {
                "regions": [{"region": "a", "inputs": [{"role": "dsm", "state": "exports_fallback"}]}],
                "batch": {"inputs": [{"source": {"collection_id": "capture"}}]},
            }
            with self.assertRaisesRegex(JobError, "preview-only"):
                manager.start(estimate)
            self.assertEqual(manager.list(), [])

    def test_malformed_raster_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            estimate = _estimate("a")
            estimate["raster_plan"] = {"regions": [{"region": "a", "inputs": None}], "batch": None}
            with self.assertRaisesRegex(JobError, "raster source selections are invalid"):
                manager.start(estimate)
            self.assertEqual(manager.list(), [])

    def test_single_job_validates_missing_export_fallback_before_queueing(self):
        from src.raster_acquisition_plan import plan_batch
        from src.raster_source_selection import survey_requests

        with tempfile.TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            estimate = _estimate("a")
            selection = survey_requests("a", [[168, -45, 168.1, -44.9]],
                                        {"elevation": "lidar", "dsm_layer": 101}, mappings=[])
            batch = plan_batch(selection["requests"])
            batch["export_fallbacks"].clear()
            estimate["raster_plan"] = {
                "regions": [{"region": "a", "inputs": selection["inputs"]}],
                "batch": batch,
            }
            with self.assertRaisesRegex(JobError, "missing selected fallbacks"):
                manager.start(estimate)
            self.assertEqual(manager.list(), [])

    def test_cloud_estimate_sums_per_job_allowances_and_pins_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            lookup = lambda region: {"buildings": 10, "bbox": [168.6, -45.05, 168.61, -45.04],
                                     "bbox_km2": .5}
            cloud = fake_cloud()
            estimate = E.estimate(["a/b/c", "d/e/f"], data, "developer", lookup,
                                  cloud=cloud, survey_fn=lambda b, r: {})
            self.assertEqual(float(estimate["cloud"]["cost_nzd"]),
                             sum(float(row["cloud"]["cost_nzd"]) for row in estimate["regions"]))
            self.assertEqual(estimate["cloud"]["vm_hours"],
                             sum(row["cloud"]["vm_hours"] for row in estimate["regions"]))
            directory = data / "manager"
            directory.mkdir()
            manager = _manager(directory)
            # Keep commands offline while exercising the actual cloud queue worker.
            manager.cloud_builder = lambda target, python, regions, paid: [
                python, "-c", "print('cloud fixture')"]
            ids = manager.start_batch(estimate, cloud_cost_acknowledged=True)
            for identifier, row in zip(ids, estimate["regions"]):
                job = _wait(manager, identifier)
                self.assertEqual(job["regions"], [row["region"]])
                self.assertEqual(job["approved"]["cloud_cost_nzd"], row["cloud"]["cost_nzd"])


class BatchHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.server, self.client = _serve(self.root)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.data = self.root / "data"

    def test_mapped_job_http_accepts_without_disk_estimate_acknowledgement(self):
        from src.raster_cache_config import display_cache_config
        from src.raster_region_plan import plan_regions
        from src.raster_source_selection import LayerMapping

        bbox = [168, -45, 168.1, -44.9]
        mapping = LayerMapping(122082, "dsm", "https://example.test/collection.json",
                               "capture", "data", "fixture")
        docs = {
            mapping.collection_url: {"id": "capture", "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "tile", "bbox": bbox, "properties": {},
                "assets": {"data": {"href": "tile.tif", "type": "image/tiff", "file:size": 100}}}}
        plan = plan_regions(
            ["a"], mappings=[mapping], bbox_for=lambda _: bbox,
            footprints_for=lambda *_: [bbox],
            survey_for=lambda *_: {"elevation": "lidar", "dsm_layer": 122082},
            get_json=docs.__getitem__)
        with patch.dict(os.environ, {"SOLAR_RASTER_JOB_BOUNDS": ""}):
            config, sources = display_cache_config(self.data)
            row = {"region": "a", "bbox_km2": 1, "buildings": 10,
                   "download_components_gb": {"imagery": 0, "reference_imagery": 0, "lidar": 0}}
            row["raster_auto_estimate"] = E.automatic_raster_estimate(
                row, {"policy": config.describe(sources)}, self.data)
            estimate = _estimate("a")
            estimate.update(status="ready", expires_at=time.time() + 60,
                            raster_plan=plan, regions=[row], blockers=[])
            store = E.EstimateStore(self.data)
            write_json_atomic(store._path(estimate["estimate_id"]), estimate)
            body = {"estimate_id": estimate["estimate_id"], "target": "local"}
            code, result = self.client.request("POST", "/api/jobs", body)
            self.assertEqual(code, 202, result)
            for _ in range(300):
                _, job = self.client.request("GET", "/api/jobs/" + result["job_id"])
                if job["status"] in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(.01)
            self.assertIsNotNone(job["raster_admission"])

    def submit(self, estimate):
        store = E.EstimateStore(self.data)
        write_json_atomic(store._path(estimate["estimate_id"]), estimate)
        # Hold the workspace so the queue can be inspected deterministically.
        lock = self.data / "pipeline-workspace.lock"
        lock.write_text(json.dumps({"pid": os.getpid()}))
        try:
            code, result = self.client.request("POST", "/api/jobs", {
                "estimate_id": estimate["estimate_id"],
                "include_imagery": estimate["options"]["include_imagery"],
                "paid_referee": estimate["options"]["paid_referee"], "target": "local"})
            self.assertEqual(code, 202, result)
            ids = result["job_ids"]
            self.assertEqual(result["job_id"], ids[0], "legacy response field is retained")
            self.assertEqual(len(ids), len(estimate["regions"]))
            code, queue = self.client.request("GET", "/api/queue")
            self.assertEqual(code, 200)
            self.assertEqual([job["job_id"] for job in queue["lanes"]["local"]["jobs"]], ids)
        finally:
            lock.unlink()
        finished = []
        for identifier, row in zip(ids, estimate["regions"]):
            for _ in range(300):
                code, job = self.client.request("GET", "/api/jobs/" + identifier)
                self.assertEqual(code, 200)
                if job["status"] not in ("queued", "running"):
                    break
                time.sleep(.01)
            self.assertEqual(job["status"], "succeeded", job)
            self.assertEqual(job["regions"], [row["region"]])
            self.assertEqual(len(job["runs"]), 1)
            if finished:
                self.assertGreaterEqual(job["started"], finished[-1]["finished"])
            finished.append(job)
        return finished

    def test_two_full_regions_from_web_estimate_create_two_jobs(self):
        code, result = self.client.request("POST", "/api/estimate", {
            "regions": ["a/b/c", "d/e/f"], "include_imagery": False})
        self.assertEqual(code, 202)
        for _ in range(300):
            code, estimate = self.client.request("GET", "/api/estimate/" + result["estimate_id"])
            if estimate["status"] != "pending":
                break
            time.sleep(.01)
        self.assertEqual(estimate["status"], "ready", estimate)
        self.assertFalse(estimate["blockers"])
        jobs = self.submit(estimate)
        self.assertFalse(jobs[0]["include_imagery"])
        self.assertEqual(jobs[-1]["final_combine"]["status"], "succeeded")

    def test_two_grid_cells_are_materialised_and_queued_individually(self):
        features = [{"properties": {"grid_id": identifier}, "geometry": {"type": "Polygon"}}
                    for identifier in ("r01c01", "r01c02", "r02c01")]
        requests = []

        def resolve(args, env):
            request = json.loads(Path(args[1]).read_text())
            requests.append(request)
            self.assertEqual(request["areas"], features, "boundary ownership needs the entire grid")
            self.assertEqual(len(request["grid_ids"]), 1)
            return {"region": selection_id(PARENT, request["grid_ids"], 4),
                    "buildings": 2, "bbox": [168.6, -45.05, 168.61, -45.04],
                    "bbox_km2": .5, "selection_count": 1}

        with patch.object(geo_worker, "run", side_effect=resolve):
            estimate = E.estimate([], self.data, selection={
                "parent_region": PARENT, "parent_linz_id": 2338, "grid_size": 4,
                "grid_ids": ["r01c02", "r01c01"]},
                grid_lookup=lambda *args: {"grid_size": 4, "features": features},
                api_key_fn=lambda: "fixture", survey_fn=lambda b, r: {},
                disk_free=10**12)
        self.assertEqual([row["grid_ids"] for row in estimate["regions"]], [["r01c01"], ["r01c02"]])
        self.assertEqual(len(requests), 2)
        self.assertEqual(estimate["totals"]["buildings"], 4)
        self.assertEqual(len(set(estimate["selection"]["selection_ids"])), 2)
        self.submit(estimate)


if __name__ == "__main__":
    unittest.main()
