"""Pipeline web panel: national area catalogue and per-region status join.

Run: .venv/bin/python tests/test_pipeline_web_catalog.py
"""

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from tools.pipeline_webui import national_catalog as NC
from tools.pipeline_webui import region_status as RS
from tools.pipeline_webui.common import web_dir, write_json_atomic

KEY = "secret-key-not-for-logs"


def _locality(i, name, kind, town, ta):
    return {"id": i, "name": name, "name_ascii": name, "additional_name": None, "type": kind,
            "major_name": town, "major_name_ascii": town, "major_name_type": "Town" if town else None,
            "territorial_authority": ta, "territorial_authority_ascii": ta, "population_estimate": 10}


LOCALITIES = [
    _locality(1, "Sunshine Bay", "Suburb", "Queenstown", "Queenstown-Lakes District"),
    _locality(2, "Fernhill", "Suburb", "Queenstown", "Queenstown-Lakes District"),
    _locality(3, "Lake Wakatipu", "Lake", None, "Queenstown-Lakes District"),
    _locality(4, "Empty Hill", "Locality", None, "Queenstown-Lakes District"),
]
HITS = {"Sunshine Bay": 249, "Fernhill": 1200, "Empty Hill": 0, "Lake Wakatipu": 3}


class Response:
    def __init__(self, payload=None, text=""):
        self.payload, self.text = payload, text

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeCatalogLinz:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def __call__(self, url, params, timeout):
        self.calls.append(params)
        if self.fail:
            raise RuntimeError("connection reset for " + url)
        cql = params["cql_filter"]
        if params.get("resultType") == "hits":
            name = next(n for n in HITS if f"'{n}'" in cql)
            return Response(text=f'<wfs:FeatureCollection numberMatched="{HITS[name]}" numberReturned="0"/>')
        if cql.startswith("id IN"):
            ids = [int(x) for x in cql[len("id IN ("):-1].split(",")]
            feats = [{"type": "Feature", "properties": {"id": i},
                      "geometry": {"type": "Polygon", "coordinates": [[
                          [168.6 + i / 100, -45.05], [168.61 + i / 100, -45.05],
                          [168.61 + i / 100, -45.04], [168.6 + i / 100, -45.04], [168.6 + i / 100, -45.05]]]}}
                     for i in ids]
            return Response({"type": "FeatureCollection", "features": feats})
        return Response({"type": "FeatureCollection",
                         "features": [{"properties": p} for p in LOCALITIES]})


def test_catalogue_excludes_water_and_empty_localities():
    stages = []
    catalog, fc = NC.build(KEY, FakeCatalogLinz(), progress=lambda s, d, t: stages.append(s), workers=2)
    regions = {r["name"]: r for r in catalog["regions"]}
    assert set(regions) == {"Sunshine Bay", "Fernhill"}, regions
    sunshine = regions["Sunshine Bay"]
    assert sunshine["region"] == "queenstown-lakes-district/queenstown/sunshine-bay"
    assert sunshine["buildings"] == 249 and sunshine["council"] == "Queenstown-Lakes District"
    assert sunshine["ta_slug"] == "queenstown-lakes-district" and sunshine["town_slug"] == "queenstown"
    assert len(sunshine["bbox"]) == 4 and sunshine["area_km2"] > 0 and len(sunshine["centroid"]) == 2
    assert catalog["source"]["water_excluded"] == 1
    assert catalog["source"]["no_buildings_excluded"] == 1
    assert [f["id"] for f in fc["features"]] == [r["linz_id"] for r in catalog["regions"]]
    assert "counting buildings" in stages and "fetching boundaries" in stages

    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        NC.save(catalog, fc, Path(tmp))
        assert NC.load(Path(tmp))["regions"] == catalog["regions"]
        assert NC.geometry_path(Path(tmp)).is_file()


def test_catalogue_errors_do_not_leak_the_key():
    try:
        NC.build(KEY, FakeCatalogLinz(fail=True))
    except NC.CatalogError as exc:
        assert KEY not in str(exc), exc
    else:
        raise AssertionError("expected CatalogError")


def test_refresher_reports_progress_and_result():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        refresher = NC.Refresher(Path(tmp), lambda: KEY, FakeCatalogLinz())
        assert refresher.status()["status"] == "idle"
        refresher.start()
        for _ in range(200):
            state = refresher.status()
            if state["status"] != "running":
                break
            time.sleep(0.05)
        assert state["status"] == "succeeded", state
        assert state["regions"] == 2
        assert NC.load(Path(tmp)) is not None


def _run(data, run_id, area, overall, finished):
    write_json_atomic(data / "pipeline_runs" / run_id / "status.json",
                      {"schema": 1, "area": area, "run_id": run_id, "overall": overall,
                       "started": finished, "finished": finished,
                       "metadata": {"git_commit": "abc123"}})


