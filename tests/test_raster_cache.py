"""Offline tests for the experimental raster cache."""

import base64
import errno
import hashlib
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

from src.raster_acquisition_plan import RasterRequest, RasterSource, plan_batch
from src.raster_cache import CachePolicy, RasterCache, RasterCacheError, _key
from src.raster_disk_plan import DiskAllocation, DiskRequirements, forecast_disk
from src.raster_reservations import (
    DiskReservationError, DiskReservations, QueueCleanupPolicy, reserve_with_queue_cleanup,
)
from src.s3_rasters import _LERC_PROBE


def _hold_lease(directory, asset, ready, release):
    cache = RasterCache(Path(directory), CachePolicy(100000, 0, 10000))
    with cache.lease(asset):
        ready.set()
        release.wait(10)


class RasterCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.payload = base64.b64decode(_LERC_PROBE)
        self.source = self.root / "source.tif"
        self.source.write_bytes(self.payload)
        self.asset = {
            "collection_url": "https://example.test/collection.json",
            "collection_id": "survey", "item_id": "tile", "asset_key": "data",
            "url": self.source.as_uri(), "size_bytes": len(self.payload),
            "checksum": "1220" + hashlib.sha256(self.payload).hexdigest(),
            "revision": "1", "bbox_wgs84": [0, 0, 1, 1],
        }
        self.cache = RasterCache(self.root / "cache",
                                 CachePolicy(max_bytes=100000, min_free_bytes=0,
                                             unknown_asset_bytes=10000))

    def tearDown(self):
        self.directory.cleanup()

    def test_warm_hit_does_not_transfer(self):
        with self.cache.lease(self.asset) as path:
            self.assertEqual(path.read_bytes(), self.payload)
        with patch("src.raster_cache.urllib.request.urlopen",
                   side_effect=AssertionError("warm cache transferred bytes")):
            with self.cache.lease(self.asset) as path:
                self.assertEqual(path.read_bytes(), self.payload)

    def test_inspection_classifies_leased_reclaimable_and_unmanaged_payloads(self):
        size = len(self.payload)
        other = {**self.asset, "item_id": "other"}
        with self.cache.lease(other) as reclaimable:
            pass
        unmanaged = self.cache.directory / "assets" / "unmanaged.tif"
        unmanaged.write_bytes(b"not-managed")
        plan = {"assets": [self.asset, other], "export_fallbacks": []}
        with self.cache.lease(self.asset) as leased:
            summary = self.cache.inspect_plan(plan)["cache"]
            self.assertEqual(summary["leased_bytes"], size)
            self.assertEqual(summary["reclaimable_bytes"], size)
            self.assertEqual(summary["unmanaged_bytes"], len(b"not-managed"))
            self.assertEqual(summary["usage_bytes"], 2 * size + len(b"not-managed"))
            self.assertTrue(leased.exists())
            self.assertTrue(reclaimable.exists())
            self.assertTrue(unmanaged.exists())
        summary = self.cache.inspect_plan(plan)["cache"]
        self.assertEqual(summary["leased_bytes"], 0)
        self.assertEqual(summary["reclaimable_bytes"], 2 * size)

    def test_inspection_does_not_count_interrupted_replacement_as_reclaimable(self):
        with self.cache.lease(self.asset) as path:
            pass
        backup = self.cache.directory / "partial" / f"{_key(self.asset)}.previous"
        os.link(path, backup)
        summary = self.cache.inspect_plan({"assets": [], "export_fallbacks": []})["cache"]
        self.assertEqual(summary["unmanaged_bytes"], len(self.payload))
        self.assertEqual(summary["reclaimable_bytes"], 0)
        self.assertTrue(path.exists())
        self.assertTrue(backup.exists())

    def test_unversioned_assets_are_not_reused_without_revalidation(self):
        def write_source(value):
            with rasterio.open(self.source, "w", driver="GTiff", width=2, height=2,
                               count=1, dtype="uint8", crs="EPSG:2193",
                               transform=from_origin(0, 2, 1, 1)) as raster:
                raster.write(np.full((2, 2), value, dtype="uint8"), 1)

        write_source(1)
        asset = {**self.asset, "revision": None, "checksum": None,
                 "size_bytes": self.source.stat().st_size}
        with self.cache.lease(asset):
            pass
        write_source(2)
        self.assertEqual(self.source.stat().st_size, asset["size_bytes"])
        inspected = self.cache.inspect_plan({"assets": [asset], "export_fallbacks": []})
        self.assertEqual(inspected["assets"][0]["cache_state"], "unversioned")
        requirements = DiskRequirements(self.cache.directory, self.root, self.root, 0, 0, 0, 0)
        forecast = forecast_disk(inspected, requirements, probe=lambda path: (1, 100000))
        self.assertEqual(forecast["filesystems"][0]["allocations"]["cache_transfer"],
                         asset["size_bytes"])
        with patch("src.raster_cache.urllib.request.urlopen",
                   wraps=urllib.request.urlopen) as transfer:
            with self.cache.lease(asset) as path:
                with rasterio.open(path) as raster:
                    np.testing.assert_array_equal(raster.read(1), np.full((2, 2), 2))
        self.assertEqual(transfer.call_count, 1)

    def test_reservation_accounts_for_eviction_between_inspection_and_use(self):
        plan = {"assets": [self.asset], "export_fallbacks": []}
        with self.cache.lease(self.asset):
            pass
        requirements = DiskRequirements(self.cache.directory, self.root, self.root, 0, 0, 0, 0)
        reservations = DiskReservations(self.root / "reservations", probe=lambda path: (1, 100000))
        with reservations.reserve(lambda: self.cache.inspect_plan(plan), requirements) as forecast:
            self.assertEqual(forecast["filesystems"][0]["allocations"]["cache_transfer"],
                             len(self.payload))
            self.assertEqual(forecast["cached_recovery_bytes"], len(self.payload))
            self.assertTrue(self.cache.cleanup(0)["target_reached"])
            with self.cache.lease(self.asset) as path:
                self.assertEqual(path.read_bytes(), self.payload)

    def test_admission_reclaims_unleased_cache_then_rechecks(self):
        size = len(self.payload)
        cache = RasterCache(self.root / "cache", CachePolicy(size, 0, size))
        with cache.lease(self.asset) as previous:
            self.assertTrue(previous.exists())
        wanted = {**self.asset, "item_id": "wanted", "bbox_wgs84": [168, -45, 169, -44]}
        plan = {"assets": [wanted], "export_fallbacks": [],
                "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        requirements = DiskRequirements(cache.directory, self.root, self.root, 0, 0, 0, 0)
        reservations = DiskReservations(self.root / "reservations", probe=lambda path: (1, 100000))
        policy = QueueCleanupPolicy(1, 1000, 0, 3600)
        with reserve_with_queue_cleanup(reservations, cache, plan, requirements, [], policy) as result:
            self.assertEqual([entry["key"] for entry in result["cleanup"]["assets"]["removed"]],
                             [_key(self.asset)])
            self.assertFalse(previous.exists())
            with cache.lease(wanted) as path:
                self.assertEqual(path.read_bytes(), self.payload)
        self.assertEqual(list((self.root / "reservations").glob("*.json")), [])

    def test_proactive_high_water_cleanup_reports_protected_entries(self):
        size = len(self.payload)
        cache = RasterCache(self.root / "cache", CachePolicy(3 * size, 0, size))
        plan = {"assets": [], "export_fallbacks": [],
                "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        requirements = DiskRequirements(cache.directory, self.root, self.root, 0, 0, 0, 0)
        reservations = DiskReservations(self.root / "reservations", probe=lambda _: (1, 100000))
        policy = QueueCleanupPolicy(1, 0, 0, 3600, size, 0)
        with cache.lease(self.asset) as path:
            orphan_key = _key({**self.asset, "item_id": "orphan"})
            partial = cache.directory / "partial" / f"{orphan_key}-orphan.part"
            partial.write_bytes(b"partial")
            os.utime(partial, (0, 0))
            with reserve_with_queue_cleanup(reservations, cache, plan, requirements, [], policy) as result:
                self.assertFalse(partial.exists())
                self.assertEqual(len(result["cleanup"]["partials"]["removed"]), 1)
                self.assertFalse(result["cleanup"]["proactive"]["target_reached"])
                self.assertTrue(path.exists())
        with reserve_with_queue_cleanup(reservations, cache, plan, requirements, [], policy) as result:
            self.assertTrue(result["cleanup"]["proactive"]["target_reached"])
            self.assertEqual(result["cleanup"]["proactive"]["remaining_bytes"], 0)
            self.assertFalse(path.exists())

    def test_proactive_cleanup_is_not_triggered_below_high_water(self):
        size = len(self.payload)
        cache = RasterCache(self.root / "cache", CachePolicy(3 * size, 0, size))
        with cache.lease(self.asset) as path:
            pass
        plan = {"assets": [], "export_fallbacks": [],
                "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        requirements = DiskRequirements(cache.directory, self.root, self.root, 0, 0, 0, 0)
        reservations = DiskReservations(self.root / "reservations", probe=lambda _: (1, 100000))
        with reserve_with_queue_cleanup(reservations, cache, plan, requirements, [],
                                        QueueCleanupPolicy(1, 0, 0, 3600, size + 1, 0)) as result:
            self.assertIsNone(result["cleanup"]["proactive"])
            self.assertTrue(path.exists())

    def test_admission_never_reclaims_a_live_lease(self):
        size = len(self.payload)
        cache = RasterCache(self.root / "cache", CachePolicy(size, 0, size))
        wanted = {**self.asset, "item_id": "wanted", "bbox_wgs84": [168, -45, 169, -44]}
        plan = {"assets": [wanted], "export_fallbacks": [],
                "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        requirements = DiskRequirements(cache.directory, self.root, self.root, 0, 0, 0, 0)
        reservations = DiskReservations(self.root / "reservations", probe=lambda path: (1, 100000))
        with cache.lease(self.asset) as path:
            with self.assertRaisesRegex(DiskReservationError, "active leases remain protected"):
                with reserve_with_queue_cleanup(reservations, cache, plan, requirements, [],
                                                QueueCleanupPolicy(1, 0, 0, 3600)):
                    self.fail("admitted without sufficient cache capacity")
            self.assertTrue(path.exists())

    def test_admission_does_not_clean_cache_for_other_filesystem_shortfall(self):
        with self.cache.lease(self.asset) as path:
            pass
        plan = {"assets": [], "export_fallbacks": [],
                "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        requirements = DiskRequirements(self.cache.directory, self.root, self.root, 1, 0, 0, 0)
        reservations = DiskReservations(self.root / "reservations",
                                        probe=lambda p: (1, 100000) if p == self.cache.directory else (2, 0))
        with self.assertRaisesRegex(DiskReservationError, "device 2"):
            with reserve_with_queue_cleanup(reservations, self.cache, plan, requirements, [],
                                            QueueCleanupPolicy(1, 0, 0, 3600)):
                self.fail("admitted without work filesystem capacity")
        self.assertTrue(path.exists())

    def test_admission_does_not_clean_cache_for_extra_filesystem_shortfall(self):
        with self.cache.lease(self.asset) as path:
            pass
        temporary = self.root / "temporary"
        temporary.mkdir()
        plan = {"assets": [], "export_fallbacks": [],
                "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        requirements = DiskRequirements(
            self.cache.directory, self.root, self.root, 0, 0, 0, 0,
            extra_allocations=(DiskAllocation("temporary", temporary, 1),))
        reservations = DiskReservations(
            self.root / "reservations",
            probe=lambda p: (2, 0) if p == temporary else (1, 100000))
        with self.assertRaisesRegex(DiskReservationError, "device 2"):
            with reserve_with_queue_cleanup(reservations, self.cache, plan, requirements, [],
                                            QueueCleanupPolicy(1, 0, 0, 3600)):
                self.fail("admitted without temporary filesystem capacity")
        self.assertTrue(path.exists())

    def test_admission_reprobes_freed_filesystem_space_and_counts_active_jobs(self):
        size = len(self.payload)
        with self.cache.lease(self.asset) as old:
            pass
        plan = {"assets": [], "export_fallbacks": [],
                "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        requirements = DiskRequirements(self.cache.directory, self.root, self.root, size, 0, 0, 0)
        reservations = DiskReservations(
            self.root / "reservations", probe=lambda _: (1, size - self.cache._usage()))
        policy = QueueCleanupPolicy(1, 0, 0, 3600)
        with reserve_with_queue_cleanup(reservations, self.cache, plan, requirements, [], policy):
            self.assertFalse(old.exists())
            errors = []

            def competing_admission():
                try:
                    with reserve_with_queue_cleanup(reservations, self.cache, plan,
                                                    requirements, [], policy):
                        errors.append("unexpected admission")
                except DiskReservationError as exc:
                    errors.append(str(exc))

            thread = threading.Thread(target=competing_admission)
            thread.start()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIn("cannot make this job fit", errors[0])

    def test_expired_partial_is_reclaimed_before_cached_assets(self):
        with self.cache.lease(self.asset) as path:
            pass
        partial = self.cache.directory / "partial" / f"{_key(self.asset)}-orphan.part"
        partial.write_bytes(b"partial")
        plan = {"assets": [], "export_fallbacks": [],
                "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        requirements = DiskRequirements(self.cache.directory, self.root, self.root, 1, 0, 0, 0)
        reservations = DiskReservations(
            self.root / "reservations", probe=lambda _: (1, 0 if partial.exists() else 1))
        with reserve_with_queue_cleanup(reservations, self.cache, plan, requirements, [],
                                        QueueCleanupPolicy(1, 0, 0, 0)) as result:
            self.assertFalse(partial.exists())
            self.assertTrue(path.exists())
            self.assertEqual(len(result["cleanup"]["partials"]["removed"]), 1)
            self.assertIsNone(result["cleanup"]["assets"])

    def test_admission_refuses_if_reprobe_still_reports_no_free_space(self):
        with self.cache.lease(self.asset) as path:
            pass
        plan = {"assets": [], "export_fallbacks": [],
                "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        requirements = DiskRequirements(self.cache.directory, self.root, self.root, 1, 0, 0, 0)
        reservations = DiskReservations(self.root / "reservations", probe=lambda _: (1, 0))
        with self.assertRaisesRegex(DiskReservationError, "cleanup reclaimed .*asset bytes"):
            with reserve_with_queue_cleanup(reservations, self.cache, plan, requirements, [],
                                            QueueCleanupPolicy(1, 0, 0, 3600)):
                self.fail("admitted despite unchanged filesystem free space")
        self.assertFalse(path.exists())
        self.assertEqual(list((self.root / "reservations").glob("*.json")), [])

    def test_admission_does_not_evict_when_even_empty_cache_cannot_fit(self):
        size = len(self.payload)
        cache = RasterCache(self.root / "cache", CachePolicy(size, 0, size))
        with cache.lease(self.asset) as path:
            pass
        wanted = {**self.asset, "item_id": "wanted", "size_bytes": size + 1}
        plan = {"assets": [wanted], "export_fallbacks": [],
                "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        requirements = DiskRequirements(cache.directory, self.root, self.root, 0, 0, 0, 0)
        reservations = DiskReservations(self.root / "reservations", probe=lambda _: (1, 100000))
        with self.assertRaisesRegex(DiskReservationError, "entire cache cannot make this job fit"):
            with reserve_with_queue_cleanup(reservations, cache, plan, requirements, [],
                                            QueueCleanupPolicy(1, 0, 0, 3600)):
                self.fail("admitted an impossible job")
        self.assertTrue(path.exists())

    def test_admission_validates_queue_before_removing_expired_partials(self):
        size = len(self.payload)
        cache = RasterCache(self.root / "cache", CachePolicy(size, 0, size))
        with cache.lease(self.asset):
            pass
        partial = cache.directory / "partial" / f"{_key(self.asset)}-orphan.part"
        partial.write_bytes(b"partial")
        wanted = {**self.asset, "item_id": "wanted"}
        plan = {"assets": [wanted], "export_fallbacks": [],
                "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        requirements = DiskRequirements(cache.directory, self.root, self.root, 0, 0, 0, 0)
        reservations = DiskReservations(self.root / "reservations", probe=lambda _: (1, 100000))
        with self.assertRaisesRegex(ValueError, "upcoming batch plan"):
            with reserve_with_queue_cleanup(reservations, cache, plan, requirements, [{}],
                                            QueueCleanupPolicy(2, 0, 0, 0)):
                self.fail("admitted invalid queue plan")
        self.assertTrue(partial.exists())

    def test_batch_plan_reuses_shared_asset_across_regions(self):
        source = RasterSource(self.asset["collection_url"], "data")
        documents = {
            self.asset["collection_url"]: {
                "id": "survey", "links": [{"rel": "item", "href": "item.json"}],
            },
            "https://example.test/item.json": {
                "id": "tile", "collection": "survey", "bbox": [0, 0, 1, 1],
                "properties": {"updated": "1"}, "assets": {
                    "data": {"href": self.source.as_uri(), "type": "image/tiff",
                             "file:size": len(self.payload),
                             "file:checksum": self.asset["checksum"]},
                },
            },
        }
        footprints = ((0.1, 0.1, 0.9, 0.9),)
        plan = plan_batch([
            RasterRequest("alpha", "dsm", footprints, source, 123),
            RasterRequest("beta", "dsm", footprints, source, 123),
        ], get_json=documents.__getitem__)
        self.assertEqual(len(plan["assets"]), 1)
        cold = self.cache.inspect_plan(plan)
        self.assertEqual(cold["cache"]["missing_count"], 1)
        requirements = DiskRequirements(self.root / "cache", self.root, self.root,
                                        200, 300, 400, 50)
        cold_forecast = forecast_disk(cold, requirements, probe=lambda path: (1, 100000))
        self.assertEqual(cold_forecast["filesystems"][0]["allocations"]["cache_transfer"],
                         len(self.payload))
        reservations = DiskReservations(self.root / "reservations", probe=lambda path: (1, 100000))
        with reservations.reserve(lambda: self.cache.inspect_plan(plan), requirements):
            with self.cache.lease_many(plan["assets"]) as paths:
                self.assertEqual(len(paths), 1)
                self.assertEqual(next(iter(paths.values())).read_bytes(), self.payload)
        with patch("src.raster_cache.urllib.request.urlopen",
                   side_effect=AssertionError("warm cache transferred bytes")):
            warm = self.cache.inspect_plan(plan)
            self.assertEqual(warm["cache"]["cached_count"], 1)
            warm_forecast = forecast_disk(warm, requirements, probe=lambda path: (1, 100000))
            self.assertEqual(warm_forecast["filesystems"][0]["allocations"]["cache_transfer"], 0)
            with self.cache.lease_many(plan["assets"]) as paths:
                self.assertEqual(len(paths), 1)

    def test_plan_inspection_distinguishes_cache_states_without_transfer(self):
        unknown = {**self.asset, "item_id": "unknown", "size_bytes": None}
        with patch("src.raster_cache.urllib.request.urlopen",
               side_effect=AssertionError("inspection transferred bytes")):
            cold = self.cache.inspect_plan({"assets": [self.asset, unknown]})
        self.assertEqual([item["cache_state"] for item in cold["assets"]], ["missing", "missing"])
        self.assertEqual(cold["cache"]["known_missing_bytes"], len(self.payload))
        self.assertEqual(cold["cache"]["unknown_missing_count"], 1)
        with self.cache.lease(self.asset) as path:
            pass
        with patch("src.raster_cache.urllib.request.urlopen",
               side_effect=AssertionError("inspection transferred bytes")):
            warm = self.cache.inspect_plan({"assets": [self.asset, unknown]})
        self.assertEqual([item["cache_state"] for item in warm["assets"]], ["cached", "missing"])
        self.assertEqual(warm["cache"]["known_missing_bytes"], 0)
        changed = self.cache.inspect_plan({"assets": [{**self.asset, "revision": "new"}]})
        self.assertEqual(changed["assets"][0]["cache_state"], "superseded")
        path.write_bytes(b"corrupt")
        invalid = self.cache.inspect_plan({"assets": [self.asset]})
        self.assertEqual(invalid["assets"][0]["cache_state"], "invalid")

    def test_same_tile_name_in_different_collection_is_distinct(self):
        with self.cache.lease(self.asset) as one:
            first = one
        other = {**self.asset, "collection_id": "other", "collection_url": "https://example.test/other"}
        with self.cache.lease(other) as two:
            self.assertNotEqual(first, two)
        self.assertEqual(len(list((self.root / "cache" / "assets").glob("*.tif"))), 2)

    def test_truncation_and_bad_checksum_do_not_publish(self):
        with self.assertRaisesRegex(RasterCacheError, "Truncated"):
            with self.cache.lease({**self.asset, "size_bytes": len(self.payload) + 1}):
                pass
        self.assertEqual(list((self.root / "cache" / "assets").glob("*.tif")), [])
        with self.assertRaisesRegex(RasterCacheError, "checksum"):
            with self.cache.lease({**self.asset, "checksum": "1220" + "0" * 64}):
                pass
        self.assertEqual(list((self.root / "cache" / "partial").iterdir()), [])

    def test_decoding_failure_in_later_band_or_block_is_not_published(self):
        tile = self.root / "internally-truncated.tif"
        with rasterio.open(tile, "w", driver="GTiff", width=32, height=32, count=2,
                           dtype="uint8", crs="EPSG:2193", tiled=True,
                           blockxsize=16, blockysize=16,
                           transform=from_origin(1200000, 5000000, 1, 1)) as dataset:
            dataset.write(np.ones((2, 32, 32), dtype="uint8"))
        with tile.open("r+b") as stream:
            stream.truncate(tile.stat().st_size - 100)
        with rasterio.open(tile) as dataset:
            self.assertEqual(dataset.read(1, window=((0, 1), (0, 1)))[0, 0], 1)
            with self.assertRaises(rasterio.errors.RasterioError):
                dataset.read(2, window=((16, 32), (16, 32)))
        payload = tile.read_bytes()
        asset = {**self.asset, "url": tile.as_uri(), "size_bytes": len(payload),
                 "checksum": "1220" + hashlib.sha256(payload).hexdigest()}
        with self.assertRaisesRegex(RasterCacheError, "Raster cannot be read"):
            with self.cache.lease(asset):
                self.fail("published an internally truncated raster")
        self.assertEqual(list((self.root / "cache" / "assets").glob("*.tif")), [])
        self.assertEqual(list((self.root / "cache" / "metadata").glob("*.json")), [])
        self.assertEqual(list((self.root / "cache" / "partial").iterdir()), [])
        with self.cache.lease(self.asset) as valid:
            original = valid.read_bytes()
        with self.assertRaisesRegex(RasterCacheError, "Raster cannot be read"):
            with self.cache.lease({**asset, "revision": "replacement"}):
                self.fail("published a corrupt replacement")
        self.assertEqual(valid.read_bytes(), original)
        with self.cache.lease(self.asset) as restored:
            self.assertEqual(restored.read_bytes(), original)

    def test_replacement_waits_for_live_lease(self):
        changed = {**self.asset, "revision": "2"}
        started = threading.Event()
        completed = threading.Event()
        failures = []

        def replace():
            started.set()
            try:
                with self.cache.lease(changed):
                    completed.set()
            except Exception as exc:
                failures.append(exc)
                completed.set()

        with self.cache.lease(self.asset) as path:
            worker = threading.Thread(target=replace)
            worker.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(completed.wait(0.2))
            self.assertEqual(path.read_bytes(), self.payload)
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(completed.is_set())
        self.assertEqual(len(list((self.root / "cache" / "assets").glob("*.tif"))), 1)

    def test_waiting_replacement_does_not_block_other_assets(self):
        other = {**self.asset, "item_id": "other"}
        with self.cache.lease(other):
            pass
        started = threading.Event()
        other_finished = threading.Event()
        failures = []

        def acquire(asset, event):
            try:
                event.set()
                with self.cache.lease(asset):
                    if asset["item_id"] == "other":
                        other_finished.set()
            except Exception as exc:
                failures.append(exc)

        with self.cache.lease(self.asset):
            replacement = threading.Thread(target=acquire, args=({**self.asset, "revision": "2"}, started))
            replacement.start()
            self.assertTrue(started.wait(2))
            time.sleep(0.1)
            other_thread = threading.Thread(target=acquire, args=(other, threading.Event()))
            other_thread.start()
            completed = other_finished.wait(2)
        replacement.join(timeout=3)
        other_thread.join(timeout=3)
        self.assertTrue(completed, "waiting replacement blocked an unrelated lease")
        self.assertFalse(replacement.is_alive())
        self.assertFalse(other_thread.is_alive())
        self.assertEqual(failures, [])

    def test_interrupted_replacement_is_restored_before_reuse(self):
        with self.cache.lease(self.asset) as path:
            pass
        backup = self.root / "cache" / "partial" / f"{path.stem}.previous"
        os.link(path, backup)
        path.unlink()
        path.write_bytes(b"interrupted replacement")
        plan = self.cache.inspect_plan({"assets": [self.asset]})
        self.assertEqual(plan["assets"][0]["cache_state"], "recovery_required")
        self.assertFalse(self.cache.cleanup(0)["target_reached"])
        self.assertTrue(backup.exists())
        with patch("src.raster_cache.urllib.request.urlopen",
                   side_effect=AssertionError("recovery downloaded raster")):
            with self.cache.lease(self.asset) as restored:
                self.assertEqual(restored.read_bytes(), self.payload)
        self.assertFalse(backup.exists())

    def test_interrupted_replacement_with_no_matching_payload_is_reported(self):
        with self.cache.lease(self.asset) as path:
            pass
        backup = self.root / "cache" / "partial" / f"{path.stem}.previous"
        backup.write_bytes(b"bad backup")
        path.write_bytes(b"bad current")
        with self.assertRaisesRegex(RasterCacheError, "neither payload matches"):
            with self.cache.lease(self.asset):
                pass
        self.assertTrue(backup.exists())
        self.assertEqual(path.read_bytes(), b"bad current")

    def test_batch_leases_deduplicate_and_hold_all_assets(self):
        other = {**self.asset, "item_id": "other"}
        with self.cache.lease_many([other, self.asset, self.asset]) as paths:
            self.assertEqual(set(paths), {_key(self.asset), _key(other)})
            self.assertTrue(all(path.read_bytes() == self.payload for path in paths.values()))
            self.assertFalse(self.cache.cleanup(0)["target_reached"])
        self.assertTrue(self.cache.cleanup(0)["target_reached"])

    def test_batch_rejects_conflicting_revisions_before_downloading(self):
        with patch("src.raster_cache.urllib.request.urlopen",
                   side_effect=AssertionError("conflicting plan transferred bytes")):
            with self.assertRaisesRegex(RasterCacheError, "Conflicting source versions"):
                with self.cache.lease_many([self.asset, {**self.asset, "revision": "new"}]):
                    pass

    def test_batch_failure_releases_previously_acquired_leases(self):
        first = self.asset
        other = {**self.asset, "item_id": "other"}
        first, other = sorted([first, other], key=_key)
        bad = {**other, "checksum": "1220" + "0" * 64}
        with self.assertRaisesRegex(RasterCacheError, "checksum"):
            with self.cache.lease_many([bad, first]):
                pass
        self.assertTrue(self.cache.cleanup(0)["target_reached"])

    def test_overlapping_batches_lock_in_stable_order(self):
        other = {**self.asset, "item_id": "other"}
        with self.cache.lease_many([self.asset, other]):
            pass
        barrier = threading.Barrier(2)
        failures = []

        def acquire(assets):
            try:
                barrier.wait(timeout=2)
                with self.cache.lease_many(assets) as paths:
                    self.assertEqual(len(paths), 2)
            except Exception as exc:
                failures.append(exc)

        workers = [threading.Thread(target=acquire, args=(assets,))
                   for assets in ([self.asset, other], [other, self.asset])]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])

    def test_concurrent_requests_transfer_once(self):
        barrier = threading.Barrier(2)
        failures = []
        from urllib.request import urlopen
        transfers = []

        def fetch(url, **kwargs):
            transfers.append(url)
            time.sleep(0.1)
            return urlopen(url, **kwargs)

        def get():
            try:
                barrier.wait(timeout=2)
                with self.cache.lease(self.asset) as path:
                    self.assertEqual(path.read_bytes(), self.payload)
            except Exception as exc:
                failures.append(exc)

        with patch("src.raster_cache.urllib.request.urlopen", side_effect=fetch):
            workers = [threading.Thread(target=get) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=5)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])
        self.assertEqual(len(transfers), 1)

    def test_budget_and_unknown_size_are_explicit(self):
        tiny = RasterCache(self.root / "tiny", CachePolicy(100, 0, 100))
        with self.assertRaisesRegex(RasterCacheError, "budget"):
            with tiny.lease(self.asset):
                pass
        small_estimate = RasterCache(self.root / "unknown", CachePolicy(100000, 0, 100))
        with self.assertRaisesRegex(RasterCacheError, "reserved"):
            with small_estimate.lease({**self.asset, "size_bytes": None}):
                pass

    def test_cleanup_protects_live_leases_and_unmanaged_files(self):
        extra = self.root / "cache" / "assets" / "user-file.tif"
        with self.cache.lease(self.asset) as path:
            extra.write_bytes(b"not managed")
            result = self.cache.cleanup(0)
            self.assertFalse(result["target_reached"])
            self.assertTrue(path.exists())
            self.assertTrue(extra.exists())
        result = self.cache.cleanup(0)
        self.assertEqual(len(result["removed"]), 1)
        self.assertFalse(path.exists())
        self.assertTrue(extra.exists())
        self.assertFalse(result["target_reached"])

    def test_expired_partials_are_removed_only_when_unlocked(self):
        with self.cache.lease(self.asset) as path:
            partial_dir = self.root / "cache" / "partial"
            expired = partial_dir / f"{path.stem}-abcdef12.part"
            recent = partial_dir / f"{path.stem}-recent.part"
            unmanaged = partial_dir / "user-file.part"
            backup = partial_dir / f"{path.stem}.previous"
            for entry in (expired, recent, unmanaged, backup):
                entry.write_bytes(b"partial")
            os.utime(expired, (100, 100))
            os.utime(unmanaged, (100, 100))
            os.utime(backup, (100, 100))
            os.utime(recent, (195, 195))
            self.assertEqual(self.cache.cleanup_partials(10, now=200)["removed"], [])
        result = self.cache.cleanup_partials(10, now=200)
        self.assertEqual([entry["name"] for entry in result["removed"]], [expired.name])
        self.assertFalse(expired.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(unmanaged.exists())
        self.assertTrue(backup.exists())

    def test_process_crash_releases_lease_for_cleanup(self):
        ctx = multiprocessing.get_context("spawn")
        ready, release = ctx.Event(), ctx.Event()
        process = ctx.Process(target=_hold_lease,
                              args=(str(self.root / "cache"), self.asset, ready, release))
        process.start()
        try:
            self.assertTrue(ready.wait(8), "child process failed to acquire lease")
            self.assertFalse(self.cache.cleanup(0)["target_reached"])
            process.terminate()
            process.join(timeout=8)
            self.assertFalse(process.is_alive())
            self.assertTrue(self.cache.cleanup(0)["target_reached"])
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=8)
            if process.is_alive():
                process.kill()
                process.join()

    def test_cleanup_keeps_preferred_entry_until_last(self):
        with self.cache.lease(self.asset) as first:
            preferred = first.stem
        other = {**self.asset, "item_id": "other"}
        with self.cache.lease(other) as second:
            other_key = second.stem
        result = self.cache.cleanup(len(self.payload), preferred_keys=frozenset({preferred}))
        self.assertEqual([entry["key"] for entry in result["removed"]], [other_key])
        self.assertTrue(first.exists())
        self.assertTrue(result["target_reached"])

    def test_queue_cleanup_prefers_exact_assets_and_separate_footprints(self):
        queued = {**self.asset, "item_id": "queued", "bbox_wgs84": [166, -45, 166.1, -44.9]}
        nearby = {**self.asset, "item_id": "near", "bbox_wgs84": [166.11, -45, 166.2, -44.9]}
        between = {**self.asset, "item_id": "between", "bbox_wgs84": [170, -45, 170.1, -44.9]}
        plans = [{
            "inputs": [{"footprints_wgs84": [[166, -45, 166.1, -44.9],
                                               [174, -45, 174.1, -44.9]]}],
            "assets": [queued],
        }]
        for asset in (queued, nearby, between):
            with self.cache.lease(asset):
                pass
        result = self.cache.cleanup_for_queue(
            len(self.payload), plans, lookahead=1, near_distance_m=30000,
            max_unused_age_s=100, now=time.time())
        self.assertEqual([item["key"] for item in result["removed"]],
                         [_key(between), _key(nearby)])
        self.assertTrue((self.root / "cache" / "assets" / f"{_key(queued)}.tif").exists())
        self.assertEqual(result["considered_plans"], 1)

    def test_queue_cleanup_superseded_first_and_active_lease_protected(self):
        changed = {**self.asset, "revision": "new", "bbox_wgs84": [168, -45, 169, -44]}
        other = {**self.asset, "item_id": "other", "bbox_wgs84": [168, -45, 169, -44]}
        original = {**changed, "revision": "1"}
        plan = {"inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}], "assets": [changed]}
        with self.cache.lease(original) as original_path:
            with self.cache.lease(other) as other_path:
                pass
            result = self.cache.cleanup_for_queue(
                len(self.payload), [plan], lookahead=1, near_distance_m=0,
                max_unused_age_s=100, now=time.time())
            self.assertEqual([item["key"] for item in result["removed"]], [_key(other)])
            self.assertTrue(original_path.exists())
            self.assertFalse(other_path.exists())
        with self.cache.lease(other):
            pass
        result = self.cache.cleanup_for_queue(
            len(self.payload), [plan], lookahead=1, near_distance_m=0,
            max_unused_age_s=100, now=time.time())
        self.assertEqual([item["key"] for item in result["removed"]], [_key(original)])

    def test_queue_cleanup_bounded_lookahead_and_validation(self):
        asset = {**self.asset, "bbox_wgs84": [168, -45, 169, -44]}
        with self.cache.lease(asset):
            pass
        plan = {"inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}], "assets": [asset]}
        with self.assertRaisesRegex(ValueError, "input footprints"):
            self.cache.cleanup_for_queue(
                0, [plan, {"assets": []}], lookahead=2, near_distance_m=0,
                max_unused_age_s=0, now=time.time())
        self.assertEqual(self.cache.inspect_plan({"assets": [asset]})["cache"]["cached_count"], 1)
        result = self.cache.cleanup_for_queue(
            0, [plan, {"assets": []}], lookahead=1, near_distance_m=0,
            max_unused_age_s=0, now=time.time())
        self.assertEqual(result["considered_plans"], 1)
        self.assertEqual(len(result["removed"]), 1)

    def test_queue_cleanup_expired_outside_precedes_farther_recent_asset(self):
        expired = {**self.asset, "item_id": "expired", "bbox_wgs84": [170, -45, 170.1, -44.9]}
        recent = {**self.asset, "item_id": "recent", "bbox_wgs84": [174, -45, 174.1, -44.9]}
        for asset, last_used in ((expired, 100), (recent, 999)):
            with self.cache.lease(asset):
                pass
            key = _key(asset)
            self.cache._write_record(key, {**self.cache._record(key), "last_used": last_used})
        plan = {"inputs": [{"footprints_wgs84": [[166, -45, 166.1, -44.9]]}], "assets": []}
        result = self.cache.cleanup_for_queue(
            len(self.payload), [plan], lookahead=1, near_distance_m=30000,
            max_unused_age_s=100, now=1000)
        self.assertEqual([item["key"] for item in result["removed"]], [_key(expired)])

    def test_queue_cleanup_rejects_conflicting_upcoming_versions(self):
        asset = {**self.asset, "bbox_wgs84": [168, -45, 169, -44]}
        plan = {"inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}], "assets": [asset]}
        changed = {**plan, "assets": [{**asset, "revision": "other"}]}
        with self.assertRaisesRegex(RasterCacheError, "Conflicting queued versions"):
            self.cache.cleanup_for_queue(
                0, [plan, changed], lookahead=2, near_distance_m=0,
                max_unused_age_s=0, now=time.time())

    def test_metadata_write_failure_preserves_previous_asset(self):
        with self.cache.lease(self.asset) as path:
            previous = path.read_bytes()
        with patch.object(self.cache, "_write_record", side_effect=OSError(errno.ENOSPC, "full")):
            with self.assertRaisesRegex(RasterCacheError, "Disk full"):
                with self.cache.lease({**self.asset, "revision": "2"}):
                    pass
        self.assertEqual(path.read_bytes(), previous)
        with patch("src.raster_cache.urllib.request.urlopen",
                   side_effect=AssertionError("previous cache entry was lost")):
            with self.cache.lease(self.asset) as again:
                self.assertEqual(again.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
