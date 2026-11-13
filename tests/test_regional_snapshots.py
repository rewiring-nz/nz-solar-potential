"""Cloud-free regional snapshot integrity and isolation tests."""

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.regional_snapshots import SnapshotError, SnapshotStore
from tools.pipeline_webui.snapshots import Snapshots


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.source = self.data / "out" / "pilot"
        self.source.mkdir(parents=True)
        (self.source / "summary.json").write_text(json.dumps({"region": "pilot", "n": 2}))
        (self.source / "detail").mkdir()
        (self.source / "detail" / "one.json").write_text('{"kwh":12}')
        self.store = SnapshotStore(self.data)

    def test_isolated_copy_and_missing_provenance(self):
        manifest = self.store.capture("pilot")
        self.assertIsNone(manifest["built_at"])
        self.assertIsNone(manifest["producer_ref"])
        self.assertEqual(len(manifest["artifacts"]), 2)
        (self.source / "detail" / "one.json").write_text("changed")
        shutil.rmtree(self.source)
        self.assertEqual(self.store.verify(manifest["snapshot_id"]), manifest)
        self.assertEqual(len(self.store.list()), 1)

    def test_corruption_removal_and_addition_fail_verification(self):
        for change in ("modify", "remove", "add", "outer"):
            with self.subTest(change=change):
                manifest = self.store.capture("pilot")
                directory = self.store.root / manifest["snapshot_id"]
                artifact = directory / "artifacts" / "detail" / "one.json"
                if change == "modify":
                    artifact.write_text("corrupt")
                elif change == "remove":
                    artifact.unlink()
                elif change == "add":
                    (artifact.parent / "extra").write_text("extra")
                else:
                    (directory / "extra").write_text("extra")
                with self.assertRaises(SnapshotError):
                    self.store.verify(manifest["snapshot_id"])

    def test_repeated_id_does_not_overwrite(self):
        identifier = "a" * 32
        original = self.store.capture("pilot", identifier)
        with self.assertRaisesRegex(SnapshotError, "overwritten"):
            self.store.capture("pilot", identifier)
        self.assertEqual(self.store.verify(identifier), original)

    def test_workspace_lock_blocks_capture(self):
        lock = self.data / "pipeline-workspace.lock"
        lock.mkdir()
        (lock / "owner.json").write_text('{"pid":1}')
        with self.assertRaisesRegex(SnapshotError, "busy"):
            self.store.capture("pilot")
        self.assertTrue((lock / "owner.json").exists())

    def test_copy_failure_is_not_completed_and_releases_lock(self):
        with patch("tools.regional_snapshots.shutil.copyfile", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(SnapshotError, "disk full"):
                self.store.capture("pilot")
        self.assertEqual(self.store.list(), [])
        self.assertFalse((self.data / "pipeline-workspace.lock").exists())
        self.assertEqual(len(list(self.store.root.glob(".incomplete-*"))), 1)

    def test_source_change_during_copy_is_rejected(self):
        original = shutil.copyfile

        def copy_and_change(source, destination):
            original(source, destination)
            (self.source / "new.json").write_text("{}")

        with patch("tools.regional_snapshots.shutil.copyfile", side_effect=copy_and_change):
            with self.assertRaisesRegex(SnapshotError, "Source changed"):
                self.store.capture("pilot")
        self.assertEqual(self.store.list(), [])

    def test_links_invalid_regions_and_storage_overlap_rejected(self):
        (self.source / "link").symlink_to(self.source / "summary.json")
        with self.assertRaisesRegex(SnapshotError, "Symlinks"):
            self.store.capture("pilot")
        with self.assertRaises(SnapshotError):
            self.store.capture("../pilot")
        with self.assertRaises(SnapshotError):
            SnapshotStore(self.data, self.source / "snapshots")
        with self.assertRaises(SnapshotError):
            SnapshotStore(self.data, self.data)
        with self.assertRaises(SnapshotError):
            self.store.verify("../pilot")

    def test_summary_required_and_identity_checked(self):
        (self.source / "summary.json").unlink()
        with self.assertRaisesRegex(SnapshotError, "summary"):
            self.store.capture("pilot")
        (self.source / "summary.json").write_text('{"region":"another"}')
        with self.assertRaisesRegex(SnapshotError, "does not match"):
            self.store.capture("pilot")

    def test_schema_evolution(self):
        manifest = self.store.capture("pilot")
        path = self.store.root / manifest["snapshot_id"] / "manifest.json"
        manifest["future_optional_field"] = {"anything": True}
        path.write_text(json.dumps(manifest))
        self.store.verify(manifest["snapshot_id"])
        manifest["schema_version"] = 99
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SnapshotError, "Unsupported"):
            self.store.verify(manifest["snapshot_id"])

    def test_custom_root(self):
        store = SnapshotStore(self.data, Path(self.temp.name) / "independent")
        manifest = store.capture("pilot")
        self.assertEqual(store.verify(manifest["snapshot_id"]), manifest)

    def test_interruption_never_completes_snapshot(self):
        with patch("tools.regional_snapshots.shutil.copyfile", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.store.capture("pilot")
        self.assertEqual(self.store.list(), [])
        self.assertFalse((self.data / "pipeline-workspace.lock").exists())

    def test_directory_and_snapshot_links_are_rejected(self):
        (self.source / "linked-directory").symlink_to(self.source / "detail", target_is_directory=True)
        with self.assertRaisesRegex(SnapshotError, "Symlinks"):
            self.store.capture("pilot")
        (self.source / "linked-directory").unlink()
        manifest = self.store.capture("pilot")
        artifacts = self.store.root / manifest["snapshot_id"] / "artifacts"
        shutil.rmtree(artifacts)
        artifacts.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(SnapshotError, "Symlinks"):
            self.store.verify(manifest["snapshot_id"])

    def test_background_operations_are_exclusive_and_persistent(self):
        manager = Snapshots(self.data)
        entered, release = threading.Event(), threading.Event()
        original = manager.store.capture

        def capture(region):
            entered.set()
            release.wait(5)
            return original(region)

        with patch.object(manager.store, "capture", side_effect=capture):
            operation = manager.start("capture", "pilot")
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaisesRegex(SnapshotError, "Another"):
                    manager.start("capture", "pilot")
                # Another panel must not mark a live operation interrupted.
                observer = Snapshots(self.data)
                self.assertEqual(observer.get(operation["operation_id"])["status"], "running")
            finally:
                release.set()
            for _ in range(200):
                result = manager.get(operation["operation_id"])
                if result["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(result["status"], "succeeded")
        restarted = Snapshots(self.data)
        self.assertEqual(restarted.get(operation["operation_id"])["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
