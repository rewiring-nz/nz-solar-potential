"""Pipeline web panel: HTTP API end to end on a loopback port.

Run: .venv/bin/python tests/test_pipeline_web_server.py
"""

import http.client
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_pipeline_web_data import KEY, FakeLinz
from test_pipeline_web_jobs import _manager
from tools.pipeline_webui import estimate as E
from tools.pipeline_webui.server import App, make_server, port_in_use_message

TOKEN = "test-token-abc"


class Client:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, token=TOKEN, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = dict(headers or {})
        if token:
            h["X-Pipeline-Token"] = token
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=h)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        try:
            return res.status, json.loads(raw)
        except ValueError:
            return res.status, raw


def _serve(tmp: Path):
    jobs = _manager(tmp)
    lookup = lambda region: {"buildings": 5, "bbox": [168.6, -45.05, 168.61, -45.04], "bbox_km2": 0.5}
    estimates = E.EstimateStore(jobs.data, lookup, disk_free=10**12,
                                survey_fn=lambda bbox, region: {"name": "t", "elevation": "lidar"})
    app = App(TOKEN, data=jobs.data, root=ROOT, api_key_fn=lambda: KEY, http_get=FakeLinz(),
              estimates=estimates, jobs=jobs)
    server = make_server(app, 0, quiet=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, Client(server.server_address[1])


def test_api_end_to_end():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        server, c = _serve(Path(tmp))
        try:
            status, health = c.request("GET", "/api/health", token=None)
            assert status == 200 and health["ok"] and health["linz_key"] is True
            assert c.request("GET", "/api/regions", token=None)[0] == 403
            assert c.request("GET", "/api/regions", token="wrong")[0] == 403
            status, fc = c.request("GET", "/api/regions")
            assert status == 200 and fc["type"] == "FeatureCollection"
            assert c.request("GET", "/api/layers", token=None)[0] == 403
            status, layers = c.request("GET", "/api/layers?source=local")
            assert status == 200 and layers["version"] == 1
            assert layers["availability"] == {"source": "local", "local_cache": True}
            status, unavailable = c.request("GET", "/api/layers?source=developer")
            assert status == 200 and not unavailable["layers"]
            assert unavailable["availability"] == {"source": "developer", "local_cache": False}
            assert "unavailable" in unavailable["warnings"][0]
            assert c.request("GET", "/api/layers?source=remote")[0] == 400
            status, loc = c.request("GET", "/api/localities?bbox=168.6,-45.1,168.7,-45.0")
            assert status == 200 and len(loc["features"]) == 2
            assert KEY not in json.dumps(loc)
            assert c.request("GET", "/api/localities?bbox=160,-46,170,-40")[0] == 413
            status, found = c.request("GET", "/api/localities/search?q=Frank")
            assert status == 200 and found["results"]
            assert c.request("GET", "/api/nope")[0] == 404

            region = "queenstown-lakes-district/queenstown/frankton"
            assert c.request("POST", "/api/estimate", {"regions": [region]},
                             headers={"Content-Type": "text/plain"})[0] == 415
            assert c.request("POST", "/api/estimate", {"regions": [region]},
                             headers={"Origin": "http://evil.example"})[0] == 403
            assert c.request("POST", "/api/estimate", {"regions": [region]}, token=None)[0] == 403
            status, started = c.request("POST", "/api/estimate", {
                "regions": [region], "include_imagery": False, "paid_referee": False},
                                        headers={"Origin": f"http://127.0.0.1:{c.port}"})
            assert status == 202
            for _ in range(100):
                status, est = c.request("GET", f"/api/estimate/{started['estimate_id']}")
                if est["status"] != "pending":
                    break
                time.sleep(0.05)
            assert est["status"] == "ready" and est["totals"]["buildings"] == 5, est
            assert est["options"] == {"include_imagery": False, "paid_referee": False}

            body = {"estimate_id": est["estimate_id"],
                    "include_imagery": est["options"]["include_imagery"],
                    "paid_referee": est["options"]["paid_referee"],
                    "target": est["target"]}
            assert c.request("POST", "/api/jobs", {
                **body, "include_imagery": True})[0] == 409, "source choices are pinned to estimate"
            status, job = c.request("POST", "/api/jobs", body)
            assert status == 202, job

            conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=30)
            conn.request("GET", f"/api/jobs/{job['job_id']}/stream?token={TOKEN}")
            res = conn.getresponse()
            assert res.status == 200 and res.getheader("Content-Type") == "text/event-stream"
            events = res.read().decode()
            conn.close()
            assert "event: log" in events and "event: end" in events and "built " + region in events
            summaries = [json.loads(block.split("data: ", 1)[1])
                         for block in events.split("\n\n") if block.startswith("event: status\n")]
            assert summaries and all("progress_known" in s and "elapsed_s" in s
                                     and "current_stage" in s and "last_activity_at" in s
                                     for s in summaries)
            status, view = c.request("GET", f"/api/jobs/{job['job_id']}")
            assert view["status"] == "succeeded", view
            assert view["include_imagery"] is False
            status, queue = c.request("GET", "/api/queue")
            assert status == 200
            assert queue["lanes"]["local"]["recent"][0]["job_id"] == job["job_id"]
            status, log = c.request("GET", f"/api/jobs/{job['job_id']}/log?offset=0")
            assert status == 200 and log["eof"]
            assert "confirm" not in view.get("approved", {})
            status, listing = c.request("GET", "/api/jobs")
            assert listing["jobs"][0]["job_id"] == job["job_id"]
            assert c.request("POST", f"/api/jobs/{job['job_id']}/cancel", {})[0] == 409
        finally:
            server.shutdown()
            server.server_close()


def test_static_files_keep_pipeline_serve_rules():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        server, c = _serve(Path(tmp))
        try:
            status, body = c.request("GET", "/pipeline.html", token=None)
            assert status == 200 and b"pipeline_ui.js" in body and b"pipeline_progress.js" in body
            assert c.request("GET", "/pipeline_progress.js", token=None)[0] == 200
            assert c.request("GET", "/.env", token=None)[0] == 404
            assert c.request("GET", "/api/health", token=None, headers={"Host": "evil.example"})[0] == 404
            assert c.request("POST", "/pipeline.html", {}, token=None)[0] == 405
            status, _ = c.request("GET", "/pipeline.html", token=None, headers={"Range": "bytes=0-9"})
            assert status == 206
        finally:
            server.shutdown()
            server.server_close()


def test_port_in_use_message_says_how_to_stop_an_old_panel():
    busy = OSError(48, "Address already in use")
    panel = port_in_use_message(8766, busy, lambda port: (4242, "python tools/pipeline_web.py"))
    assert "earlier pipeline panel" in panel and "kill 4242" in panel and "--port 8767" in panel
    other = port_in_use_message(8766, busy, lambda port: (77, "/usr/bin/some-server"))
    assert "kill" not in other and "/usr/bin/some-server" in other
    unknown = port_in_use_message(8766, busy, lambda port: None)
    assert "--port 8767" in unknown and "kill" not in unknown


if __name__ == "__main__":
    test_api_end_to_end()
    test_static_files_keep_pipeline_serve_rules()
    test_port_in_use_message_says_how_to_stop_an_old_panel()
    print("pipeline web server tests passed")
