"""Verify reservation lifetime against real local child processes."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.raster_cache import CachePolicy, RasterCache
from src.raster_disk_plan import DiskRequirements
from src.raster_job_supervisor import run_reserved_command
from src.raster_reservations import DiskReservationError, DiskReservations, QueueCleanupPolicy


@unittest.skipUnless(os.name == "posix", "POSIX process groups required")
class RasterJobSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = RasterCache(self.root / "cache", CachePolicy(10000, 0, 1000))
        self.reservations = DiskReservations(self.root / "registry", probe=lambda _: (1, 100000))
        self.requirements = DiskRequirements(self.cache.directory, self.root, self.root, 0, 0, 0, 0)
        self.plan = {"assets": [], "export_fallbacks": [],
                     "inputs": [{"footprints_wgs84": [[168, -45, 169, -44]]}]}
        self.policy = QueueCleanupPolicy(1, 0, 0, 3600)

    def run_command(self, command):
        return run_reserved_command(
            command, cwd=self.root, env=os.environ, reservations=self.reservations,
            cache=self.cache, plan=self.plan, requirements=self.requirements,
            upcoming_plans=[], policy=self.policy)

    def test_normal_and_failed_jobs_release_reservation(self):
        for code in (0, 7):
            with self.subTest(code=code):
                self.assertEqual(self.run_command([sys.executable, "-c", f"raise SystemExit({code})"]), code)
                self.assertEqual(list(self.reservations.directory.glob("*.json")), [])

    def test_surviving_child_keeps_record_and_blocks_admission(self):
        stopped = self.root / "stopped"
        script = (
            "import os,time,pathlib\n"
            "if os.fork() == 0:\n"
            " time.sleep(0.7)\n"
            f" pathlib.Path({str(stopped)!r}).touch()\n"
            " os._exit(0)\n"
        )
        with self.assertRaisesRegex(DiskReservationError, "Consumers may still be running"):
            self.run_command([sys.executable, "-c", script])
        record, = self.reservations.directory.glob("*.json")
        with self.assertRaisesRegex(DiskReservationError, "Orphaned disk reservation"):
            self.run_command([sys.executable, "-c", "pass"])
        deadline = time.monotonic() + 10
        while not stopped.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(stopped.exists(), "fixture child did not finish")
        self.reservations.recover_orphan(record.stem, consumers_stopped=True)

    def test_inconclusive_process_launch_retains_reservation(self):
        with patch("src.raster_job_supervisor.subprocess.Popen", side_effect=OSError("launch failed")):
            with self.assertRaisesRegex(DiskReservationError, "Consumers may still be running"):
                self.run_command([sys.executable, "-c", "pass"])
        record, = self.reservations.directory.glob("*.json")
        self.reservations.recover_orphan(record.stem, consumers_stopped=True)

    def test_interruption_stops_uncooperative_group_before_release(self):
        script = (
            "import os,signal,time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(0.2)\n"
            "os.kill(os.getppid(), signal.SIGINT)\n"
            "time.sleep(60)\n"
        )
        with patch("src.raster_job_supervisor._STOP_TIMEOUT_S", 0.2):
            with self.assertRaises(KeyboardInterrupt):
                self.run_command([sys.executable, "-c", script])
        self.assertEqual(list(self.reservations.directory.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