def test_attempts_join_jobs_and_runs():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        data = Path(tmp)
        region = "queenstown-lakes-district/queenstown/sunshine-bay"
        _run(data, "20261001T000000Z", region, "PASS", "2026-10-01T00:00:00+00:00")
        _run(data, "20261002T000000Z", region, "FAIL", "2026-10-02T00:00:00+00:00")
        jobs = [{"job_id": "j1", "target": "local", "include_imagery": False, "paid_referee": False,
                 "status": "succeeded", "created": "2026-10-01T00:00:00Z",
                 "runs": [{"region": region, "status": "succeeded",
                           "run_dir": str(data / "pipeline_runs" / "20261001T000000Z")}]},
                {"job_id": "j2", "target": "local", "include_imagery": True, "paid_referee": False,
                 "status": "queued", "created": "2026-10-03T00:00:00Z",
                 "runs": [{"region": region, "status": "pending"}]}]
        out = RS.attempts(data, jobs=jobs)[region]
        assert [e["status"] for e in out] == ["queued", "failed", "succeeded"], out
        assert out[0]["job_id"] == "j2" and out[1]["job_id"] is None
        assert out[2]["include_imagery"] is False and out[2]["git"] == "abc123"


def test_is_stale():
    model = {"geometry": "g1", "yield": "y1"}
    assert RS.is_stale(None, model) is None
    assert RS.is_stale({"geometry": ["g1"], "yield": "y1"}, model) is False
    assert RS.is_stale({"geometry": ["g1", "g0"], "yield": "y1"}, model) is True
    assert RS.is_stale({"geometry": ["g1"], "yield": "y0"}, model) is True


def test_grids_hide_once_the_locality_is_combined():
    parent = "queenstown-lakes-district/queenstown/fernhill"
    cell_a, cell_b = parent + "/grid-0123456789abcdef", parent + "/grid-fedcba9876543210"
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        data = Path(tmp)
        for cell, grid_id in ((cell_a, "r00c00"), (cell_b, "r00c01")):
            write_json_atomic(web_dir(data) / "selections" / (cell.replace("/", "__") + ".json"),
                              {"parent_region": parent, "parent_linz_id": 2, "grid_size": 2,
                               "grid_ids": [grid_id], "buildings": 600})
        history = {cell_a: [{"status": "succeeded", "at": "2026-10-01T00:00:00Z"}],
                   cell_b: [{"status": "running", "at": "2026-10-02T00:00:00Z"}]}
        built = {cell_a: {"built_at": "2026-10-01T00:00:00Z"}}
        grids = RS._grids(data, history, built)
        grid = grids[parent]
        assert grid["grid_size"] == 2 and set(grid["cells"]) == {"r00c00", "r00c01"}
        assert grid["built_cells"] == 1 and grid["active_cells"] == 1
        assert grid["cells"]["r00c00"]["region"] == cell_a

        older = dict(history, **{parent: [{"status": "succeeded", "at": "2026-09-01T00:00:00Z"}]})
        assert parent in RS._grids(data, older, built)
        newer = dict(history, **{parent: [{"status": "succeeded", "at": "2026-10-03T00:00:00Z"}]})
        assert parent not in RS._grids(data, newer, built)


def test_catalog_routes():
    from test_pipeline_web_server import TOKEN, Client
    from test_pipeline_web_jobs import _manager
    from tools.pipeline_webui import estimate as E
    from tools.pipeline_webui.server import App, make_server
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        jobs = _manager(Path(tmp))
        estimates = E.EstimateStore(jobs.data, lambda region: None, disk_free=10**12,
                                    survey_fn=lambda bbox, region: {"name": "t", "elevation": "lidar"})
        app = App(TOKEN, data=jobs.data, root=ROOT, api_key_fn=lambda: KEY, http_get=FakeCatalogLinz(),
                  estimates=estimates, jobs=jobs)
        server = make_server(app, 0, quiet=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            client = Client(server.server_address[1])
            assert client.request("GET", "/api/catalog")[0] == 404
            assert client.request("GET", "/api/catalog/geometry")[0] == 404
            assert client.request("GET", "/api/catalog", token=None)[0] == 403
            code, state = client.request("POST", "/api/catalog/refresh", {})
            assert code == 202, (code, state)
            for _ in range(200):
                code, state = client.request("GET", "/api/catalog/refresh")
                if state["status"] != "running":
                    break
                time.sleep(0.05)
            assert state["status"] == "succeeded", state
            code, catalog = client.request("GET", "/api/catalog")
            assert code == 200 and len(catalog["regions"]) == 2
            code, fc = client.request("GET", "/api/catalog/geometry")
            assert code == 200 and len(fc["features"]) == 2
            code, status = client.request("GET", "/api/catalog/status")
            assert code == 200 and {"regions", "grids", "calibration", "model"} <= set(status), status
            assert KEY not in str(status)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("pipeline web catalogue tests passed")
