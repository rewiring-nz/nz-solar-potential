"""Pipeline web panel: cloud targets, remote catalog and cloud approval, all faked.

Nothing here calls gcloud, git remotes or the network: the runner and HTTP
getter are stand-ins.

Run: .venv/bin/python tests/test_pipeline_web_cloud.py
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from tools.pipeline_webui import cloud as C
from tools.pipeline_webui import estimate as E
from tools.pipeline_webui import remote_catalog as RM

LOCALITY = "queenstown-lakes-district/queenstown/frankton"
GCS = RM.GCS


class FakeRunner:
    """gcloud describe and git, answering from a dict; records every command."""

    def __init__(self, vm="TERMINATED", branch="main", dirty="", ahead="0"):
        self.answers = {"describe": vm, "rev-parse": branch, "status": dirty,
                        "rev-list": ahead, "fetch": ""}
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        for key, out in self.answers.items():
            if key in cmd:
                return subprocess.CompletedProcess(cmd, 0, out + "\n", "")
        return subprocess.CompletedProcess(cmd, 1, "", "unexpected")


def fake_cloud(**kw) -> C.Cloud:
    return C.Cloud(runner=FakeRunner(**kw), alias_lookup=lambda: "tester", gcloud="/fake/gcloud")


def test_commands_never_publish_past_the_gate():
    dev = C.cloud_command("developer", "py", ["a", LOCALITY], True)
    assert dev == ["py", "tools/pipeline.py", f"a {LOCALITY}", "--mode", "cloud", "--cloud",
                   "build", "--yes", "--no-open-browser", "--paid-referee"]
    pub = C.cloud_command("published", "py", ["a", LOCALITY], False)
    assert pub[:5] == ["bash", "tools/remote_build.sh", "--regions", f"a {LOCALITY}", "--publish"]
    assert "--fetch" in pub and pub[-1] == "--no-paid-referee"
    assert "--fetch" not in C.cloud_command("published", "py", ["a"], False)
    for cmd in (dev, pub):
        assert "--reviewed" not in cmd and "--keep-running" not in cmd
    for bad in (("local", ["a"]), ("developer", []), ("developer", ["../x"])):
        try:
            C.cloud_command(bad[0], "py", bad[1], False)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)


def test_output_parsing():
    rec = {}
    for line in ("noise", "GATE_RESULT=flagged", "PREVIEW_URL=https://x/p.html",
                 "PUSHED (data v68)"):
        C.parse_cloud_line(line, rec)
    assert rec == {"gate_result": "flagged", "preview_url": "https://x/p.html",
                   "published_version": "68"}
    rec = {}
    C.parse_cloud_line("GATE FAILED -- the gate flagged items; not publishing", rec)
    assert rec["gate_result"] == "failed"


def test_cost_hours_and_wording():
    cloud = fake_cloud()
    assert cloud.vm_hours(None) == C.MIN_VM_HOURS
    assert cloud.vm_hours(10 * 3600) == 15
    assert cloud.vm_hours(100 * 3600) == C.MAX_VM_HOURS
    small, big = cloud.cost("developer", 3600), cloud.cost("published", 40 * 3600)
    assert float(big["cost_nzd"]) > float(small["cost_nzd"]) > 0
    assert "not a spending cap" in small["cost_text"] and "production" in big["cost_text"]
    assert "16 hours" in big["cost_basis"]


def test_vm_status_is_cached_and_reports_errors():
    runner = FakeRunner(vm="RUNNING")
    cloud = C.Cloud(runner=runner, alias_lookup=lambda: "t", gcloud="/fake/gcloud")
    assert cloud.vm_status()["status"] == "RUNNING"
    cloud.vm_status()
    assert sum("describe" in c for c in runner.calls) == 1
    cloud.vm_status(refresh=True)
    assert sum("describe" in c for c in runner.calls) == 2
    assert all("start" not in c for c in runner.calls), "status checks never start the VM"
    nog = C.Cloud(runner=runner, alias_lookup=lambda: "t", gcloud=None)
    nog._gcloud = None
    import shutil
    if not shutil.which("gcloud"):
        assert nog.vm_status()["error"]


def test_vm_status_failure_detail_and_single_describe():
    def failing(cmd):
        return subprocess.CompletedProcess(cmd, 1, "", "WARNING: x\nERROR: token expired\n")
    err = C.Cloud(runner=failing, alias_lookup=lambda: "t", gcloud="/g").vm_status()["error"]
    assert err.endswith(": ERROR: token expired"), err

    import threading
    import time
    calls, gate = [], threading.Event()

    def slow(cmd):
        calls.append(cmd)
        gate.wait(2)
        return subprocess.CompletedProcess(cmd, 0, "RUNNING\n", "")
    cloud = C.Cloud(runner=slow, alias_lookup=lambda: "t", gcloud="/g")
    results = []
    threads = [threading.Thread(target=lambda: results.append(cloud.vm_status(refresh=True)))
               for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join()
    assert len(calls) == 1 and [r["status"] for r in results] == ["RUNNING"] * 4


def test_publish_blockers():
    assert fake_cloud().publish_blockers() == []
    blockers = fake_cloud(branch="feature", dirty=" M site-config.js", ahead="2").publish_blockers()
    text = " ".join(blockers)
    assert "not on main" in text and "site-config.js" in text and "2 unpushed" in text


def _estimate(target, cloud, tmp):
    lookup = lambda region: {"buildings": 100, "bbox": [168.6, -45.05, 168.61, -45.04],
                             "bbox_km2": 0.5}
    return E.estimate(["a", LOCALITY], Path(tmp), target, lookup,
                      survey_fn=lambda b, r: {"name": "t", "elevation": "lidar"}, cloud=cloud)


def test_cloud_estimates():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        dev = _estimate("developer", fake_cloud(), tmp)
        assert not dev["blockers"], dev["blockers"]
        assert dev["cloud"]["preview_user"] == "tester"
        assert dev["cloud"]["preview_url"].endswith("/devel/tester/rooftop-solar.html")
        assert dev["cloud_cost_nzd"] == dev["cloud"]["cost_nzd"]
        assert "not a spending cap" in dev["cloud"]["cost_text"]
        assert "disk_free_gb" not in dev["machine"]
        pub = _estimate("published", fake_cloud(ahead="1"), tmp)
        assert any("unpushed" in b for b in pub["blockers"])
        assert any("live map" in w for w in pub["warnings"])
        busy = _estimate("developer", fake_cloud(vm="STOPPING"), tmp)
        assert any("STOPPING" in b for b in busy["blockers"])
        try:
            E.estimate(["a"], Path(tmp), "developer", None)
        except E.EstimateError:
            pass
        else:
            raise AssertionError("cloud estimate without a cloud")
        failing = lambda region: (_ for _ in ()).throw(LookupError("no outlines"))
        fresh = Path(tmp) / "fresh"
        row = E.estimate([LOCALITY], fresh, "developer", failing, cloud=fake_cloud(),
                         survey_fn=lambda b, r: {})["regions"][0]
        assert not row["blockers"] and "VM resolves" in row["warnings"][0]


def _fake_http(pages: dict):
    def get(url):
        for key, body in pages.items():
            if url == key or (key.endswith("*") and url.startswith(key[:-1])):
                return json.dumps(body).encode() if not isinstance(body, str) else body.encode()
        raise OSError("404 " + url)
    return get


def test_remote_catalog():
    pub_base = f"{GCS}/tiles/v67/"
    dev_base = f"{GCS}/previews/devel/al/runs/r1/"
    summary = lambda r: {"region": r, "built_at": "2026-01-01T00:00:00Z", "git": "abc",
                         "centroid": [168.7, -45.0], "n": 3, "n_est": 2, "kwp": 1.0, "kwh": 5}
    pages = {
        f"{GCS}/storage/v1/b/tiles/o*": {"prefixes": ["v66/", "v67/", "v100/", "misc/"]},
        pub_base + "data/build_summary.json": {"regions": ["a", LOCALITY, "../bad"]},
        pub_base + "data/summaries/a.json": summary("a"),
        f"{pub_base}data/summaries/{LOCALITY}.json": summary(LOCALITY),
        f"{GCS}/storage/v1/b/previews/o*": {"prefixes": ["devel/al/", "devel/BAD!/", "devel/nobody/"]},
        f"{GCS}/previews/devel/al/site-config.js": f'window.SITE = {{ dataVersion: "r1", dataBase: "{dev_base}" }};',
        dev_base + "data/build_summary.json": {"regions": ["a", "missing"]},
        dev_base + "data/summaries/a.json": summary("a"),
    }
    now = [1000.0]
    cat = RM.RemoteCatalog(http_get=_fake_http(pages), preview_bucket="gs://previews",
                           site_config_text=lambda: f'dataVersion: "67", dataBase: "{pub_base}"',
                           now=lambda: now[0])
    pub = cat.get("published")
    assert pub["versions"] == ["v66", "v67", "v100"] and pub["data_version"] == "67"
    assert [f["properties"]["region"] for f in pub["features"]] == ["a", LOCALITY]
    url = pub["features"][0]["properties"]["preview_url"]
    assert url.startswith(RM.LIVE_SITE + "?lat=-45.00000&lng=168.70000")
    dev = cat.get("developer")
    assert [a["alias"] for a in dev["aliases"]] == ["al", "nobody"]
    assert len(dev["features"]) == 1 and dev["features"][0]["properties"]["alias"] == "al"
    assert any("missing" in w for w in dev["warnings"]) and any("nobody" in w for w in dev["warnings"])
    assert cat.get("developer") is dev, "cached"
    now[0] += RM.CACHE_SECONDS + 1
    assert cat.get("developer") is not dev
    try:
        cat.get("local")
    except ValueError:
        pass
    else:
        raise AssertionError("local is not a remote source")
    try:
        RM._default_get("https://evil.example/x")
    except ValueError:
        pass
    else:
        raise AssertionError("remote catalog must read only storage.googleapis.com")


def test_server_cloud_approval_and_queue():
    import http.client
    import threading
    from test_pipeline_web_data import KEY, FakeLinz
    from test_pipeline_web_jobs import _cloud_manager
    from test_pipeline_web_server import TOKEN, Client
    from tools.pipeline_webui.server import App, make_server

    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        jobs = _cloud_manager(Path(tmp))
        cloud = fake_cloud()
        lookup = lambda region: {"buildings": 5, "bbox": [168.6, -45.05, 168.61, -45.04], "bbox_km2": 0.5}
        estimates = E.EstimateStore(jobs.data, lookup, disk_free=10**12, cloud=cloud,
                                    survey_fn=lambda b, r: {"name": "t", "elevation": "lidar"})
        remote = RM.RemoteCatalog(http_get=_fake_http({}), preview_bucket="gs://previews",
                                  site_config_text=lambda: "")
        app = App(TOKEN, data=jobs.data, root=ROOT, api_key_fn=lambda: KEY, http_get=FakeLinz(),
                  estimates=estimates, jobs=jobs, cloud=cloud, remote=remote)
        server = make_server(app, 0, quiet=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        c = Client(server.server_address[1])
        try:
            status, pub = c.request("GET", "/api/regions?source=published")
            assert status == 200 and pub["features"] == [] and pub["warnings"]
            assert c.request("GET", "/api/regions?source=elsewhere")[0] == 400
            status, vm = c.request("GET", "/api/cloud/status")
            assert status == 200 and vm["status"] == "TERMINATED" and vm["busy_with"] is None

            status, started = c.request("POST", "/api/estimate", {"regions": ["a"], "target": "developer"})
            assert status == 202
            for _ in range(100):
                est = c.request("GET", f"/api/estimate/{started['estimate_id']}")[1]
                if est["status"] != "pending":
                    break
                time.sleep(0.05)
            assert est["status"] == "ready" and est["target"] == "developer", est
            body = {"estimate_id": est["estimate_id"],
                    "include_imagery": est["options"]["include_imagery"],
                    "paid_referee": est["options"]["paid_referee"],
                    "target": "developer"}
            assert c.request("POST", "/api/jobs", body)[0] == 400, "cloud cost needs checkbox approval"
            body["cloud_cost_acknowledged"] = True
            assert c.request("POST", "/api/jobs", body)[0] == 400, "cost must be echoed back"
            body["cost_nzd"] = "0.01"
            assert c.request("POST", "/api/jobs", body)[0] == 400
            body["cost_nzd"] = est["cloud"]["cost_nzd"]
            assert c.request("POST", "/api/jobs", {**body, "target": "published"})[0] == 409
            status, job = c.request("POST", "/api/jobs", body)
            assert status == 202, job
            status, queue = c.request("GET", "/api/queue")
            assert status == 200 and set(queue["lanes"]) == {"local", "cloud"}
            for _ in range(200):
                view = c.request("GET", f"/api/jobs/{job['job_id']}")[1]
                if view["status"] in ("succeeded", "failed"):
                    break
                time.sleep(0.05)
            assert view["status"] == "succeeded" and view["gate_result"] == "pass", view
            assert view["approved"]["cloud_cost_nzd"] == est["cloud"]["cost_nzd"]
            status, started = c.request("POST", "/api/estimate", {"regions": ["a"], "target": "published"})
            for _ in range(100):
                pub_est = c.request("GET", f"/api/estimate/{started['estimate_id']}")[1]
                if pub_est["status"] != "pending":
                    break
                time.sleep(0.05)
            assert pub_est["status"] == "ready" and not pub_est["blockers"], pub_est
            cloud._runner.answers["rev-list"] = "3"  # a commit lands after the estimate
            status, err = c.request("POST", "/api/jobs", {
                "estimate_id": pub_est["estimate_id"], "target": "published",
                "include_imagery": pub_est["options"]["include_imagery"],
                "paid_referee": pub_est["options"]["paid_referee"],
                "cloud_cost_acknowledged": True,
                "cost_nzd": pub_est["cloud"]["cost_nzd"]})
            assert status == 409 and "unpushed" in err["error"], err
            health = c.request("GET", "/api/health", token=None)[1]
            assert set(health["active_jobs"]) == {"local", "cloud"}
        finally:
            server.shutdown()
            server.server_close()


def test_pipeline_cli_checks_every_cloud_region_before_any_cloud_call():
    # An invalid region in the list fails argument parsing, before gcloud is touched.
    result = subprocess.run(
        [sys.executable, "tools/pipeline.py", "pilot ../escape", "--mode", "cloud",
         "--cloud", "build", "--no-open-browser", "--no-paid-referee"],
        cwd=ROOT, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert result.returncode == 2, result.stdout + result.stderr
    result = subprocess.run(
        [sys.executable, "tools/pipeline.py", "pilot ../escape"],
        cwd=ROOT, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert result.returncode == 2, "local mode still takes one region"


if __name__ == "__main__":
    for test in (test_commands_never_publish_past_the_gate, test_output_parsing,
                 test_cost_hours_and_wording, test_vm_status_is_cached_and_reports_errors,
                 test_vm_status_failure_detail_and_single_describe,
                 test_publish_blockers, test_cloud_estimates, test_remote_catalog,
                 test_server_cloud_approval_and_queue,
                 test_pipeline_cli_checks_every_cloud_region_before_any_cloud_call):
        test()
    print("pipeline web cloud tests passed")
