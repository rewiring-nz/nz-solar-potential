"""Cloud-free local queue, real worker-process and pipeline routing checks."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
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
from tools.pipeline_queue import PROVENANCE_FILES, make_task, run_local, stop_process


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
        stop_process(process)
        output, _ = process.communicate(timeout=10)
        self.assertIn("ready", output)
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
        workspace = self.fixture("pool")
        args = argparse.Namespace(python=sys.executable, workers=2, queue_dir=str(self.directory / "queue"),
                                  port=None, no_open_browser=True, step_offset=0)
        self.assertEqual(run_local(args, "pilot", workspace), 0)
        state = json.loads((Path(args.queue_dir) / "state.json").read_text())
        self.assertEqual(state["tasks"][0]["status"], "succeeded")

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
        with patch("tools.pipeline.RUNS", self.directory / "runs"), \
                patch("tools.pipeline.DATA", self.directory / "data"), \
                patch("tools.pipeline._preflight", return_value=(False, "fixture missing input", {"name": "pilot"})):
            self.assertEqual(_run_local(args, "pilot"), 2)
        reports = list((self.directory / "runs").glob("*/report.md"))
        self.assertEqual(len(reports), 1)
        self.assertIn("fixture missing input", reports[0].read_text())
        self.assertIn("NOT RUN", reports[0].read_text())


if __name__ == "__main__":
    unittest.main()
