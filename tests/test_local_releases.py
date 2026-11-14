"""Cloud-free lifecycle, concurrent updates, integrity and retention tests."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.local_releases import AssemblyError, CORE_FILES, POLICY_KEYS, LocalReleases, SnapshotError
from tools.regional_snapshots import SnapshotStore
from tools.regional_staging import StagingStore
from src.output_contract import CONTRACT_VERSION, CELL_REQUIRED


POLICY = {key: 1 for key in POLICY_KEYS}


def emitted(data: Path, region: str, building: str, n: int = 1, real_tiles: bool = False) -> Path:
    directory = data / "out" / region
    directory.mkdir(parents=True, exist_ok=True)
    summary = {
        "region": region, "built_at": "2026-11-01T00:00:00Z", "git": "fixture",
        "n": n, "n_est": n, "panel_count": n * 2, "kwp": n, "kwh": n * 1200,
        "ladder": {building if i == 0 else f"{building}-{i}": [2, 1200] for i in range(n)}, "by_type": {},
        "centroid": [168.66, -45.03], "assumptions": {"panel_rated_power_w": 500},
        "model": {"geometry": ["abcdef"], "yield": "123456"}, "contract": CONTRACT_VERSION,
        "workings": {}, "elevation": "lidar",
    }
    (directory / "summary.json").write_text(json.dumps(summary))
    for name in ("sun.json",):
        (directory / name).write_text("{}")
    (directory / "addresses.json").write_text("[]")
    cell = {key: 0 for key in CELL_REQUIRED}
    cell.update(n=n, n_est=n, fitted_kwp=n, fitted_kwh=n * 1200, panel_count=n * 2, area=100)
    (directory / "cells.json").write_text(json.dumps({"16/63471/41970/home": cell}))
    feature = {"type": "Feature", "properties": {"building_id": building},
               "geometry": {"type": "Polygon", "coordinates": [[
                   [168.66, -45.03], [168.6601, -45.03], [168.6601, -45.0301],
                   [168.66, -45.0301], [168.66, -45.03]]]}}
    (directory / "solar_potential.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": [feature]}))
    detail = directory / "detail/13/7933"
    detail.mkdir(parents=True, exist_ok=True)
    (detail / "5246.json").write_text(json.dumps({building: {"mv": "gabcdef-y123456"}}))
    for name, layer in (("buildings.pmtiles", "buildings"), ("panel_layouts.pmtiles", "layout")):
        if real_tiles:
            if layer == "layout":
                feature["properties"].update(kind="no_estimate", btype="home")
            source = directory / "fixture.geojson"
            source.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
            subprocess.run(["tippecanoe", "-q", "-f", "-Z", "13", "-z", "14",
                            "-l", layer, "-o", str(directory / name), str(source)], check=True)
            source.unlink()
        else:
            (directory / name).write_bytes(b"fixture-pmtiles")
    return directory


def fixture_assembler(regions, inputs, destination, log):
    log.write_text("fixture assembler; no processing\n")
    for name in CORE_FILES:
        (destination / name).write_text(json.dumps({"regions": regions}))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve() / "data"
        self.snapshots = SnapshotStore(self.data)
        self.staging = StagingStore(self.snapshots)
        self.store = LocalReleases(self.staging, assembler=fixture_assembler)

    def stage(self, region="pilot", building="1", n=1):
        emitted(self.data, region, building, n)
        identifier = self.snapshots.capture(region)["snapshot_id"]
        self.staging.stage(identifier)
        return identifier

    def candidate(self):
        return self.store.create([self.stage()], 0)["release_id"]

    def deploy(self, identifier, generation=0):
        self.store.set_policy(POLICY)
        self.store.approve(identifier, generation, "Inspected fixture and unknown provenance")
        return self.store.activate(identifier, generation)

    def test_isolation_and_no_activation_during_assembly(self):
        identifier = self.candidate()
        self.assertIsNone(self.store.state()["active"])
        manifest = self.store.verify(identifier)
        shutil.rmtree(self.data / "out")
        shutil.rmtree(self.staging.root)
        shutil.rmtree(self.snapshots.root)
        self.assertEqual(self.store.verify(identifier), manifest)
        config = (self.store.path(identifier) / "site/site-config.js").read_text()
        self.assertIn('dataBase: "./"', config)
        self.assertNotIn('dataBase: "https://', config)

    def test_activate_preserve_regions_compare_and_rollback(self):
        a, b = self.stage(), self.stage("other", "2")
        first = self.store.create([a, b], 0)["release_id"]
        self.deploy(first)
        replacement = self.stage(n=2)
        second = self.store.create([replacement], 1)["release_id"]
        self.assertEqual(self.store.verify(second)["region_builds"]["other"]["snapshot_id"], b)
        comparison = self.store.compare(second)
        self.assertFalse(comparison["automatic_eligible"])
        self.assertTrue(any(r["status"] == "unknown" for r in comparison["metrics"]))
        self.store.approve(second, 1, "Checked updated roof data")
        self.store.activate(second, 1)
        self.assertEqual(self.store.state()["active"], second)
        self.assertEqual(self.store.rollback(2)["active"], first)
        self.assertEqual(self.store.state()["generation"], 3)

    def test_unknown_metadata_requires_reason_and_policy_is_explicit(self):
        identifier = self.candidate()
        with self.assertRaisesRegex(SnapshotError, "review reason"):
            self.store.approve(identifier, 0)
        self.store.approve(identifier, 0, "Inspected")
        with self.assertRaisesRegex(SnapshotError, "retention"):
            self.store.activate(identifier, 0)
        with self.assertRaises(SnapshotError):
            self.store.set_policy({"rollback_hours": 1})
        with self.assertRaises(SnapshotError):
            self.store.set_policy(dict(POLICY, rollback_hours=0))
        with self.assertRaises(SnapshotError):
            self.store.cleanup()

    def test_concurrent_baseline_conflict_never_loses_regions(self):
        first = self.candidate()
        second = self.store.create([self.stage("other", "2")], 0)["release_id"]
        self.store.approve(second, 0, "Inspected")
        self.deploy(first)
        with self.assertRaisesRegex(SnapshotError, "changed"):
            self.store.activate(second, 0)
        self.assertTrue(self.store.compare(second)["stale"])
        with self.assertRaisesRegex(SnapshotError, "stale"):
            self.store.approve(second, 1, "Inspected")
        refreshed = self.store.create([self.stage("other", "2")], 1)
        self.assertEqual(set(refreshed["region_builds"]), {"pilot", "other"})

    def test_tampering_blocks_approval_and_activation(self):
        identifier = self.candidate()
        self.store.approve(identifier, 0, "Inspected")
        self.store.set_policy(POLICY)
        artifact = self.store.path(identifier) / "site/data/buildings.pmtiles"
        artifact.write_bytes(b"corruption")
        with self.assertRaisesRegex(SnapshotError, "integrity"):
            self.store.activate(identifier, 0)
        self.assertIsNone(self.store.state()["active"])

    def test_manifest_changes_invalidate_approval(self):
        identifier = self.candidate()
        self.store.approve(identifier, 0, "Inspected")
        self.store.set_policy(POLICY)
        path = self.store.path(identifier) / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["extra"] = True
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SnapshotError, "exact candidate"):
            self.store.activate(identifier, 0)

    def test_partial_duplicate_overlapping_and_incompatible_rejected(self):
        partial = "council/town/suburb/grid-0123456789abcdef"
        identifier = self.stage(partial)
        with self.assertRaisesRegex(SnapshotError, "Partial"):
            self.store.create([identifier], 0)
        a = self.stage()
        with self.assertRaisesRegex(SnapshotError, "only one"):
            self.store.create([a, a], 0)
        b = self.stage("other", "1")
        with self.assertRaisesRegex(SnapshotError, "ownership"):
            self.store.create([a, b], 0)
        source = emitted(self.data, "wrong", "3")
        summary = json.loads((source / "summary.json").read_text())
        summary["contract"] = 999
        (source / "summary.json").write_text(json.dumps(summary))
        wrong = self.snapshots.capture("wrong")["snapshot_id"]
        self.staging.stage(wrong)
        with self.assertRaisesRegex(SnapshotError, "incompatible"):
            self.store.create([wrong], 0)

    def test_failed_assembly_never_ready_or_active(self):
        def fail(*args):
            raise SnapshotError("fixture failure")
        self.store.assembler = fail
        with self.assertRaisesRegex(SnapshotError, "fixture"):
            self.candidate()
        self.assertEqual(self.store.list()["releases"], [])
        self.assertIsNone(self.store.state()["active"])

    def test_rollback_expiry_and_cleanup_grace_protection(self):
        with patch("tools.local_releases.now", return_value="2026-12-01T00:00:00Z"):
            first = self.candidate()
            self.deploy(first)
            second = self.store.create([self.stage(n=2)], 1)["release_id"]
            self.store.approve(second, 1, "Inspected")
            self.store.activate(second, 1)
            self.assertEqual(self.store.cleanup()["eligible"], [])
        with patch("tools.local_releases.now", return_value="2026-12-02T00:00:00Z"):
            with self.assertRaisesRegex(SnapshotError, "unexpired"):
                self.store.rollback(2)
            preview = self.store.cleanup()
            self.assertTrue(any(item["id"] == first for item in preview["eligible"]))
            self.assertFalse(self.store.cleanup(True)["deleted"])
        with patch("tools.local_releases.now", return_value="2026-12-03T00:00:00Z"):
            result = self.store.cleanup(True)
            self.assertIn("release:" + first, result["deleted"])
            self.assertTrue(self.store.path(second).is_dir())
            self.assertFalse(self.store.path(first).exists())
            self.assertTrue(list((self.store.root / "purged").glob("*.json")))
            self.store.verify(second)

    def test_process_lock_blocks_other_writers_and_cleanup(self):
        self.store.root.mkdir(parents=True)
        with self.store.locked():
            with self.assertRaisesRegex(SnapshotError, "busy"):
                self.store.set_policy(POLICY)
            with self.assertRaisesRegex(SnapshotError, "busy"):
                self.snapshots.capture("pilot")

    def test_custom_roots_do_not_overlap(self):
        with self.assertRaises(SnapshotError):
            LocalReleases(self.staging, self.staging.root)
        with self.assertRaises(SnapshotError):
            LocalReleases(self.staging, self.data)

    def test_auto_review_and_cli_persisted_state(self):
        source = emitted(self.data, "pilot", "1")
        document = json.loads((source / "summary.json").read_text())
        document.update(sources={"elevation": "fixture"}, coverage={"complete": True},
                        boundary_version="fixture-v1")
        (source / "summary.json").write_text(json.dumps(document))
        identifier = self.snapshots.capture("pilot")["snapshot_id"]
        self.staging.stage(identifier)
        candidate = self.store.create([identifier], 0)["release_id"]
        self.store.set_policy(POLICY)
        self.assertTrue(self.store.compare(candidate)["automatic_eligible"])
        self.store.publish(candidate, 0)
        self.assertEqual(self.store.state()["approvals"][candidate]["actor"], "automatic-policy")
        result = subprocess.run([sys.executable, str(ROOT / "tools/local_releases.py"),
                                 "list", "--data", str(self.data)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"]["active"], candidate)

    def test_mixed_assumptions_and_corrupt_staged_copy_rejected(self):
        first = self.stage()
        source = emitted(self.data, "other", "2")
        document = json.loads((source / "summary.json").read_text())
        document["assumptions"]["panel_rated_power_w"] = 600
        (source / "summary.json").write_text(json.dumps(document))
        identifier = self.snapshots.capture("other")["snapshot_id"]
        self.staging.stage(identifier)
        with self.assertRaisesRegex(SnapshotError, "Mixed PV"):
            self.store.create([first, identifier], 0)
        (self.staging.root / first / "snapshot/artifacts/cells.json").write_text("corrupt")
        with self.assertRaises(SnapshotError):
            self.store.create([first], 0)

    def test_malformed_metadata_fails_explicitly(self):
        identifier = self.candidate()
        path = self.store.path(identifier) / "manifest.json"
        document = json.loads(path.read_text())
        document.pop("site_inventory")
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(SnapshotError, "schema"):
            self.store.verify(identifier)
        self.store.root.joinpath("state.json").write_text('{"schema_version":1}')
        with self.assertRaisesRegex(SnapshotError, "schema"):
            self.store.list()

    def test_cross_process_lock_and_bounded_rollback_window(self):
        with self.store.locked():
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/local_releases.py"), "policy",
                 "--data", str(self.data), "--policy-json", json.dumps(POLICY)],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("busy", result.stderr)
        with patch("tools.local_releases.now", return_value="2026-12-01T00:00:00Z"):
            first = self.candidate()
            self.deploy(first)
            second = self.store.create([self.stage(n=2)], 1)["release_id"]
            self.store.publish(second, 1, "Inspected")
            deadline = self.store.state()["rollback_until"]
        with patch("tools.local_releases.now", return_value="2026-12-01T00:30:00Z"):
            self.store.rollback(2)
            self.assertEqual(self.store.state()["rollback_until"], deadline)

    def test_corrupt_rollback_never_switches(self):
        first = self.candidate()
        self.deploy(first)
        second = self.store.create([self.stage(n=2)], 1)["release_id"]
        self.store.publish(second, 1, "Inspected")
        (self.store.path(first) / "site/data/buildings.pmtiles").write_text("corrupt")
        with self.assertRaisesRegex(SnapshotError, "integrity"):
            self.store.rollback(2)
        self.assertEqual(self.store.state()["active"], second)
        self.assertEqual(self.store.state()["generation"], 2)

    def test_unused_artifact_cleanup_policy_reset_and_link_rejection(self):
        unused = self.stage()
        self.store.set_policy(POLICY)
        with patch("tools.local_releases.now", return_value="2090-01-01T00:00:00Z"):
            plan = self.store.cleanup()
            self.assertEqual({row["kind"] for row in plan["eligible"]}, {"snapshot", "staging"})
            self.assertFalse(plan["deleted"])
        with patch("tools.local_releases.now", return_value="2090-01-02T00:00:00Z"):
            self.store.set_policy(POLICY)
            self.assertFalse(self.store.cleanup(True)["deleted"], "policy resets the grace period")
        with patch("tools.local_releases.now", return_value="2090-01-03T00:00:00Z"):
            result = self.store.cleanup(True)
            self.assertEqual(set(result["deleted"]), {"snapshot:" + unused, "staging:" + unused})
            self.assertFalse((self.staging.root / unused).exists())
            self.assertFalse((self.snapshots.root / unused).exists())
        self.staging.root.rmdir()
        outside = self.data / "unmanaged"
        outside.mkdir()
        self.staging.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(SnapshotError, "linked storage"):
            self.store.cleanup(True)
        self.assertTrue(outside.is_dir())

    def test_extreme_policy_values_are_explicit_errors(self):
        for value in (float("inf"), float("nan"), 1e308, True):
            with self.assertRaises(SnapshotError):
                self.store.set_policy(dict(POLICY, grace_hours=value))

    def test_explicit_incomplete_coverage_cannot_auto_approve(self):
        source = emitted(self.data, "pilot", "1")
        document = json.loads((source / "summary.json").read_text())
        document.update(sources={"elevation": "fixture"}, coverage={"complete": False},
                        boundary_version="fixture-v1")
        (source / "summary.json").write_text(json.dumps(document))
        identifier = self.snapshots.capture("pilot")["snapshot_id"]
        self.staging.stage(identifier)
        candidate = self.store.create([identifier], 0)["release_id"]
        comparison = self.store.compare(candidate)
        self.assertFalse(comparison["automatic_eligible"])
        self.assertEqual(next(row["status"] for row in comparison["metrics"] if row["field"] == "coverage"), "fail")
        with self.assertRaisesRegex(SnapshotError, "review reason"):
            self.store.approve(candidate, 0)


@unittest.skipUnless(shutil.which("tippecanoe") and shutil.which("tile-join"), "map tile tools unavailable")
class AssemblyIntegrationTests(unittest.TestCase):
    def test_real_frozen_assembly(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / "data"
            snapshots = SnapshotStore(data)
            staging = StagingStore(snapshots)
            identifiers = []
            for region, building in (("pilot", "1"), ("other", "2")):
                emitted(data, region, building, real_tiles=True)
                identifier = snapshots.capture(region)["snapshot_id"]
                staging.stage(identifier)
                identifiers.append(identifier)
            store = LocalReleases(staging)
            try:
                result = store.create(identifiers, 0)
            except SnapshotError:
                self.fail(next((store.root / "releases").glob("*/assembly.log")).read_text())
            manifest = store.verify(result["release_id"])
            self.assertEqual(set(manifest["region_builds"]), {"pilot", "other"})
            combined = json.loads((store.path(result["release_id"]) / "site/data/build_summary.json").read_text())
            self.assertEqual(combined["totals"]["n"], 2)

    def test_snapshotted_invalid_tiles_fail_contract_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / "data"
            snapshots = SnapshotStore(data)
            staging = StagingStore(snapshots)
            source = emitted(data, "pilot", "1")
            (source / "buildings.pmtiles").write_bytes(b"not valid PMTiles")
            identifier = snapshots.capture("pilot")["snapshot_id"]
            staging.stage(identifier)
            store = LocalReleases(staging)
            with self.assertRaisesRegex(AssemblyError, "Assembly failed") as caught:
                store.create([identifier], 0)
            self.assertTrue(store.incomplete_log(caught.exception.release_id)["text"])
            self.assertFalse(store.list()["releases"])
            self.assertIsNone(store.state()["active"])


if __name__ == "__main__":
    unittest.main()
