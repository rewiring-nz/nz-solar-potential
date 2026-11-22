"""Cooperative reservation admission and owner-crash tests."""

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.raster_disk_plan import DiskAllocation, DiskRequirements
from src.raster_reservations import DiskReservationError, DiskReservations


def _hold_reservation(registry, plan, requirements, ready, release):
    reservations = DiskReservations(Path(registry), probe=lambda path: (1, 1500))
    with reservations.reserve(lambda: plan, requirements):
        ready.set()
        release.wait(20)


def _race_reservation(registry, plan, requirements, start, release, results):
    reservations = DiskReservations(Path(registry), probe=lambda path: (1, 1500))
    start.wait(8)
    try:
        with reservations.reserve(lambda: plan, requirements):
            results.put("admitted")
            release.wait(8)
    except DiskReservationError:
        results.put("rejected")


def _surviving_consumer(ready, release, stopped):
    ready.set()
    release.wait(20)
    stopped.set()


def _owner_with_consumer(registry, plan, requirements, ready, release, stopped):
    reservations = DiskReservations(Path(registry), probe=lambda path: (1, 1500))
    with reservations.reserve(lambda: plan, requirements):
        process = multiprocessing.get_context("spawn").Process(
            target=_surviving_consumer, args=(ready, release, stopped))
        process.start()
        process.join()


class RasterReservationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.registry = root / "registry"
        self.cache = root / "cache"
        self.work = root / "work"
        self.output = root / "output"
        for path in (self.cache, self.work, self.output):
            path.mkdir()
        self.requirements = DiskRequirements(self.cache, self.work, self.output, 100, 200, 300, 50)
        self.plan = {
            "assets": [{
                "collection_url": "https://example.test/collection.json", "collection_id": "one",
                "item_id": "tile", "asset_key": "data", "size_bytes": 400, "cache_state": "missing",
                "revision": "1",
            }],
            "cache": {"unknown_asset_estimate_bytes": 500, "usage_bytes": 0, "budget_bytes": 5000,
                      "directory": str(self.cache)},
            "export_fallbacks": [],
        }
        self.reservations = DiskReservations(self.registry, probe=lambda path: (1, 1500))

    def test_simultaneous_reservations_refused_then_released(self):
        calls = []

        def inspect():
            calls.append(True)
            return self.plan

        with self.reservations.reserve(inspect, self.requirements) as forecast:
            self.assertTrue(forecast["reservation"])
            self.assertTrue(forecast["cooperative_only"])
            with self.assertRaisesRegex(DiskReservationError, "device 1: 150 bytes"):
                with self.reservations.reserve(inspect, self.requirements):
                    self.fail("overcommitted disk")
        self.assertEqual(len(calls), 2)
        with self.reservations.reserve(inspect, self.requirements):
            pass
        self.assertEqual(list(self.registry.glob("*.json")), [])

    def test_concurrent_jobs_account_for_additional_filesystems(self):
        temporary = self.work / "temporary"
        temporary.mkdir()
        requirements = DiskRequirements(
            self.cache, self.work, self.output, 0, 0, 0, 50,
            extra_allocations=(DiskAllocation("temporary", temporary, 600),))
        reservations = DiskReservations(
            self.registry, probe=lambda path: (2, 1100) if path == temporary else (1, 10000))
        with reservations.reserve(lambda: self.plan, requirements):
            with self.assertRaisesRegex(DiskReservationError, "device 2: 150 bytes"):
                with reservations.reserve(lambda: self.plan, requirements):
                    self.fail("overcommitted temporary filesystem")
        self.assertEqual(list(self.registry.glob("*.json")), [])

    def test_reservation_released_on_exception(self):
        with self.assertRaisesRegex(RuntimeError, "build failed"):
            with self.reservations.reserve(lambda: self.plan, self.requirements):
                raise RuntimeError("build failed")
        with self.reservations.reserve(lambda: self.plan, self.requirements):
            pass

    def test_exclusive_reservation_excludes_other_cooperating_operations(self):
        reservations = DiskReservations(self.registry, probe=lambda path: (1, 10000))
        with reservations.reserve(lambda: self.plan, self.requirements, exclusive=True):
            with self.assertRaisesRegex(DiskReservationError, "Another raster operation"):
                with reservations.reserve(lambda: self.plan, self.requirements):
                    self.fail("admitted alongside an exclusive operation")
        with reservations.reserve(lambda: self.plan, self.requirements):
            with self.assertRaisesRegex(DiskReservationError, "Another raster operation"):
                with reservations.reserve(lambda: self.plan, self.requirements, exclusive=True):
                    self.fail("exclusive operation admitted alongside an active operation")
        with reservations.reserve(lambda: self.plan, self.requirements, exclusive=True):
            pass

    def test_verify_bound_reservation_rejects_mismatch_and_orphan(self):
        binding = "a" * 64
        with self.assertRaisesRegex(DiskReservationError, "Consumers may still be running"):
            with self.reservations.reserve(lambda: self.plan, self.requirements,
                                           binding=binding,
                                           consumers_stopped=lambda: False) as forecast:
                identifier = forecast["reservation_id"]
                self.reservations.verify_active(identifier, binding)
                with self.assertRaisesRegex(DiskReservationError, "does not match"):
                    self.reservations.verify_active(identifier, "b" * 64)
        with self.assertRaisesRegex(DiskReservationError, "not active"):
            self.reservations.verify_active(identifier, binding)
        self.reservations.recover_orphan(identifier, consumers_stopped=True)
        with self.assertRaisesRegex(DiskReservationError, "not found"):
            self.reservations.verify_active(identifier, binding)

    def test_unverified_consumers_leave_a_recoverable_record(self):
        with self.assertRaisesRegex(DiskReservationError, "Consumers may still be running"):
            with self.reservations.reserve(lambda: self.plan, self.requirements,
                                           consumers_stopped=lambda: False):
                pass
        record, = self.registry.glob("*.json")
        with self.assertRaisesRegex(DiskReservationError, "Orphaned disk reservation"):
            with self.reservations.reserve(lambda: self.plan, self.requirements):
                self.fail("admitted while consumers may survive")
        self.reservations.recover_orphan(record.stem, consumers_stopped=True)

    def test_failed_consumer_check_also_retains_record(self):
        def broken_check():
            raise RuntimeError("consumer check failed")

        with self.assertRaisesRegex(DiskReservationError, "Cannot verify consumers stopped"):
            with self.reservations.reserve(lambda: self.plan, self.requirements,
                                           consumers_stopped=broken_check):
                pass
        record, = self.registry.glob("*.json")
        self.reservations.recover_orphan(record.stem, consumers_stopped=True)

    def test_active_cache_budget_is_accounted_independently_of_disk(self):
        self.plan["cache"]["budget_bytes"] = 700
        reservations = DiskReservations(self.registry, probe=lambda path: (1, 10000))
        other = {**self.plan, "assets": [{**self.plan["assets"][0], "item_id": "other"}]}
        with reservations.reserve(lambda: self.plan, self.requirements):
            with self.assertRaisesRegex(DiskReservationError, "cache budget: 100 bytes"):
                with reservations.reserve(lambda: other, self.requirements):
                    self.fail("overcommitted cache")

    def test_warm_asset_contingency_is_counted_for_disk_and_cache_budget(self):
        self.plan["assets"][0]["cache_state"] = "cached"
        self.plan["cache"]["usage_bytes"] = 400
        self.plan["cache"]["budget_bytes"] = 700
        with self.assertRaisesRegex(DiskReservationError, "cache budget: 100 bytes"):
            with self.reservations.reserve(lambda: self.plan, self.requirements):
                self.fail("ignored possible cached-asset replacement")
        self.plan["cache"]["budget_bytes"] = 5000
        with self.reservations.reserve(lambda: self.plan, self.requirements) as forecast:
            self.assertEqual(forecast["cached_recovery_bytes"], 400)
            self.assertEqual(forecast["filesystems"][0]["allocations"]["cache_transfer"], 400)
            with self.assertRaisesRegex(DiskReservationError, "device 1: 150 bytes"):
                with self.reservations.reserve(lambda: self.plan, self.requirements):
                    self.fail("ignored possible replacement")

    def test_unknown_warm_asset_uses_explicit_recovery_bound(self):
        self.plan["assets"][0].update(cache_state="cached", size_bytes=None)
        with self.reservations.reserve(lambda: self.plan, self.requirements) as forecast:
            self.assertEqual(forecast["cached_recovery_bytes"], 500)
            self.assertEqual(forecast["unknown_asset_count"], 1)

    def test_shared_device_uses_largest_active_minimum_reserve(self):
        reservations = DiskReservations(self.registry, probe=lambda path: (1, 1800))
        strict = DiskRequirements(self.cache, self.work, self.output, 100, 200, 300, 300)
        with reservations.reserve(lambda: self.plan, strict):
            with self.assertRaisesRegex(DiskReservationError, "device 1: 100 bytes"):
                with reservations.reserve(lambda: self.plan, self.requirements):
                    self.fail("ignored stricter reserve")

    def test_separate_filesystem_shortfall_is_identified(self):
        devices = {self.cache: (1, 1000), self.work: (2, 550), self.output: (3, 1000)}
        reservations = DiskReservations(self.registry, probe=devices.__getitem__)
        with reservations.reserve(lambda: self.plan, self.requirements):
            with self.assertRaisesRegex(DiskReservationError, "device 2: 100 bytes"):
                with reservations.reserve(lambda: self.plan, self.requirements):
                    self.fail("overcommitted work filesystem")

    def test_shared_missing_asset_counted_once_until_last_job_releases(self):
        reservations = DiskReservations(self.registry, probe=lambda path: (1, 1700))
        self.plan["cache"]["budget_bytes"] = 400
        first = reservations.reserve(lambda: self.plan, self.requirements)
        first.__enter__()
        try:
            with reservations.reserve(lambda: self.plan, self.requirements):
                first.__exit__(None, None, None)
                first = None
                with reservations.reserve(lambda: self.plan, self.requirements):
                    with self.assertRaisesRegex(DiskReservationError, "device 1"):
                        with reservations.reserve(lambda: self.plan, self.requirements):
                            self.fail("overcommitted shared asset")
        finally:
            if first is not None:
                first.__exit__(None, None, None)

    def test_shared_unversioned_asset_reserves_replacement_headroom(self):
        reservations = DiskReservations(self.registry, probe=lambda path: (1, 1700))
        self.plan["assets"][0]["revision"] = None
        with reservations.reserve(lambda: self.plan, self.requirements):
            with self.assertRaisesRegex(DiskReservationError, "device 1: 350 bytes"):
                with reservations.reserve(lambda: self.plan, self.requirements):
                    self.fail("unversioned replacement could exhaust the filesystem")
        larger = DiskReservations(self.registry, probe=lambda path: (1, 2050))
        with larger.reserve(lambda: self.plan, self.requirements):
            with larger.reserve(lambda: self.plan, self.requirements):
                pass

    def test_shared_unversioned_replacement_counts_against_cache_budget(self):
        self.plan["assets"][0]["revision"] = None
        self.plan["cache"]["budget_bytes"] = 700
        reservations = DiskReservations(self.registry, probe=lambda path: (1, 10000))
        with reservations.reserve(lambda: self.plan, self.requirements):
            with self.assertRaisesRegex(DiskReservationError, "cache budget: 100 bytes"):
                with reservations.reserve(lambda: self.plan, self.requirements):
                    self.fail("unversioned replacement exceeded the conservative cache budget")

    def test_one_job_with_shared_unversioned_input_reserves_replacement(self):
        self.plan["assets"][0].update(
            revision=None,
            uses=[{"region": "alpha", "role": "dsm"},
                  {"region": "beta", "role": "dsm"}])
        reservations = DiskReservations(self.registry, probe=lambda path: (1, 1400))
        with self.assertRaisesRegex(DiskReservationError, "device 1: 50 bytes"):
            with reservations.reserve(lambda: self.plan, self.requirements):
                self.fail("repeated leases were not counted")
        enough = DiskReservations(self.registry, probe=lambda path: (1, 1450))
        with enough.reserve(lambda: self.plan, self.requirements) as forecast:
            self.assertEqual(forecast["shared_unversioned_replacement_bytes"], 400)
        self.plan["assets"][0]["revision"] = "1"
        with reservations.reserve(lambda: self.plan, self.requirements) as forecast:
            self.assertEqual(forecast["shared_unversioned_replacement_bytes"], 0)

    def test_invalid_asset_uses_fail_before_admission(self):
        self.plan["assets"][0]["uses"] = [{"region": "alpha"}]
        with self.assertRaisesRegex(ValueError, "Invalid raster asset uses"):
            with self.reservations.reserve(lambda: self.plan, self.requirements):
                self.fail("invalid asset uses were accepted")
        self.assertEqual(list(self.registry.glob("*.json")), [])

    def test_old_reservation_records_default_to_unversioned(self):
        reservations = DiskReservations(self.registry, probe=lambda path: (1, 1700))
        with reservations.reserve(lambda: self.plan, self.requirements):
            record_path, = self.registry.glob("*.json")
            record = json.loads(record_path.read_text())
            del record["assets"][0]["unversioned"]
            record_path.write_text(json.dumps(record))
            with self.assertRaisesRegex(DiskReservationError, "device 1: 350 bytes"):
                with reservations.reserve(lambda: self.plan, self.requirements):
                    self.fail("older reservation omitted possible replacement headroom")

    def test_conflicting_active_versions_are_refused(self):
        changed = {**self.plan, "assets": [{**self.plan["assets"][0], "revision": "new"}]}
        with self.reservations.reserve(lambda: self.plan, self.requirements):
            with self.assertRaisesRegex(DiskReservationError, "conflicting versions"):
                with self.reservations.reserve(lambda: changed, self.requirements):
                    self.fail("admitted incompatible source")

    def test_asset_from_a_different_cache_is_not_deduplicated(self):
        other_cache = self.cache.parent / "other-cache"
        other_cache.mkdir()
        other_plan = {**self.plan, "cache": {**self.plan["cache"], "directory": str(other_cache)}}
        other_requirements = DiskRequirements(other_cache, self.work, self.output,
                                              100, 200, 300, 50)
        reservations = DiskReservations(self.registry, probe=lambda path: (1, 1800))
        with reservations.reserve(lambda: self.plan, self.requirements):
            with self.assertRaisesRegex(DiskReservationError, "device 1: 250 bytes"):
                with reservations.reserve(lambda: other_plan, other_requirements):
                    self.fail("deduplicated across separate caches")

    def test_differing_unknown_estimates_use_larger_shared_bound(self):
        self.plan["assets"][0]["size_bytes"] = None
        small = {**self.plan, "cache": {**self.plan["cache"], "unknown_asset_estimate_bytes": 100}}
        large = {**self.plan, "cache": {**self.plan["cache"], "unknown_asset_estimate_bytes": 500}}
        reservations = DiskReservations(self.registry, probe=lambda path: (1, 1700))
        with reservations.reserve(lambda: small, self.requirements):
            with self.assertRaisesRegex(DiskReservationError, "device 1: 50 bytes"):
                with reservations.reserve(lambda: large, self.requirements):
                    self.fail("ignored larger unknown-size bound")

    def test_dead_owner_blocks_until_explicit_recovery(self):
        ctx = multiprocessing.get_context("spawn")
        ready, release = ctx.Event(), ctx.Event()
        process = ctx.Process(target=_hold_reservation,
                              args=(str(self.registry), self.plan, self.requirements, ready, release))
        process.start()
        try:
            self.assertTrue(ready.wait(8), "child failed to reserve")
            with self.assertRaises(DiskReservationError):
                with self.reservations.reserve(lambda: self.plan, self.requirements):
                    self.fail("ignored active process")
            process.terminate()
            process.join(timeout=8)
            self.assertFalse(process.is_alive())
            orphan = next(self.registry.glob("*.json"))
            with self.assertRaisesRegex(DiskReservationError, "Orphaned disk reservation"):
                with self.reservations.reserve(lambda: self.plan, self.requirements):
                    self.fail("ignored orphaned reservation")
            self.reservations.recover_orphan(orphan.stem, consumers_stopped=True)
            with self.reservations.reserve(lambda: self.plan, self.requirements):
                self.assertEqual(len(list(self.registry.glob("*.json"))), 1)
            self.assertEqual(list(self.registry.glob("*.json")), [])
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=8)
            if process.is_alive():
                process.kill()
                process.join()

    def test_simultaneous_processes_cannot_overcommit(self):
        ctx = multiprocessing.get_context("spawn")
        start, release = ctx.Event(), ctx.Event()
        results = ctx.Queue()
        processes = [ctx.Process(target=_race_reservation,
                                 args=(str(self.registry), self.plan, self.requirements,
                                       start, release, results)) for _ in range(2)]
        for process in processes:
            process.start()
        try:
            start.set()
            outcomes = [results.get(timeout=8), results.get(timeout=8)]
            self.assertCountEqual(outcomes, ["admitted", "rejected"])
        finally:
            release.set()
            for process in processes:
                process.join(timeout=8)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=8)
                if process.is_alive():
                    process.kill()
                    process.join()
            results.close()
        self.assertTrue(all(process.exitcode == 0 for process in processes))

    def test_unlocked_incomplete_record_blocks_until_recovered(self):
        self.registry.mkdir()
        stale = self.registry / ("a" * 32 + ".json")
        stale.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(DiskReservationError, "Orphaned disk reservation"):
            with self.reservations.reserve(lambda: self.plan, self.requirements):
                self.fail("ignored incomplete reservation")
        self.reservations.recover_orphan(stale.stem, consumers_stopped=True)
        self.assertFalse(stale.exists())

    def test_recovery_requires_confirmation_and_cannot_remove_active_reservation(self):
        with self.reservations.reserve(lambda: self.plan, self.requirements):
            path = next(self.registry.glob("*.json"))
            record = json.loads(path.read_text())
            self.assertEqual(record["owner_pid"], os.getpid())
            with self.assertRaisesRegex(ValueError, "confirmation"):
                self.reservations.recover_orphan(path.stem, consumers_stopped=False)
            with self.assertRaisesRegex(DiskReservationError, "still active"):
                self.reservations.recover_orphan(path.stem, consumers_stopped=True)
            self.assertTrue(path.exists())
        with self.assertRaises(ValueError):
            self.reservations.recover_orphan("../other", consumers_stopped=True)

    def test_recovery_cli_requires_explicit_confirmation(self):
        self.registry.mkdir()
        orphan = self.registry / ("a" * 32 + ".json")
        orphan.write_text("{}", encoding="utf-8")
        script = Path(__file__).resolve().parents[1] / "tools" / "raster_reservation_recover.py"
        command = [sys.executable, str(script), str(self.registry), orphan.stem]
        refused = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(orphan.exists())
        recovered = subprocess.run(command + ["--confirm-consumers-stopped"],
                                   capture_output=True, text=True, check=False)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertFalse(orphan.exists())

    def test_dead_owner_with_live_consumer_blocks_admission(self):
        ctx = multiprocessing.get_context("spawn")
        ready, release, stopped = ctx.Event(), ctx.Event(), ctx.Event()
        owner = ctx.Process(target=_owner_with_consumer,
                            args=(str(self.registry), self.plan, self.requirements,
                                  ready, release, stopped))
        owner.start()
        try:
            self.assertTrue(ready.wait(8), "consumer failed to start")
            owner.terminate()
            owner.join(timeout=8)
            self.assertFalse(owner.is_alive())
            self.assertFalse(stopped.is_set())
            orphan = next(self.registry.glob("*.json"))
            with self.assertRaisesRegex(DiskReservationError, "Orphaned disk reservation"):
                with self.reservations.reserve(lambda: self.plan, self.requirements):
                    self.fail("admitted while orphan consumer is running")
            release.set()
            self.assertTrue(stopped.wait(8), "consumer failed to stop")
            self.reservations.recover_orphan(orphan.stem, consumers_stopped=True)
            with self.reservations.reserve(lambda: self.plan, self.requirements):
                pass
        finally:
            release.set()
            if owner.is_alive():
                owner.terminate()
                owner.join(timeout=8)

    def test_invalid_locked_record_stops_admission(self):
        with self.reservations.reserve(lambda: self.plan, self.requirements):
            path = next(self.registry.glob("*.json"))
            path.write_text(json.dumps({"version": 1}), encoding="utf-8")
            with self.assertRaisesRegex(DiskReservationError, "Invalid reservation filesystems"):
                with self.reservations.reserve(lambda: self.plan, self.requirements):
                    self.fail("trusted malformed reservation")


if __name__ == "__main__":
    unittest.main()
