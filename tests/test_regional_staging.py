"""Local staging uses verified independent copies, without cloud or builds."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.regional_snapshots import SnapshotError, SnapshotStore
from tools.regional_staging import StagingStore


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve() / "data"
        source = self.data / "out" / "pilot"
        source.mkdir(parents=True)
        (source / "summary.json").write_text('{"region":"pilot"}')
        (source / "detail.json").write_text('{"kwh":12}')
        self.snapshots = SnapshotStore(self.data)
        self.snapshot = self.snapshots.capture("pilot")
        self.identifier = self.snapshot["snapshot_id"]
        self.source = self.snapshots.root / self.identifier
        self.store = StagingStore(self.snapshots)

    def test_independent_copy_preserves_manifest_and_unknown_provenance(self):
        staged = self.store.stage(self.identifier)
        self.assertIsNone(staged["built_at"])
        copied = self.store.root / self.identifier / "snapshot"
        self.assertEqual((self.source / "manifest.json").read_bytes(),
                         (copied / "manifest.json").read_bytes())
        self.assertNotEqual((self.source / "artifacts" / "detail.json").stat().st_ino,
                            (copied / "artifacts" / "detail.json").stat().st_ino)
        shutil.rmtree(self.data / "out")
        shutil.rmtree(self.snapshots.root)
        self.assertEqual(self.store.verify(self.identifier), staged)

    def test_repeat_is_idempotent_and_does_not_copy(self):
        staged = self.store.stage(self.identifier)
        with patch("tools.regional_staging.shutil.copytree", side_effect=AssertionError("copied twice")):
            self.assertEqual(self.store.stage(self.identifier), staged)
        self.assertEqual(len(self.store.list()), 1)

    def test_corrupt_source_is_refused(self):
        (self.source / "artifacts" / "detail.json").write_text("corrupt")
        with self.assertRaises(SnapshotError):
            self.store.stage(self.identifier)
        self.assertEqual(self.store.list(), [])

    def test_corrupt_staged_copy_never_overwritten(self):
        self.store.stage(self.identifier)
        artifact = self.store.root / self.identifier / "snapshot" / "artifacts" / "detail.json"
        artifact.write_text("corrupt")
        with self.assertRaises(SnapshotError):
            self.store.verify(self.identifier)
        with self.assertRaises(SnapshotError):
            self.store.stage(self.identifier)
        self.assertEqual(artifact.read_text(), "corrupt")

    def test_interruption_and_failed_copy_are_incomplete(self):
        for error in (OSError("disk full"), KeyboardInterrupt()):
            identifier = self.snapshots.capture("pilot")["snapshot_id"]
            with patch("tools.regional_staging.shutil.copytree", side_effect=error):
                with self.assertRaises(type(error) if isinstance(error, KeyboardInterrupt) else SnapshotError):
                    self.store.stage(identifier)
            self.assertEqual(self.store.list(), [])
            with self.assertRaisesRegex(SnapshotError, "incomplete"):
                self.store.stage(identifier)

    def test_source_change_during_copy_rejected(self):
        original = shutil.copytree

        def copy_then_change(source, destination, *args, **kwargs):
            result = original(source, destination, *args, **kwargs)
            if Path(source) == self.source:
                (self.source / "manifest.json").write_text("{}")
            return result

        with patch("tools.regional_staging.shutil.copytree", side_effect=copy_then_change):
            with self.assertRaises(SnapshotError):
                self.store.stage(self.identifier)
        self.assertEqual(self.store.list(), [])

    def test_root_overlap_and_bad_ids_rejected(self):
        for root in (self.snapshots.root, self.source, self.data, self.data / "out" / "staging"):
            with self.assertRaises(SnapshotError):
                StagingStore(self.snapshots, root)
        with self.assertRaises(SnapshotError):
            self.store.stage("../outside")
        with self.assertRaises(SnapshotError):
            self.store.verify("../outside")

    def test_staged_metadata_tampering_and_unexpected_files(self):
        self.store.stage(self.identifier)
        directory = self.store.root / self.identifier
        copied_manifest = directory / "snapshot" / "manifest.json"
        manifest = json.loads(copied_manifest.read_text())
        manifest["built_at"] = "different"
        copied_manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SnapshotError, "recorded identity"):
            self.store.verify(self.identifier)

    def test_custom_root(self):
        store = StagingStore(self.snapshots, self.data.parent / "custom-staging")
        manifest = store.stage(self.identifier)
        self.assertEqual(store.verify(self.identifier), manifest)

    def test_links_and_added_or_removed_files_are_rejected(self):
        for change in ("link", "add", "remove", "outer"):
            with self.subTest(change=change):
                identifier = self.snapshots.capture("pilot")["snapshot_id"]
                self.store.stage(identifier)
                directory = self.store.root / identifier
                artifact = directory / "snapshot" / "artifacts" / "detail.json"
                if change == "link":
                    artifact.unlink()
                    artifact.symlink_to(self.source / "artifacts" / "detail.json")
                elif change == "add":
                    (artifact.parent / "extra.json").write_text("{}")
                elif change == "remove":
                    artifact.unlink()
                else:
                    (directory / "extra").write_text("extra")
                with self.assertRaises(SnapshotError):
                    self.store.verify(identifier)

    def test_changed_source_metadata_does_not_replace_staged_copy(self):
        original = self.store.stage(self.identifier)
        path = self.source / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["future_optional"] = True
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SnapshotError, "metadata changed"):
            self.store.stage(self.identifier)
        self.assertEqual(self.store.verify(self.identifier), original)

    def test_second_writer_cannot_copy_same_snapshot(self):
        original = shutil.copytree
        attempted = False

        def copy_with_competing_writer(source, destination, *args, **kwargs):
            nonlocal attempted
            if not attempted:
                attempted = True
                with self.assertRaisesRegex(SnapshotError, "busy"):
                    StagingStore(self.snapshots).stage(self.identifier)
            return original(source, destination, *args, **kwargs)

        with patch("tools.regional_staging.shutil.copytree", side_effect=copy_with_competing_writer):
            staged = self.store.stage(self.identifier)
        self.assertTrue(attempted)
        self.assertEqual(self.store.verify(self.identifier), staged)

    def test_distinct_regions_remain_independent(self):
        self.store.stage(self.identifier)
        source = self.data / "out" / "another"
        source.mkdir()
        (source / "summary.json").write_text('{"region":"another","sources":["different"]}')
        other = self.snapshots.capture("another")
        self.store.stage(other["snapshot_id"])
        self.assertEqual(len(self.store.list()), 2)
        self.assertEqual(self.store.verify(self.identifier)["region_id"], "pilot")
        self.assertEqual(self.store.verify(other["snapshot_id"])["region_id"], "another")

    def test_staging_schema_evolution(self):
        manifest = self.store.stage(self.identifier)
        path = self.store.root / self.identifier / "manifest.json"
        manifest["future_optional"] = {"anything": True}
        path.write_text(json.dumps(manifest))
        self.store.verify(self.identifier)
        manifest["schema_version"] = 2
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SnapshotError, "Unsupported staging schema"):
            self.store.verify(self.identifier)


if __name__ == "__main__":
    unittest.main()
