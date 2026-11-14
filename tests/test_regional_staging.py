"""Staging: frozen, checksummed copies of data/out/<region>, without cloud or builds."""

import hashlib
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

from tools.artifact_common import ArtifactError, inventory
from tools.pipeline_webui.artifacts import Artifacts
from tools.regional_staging import StagingStore


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve() / "data"
        self.source = self.data / "out" / "pilot"
        (self.source / "detail").mkdir(parents=True)
        (self.source / "summary.json").write_text('{"region":"pilot"}')
        (self.source / "detail" / "one.json").write_text('{"kwh":12}')
        self.store = StagingStore(self.data)

    def test_independent_copy_and_unknown_provenance(self):
        staged = self.store.stage("pilot")
        identifier = staged["staging_id"]
        self.assertEqual(staged["schema_version"], 2)
        self.assertIsNone(staged["built_at"])
        self.assertIsNone(staged["producer_ref"])
        self.assertEqual(len(staged["artifacts"]), 2)
        copied = self.store.artifacts_path(identifier) / "detail" / "one.json"
        self.assertNotEqual((self.source / "detail" / "one.json").stat().st_ino, copied.stat().st_ino)
        shutil.rmtree(self.data / "out")
        self.assertEqual(self.store.verify(identifier), staged)
        self.assertEqual([r["region_id"] for r in self.store.list()], ["pilot"])

    def test_restaging_makes_a_new_copy(self):
        first = self.store.stage("pilot")["staging_id"]
        second = self.store.stage("pilot")["staging_id"]
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.store.list()), 2)

    def test_repeated_id_does_not_overwrite(self):
        identifier = "a" * 32
        original = self.store.stage("pilot", identifier)
        with self.assertRaisesRegex(ArtifactError, "overwritten"):
            self.store.stage("pilot", identifier)
        self.assertEqual(self.store.verify(identifier), original)

    def test_tampering_is_detected(self):
        for change in ("modify", "remove", "add", "link", "outer", "manifest"):
            with self.subTest(change=change):
                identifier = self.store.stage("pilot")["staging_id"]
                directory = self.store.root / identifier
                artifact = directory / "artifacts" / "detail" / "one.json"
                if change == "modify":
                    artifact.write_text("corrupt")
                elif change == "remove":
                    artifact.unlink()
                elif change == "add":
                    (artifact.parent / "extra").write_text("extra")
                elif change == "link":
                    artifact.unlink()
                    artifact.symlink_to(self.source / "detail" / "one.json")
                elif change == "outer":
                    (directory / "extra").write_text("extra")
                else:
                    path = directory / "manifest.json"
                    manifest = json.loads(path.read_text())
                    manifest["artifacts"][0]["sha256"] = "0" * 64
                    path.write_text(json.dumps(manifest))
                with self.assertRaises(ArtifactError):
                    self.store.verify(identifier)

    def test_failed_or_interrupted_copy_never_completes_and_releases_locks(self):
        for error in (OSError("disk full"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with patch("tools.regional_staging.shutil.copyfile", side_effect=error):
                    with self.assertRaises(KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else ArtifactError):
                        self.store.stage("pilot")
                self.assertEqual(self.store.list(), [])
                self.assertEqual(self.store.staging_regions(), set())
                self.assertFalse((self.data / ".artifact-lifecycle-lock").exists())
        self.assertEqual(len(list(self.store.root.glob(".incomplete-*"))), 2)

    def test_rebuild_during_copy_is_rejected(self):
        original = shutil.copyfile

        def copy_and_change(source, destination):
            original(source, destination)
            (self.source / "new.json").write_text("{}")

        with patch("tools.regional_staging.shutil.copyfile", side_effect=copy_and_change):
            with self.assertRaisesRegex(ArtifactError, "changed while staging"):
                self.store.stage("pilot")
        self.assertEqual(self.store.list(), [])

    def test_region_lock_is_visible_and_exclusive(self):
        original = shutil.copyfile
        seen = []

        def copy_while_watching(source, destination):
            seen.append(StagingStore(self.data).staging_regions())
            with self.assertRaisesRegex(ArtifactError, "already being staged"):
                StagingStore(self.data).stage("pilot")
            original(source, destination)

        with patch("tools.regional_staging.shutil.copyfile", side_effect=copy_while_watching):
            self.store.stage("pilot")
        self.assertIn({"pilot"}, seen)
        self.assertEqual(self.store.staging_regions(), set())

    def test_does_not_take_the_build_workspace_lock(self):
        lock = self.data / "pipeline-workspace.lock"
        lock.mkdir()
        self.store.stage("pilot")
        self.assertTrue(lock.exists())

    def test_unbuilt_cells_bad_ids_links_and_overlap_rejected(self):
        with self.assertRaises(ArtifactError):
            self.store.stage("../pilot")
        with self.assertRaisesRegex(ArtifactError, "Grid cells"):
            self.store.stage("auckland/auckland/ponsonby/grid-" + "0" * 16)
        with self.assertRaisesRegex(ArtifactError, "summary"):
            self.store.stage("another")
        with self.assertRaises(ArtifactError):
            self.store.verify("../pilot")
        for root in (self.data / "out", self.source / "staging", self.data):
            with self.assertRaises(ArtifactError):
                StagingStore(self.data, root)
        (self.source / "link").symlink_to(self.source / "summary.json")
        with self.assertRaisesRegex(ArtifactError, "Symlinks"):
            self.store.stage("pilot")

    def test_summary_identity_checked(self):
        (self.source / "summary.json").write_text('{"region":"another"}')
        with self.assertRaisesRegex(ArtifactError, "does not match"):
            self.store.stage("pilot")

    def test_schema_evolution(self):
        manifest = self.store.stage("pilot")
        identifier = manifest["staging_id"]
        path = self.store.root / identifier / "manifest.json"
        manifest["future_optional"] = {"anything": True}
        path.write_text(json.dumps(manifest))
        self.store.verify(identifier)
        manifest["schema_version"] = 99
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ArtifactError, "Unsupported staging schema"):
            self.store.verify(identifier)

    def test_custom_root(self):
        store = StagingStore(self.data, self.data.parent / "custom-staging")
        manifest = store.stage("pilot")
        self.assertEqual(store.verify(manifest["staging_id"]), manifest)

    def _legacy_copy(self, identifier="b" * 32):
        """A schema 1 staged copy: a captured snapshot wrapped by staging."""
        directory = self.store.root / identifier
        inner = directory / "snapshot"
        shutil.copytree(self.source, inner / "artifacts")
        snapshot = {"schema_version": 1, "snapshot_id": identifier, "region_id": "pilot",
                    "created_at": "2026-01-01T00:00:00Z", "built_at": None, "producer_ref": "abc",
                    "summary": {"region": "pilot"}, "artifacts": inventory(inner / "artifacts")}
        (inner / "manifest.json").write_text(json.dumps(snapshot))
        digest = hashlib.sha256((inner / "manifest.json").read_bytes()).hexdigest()
        (directory / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "staging_id": identifier, "source_snapshot_id": identifier,
            "source_manifest_sha256": digest, "region_id": "pilot",
            "staged_at": "2026-01-02T00:00:00Z", "built_at": None}))
        return identifier, inner

    def test_legacy_copies_remain_readable_and_checked(self):
        identifier, inner = self._legacy_copy()
        verified = self.store.verify(identifier)
        self.assertEqual(verified["producer_ref"], "abc")
        self.assertEqual(len(verified["artifacts"]), 2)
        self.assertEqual(self.store.artifacts_path(identifier), inner / "artifacts")
        self.assertEqual(self.store.list()[0]["schema_version"], 1)
        (inner / "artifacts" / "detail" / "one.json").write_text("corrupt")
        with self.assertRaises(ArtifactError):
            self.store.verify(identifier)

    def test_background_operations_are_exclusive_and_persistent(self):
        manager = Artifacts(self.data)
        entered, release = threading.Event(), threading.Event()
        original = manager.staging.stage

        def stage(region):
            entered.set()
            release.wait(5)
            return original(region)

        with patch.object(manager.staging, "stage", side_effect=stage):
            operation = manager.start("stage", "pilot")
            try:
                self.assertTrue(entered.wait(2))
                self.assertIn("pilot", manager.staging_regions())
                with self.assertRaisesRegex(ArtifactError, "Another"):
                    manager.start("stage", "pilot")
                # Another panel must not mark a live operation interrupted.
                observer = Artifacts(self.data)
                self.assertEqual(observer.get(operation["operation_id"])["status"], "running")
            finally:
                release.set()
            for _ in range(200):
                result = manager.get(operation["operation_id"])
                if result["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["region_id"], "pilot")
        self.assertEqual(manager.staging_regions(), set())
        restarted = Artifacts(self.data)
        self.assertEqual(restarted.get(operation["operation_id"])["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
