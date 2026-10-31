"""Pipeline web panel: region catalogue, LINZ locality proxy and estimates.

Offline: LINZ responses come from tests/fixtures/linz_localities_wfs.json.
Run: .venv/bin/python tests/test_pipeline_web_data.py
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.pipeline_webui import estimate as E
from tools.pipeline_webui import locality_proxy as LP
from tools.pipeline_webui import region_catalog as RC
from tools.pipeline_webui.common import write_json_atomic

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "linz_localities_wfs.json").read_text())
KEY = "secret-test-key-123"


class FakeResponse:
    def __init__(self, payload, fail=False):
        self.payload, self.fail = payload, fail

    def raise_for_status(self):
        if self.fail:
            raise RuntimeError(f"500 for url https://data.linz.govt.nz/services;key={KEY}/wfs")

    def json(self):
        return self.payload


class FakeLinz:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, params, timeout):
        self.calls.append(params)
        cql = params["cql_filter"]
        if cql.startswith("BBOX("):
            return FakeResponse(FIXTURE["bbox"], self.fail)
        if cql.startswith("name IN"):
            return FakeResponse(FIXTURE["homonyms"], self.fail)
        return FakeResponse(FIXTURE["search"], self.fail)


def _built(data: Path, region: str, run_id: str, overall: str = "PASS", n: int = 100) -> None:
    write_json_atomic(data / "out" / region / "summary.json",
                      {"n": n, "kwp": 10.0, "kwh": 12000, "centroid": [168.66, -45.03],
                       "built_at": "2026-10-01T00:00:00Z", "git": "abc"})
    write_json_atomic(data / "pipeline_runs" / run_id / "status.json",
                      {"schema": 1, "area": region, "run_id": run_id, "overall": overall,
                       "steps": [{"key": "fetch", "seconds": 999.0},
                                 {"key": "estimates", "seconds": 50.0}]})


def test_catalog_states_and_links():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        data = Path(tmp)
        _built(data, "pilot", "20260101T000000Z")
        region = "queenstown-lakes-district/queenstown/sunshine-bay"
        write_json_atomic(data / "regions" / region / "locality.json",
                          {"name": "Sunshine Bay", "id": 2338, "bbox": [168.6, -45.05, 168.64, -45.03],
                           "buildings": 249})
        (data / "pipeline_runs" / "20260102T000000Z").mkdir(parents=True)
        (data / "pipeline_runs" / "20260102T000000Z" / "report.md").write_text(
            f"# Pipeline run: {region}\n\n**Status:** **FAIL**\n")
        fc = RC.catalog(data, use_cache=False)
        props = {f["properties"]["region"]: f for f in fc["features"]}
        assert props["pilot"]["properties"]["state"] == "built"
        assert props["pilot"]["properties"]["report_url"] == "/data/pipeline_runs/20260101T000000Z/report.md"
        assert props["pilot"]["properties"]["preview_url"].startswith("/preview.html?lat=-45.03")
        loc = props[region]
        assert loc["properties"]["state"] == "failed", loc["properties"]
        assert loc["geometry"]["type"] == "Polygon"
        assert RC.region_detail("../etc", data) is None


def test_bbox_validation():
    assert LP.parse_bbox("168.6,-45.1,168.7,-45.0") == [168.6, -45.1, 168.7, -45.0]
    for bad, status in (("1,2,3", 400), ("a,b,c,d", 400), ("170,-45,169,-44", 400),
                        ("168,-46,170,-44", 413)):
        try:
            LP.parse_bbox(bad)
        except LP.LocalityError as exc:
            assert exc.status == status, (bad, exc.status)
        else:
            raise AssertionError(bad)


def test_localities_drop_water_mark_homonyms_and_cache():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        data = Path(tmp)
        fake = FakeLinz()
        fc = LP.localities_in_bbox([168.6, -45.1, 168.7, -45.0], KEY, data, fake, now=1000)
        names = {f["properties"]["name"]: f["properties"] for f in fc["features"]}
        assert "Lake Wakatipu" not in names
        assert names["Frankton"]["region_id"].endswith("-1001"), names["Frankton"]
        assert names["Sunshine Bay"]["region_id"] == "queenstown-lakes-district/queenstown/sunshine-bay"
        assert all(k not in json.dumps(fc) for k in (KEY,))
        assert (data / "pipeline_web" / "cache" / "localities" / "2338.geojson").exists()
        calls = len(fake.calls)
        LP.localities_in_bbox([168.6, -45.1, 168.7, -45.0], KEY, data, fake, now=2000)
        assert len(fake.calls) == calls, "second request must come from the cache"


def test_locality_errors_never_carry_the_key():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        try:
            LP.localities_in_bbox([168.6, -45.1, 168.7, -45.0], KEY, Path(tmp), FakeLinz(fail=True))
        except LP.LocalityError as exc:
            assert KEY not in str(exc) and exc.status == 502
        else:
            raise AssertionError("expected LocalityError")
    try:
        LP.search("Frankton", "", FakeLinz())
    except LP.LocalityError as exc:
        assert exc.status == 503
    results = LP.search("Frank", KEY, FakeLinz())
    assert results and all(r["type"] not in LP.WATER_TYPES for r in results)
    assert results[0]["region_id"].endswith("-1001")
    assert results[0]["bbox"] == [168.7, -45.02, 168.74, -45.0]
    assert all(r["bbox"] is None for r in results[1:])


def test_estimate_arithmetic_and_cache_effects():
    defaults = E.load_defaults()
    size = {"buildings": 1000, "bbox": [0, 0, 0.01, 0.01], "bbox_km2": 2.0, "source": "test"}
    lidar = {"name": "x", "elevation": "lidar"}
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        data = Path(tmp)
        fresh = E.estimate_region("r", size, lidar, data, defaults, 0.5)
        expected_gb = 2.0 * (defaults["imagery_gb_per_km2"] + defaults["reference_imagery_gb_per_km2"]
                             + defaults["lidar_gb_per_km2"])
        assert abs(fresh["download_gb"] - round(expected_gb, 3)) < 1e-9
        assert fresh["compute_s"] == round(defaults["fixed_overhead_s"] + 500)
        for name in ("building_outlines.geojson", "pointcloud_tiles.txt", "imagery_mosaic.tif",
                     "reference_imagery_mosaic.tif"):
            (data / "regions" / "r").mkdir(parents=True, exist_ok=True)
            (data / "regions" / "r" / name).write_text("x")
        cached = E.estimate_region("r", size, lidar, data, defaults, 0.5)
        assert cached["download_gb"] == 0 and cached["download_s"] == 0
        synthetic = E.estimate_region("s", size, {"name": "y", "elevation": "synthetic"}, data, defaults, 0.5)
        assert any("No LiDAR" in w for w in synthetic["warnings"])
        uncovered = E.estimate_region("s", size, {"error": "no survey"}, data, defaults, 0.5)
        assert uncovered["blockers"]


def test_calibration_uses_finished_runs_only():
    defaults = E.load_defaults()
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        data = Path(tmp)
        assert E.calibrate(data, defaults)["source"] == "defaults"
        _built(data, "pilot", "20260101T000000Z", n=100)
        _built(data, "other", "20260102T000000Z", overall="FAIL", n=1)
        cal = E.calibrate(data, defaults)
        assert cal == {"source": "history", "seconds_per_building": 0.5, "runs_used": 1}, cal


def test_estimate_store_lifecycle_and_blockers():
    lookups = []

    def lookup(region):
        lookups.append(region)
        return {"buildings": 10, "bbox": [168.6, -45.05, 168.61, -45.04], "bbox_km2": 0.8}

    survey = lambda bbox, region: {"name": "t", "elevation": "lidar"}
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        data = Path(tmp)
        region = "queenstown-lakes-district/queenstown/frankton"
        store = E.EstimateStore(data, lookup, survey_fn=survey, disk_free=10**12)
        eid = store.start([region], background=False)
        rec = store.usable(eid)
        assert rec["totals"]["buildings"] == 10 and lookups == [region]
        assert rec["cloud_cost_usd"] == 0.0 and rec["paid_referee"]["default"] is False
        store.start([region], background=False)
        assert lookups == [region], "the size must be cached after the first lookup"
        for bad in (lambda: store.usable(eid, now=rec["expires_at"] + 1),):
            try:
                bad()
            except E.EstimateError as exc:
                assert exc.status == 409
            else:
                raise AssertionError("expired estimate accepted")
        tight = E.EstimateStore(data, lookup, survey_fn=survey, disk_free=1)
        blocked = tight.start([region], background=False)
        try:
            tight.usable(blocked)
        except E.EstimateError as exc:
            assert exc.status == 409
        else:
            raise AssertionError("blocked estimate accepted")
        for bad in ([], ["../x"], [f"r{i}" for i in range(E.MAX_REGIONS + 1)], "pilot"):
            try:
                store.start(bad)
            except E.EstimateError as exc:
                assert exc.status == 400
            else:
                raise AssertionError(bad)
        try:
            store.get("../../etc/passwd")
        except E.EstimateError as exc:
            assert exc.status == 404


if __name__ == "__main__":
    for test in (test_catalog_states_and_links, test_bbox_validation,
                 test_localities_drop_water_mark_homonyms_and_cache,
                 test_locality_errors_never_carry_the_key,
                 test_estimate_arithmetic_and_cache_effects,
                 test_calibration_uses_finished_runs_only,
                 test_estimate_store_lifecycle_and_blockers):
        test()
    print("pipeline web data tests passed")
