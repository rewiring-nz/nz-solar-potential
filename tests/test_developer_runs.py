"""Focused lifecycle and dashboard tests for shared-VM developer runs."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import developer_runs


class DeveloperRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(
            os.environ, {"SOLAR_DEVELOPER_RUNS_ROOT": str(self.root / "registry")},
        )
        self.env.start()
        self.dashboard = patch.object(developer_runs, "_try_publish_dashboard")
        self.dashboard.start()
        self.addCleanup(self.dashboard.stop)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

    def _record(self, **values: object) -> dict[str, object]:
        record: dict[str, object] = {
            "run_id": "20261018T010101Z-00000001",
            "user": "alice",
            "regions": "frankton_arm_lake",
            "commit": "a" * 40,
            "status": "running",
            "stage": "build",
            "started_at": "2026-10-18T01:01:01+00:00",
            "updated_at": "2026-10-18T01:01:01+00:00",
            "pid": None,
            "checkout": str(self.root / "checkout"),
            "run_root": str(self.root / "run"),
            "log_path": str(self.root / "run.log"),
        }
        record.update(values)
        return record

    def _save(self, record: dict[str, object]) -> None:
        lock, records = developer_runs._locked_registry()
        records.append(record)
        developer_runs._save_registry(lock, records)

    def test_dashboard_snapshot_only_exposes_selected_public_fields(self) -> None:
        record = self._record(secret="must-not-leak", error="/home/private/path")
        snapshot = developer_runs._dashboard_snapshot([record])
        self.assertEqual(len(snapshot["runs"]), 1)
        row = snapshot["runs"][0]
        self.assertEqual(row["run_id"], record["run_id"])
        self.assertNotIn("checkout", row)
        self.assertNotIn("secret", row)
        self.assertNotIn("error", row)

    def test_dashboard_publishes_status_and_assets_outside_expiring_run_prefix(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(developer_runs.subprocess, "run", side_effect=fake_run):
            developer_runs._publish_dashboard([])
        destinations = [command[-1] for command in commands]
        self.assertIn(
            "gs://rewiring-solar-previews/run-dashboard/status.json", destinations,
        )
        self.assertIn(
            "gs://rewiring-solar-previews/run-dashboard/index.html", destinations,
        )
        self.assertIn(
            "gs://rewiring-solar-previews/run-dashboard/developer-runs.js", destinations,
        )

    def test_public_log_excerpt_redacts_secret_assignments(self) -> None:
        log_path = self.root / "run.log"
        log_path.write_text(
            "LINZ_API_KEY=private-value\nAuthorization: Bearer private-token\n",
            encoding="utf-8",
        )
        record = self._record(log_path=str(log_path))
        uploaded: list[str] = []

        def fake_run(command, **_kwargs):
            uploaded.append(Path(command[-2]).read_text(encoding="utf-8"))
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(developer_runs.subprocess, "run", side_effect=fake_run):
            developer_runs._publish_log(record)
        self.assertIn("LINZ_API_KEY=[redacted]", uploaded[0])
        self.assertNotIn("private-value", uploaded[0])
        self.assertNotIn("private-token", uploaded[0])

    def test_cleanup_removes_only_terminal_run_worktrees_after_retention(self) -> None:
        workspaces = self.root / "workspaces"
        run_id = "20261018T010101Z-00000001"
        run_root = workspaces / "alice" / "runs" / run_id
        checkout = run_root / "checkout"
        checkout.mkdir(parents=True)
        finished = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        self._save(self._record(
            status="succeeded", finished_at=finished,
            run_root=str(run_root), checkout=str(checkout),
        ))
        with (
            patch.dict(os.environ, {"SOLAR_DEVELOPER_WORKSPACES_ROOT": str(workspaces)}),
            patch.object(developer_runs.subprocess, "run") as git_remove,
        ):
            self.assertEqual(developer_runs.cleanup_runs(), 1)
        self.assertFalse(run_root.exists())
        self.assertEqual(git_remove.call_args.args[0][:4], [
            "git", "worktree", "remove", "--force",
        ])

    def test_start_rejects_second_run_for_developer_by_default(self) -> None:
        self._save(self._record(
            pid=os.getpid(),
            pid_start_ticks=1,
        ))
        with patch.object(developer_runs, "_proc_start_ticks", return_value=1):
            with self.assertRaisesRegex(RuntimeError, "already has an active run"):
                developer_runs.start(
                    "alice", "20261018T010102Z-00000002", "frankton_arm_lake",
                    "b" * 40, self.root / "checkout-2", self.root / "run-2",
                )

    def test_parallel_opt_in_allows_second_run_and_registers_supervisor(self) -> None:
        self._save(self._record(
            pid=os.getpid(),
            pid_start_ticks=1,
        ))
        checkout = self.root / "checkout-2"
        checkout.mkdir()
        fake_process = SimpleNamespace(pid=12345)
        with (
            patch.object(developer_runs, "_proc_start_ticks", return_value=1),
            patch.object(developer_runs.subprocess, "Popen", return_value=fake_process),
        ):
            result = developer_runs.start(
                "alice", "20261018T010102Z-00000002", "frankton_arm_lake",
                "b" * 40, checkout, self.root / "run-2", parallel=True,
            )
        self.assertEqual(result, 0)
        records = json.loads(developer_runs._registry_path().read_text())
        created = next(item for item in records if item["run_id"].endswith("00000002"))
        self.assertEqual(created["status"], "running")
        self.assertEqual(created["pid"], 12345)

    def test_reconcile_marks_dead_supervisor_interrupted(self) -> None:
        record = self._record(pid=99999999)
        changed = developer_runs._reconcile([record])
        self.assertTrue(changed)
        self.assertEqual(record["status"], "interrupted")
        self.assertIn("no longer running", record["error"])

    def test_cancel_request_stops_only_the_run_stage_process_group(self) -> None:
        run_id = "20261018T010101Z-00000001"
        self._save(self._record(run_id=run_id))
        request = self.root / "registry" / "cancel" / f"{run_id}.request"
        request.parent.mkdir(parents=True)
        request.write_text("cancel", encoding="utf-8")
        log = self.root / "run.log"
        with self.assertRaisesRegex(InterruptedError, "run cancelled"):
            developer_runs._run_command(
                run_id, [sys.executable, "-c", "import time; time.sleep(30)"],
                self.root, log,
            )
        self.assertTrue(log.is_file())

    def test_active_run_listing_is_owner_scoped(self) -> None:
        self._save(self._record())
        self._save(self._record(
            run_id="20261018T010102Z-00000002", user="bob",
        ))
        output = io.StringIO()
        with patch("sys.stdout", output):
            developer_runs.list_runs("alice")
        rows = json.loads(output.getvalue())
        self.assertEqual([row["user"] for row in rows], ["alice"])


if __name__ == "__main__":
    unittest.main()
