"""Cloud-free local queue, real worker-process and pipeline routing checks."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import io
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.local_queue import Coordinator, QueueClient, QueueError, QueueState
from tools.pipeline_queue import (
    PROVENANCE_FILES, execute, make_task, report_raster_cleanup, run_local, stop_process,
    upcoming_raster_plans,
)


class LocalQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="queue-test-", dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def task(self, workspace: Path | None = None, region: str = "pilot"):
        return make_task(workspace or ROOT, region, sys.executable, no_open_browser=True)

    def fixture(self, name: str, code: str = "return 0") -> Path:
        workspace = self.directory / name
        (workspace / "tools").mkdir(parents=True)
        (workspace / "src").mkdir()
        for path in PROVENANCE_FILES:
            if path not in {"config.py", "tools/pipeline.py"}:
                shutil.copyfile(ROOT / path, workspace / path)
        (workspace / "config.py").write_text("# fixture configuration\n")
        (workspace / "tools/pipeline.py").write_text(
            "import time\nfrom pathlib import Path\n"
            "def _run_local(args, area):\n"
            "    print('[QS-01] fixture step', flush=True)\n"
            "    Path('started').write_text(area)\n"
            + "".join(f"    {line}\n" for line in code.splitlines())
        )
        return workspace

    def wait_for(self, predicate, timeout: float = 10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail("Timed out waiting for subprocess evidence.")

    def start_worker(self, queue: Path, name: str):
        process = subprocess.Popen(
            [sys.executable, "-m", "tools.pipeline_queue", "worker",
             "--queue-dir", str(queue), "--worker", name],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, start_new_session=(os.name == "posix"),
        )
        self.addCleanup(stop_process, process)
        return process

    def idle_client(self):
        return unittest.mock.Mock(**{"call.return_value": {"tasks": []}})

    def test_competing_claims_and_attempt_ownership(self):
        with Coordinator(self.directory) as coordinator:
            client = QueueClient(self.directory)
            task = self.task()
            client.call("submit", task=task)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(
                    lambda n: QueueClient(self.directory).call("claim", worker=f"worker-{n}"),
                    range(8),
                ))
            claimed = [r for r in results if r.get("task")]
            self.assertEqual(len(claimed), 1)
            claim = claimed[0]
            with self.assertRaisesRegex(QueueError, "no longer owns"):
                client.call("complete", id=task["id"], attempt="stale", worker=claim["worker"])
            client.call("heartbeat", id=task["id"], attempt=claim["attempt"], worker=claim["worker"])
            client.call("complete", id=task["id"], attempt=claim["attempt"], worker=claim["worker"])
            with self.assertRaisesRegex(QueueError, "no longer owns"):
                client.call("complete", id=task["id"], attempt=claim["attempt"], worker=claim["worker"])
            self.assertEqual(coordinator.state.document["tasks"][0]["status"], "succeeded")
        self.assertEqual(json.loads((self.directory / "state.json").read_text())["tasks"][0]["status"], "succeeded")
        self.assertFalse((self.directory / ".connection.json").exists())

    def test_same_workspace_serialized_but_other_workspaces_can_run(self):
        other_workspace = self.fixture("other")
        with Coordinator(self.directory):
            client = QueueClient(self.directory)
            for workspace in (ROOT, ROOT, other_workspace):
                client.call("submit", task=self.task(workspace))
            first = client.call("claim", worker="one")
            second = client.call("claim", worker="two")
            self.assertNotEqual(first["task"]["workspace"], second["task"]["workspace"])
            self.assertIsNone(client.call("claim", worker="three")["task"])

    def test_duplicate_submission_and_invalid_task_fail(self):
        with Coordinator(self.directory):
            client = QueueClient(self.directory)
            task = self.task()
            client.call("submit", task=task)
            with self.assertRaisesRegex(QueueError, "Duplicate"):
                client.call("submit", task=task)
            task["region"] = "../outside"
            with self.assertRaisesRegex(QueueError, "Invalid task region"):
                client.call("submit", task=task)
            task["region"] = "pilot"
            task["options"]["port"] = -1
            with self.assertRaisesRegex(QueueError, "port"):
                client.call("submit", task=task)

    def test_queue_detects_added_or_modified_source_before_execution(self):
        workspace = self.fixture("source-tree")
        module = workspace / "src" / "new_processing.py"
        module.write_text("VALUE = 1\n")
        task = self.task(workspace)
        module.write_text("VALUE = 2\n")
        with self.assertRaisesRegex(QueueError, "Code/configuration changed"):
            execute(task, "attempt", self.directory / "modified-source.log",
                    unittest.mock.Mock(), "worker")
        task = self.task(workspace)
        (workspace / "tools" / "new_stage.sh").write_text("#!/bin/sh\n")
        with self.assertRaisesRegex(QueueError, "Code/configuration changed"):
            execute(task, "attempt", self.directory / "added-source.log",
                    unittest.mock.Mock(), "worker")
        self.assertFalse((workspace / "started").exists())

    def test_upcoming_cleanup_preferences_use_approved_pending_jobs_on_same_cache(self):
        from src.raster_plan_handoff import plan_digest
        from src.raster_region_plan import plan_regions

        workspace = self.fixture("lookahead")
        task, _, _ = self.admission_task(workspace)
        bbox = [168, -45, 168.1, -44.9]
        plan = plan_regions(
            ["pilot"], mappings=[], bbox_for=lambda _: bbox, footprints_for=lambda *_: [bbox],
            survey_for=lambda *_: {"elevation": "lidar", "dsm_layer": 101})
        path = Path(task["source_environment"]["SOLAR_RASTER_PLAN_JOB"])
        path.write_text(json.dumps({"raster_plan": plan}))
        task["source_environment"]["SOLAR_RASTER_PLAN_SHA256"] = plan_digest(plan)
        other_cache = {**task, "raster_admission": {
            **task["raster_admission"], "cache": {
                **task["raster_admission"]["cache"], "directory": str(self.directory / "other")}}}
        client = unittest.mock.Mock()
        client.call.return_value = {"tasks": [
            {"status": "running", "task": task},
            {"status": "pending", "task": other_cache},
            {"status": "pending", "task": task},
            {"status": "pending", "task": task},
        ]}
        plans = upcoming_raster_plans(client, Path(task["raster_admission"]["cache"]["directory"]), 1)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["export_fallbacks"][0]["layer_id"], 101)
        self.assertEqual(upcoming_raster_plans(client, self.directory / "other", 0), [])

    def test_cleanup_results_report_bytes_and_unmet_target(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            report_raster_cleanup({
                "partials": {"removed": [{"bytes": 7}]},
                "assets": None,
                "proactive": {"removed": [{"bytes": 11}, {"bytes": 13}],
                              "target_reached": False},
            })
        self.assertIn("partials cleanup: 1 entries, 7 bytes", output.getvalue())
        self.assertIn("proactive cleanup: 2 entries, 24 bytes; target not reached", output.getvalue())

    def test_upcoming_preferences_follow_real_queue_and_skip_changed_approval(self):
        from src.raster_plan_handoff import plan_digest
        from src.raster_region_plan import plan_regions

        bbox = [168, -45, 168.1, -44.9]
        tasks = []
        for index in range(3):
            task, _, _ = self.admission_task(self.fixture(f"pending-{index}"))
            plan = plan_regions(
                ["pilot"], mappings=[], bbox_for=lambda _: bbox, footprints_for=lambda *_: [bbox],
                survey_for=lambda *_, layer=101 + index: {"elevation": "lidar", "dsm_layer": layer})
            Path(task["source_environment"]["SOLAR_RASTER_PLAN_JOB"]).write_text(
                json.dumps({"raster_plan": plan}))
            digest = plan_digest(plan)
            task["source_environment"]["SOLAR_RASTER_PLAN_SHA256"] = digest
            task["raster_admission"]["plan_sha256"] = digest
            tasks.append(task)
        queue = self.directory / "lookahead-queue"
        with Coordinator(queue):
            client = QueueClient(queue)
            for task in tasks:
                client.call("submit", task=task)
            client.call("claim", worker="running")
            cache_dir = Path(tasks[0]["raster_admission"]["cache"]["directory"])
            plans = upcoming_raster_plans(client, cache_dir, 1)
            self.assertEqual([p["export_fallbacks"][0]["layer_id"] for p in plans], [102])
            Path(tasks[1]["source_environment"]["SOLAR_RASTER_PLAN_JOB"]).write_text("{}")
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                plans = upcoming_raster_plans(client, cache_dir, 2)
            self.assertEqual([p["export_fallbacks"][0]["layer_id"] for p in plans], [103])
            self.assertIn("WARNING: skipping invalid upcoming raster preference", errors.getvalue())

    def test_coordinator_lock_and_port_conflict(self):
        with self.assertRaisesRegex(QueueError, "port"):
            Coordinator(self.directory, -1)
        with Coordinator(self.directory) as running:
            with self.assertRaisesRegex(QueueError, "already owned"):
                Coordinator(self.directory)
            assert running.server is not None
            other = self.directory / "other"
            with self.assertRaises(OSError):
                Coordinator(other, running.server.server_port)
            self.assertFalse((other / "coordinator.lock").exists())
            self.assertTrue((self.directory / ".connection.json").is_file())

    @unittest.skipUnless(os.name == "posix", "foreground signal cleanup uses POSIX/WSL")
    def test_foreground_coordinator_cli_is_responsive_without_cloud_profile(self):
        queue = self.directory / "cli"
        environment = dict(os.environ, SOLAR_CLOUD_PROFILE="not-configured",
                           GOOGLE_APPLICATION_CREDENTIALS="/not-configured")
        process = subprocess.Popen(
            [sys.executable, "-m", "tools.pipeline_queue", "serve", "--queue-dir", str(queue)],
            cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, start_new_session=(os.name == "posix"),
        )
        self.addCleanup(stop_process, process)
        self.wait_for(lambda: (queue / ".connection.json").exists())
        self.assertIsNone(process.poll())
        self.assertEqual(QueueClient(queue).call("status")["tasks"], [])
        assert process.stdout is not None
        readable, _, _ = select.select([process.stdout], [], [], 10)
        self.assertTrue(readable, "coordinator did not announce readiness")
        self.assertIn("ready", process.stdout.readline())
        stop_process(process)
        process.communicate(timeout=10)
        self.assertFalse((queue / "coordinator.lock").exists())

    def test_authentication_origin_and_descriptor_permissions(self):
        with Coordinator(self.directory):
            client = QueueClient(self.directory)
            for headers in ({}, {"Authorization": f"Bearer {client.token}", "Origin": "https://example.test"}):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(client.url + "/status", data=b"{}", headers=headers), timeout=3)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            if os.name == "posix":
                self.assertEqual((self.directory / ".connection.json").stat().st_mode & 0o777, 0o600)

    def test_shutdown_and_restart_do_not_silently_retry(self):
        with Coordinator(self.directory):
            client = QueueClient(self.directory)
            client.call("submit", task=self.task())
            client.call("claim", worker="old")
        with Coordinator(self.directory):
            client = QueueClient(self.directory)
            self.assertEqual(client.call("status")["tasks"][0]["status"], "interrupted")
            self.assertTrue(client.call("claim", worker="new")["finished"])

    def test_crashed_state_is_interrupted_and_stale_ack_rejected(self):
        path = self.directory / "state.json"
        state = QueueState(path)
        task = self.task()
        state.dispatch("submit", {"task": task})
        record = state.dispatch("claim", {"worker": "old"})
        recovered = QueueState(path)
        self.assertEqual(recovered.document["tasks"][0]["status"], "interrupted")
        with self.assertRaisesRegex(QueueError, "no longer owns"):
            recovered.dispatch("complete", {"id": task["id"], "attempt": record["attempt"], "worker": "old"})

    def test_persistence_failure_prevents_false_success(self):
        state = QueueState(self.directory / "state.json")
        task = self.task()
        with patch("src.local_queue.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                state.dispatch("submit", {"task": task})
        with self.assertRaisesRegex(QueueError, "storage failed"):
            state.dispatch("claim", {"worker": "one"})

    def test_invalid_persistent_state_fails_and_releases_coordinator_lock(self):
        (self.directory / "state.json").write_text('{"version": 1, "tasks": [{}]}')
        with self.assertRaisesRegex(QueueError, "Invalid task record"):
            Coordinator(self.directory)
        self.assertFalse((self.directory / "coordinator.lock").exists())

    def test_real_workers_execute_two_workspaces_without_cloud(self):
        # Each waits for the other's start marker: this proves actual overlap.
        one = self.fixture("one", "time.sleep(0.1)\nreturn 0")
        two = self.fixture("two", "time.sleep(0.1)\nreturn 0")
        for here, other in ((one, two), (two, one)):
            text = (here / "tools/pipeline.py").read_text()
            (here / "tools/pipeline.py").write_text(text.replace(
                "    time.sleep(0.1)",
                f"    deadline = time.monotonic() + 8\n"
                f"    while not Path({str(other / 'started')!r}).exists():\n"
                "        if time.monotonic() > deadline: return 9\n"
                "        time.sleep(0.05)",
            ))
        queue = self.directory / "queue"
        with Coordinator(queue):
            client = QueueClient(queue)
            client.call("submit", task=self.task(one))
            client.call("submit", task=self.task(two))
            processes = [self.start_worker(queue, name) for name in ("one", "two")]
            for process in processes:
                output, _ = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, output)
            self.assertEqual([r["status"] for r in client.call("status")["tasks"]], ["succeeded"] * 2)
            logs = list((queue / "attempts").glob("*.log"))
            self.assertEqual(len(logs), 2)
            self.assertTrue(all("[QS-01]" in path.read_text() for path in logs))

    def test_worker_uses_submitter_source_environment_not_its_own(self):
        workspace = self.fixture(
            "sources",
            "import json, os\n"
            "keys = ('SOLAR_SURVEY_OVERRIDES', 'SOLAR_SURVEY_OVERRIDES_B64', "
            "'SOLAR_RASTER_PLAN_JOB', 'SOLAR_RASTER_PLAN_SHA256')\n"
            "Path('source-env.json').write_text(json.dumps({key: os.environ.get(key) for key in keys}))\n"
            "return 0")
        queue = self.directory / "source-queue"
        plan = str(self.directory / "approved.json")
        submitted = {"SOLAR_SURVEY_OVERRIDES": '{"pilot":{"dsm_layer":101}}',
                     "SOLAR_RASTER_PLAN_JOB": plan, "SOLAR_RASTER_PLAN_SHA256": "a" * 64}
        with Coordinator(queue):
            client = QueueClient(queue)
            with patch.dict(os.environ, submitted, clear=True):
                client.call("submit", task=self.task(workspace))
            with patch.dict(os.environ, {"SOLAR_SURVEY_OVERRIDES": "stale",
                                         "SOLAR_SURVEY_OVERRIDES_B64": "stale",
                                         "SOLAR_RASTER_PLAN_JOB": "/stale"}):
                process = self.start_worker(queue, "source-worker")
            output, _ = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, output)
            self.assertEqual(json.loads((workspace / "source-env.json").read_text()), {
                **submitted, "SOLAR_SURVEY_OVERRIDES_B64": None})

    def admission_task(self, workspace, *, mosaic_bytes=1):
        from src.raster_plan_handoff import plan_digest

        data = workspace / "data"
        data.mkdir(exist_ok=True)
        plan = {"regions": [{"region": "pilot", "inputs": []}], "batch": None}
        digest = plan_digest(plan)
        job = self.directory / f"{workspace.name}-job.json"
        job.write_text(json.dumps({"raster_plan": plan}))
        policy = self.directory / f"{workspace.name}-policy.json"
        policy.write_text(json.dumps({"version": 1, "directory": str(self.directory / "cache"),
                                      "max_bytes": 1000000, "min_free_bytes": 0}))
        bounds = self.directory / f"{workspace.name}-bounds.json"
        bounds.write_text(json.dumps({
            "version": 1, "region": "pilot", "workspace": str(workspace.resolve()),
            "plan_sha256": digest, "include_imagery": True, "paid_referee": False,
            "evidence": "fixture bounds", "cache_policy": str(policy),
            "work_dir": str(data), "output_dir": str(data), "mosaic_bytes": mosaic_bytes,
            "scratch_bytes": 1, "output_bytes": 1, "export_bytes": 1,
            "extra_allocations": []}))
        with patch.dict(os.environ, {"SOLAR_RASTER_JOB_BOUNDS": str(bounds),
                                     "SOLAR_RASTER_PLAN_JOB": str(job),
                                     "SOLAR_RASTER_PLAN_SHA256": digest}):
            task = make_task(workspace, "pilot", sys.executable, no_preview_server=True)
        return task, bounds, policy

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_admitted_queue_job_retains_reservation_and_workspace_for_review(self):
        from src.raster_reservations import DiskReservations

        workspace = self.fixture("admitted")
        task, bounds, policy = self.admission_task(workspace)
        bounds.write_text("{}")
        policy.write_text("{}")
        with patch("src.raster_cache.RasterCache.cleanup_for_queue") as cleanup, \
                patch("src.raster_cache.RasterCache.cleanup_partials") as partials:
            with self.assertRaisesRegex(QueueError, "reservation.*retained|reservation.*recovery"):
                execute(task, "attempt", self.directory / "admitted.log", self.idle_client(), "worker")
            cleanup.assert_not_called()
            partials.assert_not_called()
        self.assertTrue((workspace / "started").exists())
        lock = workspace / "data/pipeline-workspace.lock"
        owner = json.loads((lock / "owner.json").read_text())
        registry = Path(owner["reservation_registry"])
        record = registry / (owner["reservation_id"] + ".json")
        self.assertTrue(record.is_file())
        DiskReservations(registry).recover_orphan(owner["reservation_id"], consumers_stopped=True)

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_insufficient_job_bounds_fail_before_pipeline_launch(self):
        workspace = self.fixture("admission-full")
        task, _, _ = self.admission_task(workspace, mosaic_bytes=2**62)
        with patch("src.raster_cache.RasterCache.cleanup_for_queue") as cleanup, \
                patch("src.raster_cache.RasterCache.cleanup_partials") as partials:
            with self.assertRaisesRegex(QueueError, "admission failed"):
                execute(task, "attempt", self.directory / "full.log", self.idle_client(), "worker")
            cleanup.assert_not_called()
            partials.assert_not_called()
        self.assertFalse((workspace / "started").exists())
        self.assertFalse((workspace / "data/pipeline-workspace.lock").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_changed_approval_fails_before_queue_launch_and_admission(self):
        workspace = self.fixture("admission-changed")
        task, _, _ = self.admission_task(workspace)
        Path(task["source_environment"]["SOLAR_RASTER_PLAN_JOB"]).write_text("{}")
        with self.assertRaisesRegex(QueueError, "Raster approval invalid"):
            execute(task, "attempt", self.directory / "changed-plan.log",
                    self.idle_client(), "worker")
        self.assertFalse((workspace / "started").exists())
        self.assertFalse((workspace / "data/pipeline-workspace.lock").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_active_orphan_prevents_next_admitted_job_starting(self):
        from src.raster_reservations import DiskReservations

        first = self.fixture("admission-first")
        second = self.fixture("admission-second")
        first_task, _, _ = self.admission_task(first)
        second_task, _, _ = self.admission_task(second)
        with self.assertRaises(QueueError):
            execute(first_task, "first", self.directory / "first.log",
                    self.idle_client(), "worker")
        with self.assertRaisesRegex(QueueError, "Orphaned disk reservation"):
            execute(second_task, "second", self.directory / "second.log",
                    self.idle_client(), "worker")
        self.assertFalse((second / "started").exists())
        self.assertFalse((second / "data/pipeline-workspace.lock").exists())
        owner = json.loads((first / "data/pipeline-workspace.lock/owner.json").read_text())
        DiskReservations(Path(owner["reservation_registry"])).recover_orphan(
            owner["reservation_id"], consumers_stopped=True)

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_worker_reports_manual_review_after_admitted_build(self):
        from src.raster_reservations import DiskReservations

        workspace = self.fixture("admission-worker")
        task, _, _ = self.admission_task(workspace)
        queue = self.directory / "admission-queue"
        with Coordinator(queue):
            client = QueueClient(queue)
            client.call("submit", task=task)
            process = self.start_worker(queue, "admitted-worker")
            output, _ = process.communicate(timeout=15)
            record = client.call("status")["tasks"][0]
            self.assertEqual(record["status"], "failed", output)
            self.assertIn("manual recovery", record["error"])
            self.assertTrue((workspace / "started").exists())
        owner = json.loads((workspace / "data/pipeline-workspace.lock/owner.json").read_text())
        DiskReservations(Path(owner["reservation_registry"])).recover_orphan(
            owner["reservation_id"], consumers_stopped=True)

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_interrupted_admitted_worker_retains_reservation_and_lock(self):
        from src.raster_reservations import DiskReservations

        workspace = self.fixture("admission-interrupt", "time.sleep(60)\nreturn 0")
        task, _, _ = self.admission_task(workspace)
        queue = self.directory / "interrupted-queue"
        with Coordinator(queue):
            client = QueueClient(queue)
            client.call("submit", task=task)
            process = self.start_worker(queue, "interrupted-worker")
            self.wait_for(lambda: (workspace / "started").exists())
            os.kill(process.pid, signal.SIGINT)
            process.communicate(timeout=15)
            record = client.call("status")["tasks"][0]
            self.assertEqual(record["status"], "failed")
            self.assertIn("retained", record["error"])
        owner = json.loads((workspace / "data/pipeline-workspace.lock/owner.json").read_text())
        DiskReservations(Path(owner["reservation_registry"])).recover_orphan(
            owner["reservation_id"], consumers_stopped=True)

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_tampered_frozen_bounds_fail_before_launch(self):
        workspace = self.fixture("admission-tampered")
        task, _, _ = self.admission_task(workspace)
        task["raster_admission"]["scratch_bytes"] = False
        with self.assertRaisesRegex(ValueError, "scratch_bytes"):
            execute(task, "attempt", self.directory / "tampered.log",
                    self.idle_client(), "worker")
        self.assertFalse((workspace / "started").exists())
        self.assertFalse((workspace / "data/pipeline-workspace.lock").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_cache_inspection_failure_is_reported_before_launch(self):
        from src.raster_cache import RasterCacheError

        workspace = self.fixture("admission-cache-error")
        task, _, _ = self.admission_task(workspace)
        with patch("src.raster_cache.RasterCache.inspect_plan",
                   side_effect=RasterCacheError("fixture cache failure")):
            with self.assertRaisesRegex(QueueError, "Raster disk admission failed: fixture cache failure"):
                execute(task, "attempt", self.directory / "cache-error.log",
                        self.idle_client(), "worker")
        self.assertFalse((workspace / "started").exists())
        self.assertFalse((workspace / "data/pipeline-workspace.lock").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_pipeline_handoff_checks_active_job_bound_reservation(self):
        from src.raster_job_admission import (
            ADMISSION_ENV, active_admission, admission_binding, approved_build_plan,
        )
        from src.raster_job_bounds import job_resources
        from src.raster_reservations import DiskReservationError, DiskReservations

        workspace = self.fixture("admission-handoff")
        task, _, _ = self.admission_task(workspace)
        bounds = task["raster_admission"]
        cache, requirements = job_resources(bounds)
        registry = DiskReservations(cache.directory / "reservations")
        empty_plan = {"inputs": [], "assets": [], "export_fallbacks": []}
        with registry.reserve(lambda: cache.inspect_plan(empty_plan), requirements,
                              binding=admission_binding(bounds)) as forecast:
            handoff = {"bounds": bounds, "reservation_id": forecast["reservation_id"]}
            env = {**task["source_environment"], ADMISSION_ENV: json.dumps(handoff)}
            kwargs = {"region": "pilot", "workspace": workspace,
                      "plan_digest": bounds["plan_sha256"],
                      "include_imagery": True, "paid_referee": False}
            self.assertEqual(active_admission(env, **kwargs), bounds)
            build_kwargs = {key: value for key, value in kwargs.items() if key != "plan_digest"}
            snapshot, actual = approved_build_plan(env, **build_kwargs, no_preview_server=True)
            self.assertEqual(snapshot["regions"][0]["region"], "pilot")
            self.assertEqual(actual, bounds)
            with self.assertRaisesRegex(ValueError, "no-preview-server"):
                approved_build_plan(env, **build_kwargs, no_preview_server=False)
            handoff["bounds"] = {**bounds, "scratch_bytes": 2}
            with self.assertRaisesRegex(DiskReservationError, "does not match"):
                active_admission({ADMISSION_ENV: json.dumps(handoff)}, **kwargs)
            with self.assertRaisesRegex(ValueError, "do not match"):
                active_admission(env, **{**kwargs, "include_imagery": False})
        with self.assertRaisesRegex(DiskReservationError, "not found"):
            active_admission(env, **kwargs)

    def test_operator_processing_options_match_snapshot_roles(self):
        from src.raster_job_bounds import processing_for_snapshot, validate_job_bounds

        workspace = self.fixture("admission-processing")
        task, _, _ = self.admission_task(workspace)
        bounds = task["raster_admission"]
        options = {"dsm": {"resolution": 1.0, "resampling": "bilinear"}}
        configured = {**bounds, "processing": options}
        validated = validate_job_bounds(
            configured, region="pilot", workspace=workspace,
            plan_digest=bounds["plan_sha256"], include_imagery=True,
            paid_referee=False, frozen=True)
        snapshot = {"regions": [{"region": "pilot", "inputs": []}], "batch": None}
        with self.assertRaisesRegex(ValueError, "match the approved mapped roles"):
            processing_for_snapshot(validated, snapshot, "pilot")
        self.assertEqual(processing_for_snapshot(bounds, snapshot, "pilot"), {})
        for invalid in (
            {"dsm": {"resolution": 0, "resampling": "average"}},
            {"dsm": {"resolution": 1, "resampling": "unknown"}},
            {"unknown": {"resolution": 1, "resampling": "average"}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_job_bounds(
                    {**bounds, "processing": invalid}, region="pilot", workspace=workspace,
                    plan_digest=bounds["plan_sha256"], include_imagery=True,
                    paid_referee=False, frozen=True)

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_mapped_job_handoff_reaches_real_worker_under_its_reservation(self):
        import hashlib
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        from rasterio.warp import transform_bounds
        from src.raster_plan_handoff import plan_digest
        from src.raster_region_plan import plan_regions
        from src.raster_reservations import DiskReservations
        from src.raster_source_selection import LayerMapping

        code = (
            "import json, os, sys, src\n"
            "from unittest.mock import patch\n"
            "from contextlib import ExitStack\n"
            f"src.__path__.append({str(ROOT / 'src')!r})\n"
            "from src.raster_job_admission import approved_build_plan\n"
            "snapshot, bounds = approved_build_plan(os.environ, region=area, "
            "workspace=Path.cwd(), include_imagery=True, paid_referee=False, "
            "no_preview_server=True)\n"
            "Path('verified-admission.json').write_text(json.dumps(snapshot['regions']))\n"
            "from src import fetch_regions as fetch\n"
            "box = snapshot['regions'][0]['bbox_wgs84']\n"
            "data = Path.cwd() / 'data'\n"
            "os.environ['SOLAR_INCLUDE_IMAGERY'] = '1'\n"
            "os.environ['SOLAR_DEMOLISHED_REFEREE'] = '0'\n"
            "with ExitStack() as stack:\n"
            "    stack.enter_context(patch.object(sys, 'argv', ['fetch_regions.py', area]))\n"
            "    stack.enter_context(patch.object(fetch, 'DATA_DIR', data))\n"
            "    stack.enter_context(patch.object(fetch, 'REGIONS_DIR', data / 'regions'))\n"
            "    stack.enter_context(patch.object(fetch, 'load_dotenv'))\n"
            "    stack.enter_context(patch('src.project_secrets.linz_api_key', return_value='fixture'))\n"
            "    stack.enter_context(patch.object(fetch, 'ensure_dem_wide'))\n"
            "    stack.enter_context(patch.object(fetch, 'area_bbox_wgs84', return_value=box))\n"
            "    stack.enter_context(patch.object(fetch, 'acquisition_footprints', return_value=[box]))\n"
            "    stack.enter_context(patch.object(fetch, 'survey_for', return_value="
            "{'elevation': 'lidar', 'dsm_layer': 101}))\n"
            "    stack.enter_context(patch.object(fetch, 'fetch_building_outlines', "
            "return_value={'features': []}))\n"
            "    stack.enter_context(patch('src.fetch_pointcloud_regions.main', return_value=[]))\n"
            "    fetch.main()\n"
            "return 0"
        )
        workspace = self.fixture("mapped-worker", code)
        for module in ("raster_job_admission", "raster_job_bounds", "raster_processing",
                       "raster_cache", "raster_cache_config", "raster_disk_plan",
                       "raster_reservations", "raster_plan_handoff"):
            shutil.copyfile(ROOT / "src" / f"{module}.py", workspace / "src" / f"{module}.py")
        task, bounds_path, _ = self.admission_task(workspace)
        tile = workspace / "tile.tif"
        with rasterio.open(tile, "w", driver="GTiff", count=1, width=8, height=8,
                           dtype="float32", crs="EPSG:2193", nodata=-9999,
                           transform=from_origin(1200000, 5000000, 8, 8)) as dataset:
            dataset.write(np.ones((8, 8), dtype="float32"), 1)
            bbox = list(transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds,
                                         densify_pts=21))
        payload = tile.read_bytes()
        mapping = LayerMapping(101, "dsm", "https://example.test/collection.json",
                               "capture", "data", "fixture evidence")
        docs = {
            mapping.collection_url: {"id": "capture", "links": [{"rel": "item", "href": "item.json"}]},
            "https://example.test/item.json": {
                "id": "tile", "collection": "capture", "bbox": bbox,
                "assets": {"data": {"href": tile.as_uri(), "type": "image/tiff",
                                    "file:size": len(payload),
                                    "file:checksum": "1220" + hashlib.sha256(payload).hexdigest()}}}}
        snapshot = plan_regions(
            ["pilot"], mappings=[mapping], bbox_for=lambda _: bbox,
            footprints_for=lambda *_: [bbox],
            survey_for=lambda *_: {"elevation": "lidar", "dsm_layer": 101},
            get_json=docs.__getitem__)
        job_path = Path(task["source_environment"]["SOLAR_RASTER_PLAN_JOB"])
        job_path.write_text(json.dumps({"raster_plan": snapshot}))
        digest = plan_digest(snapshot)
        bounds = json.loads(bounds_path.read_text())
        bounds["plan_sha256"] = digest
        bounds["processing"] = {"dsm": {"resolution": None, "resampling": "average"}}
        bounds_path.write_text(json.dumps(bounds))
        with patch.dict(os.environ, {"SOLAR_RASTER_JOB_BOUNDS": str(bounds_path),
                                     "SOLAR_RASTER_PLAN_JOB": str(job_path),
                                     "SOLAR_RASTER_PLAN_SHA256": digest}):
            task = make_task(workspace, "pilot", sys.executable, no_preview_server=True)
        with patch("src.raster_freshness.verify_due_metadata",
                   side_effect=ValueError("source changed; estimate again")):
            with self.assertRaisesRegex(QueueError, "metadata refresh failed.*source changed"):
                execute(task, "attempt", self.directory / "refresh-failed.log",
                        self.idle_client(), "worker")
        self.assertFalse((workspace / "started").exists())
        self.assertFalse((workspace / "data/pipeline-workspace.lock").exists())
        queue = self.directory / "mapped-queue"
        with Coordinator(queue):
            client = QueueClient(queue)
            client.call("submit", task=task)
            process = self.start_worker(queue, "mapped-worker")
            output, _ = process.communicate(timeout=20)
            record = client.call("status")["tasks"][0]
            self.assertEqual(record["status"], "failed", output)
            self.assertIn("retained", record["error"])
            logs = list((queue / "attempts").glob("*.log"))
            self.assertTrue((workspace / "verified-admission.json").exists(),
                            output + "\n" + "\n".join(path.read_text() for path in logs))
            self.assertEqual(json.loads((workspace / "verified-admission.json").read_text())[0]
                             ["inputs"][0]["state"], "mapped", output)
            self.assertTrue((workspace / "data/regions/pilot/dsm_mosaic.tif").is_file(), output)
            self.assertTrue((workspace / "data/regions/pilot/dsm_mosaic.tif.source.json").is_file(),
                            output)
        owner = json.loads((workspace / "data/pipeline-workspace.lock/owner.json").read_text())
        DiskReservations(Path(owner["reservation_registry"])).recover_orphan(
            owner["reservation_id"], consumers_stopped=True)

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_reservation_release_failure_keeps_workspace_lock_before_launch(self):
        from src.local_queue import atomic_json
        from src.raster_reservations import DiskReservationError

        workspace = self.fixture("admission-release-error")
        task, _, _ = self.admission_task(workspace)
        context = unittest.mock.MagicMock()
        context.__enter__.return_value = {"reservation_id": "a" * 32}
        context.__exit__.side_effect = DiskReservationError("fixture release failure")
        def write_owner(path, value):
            if "reservation_id" in value:
                raise OSError("fixture owner write failure")
            atomic_json(path, value)

        with (patch("src.raster_reservations.DiskReservations.reserve", return_value=context),
              patch("tools.pipeline_queue.atomic_json", side_effect=write_owner)):
            with self.assertRaisesRegex(QueueError, "fixture release failure"):
                execute(task, "attempt", self.directory / "release-error.log",
                        self.idle_client(), "worker")
        self.assertTrue((workspace / "data/pipeline-workspace.lock/owner.json").exists())
        self.assertFalse((workspace / "started").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX reservations required")
    def test_failed_launch_still_retains_admission_for_manual_review(self):
        from src.raster_reservations import DiskReservations

        workspace = self.fixture("admission-launch-error")
        task, _, _ = self.admission_task(workspace)
        with (patch("tools.pipeline_queue.provenance", return_value=task["provenance"]),
              patch("tools.pipeline_queue.subprocess.Popen", side_effect=OSError("fixture launch failure"))):
            with self.assertRaisesRegex(QueueError, "fixture launch failure"):
                execute(task, "attempt", self.directory / "launch-error.log",
                        self.idle_client(), "worker")
        self.assertFalse((workspace / "started").exists())
        owner = json.loads((workspace / "data/pipeline-workspace.lock/owner.json").read_text())
        DiskReservations(Path(owner["reservation_registry"])).recover_orphan(
            owner["reservation_id"], consumers_stopped=True)

    def test_job_bounds_must_match_approved_workload_and_disable_preview(self):
        workspace = self.fixture("admission-options")
        task, bounds, _ = self.admission_task(workspace)
        raw = json.loads(bounds.read_text())
        raw["region"] = "other"
        bounds.write_text(json.dumps(raw))
        with patch.dict(os.environ, {"SOLAR_RASTER_JOB_BOUNDS": str(bounds),
                                    **task["source_environment"]}):
            with self.assertRaisesRegex(ValueError, "do not match"):
                make_task(workspace, "pilot", sys.executable, no_preview_server=True)
            with self.assertRaisesRegex(QueueError, "no-preview-server"):
                make_task(workspace, "pilot", sys.executable)

    def test_task_rejects_unexpected_or_relative_source_environment(self):
        from src.local_queue import validate_task

        task = self.task()
        for value in ({"LINZ_API_KEY": "secret"}, {"SOLAR_RASTER_PLAN_JOB": "relative"},
                      {"SOLAR_SURVEY_OVERRIDES": 7}, {"SOLAR_RASTER_PLAN_SHA256": "bad"}):
            task["source_environment"] = value
            with self.assertRaisesRegex(QueueError, "source environment"):
                validate_task(task)

    def test_failed_pipeline_and_workspace_conflict_are_reported(self):
        for name, conflict in (("bad-exit", False), ("conflict", True)):
            workspace = self.fixture(name, "return 7")
            if conflict:
                (workspace / "data/pipeline-workspace.lock").mkdir(parents=True)
            queue = self.directory / f"queue-{name}"
            with Coordinator(queue):
                client = QueueClient(queue)
                client.call("submit", task=self.task(workspace))
                process = self.start_worker(queue, name)
                output, _ = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 1, output)
                record = client.call("status")["tasks"][0]
                self.assertEqual(record["status"], "failed", output)
                self.assertIn("Workspace conflict" if conflict else "exited 7", record["error"])

    def test_changed_configuration_rejected_before_execution(self):
        workspace = self.fixture("changed")
        queue = self.directory / "queue"
        with Coordinator(queue):
            client = QueueClient(queue)
            client.call("submit", task=self.task(workspace))
            (workspace / "config.py").write_text("# changed\n")
            process = self.start_worker(queue, "changed")
            output, _ = process.communicate(timeout=10)
            record = client.call("status")["tasks"][0]
            self.assertEqual(record["status"], "failed", output)
            self.assertIn("changed after submission", record["error"])
            self.assertFalse((workspace / "started").exists())

    @unittest.skipUnless(os.name == "posix", "process-group lifetime check")
    def test_completed_leader_with_surviving_child_keeps_workspace_lock(self):
        workspace = self.fixture(
            "surviving-child",
            "import os\n"
            "if os.fork() == 0:\n"
            "    deadline = time.monotonic() + 15\n"
            "    while not Path('release-child').exists() and time.monotonic() < deadline:\n"
            "        time.sleep(0.05)\n"
            "    Path('child-stopped').touch()\n"
            "    os._exit(0)\n"
            "return 0")
        queue = self.directory / "survivor-queue"
        with Coordinator(queue):
            client = QueueClient(queue)
            client.call("submit", task=self.task(workspace))
            process = self.start_worker(queue, "survivor")
            try:
                output, _ = process.communicate(timeout=10)
                record = client.call("status")["tasks"][0]
                self.assertEqual(record["status"], "failed", output)
                self.assertIn("consumers may still be running", record["error"])
                lock = workspace / "data/pipeline-workspace.lock"
                self.assertTrue(lock.is_dir())
                self.assertIsInstance(json.loads((lock / "owner.json").read_text())["process_group"], int)
                client.call("submit", task=self.task(workspace))
                next_worker = self.start_worker(queue, "next")
                output, _ = next_worker.communicate(timeout=10)
                self.assertEqual(client.call("status")["tasks"][1]["status"], "failed", output)
                self.assertIn("Workspace conflict", client.call("status")["tasks"][1]["error"])
            finally:
                (workspace / "release-child").touch()
            self.wait_for(lambda: (workspace / "child-stopped").exists())

    @unittest.skipUnless(os.name == "posix", "process-group lifetime check")
    def test_inconclusive_group_check_retains_workspace_lock(self):
        workspace = self.fixture("inconclusive")
        task = self.task(workspace)
        with patch("tools.pipeline_queue.process_group_stopped", return_value=False):
            with self.assertRaisesRegex(QueueError, "consumers may still be running"):
                execute(task, "attempt", self.directory / "inconclusive.log",
                        unittest.mock.Mock(), "worker")
        owner = workspace / "data/pipeline-workspace.lock/owner.json"
        self.assertIsInstance(json.loads(owner.read_text())["process_group"], int)

    @unittest.skipUnless(os.name == "posix", "process-group lifetime check")
    def test_inconclusive_launch_retains_workspace_lock(self):
        workspace = self.fixture("failed-launch")
        task = self.task(workspace)
        with patch("tools.pipeline_queue.provenance", return_value=task["provenance"]), \
                patch("tools.pipeline_queue.subprocess.Popen", side_effect=OSError("launch failed")):
            with self.assertRaisesRegex(QueueError, "consumers may still be running") as error:
                execute(task, "attempt", self.directory / "failed-launch.log",
                        unittest.mock.Mock(), "worker")
        self.assertIn("launch failed", str(error.exception.__context__))
        self.assertTrue((workspace / "data/pipeline-workspace.lock/owner.json").is_file())

    @unittest.skipUnless(os.name == "posix", "process-group interruption check")
    def test_worker_interrupt_stops_child_and_records_failure(self):
        workspace = self.fixture("interrupt", "time.sleep(60)\nreturn 0")
        queue = self.directory / "queue"
        with Coordinator(queue):
            client = QueueClient(queue)
            client.call("submit", task=self.task(workspace))
            process = self.start_worker(queue, "interrupt")
            self.wait_for(lambda: (workspace / "started").exists())
            process.send_signal(signal.SIGTERM)
            output, _ = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 130, output)
            self.assertEqual(client.call("status")["tasks"][0]["status"], "failed")
            self.assertFalse((workspace / "data/pipeline-workspace.lock").exists())

    def test_coordinator_loss_stops_active_worker_without_false_completion(self):
        workspace = self.fixture("coordinator-loss", "time.sleep(60)\nreturn 0")
        queue = self.directory / "queue"
        with Coordinator(queue):
            client = QueueClient(queue)
            client.call("submit", task=self.task(workspace))
            process = self.start_worker(queue, "coordinator-loss")
            self.wait_for(lambda: (workspace / "started").exists())
        output, _ = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 2, output)
        self.assertFalse((workspace / "data/pipeline-workspace.lock").exists())
        state = json.loads((queue / "state.json").read_text())
        self.assertEqual(state["tasks"][0]["status"], "interrupted")

    def test_pipeline_pool_runs_task_and_keeps_numbered_logs(self):
        for include_imagery in (True, False):
            with self.subTest(include_imagery=include_imagery):
                workspace = self.fixture(
                    f"pool-{include_imagery}",
                    f"assert args.include_imagery is {include_imagery!r}\nreturn 0")
                args = argparse.Namespace(
                    python=sys.executable, workers=2,
                    queue_dir=str(self.directory / f"queue-{include_imagery}"),
                    port=None, no_open_browser=True, step_offset=0,
                    include_imagery=include_imagery)
                self.assertEqual(run_local(args, "pilot", workspace), 0)
                state = json.loads((Path(args.queue_dir) / "state.json").read_text())
                self.assertEqual(state["tasks"][0]["status"], "succeeded")
                self.assertIs(state["tasks"][0]["task"]["options"]["include_imagery"],
                              include_imagery)

    def test_imagery_option_defaults_for_legacy_tasks_and_rejects_non_booleans(self):
        from src.local_queue import validate_task
        task = self.task()
        task["options"].pop("include_imagery", None)
        self.assertIs(validate_task(task)["options"]["include_imagery"], True)
        for value in (None, 0, 1, "false"):
            task["options"]["include_imagery"] = value
            with self.subTest(value=value), self.assertRaisesRegex(QueueError, "include_imagery"):
                validate_task(task)

    def test_pipeline_default_routes_through_queue_and_rejects_bad_workers(self):
        from tools.pipeline import main
        with patch.object(sys, "argv", ["pipeline.py", "pilot"]), \
                patch("tools.pipeline_queue.run_local", return_value=0) as queued:
            self.assertEqual(main(), 0)
            self.assertEqual(queued.call_args.args[0].workers, 1)
            self.assertEqual(queued.call_args.args[1], "pilot")
        for arguments in (["--workers", "0"], ["--workers", "33"],
                          ["--mode", "cloud", "--workers", "2"]):
            with patch.object(sys, "argv", ["pipeline.py", *arguments]):
                with self.assertRaises(SystemExit) as result:
                    main()
                self.assertEqual(result.exception.code, 2)

    def test_existing_pipeline_preflight_failure_keeps_numbered_report(self):
        from tools.pipeline import _run_local
        args = argparse.Namespace(python=sys.executable, port=None,
                                  no_open_browser=True, step_offset=0)
        with patch.dict(os.environ), \
                patch("tools.pipeline.RUNS", self.directory / "runs"), \
                patch("tools.pipeline.DATA", self.directory / "data"), \
                patch("tools.pipeline._preflight", return_value=(False, "fixture missing input", {"name": "pilot"})):
            self.assertEqual(_run_local(args, "pilot"), 2)
        reports = list((self.directory / "runs").glob("*/report.md"))
        self.assertEqual(len(reports), 1)
        self.assertIn("fixture missing input", reports[0].read_text())
        self.assertIn("NOT RUN", reports[0].read_text())


if __name__ == "__main__":
    unittest.main()
