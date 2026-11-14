"""Loopback HTTP lifecycle: authenticated writes and immutable range-capable previews."""

import http.client
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_local_releases import POLICY, emitted, fixture_assembler
from test_pipeline_web_server import _serve
from tools.regional_snapshots import SnapshotStore
from tools.regional_staging import StagingStore
from tools.local_releases import AssemblyError, LocalReleases


class ReleaseWebTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.data = self.root / "data"
        self.snapshots = SnapshotStore(self.data)
        self.staging = StagingStore(self.snapshots)
        # Only assembly is replaced; HTTP, copying, verification, review and switching are real.
        self.patcher = patch.object(LocalReleases, "_assemble", staticmethod(fixture_assembler))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.server, self.client = _serve(self.root)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def operation(self, action, body):
        code, result = self.client.request("POST", "/api/releases/" + action, body)
        self.assertEqual(code, 202, result)
        for _ in range(200):
            code, operation = self.client.request(
                "GET", "/api/snapshots/operations/" + result["operation_id"])
            self.assertEqual(code, 200)
            if operation["status"] != "running":
                return operation
            time.sleep(.01)
        self.fail("Background release operation did not complete")

    def success(self, action, body):
        result = self.operation(action, body)
        self.assertEqual(result["status"], "succeeded", result)
        return result["result"]

    def stage(self, region="pilot", building="1"):
        emitted(self.data, region, building)
        identifier = self.snapshots.capture(region)["snapshot_id"]
        self.staging.stage(identifier)
        return identifier

    def test_api_review_switch_rollback_and_pinned_preview(self):
        self.assertEqual(self.client.request("GET", "/api/releases", token=None)[0], 403)
        self.assertEqual(self.client.request("GET", "/local-deploy/")[0], 404)
        self.assertEqual(self.client.request("POST", "/api/releases/create", {}, token=None)[0], 403)
        self.assertEqual(self.client.request("POST", "/api/releases/create", {},
                                           headers={"Origin": "https://unrelated.example"})[0], 403)
        a = self.stage()
        candidate = self.success("create", {"staging_ids": [a], "generation": 0})["release_id"]
        self.assertTrue(self.success("verify", {"release_id": candidate})["verified"])
        comparison = self.success("compare", {"release_id": candidate})
        self.assertFalse(comparison["automatic_eligible"])
        self.assertEqual(self.operation("approve", {"release_id": candidate, "generation": 0})["status"], "failed")
        self.success("approve", {"release_id": candidate, "generation": 0, "reason": "Inspected fixture"})
        self.success("policy", {"policy": POLICY})
        self.success("activate", {"release_id": candidate, "generation": 0})

        conn = http.client.HTTPConnection("127.0.0.1", self.client.port)
        conn.request("GET", "/local-deploy/")
        response = conn.getresponse()
        self.assertEqual(response.status, 302)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("Location"), f"/local-releases/{candidate}/rooftop-solar.html")
        response.read()
        conn.close()
        prefix = f"/local-releases/{candidate}/"
        self.assertEqual(self.client.request("GET", prefix + "rooftop-solar.html")[0], 200)
        code, config = self.client.request("GET", prefix + "site-config.js")
        self.assertEqual(code, 200)
        self.assertIn(b'terrainAvailable: false', config)
        for name in ("manifest.json", "../../site-config.js", "data/missing.pmtiles", ".hidden"):
            self.assertNotEqual(self.client.request("GET", prefix + name)[0], 200, name)
        for method in ("GET", "HEAD"):
            conn = http.client.HTTPConnection("127.0.0.1", self.client.port)
            conn.request(method, prefix + "data/buildings.pmtiles", headers={"Range": "bytes=0-6"})
            response = conn.getresponse()
            self.assertEqual(response.status, 206 if method == "GET" else 200)
            if method == "GET":
                self.assertTrue(response.getheader("Content-Range").startswith("bytes 0-6/"))
            self.assertEqual(len(response.read()), 7 if method == "GET" else 0)
            conn.close()

        b = self.stage("other", "2")
        second = self.success("create", {"staging_ids": [b], "generation": 1})
        self.assertEqual(set(second["region_builds"]), {"pilot", "other"})
        self.success("publish", {"release_id": second["release_id"], "generation": 1, "reason": "Inspected"})
        self.assertEqual(self.client.request("GET", prefix + "data/buildings.pmtiles")[0], 200)
        self.assertEqual(self.success("rollback", {"generation": 2})["active"], candidate)
        plan = self.success("cleanup", {"apply": False})
        self.assertFalse(plan["deleted"])
        self.assertEqual(self.operation("cleanup", {"apply": "true"})["status"], "failed")
        self.assertEqual(self.operation("create", {"staging_ids": [], "generation": 3})["status"], "failed")
        self.assertEqual(self.client.request("GET", "/api/releases")[1]["state"]["generation"], 3)

    def test_failed_assembly_log_is_authenticated_bounded_and_persistent(self):
        def fail(regions, inputs, destination, log):
            log.write_text("x" * 70000)
            raise AssemblyError(log.parent.name.removeprefix(".incomplete-"), log, 1)

        with patch.object(LocalReleases, "_assemble", staticmethod(fail)):
            # The server retains its bound assembler; create a fresh manager with the failure fixture.
            from tools.pipeline_webui.snapshots import Snapshots
            manager = Snapshots(self.data, frontend=ROOT)
            identifier = self.stage()
            operation = manager.start("release_create", {"staging_ids": [identifier], "generation": 0})
            for _ in range(200):
                record = manager.get(operation["operation_id"])
                if record["status"] != "running":
                    break
                time.sleep(.01)
            self.assertEqual(record["status"], "failed", record)
            path = "/api/releases/incomplete/" + record["failed_release_id"] + "/log"
            self.assertEqual(self.client.request("GET", path, token=None)[0], 403)
            code, result = self.client.request("GET", path)
            self.assertEqual(code, 200, result)
            self.assertTrue(result["truncated"])
            self.assertEqual(len(result["text"]), 64 * 1024)
            self.assertEqual(self.client.request("GET", "/api/releases")[1]["releases"], [])


if __name__ == "__main__":
    unittest.main()
