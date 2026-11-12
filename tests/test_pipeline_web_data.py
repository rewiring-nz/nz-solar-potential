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
from tools.pipeline_webui import grid_proxy as GP
from tools.pipeline_webui import region_catalog as RC
from tools.pipeline_webui.common import web_dir, write_json_atomic
from src import grid_selection as GS
from src.dataset_timing import format_duration, measure, phase, transfer

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
        assert props["pilot"]["properties"]["preview_url"].startswith("/rooftop-solar.html?lat=-45.03")
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


def test_locality_feature_resolves_grid_parent_and_caches_geometry():
    region = "queenstown-lakes-district/queenstown/frankton-1001"
    grid = region + "/grid-0123456789abcdef"
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        data = Path(tmp)
        fake = FakeLinz()
        feature = LP.feature_for_region(grid, KEY, data, fake)
        assert feature["properties"]["region_id"] == region
        assert feature["properties"]["linz_id"] == 1001
        assert feature["geometry"]["type"] == "Polygon"
        assert LP.feature_for_region(region, KEY, data, fake) == feature
        try:
            LP.feature_for_region("pilot", KEY, data, fake)
        except LP.LocalityError as exc:
            assert exc.status == 404
        else:
            raise AssertionError("legacy areas must not be resolved as LINZ localities")


def test_grid_cells_are_clipped_to_the_locality_and_configurable():
    region = "queenstown-lakes-district/queenstown/sunshine-bay"
    parent = {
        "type": "Feature", "properties": {"id": 2338, "name": "Sunshine Bay",
        "major_name": "Queenstown", "territorial_authority": "Queenstown-Lakes District"},
        "geometry": {"type": "Polygon", "coordinates": [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
        ]},
    }

    class FakeLocality:
        def __init__(self):
            self.calls = []

        def __call__(self, url, params, timeout):
            self.calls.append(params)
            return FakeResponse({"features": [parent]})

    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        fake = FakeLocality()
        fc = GP.grid_cells(region, 2338, KEY, fake, grid_size=3)
        assert fc["grid_size"] == 3
        assert len(fc["features"]) == 9
        assert fc["features"][0]["properties"]["grid_id"] == "r01c01"
        assert fc["features"][4]["geometry"]["type"] == "Polygon"
        assert len(fake.calls) == 1
        try:
            GP.grid_cells(region, 2338, KEY, fake, grid_size=11)
        except LP.LocalityError as exc:
            assert exc.status == 400
        else:
            raise AssertionError("out-of-range grid size accepted")
        try:
            GP.grid_cells("not/a/locality", 2338, KEY, fake)
        except LP.LocalityError as exc:
            assert exc.status == 400
        else:
            raise AssertionError("non-locality parent accepted")


def test_grid_selection_validation_identity_and_boundary_ownership():
    region = "queenstown-lakes-district/queenstown/sunshine-bay"
    assert GS.selection_id(region, ["r01c02", "r01c01"], 4) == GS.selection_id(
        region, ["r01c01", "r01c02"], 4)
    for bad in (None, {}, {"parent_region": region, "parent_linz_id": 2338, "grid_ids": []},
                {"parent_region": "pilot", "parent_linz_id": 2338, "grid_ids": ["r01c01"],
                 "grid_size": 4}):
        try:
            E.validate_selection(bad)
        except E.EstimateError:
            pass
        else:
            raise AssertionError(f"invalid selection accepted: {bad}")
    grid_selection = E.validate_selection({
        "parent_region": region, "parent_linz_id": 2338,
        "grid_size": 4, "grid_ids": ["r01c01"]})
    assert grid_selection["grid_ids"] == ["r01c01"]
    assert GS.selection_id(region, ["r01c01"], 4).startswith(region + "/grid-")
    try:
        E.validate_selection({
            "parent_region": region, "parent_linz_id": 2338,
            "grid_size": 11, "grid_ids": ["r01c01"]})
    except E.EstimateError:
        pass
    else:
        raise AssertionError("out-of-range grid selection accepted")

    import pyproj
    from shapely.geometry import box, mapping
    to_nztm = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    boundary_lon = 168.66
    low, high = (168.65, 168.66), (168.66, 168.67)
    cells = [
        {"properties": {"grid_id": "r01c01"},
         "geometry": mapping(box(low[0], -45.05, low[1], -45.04))},
        {"properties": {"grid_id": "r01c02"},
         "geometry": mapping(box(high[0], -45.05, high[1], -45.04))},
    ]
    x0, y0 = to_nztm(boundary_lon, -45.05)
    x1, y1 = to_nztm(boundary_lon, -45.04)
    x, y = (x0 + x1) / 2, (y0 + y1) / 2
    whole_building = {"type": "Polygon", "coordinates": [[
        [x - 2, y - 2], [x + 2, y - 2], [x + 2, y + 2], [x - 2, y + 2], [x - 2, y - 2]]]}
    outline = {"type": "Feature", "properties": {"id": 77}, "geometry": whole_building}
    fc = {"type": "FeatureCollection", "features": [outline]}
    owned_low = GS.select_buildings(fc, cells, ["r01c01"])
    owned_high = GS.select_buildings(fc, cells, ["r01c02"])
    assert owned_low["features"] == [outline]
    assert owned_high["features"] == []
    assert owned_low["features"][0]["geometry"] == whole_building


def test_grid_materialisation_is_parent_scoped_and_resumable():
    import pyproj
    from shapely.geometry import box, mapping
    from shapely.ops import transform

    region = "queenstown-lakes-district/queenstown/sunshine-bay"
    to_nztm = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    cell = {"type": "Feature", "properties": {"grid_id": "r01c01"},
             "geometry": mapping(box(168.65, -45.05, 168.66, -45.04))}
    outside = {"type": "Feature", "properties": {"grid_id": "r01c02"},
               "geometry": mapping(box(168.66, -45.05, 168.67, -45.04))}
    x, y = to_nztm(168.655, -45.045)
    outline = {"type": "Feature", "properties": {"building_id": 77},
               "geometry": mapping(box(x - 3, y - 3, x + 3, y + 3))}
    props = {"id": 2338, "name": "Sunshine Bay", "name_ascii": "Sunshine Bay",
             "major_name": "Queenstown", "major_name_ascii": "Queenstown",
             "territorial_authority": "Queenstown-Lakes District",
             "territorial_authority_ascii": "Queenstown-Lakes District"}
    original_resolve, original_wfs = GS.localities.resolve, GS.localities._wfs
    GS.localities.resolve = lambda _key, _region: props
    calls = []

    def fake_wfs(_key, _layer, _cql, **_kwargs):
        calls.append(_cql)
        return [outline]

    GS.localities._wfs = fake_wfs
    try:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            data = Path(tmp)
            parent_file = data / "regions" / region / "building_outlines.geojson"
            write_json_atomic(parent_file, {"type": "FeatureCollection", "features": []})
            original_parent = parent_file.read_text()
            request = data / "selection-request.json"
            request.write_text(json.dumps({
                "parent_region": region, "parent_linz_id": 2338,
                "grid_size": 4, "grid_ids": ["r01c01"],
                "areas": [cell, outside],
            }))
            manifest = GS.materialise(request, data, KEY)
            assert manifest["selection_id"].startswith(region + "/grid-")
            area = data / "regions" / manifest["selection_id"]
            assert area.parent == data / "regions" / region
            assert manifest["grid_ids"] == ["r01c01"]
            assert manifest["building_ids"] == ["77"]
            assert manifest["building_selection_rule"] == GS.OWNERSHIP_RULE
            written = json.loads((area / "building_outlines.geojson").read_text())["features"]
            assert len(written) == 1, written
            assert written == json.loads(json.dumps([outline]))
            assert parent_file.read_text() == original_parent
            assert GS.materialise(request, data, KEY) == json.loads(json.dumps(manifest))
            acquisitions = json.loads((area / "acquisition_timings.json").read_text())["acquisitions"]
            assert [item["outcome"] for item in acquisitions] == [
                "downloaded", "reused_from_cache"]
            assert len(calls) == 1
    finally:
        GS.localities.resolve, GS.localities._wfs = original_resolve, original_wfs


def test_empty_grid_selection_reports_actionable_worker_error():
    import contextlib
    import io
    from tools.pipeline_webui import geo_worker

    props = {
        "id": 2338, "name": "Sunshine Bay", "major_name": "Queenstown",
        "territorial_authority": "Queenstown-Lakes District",
    }
    original_resolve, original_wfs = GS.localities.resolve, GS.localities._wfs
    GS.localities.resolve = lambda _key, _region: props
    GS.localities._wfs = lambda *_args, **_kwargs: []
    try:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            data = Path(tmp)
            request = data / "selection-request.json"
            request.write_text(json.dumps({
                "parent_region": "queenstown-lakes-district/queenstown/sunshine-bay",
                "parent_linz_id": 2338,
                "grid_size": 4,
                "grid_ids": ["r01c01"],
                "areas": [{
                    "type": "Feature",
                    "properties": {"grid_id": "r01c01"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[168.65, -45.05], [168.66, -45.05],
                                         [168.66, -45.04], [168.65, -45.04],
                                         [168.65, -45.05]]],
                    },
                }],
            }))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = geo_worker.main(["selection", str(request), str(data)])
            assert status == 1
            result = json.loads(output.getvalue().strip().splitlines()[-1])
            assert result["error"] == (
                "no LINZ building outlines were found in the selected grid cells; "
                "choose cells containing buildings")
    finally:
        GS.localities.resolve, GS.localities._wfs = original_resolve, original_wfs


def test_dataset_timing_format_and_failure_record():
    assert format_duration(3661.4) == "01:01:01"
    assert format_duration(100 * 3600) == "100:00:00"
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        path = Path(tmp) / "acquisition_timings.json"
        try:
            with measure("building outlines", "LINZ layer 101290", "area",
                         "downloaded", path) as timing:
                with transfer():
                    timing.set_outcome("partially_resumed")
                with phase("export_wait"):
                    pass
                with phase("local_prepare"):
                    pass
                    raise RuntimeError("sensitive URL must not be logged")
        except RuntimeError:
            pass
        record = json.loads(path.read_text())["acquisitions"][0]
        assert record["outcome"] == "failed"
        assert record["elapsed_hms"] == format_duration(record["elapsed_seconds"])
        assert record["transfer_seconds"] is not None
        assert record["phases"]["export_wait"]["elapsed_hms"] == "00:00:00"
        assert record["phases"]["local_prepare"]["elapsed_hms"] == "00:00:00"
        assert record["error_type"] == "RuntimeError"
        assert "sensitive URL" not in path.read_text()
        with measure("building outlines", "LINZ layer 101290", "area",
                     "reused_from_cache", path):
            pass
        records = json.loads(path.read_text())["acquisitions"]
        assert [item["outcome"] for item in records] == ["failed", "reused_from_cache"]
        assert records[0]["error_type"] == "RuntimeError"
        assert "error_type" not in records[1]


def test_disjoint_raster_fetch_creates_a_resumable_parts_directory():
    import importlib
    import types
    from unittest.mock import patch

    rasterio = types.ModuleType("rasterio")
    rasterio.__path__ = []
    raster_merge = types.ModuleType("rasterio.merge")
    raster_merge.merge = lambda *_args, **_kwargs: None
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *_args, **_kwargs: None
    with patch.dict(sys.modules, {"rasterio": rasterio, "rasterio.merge": raster_merge,
                                  "dotenv": dotenv}):
        fetch_regions = importlib.import_module("src.fetch_regions")
        original_fetch = fetch_regions.fetch_raster_chunked
        original_merge = fetch_regions._merge_footprints
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out_dir = Path(tmp)
                seen = []

                def fake_chunk(footprint, _key, _layer, _name, directory, _format):
                    assert directory.is_dir()
                    part = directory / f"part-{len(seen)}.tif"
                    part.touch()
                    seen.append(footprint)
                    return part

                fetch_regions.fetch_raster_chunked = fake_chunk
                fetch_regions._merge_footprints = lambda paths, destination: (paths, destination)
                result = fetch_regions.fetch_raster_footprints(
                    [[1, 2, 3, 4], [5, 6, 7, 8]], KEY, 42, "dsm", out_dir, "grid")
                assert seen == [[1, 2, 3, 4], [5, 6, 7, 8]]
                assert (out_dir / "dsm_footprints").is_dir()
                assert result[1] == out_dir / "dsm_mosaic.tif"
                assert fetch_regions.raster_acquisition_outcome(out_dir, "dsm") == "downloaded"
                (out_dir / "dsm_footprints" / "part0000_mosaic.tif").touch()
                assert fetch_regions.raster_acquisition_outcome(
                    out_dir, "dsm") == "partially_resumed"
                (out_dir / "dsm_mosaic.tif").touch()
                assert fetch_regions.raster_acquisition_outcome(
                    out_dir, "dsm") == "reused_from_cache"
        finally:
            fetch_regions.fetch_raster_chunked = original_fetch
            fetch_regions._merge_footprints = original_merge


def test_wide_dem_uses_only_disjoint_selection_coverage():
    import importlib
    import types
    from unittest.mock import patch

    rasterio = types.ModuleType("rasterio")
    rasterio.__path__ = []
    raster_merge = types.ModuleType("rasterio.merge")
    raster_merge.merge = lambda *_args, **_kwargs: None
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *_args, **_kwargs: None
    with patch.dict(sys.modules, {"rasterio": rasterio, "rasterio.merge": raster_merge,
                                  "dotenv": dotenv}):
        wide_dem = importlib.import_module("src.fetch_dem_wide")
        region = "qld/queenstown/frankton/grid-0123456789abcdef"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "regions" / region / "locality.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"acquisition_footprints": [
                [172.0, -42.0, 172.01, -41.99],
                [175.0, -40.0, 175.01, -39.99],
            ]}))
            area = wide_dem.wide_dem_area_wgs84([region], tmp)
            assert len(area.geoms) == 2
            bbox = wide_dem.wide_dem_bbox_wgs84([region], tmp)
            assert bbox[0] > 170 and bbox[2] < 177, bbox


def test_estimate_arithmetic_and_cache_effects():
    defaults = E.load_defaults()
    size = {"buildings": 1000, "bbox": [0, 0, 0.01, 0.01], "bbox_km2": 2.0, "source": "test"}
    lidar = {"name": "x", "elevation": "lidar",
             "source_layers": {"pointcloud_bulk_url": "https://example.test/pointcloud"}}
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        data = Path(tmp)
        fresh = E.estimate_region("r", size, lidar, data, defaults, 0.5)
        expected_gb = 2.0 * (defaults["imagery_gb_per_km2"] + defaults["reference_imagery_gb_per_km2"]
                             + defaults["dsm_gb_per_km2"] + defaults["lidar_gb_per_km2"])
        assert abs(fresh["download_gb"] - round(expected_gb, 3)) < 1e-9
        assert fresh["compute_s"] == round(defaults["fixed_overhead_s"] + 500)
        without_imagery = E.estimate_region("r", size, lidar, data, defaults, 0.5,
                                            include_imagery=False)
        expected_without_imagery = 2.0 * (defaults["dsm_gb_per_km2"]
                                          + defaults["lidar_gb_per_km2"])
        assert abs(without_imagery["download_gb"]
                   - round(expected_without_imagery, 3)) < 1e-9
        assert any("excluded" in warning for warning in without_imagery["warnings"])
        for name in ("building_outlines.geojson", "pointcloud_tiles.txt", "dsm_mosaic.tif",
                     "imagery_mosaic.tif",
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
        assert rec["cloud_cost_nzd"] == "0" and rec["cloud"] is None and rec["paid_referee"]["default"] is False
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


PROJ_PACKAGES = ("pyproj", "rasterio", "fiona", "osgeo", "pyogrio", "geopandas")


def test_server_process_never_loads_proj():
    """PROJ in the server makes every later fork crash on macOS; it runs in geo_worker."""
    import subprocess
    # Keep this test's own subprocess safe if an earlier test imported PROJ.
    probe = f"""
import sys, tempfile, pathlib
sys.path.insert(0, {str(ROOT)!r})
import config
from tools.pipeline_webui import server, estimate as E
from tools.pipeline_webui import geo_worker
from src.grid_selection import selection_id
selection_id("queenstown-lakes-district/queenstown/sunshine-bay",
             ["r01c01"], 4)
region = next(iter(config.REGIONS))
with tempfile.TemporaryDirectory() as d:
    size = E.region_size(region, pathlib.Path(d), None)
    assert size["bbox_km2"] > 0, size
    catalog = geo_worker.run(["catalog", d])
    assert catalog["availability"] == {{"source": "local", "local_cache": True}}
loaded = sorted({{m.split(".")[0] for m in sys.modules}} & set({PROJ_PACKAGES!r}))
print("LOADED", loaded)
"""
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         timeout=120, check=False,
                         close_fds=sys.platform != "darwin")
    assert out.returncode == 0, out.stderr[-2000:]
    assert "LOADED []" in out.stdout, out.stdout


def test_geo_worker_keeps_the_key_out_of_arguments_and_errors():
    from tools.pipeline_webui import geo_worker as G
    seen = {}

    def fake_run(args, env=None, python=sys.executable):
        seen.update(args=args, env=env)
        return {"buildings": 3, "bbox": [1, 2, 3, 4], "bbox_km2": 1.0}
    real_run, G.run = G.run, fake_run
    try:
        assert E.linz_size_lookup(lambda: KEY)("a/b/c")["buildings"] == 3
    finally:
        G.run = real_run
    assert KEY not in " ".join(seen["args"]) and seen["env"] == {"LINZ_API_KEY": KEY}

    import contextlib
    import io
    real_size = G._size
    G._size = lambda region: (_ for _ in ()).throw(
        RuntimeError(f"500 for https://data.linz.govt.nz/services;key={KEY}/wfs"))
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert G.main(["size", "a/b/c"]) == 1
    finally:
        G._size = real_size
    assert KEY not in buf.getvalue() and "RuntimeError" in buf.getvalue()


def test_geo_worker_uses_spawn_safe_launch_arguments():
    import subprocess
    from unittest.mock import patch
    from tools.pipeline_webui import geo_worker as G

    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='{"ok": true}\n', stderr="")
    captured = {}

    def fake_run(args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return completed

    with patch.object(G.subprocess, "run", side_effect=fake_run):
        assert G.run(["probe"]) == {"ok": True}

    assert captured["args"] == [
        sys.executable, str(ROOT / "tools" / "pipeline_webui" / "geo_worker.py"), "probe"]
    assert "cwd" not in captured["kwargs"]
    assert captured["kwargs"]["close_fds"] is (sys.platform != "darwin")


def test_estimate_store_preserves_safe_worker_errors():
    from unittest.mock import patch

    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        store = E.EstimateStore(Path(tmp), disk_free=10**12)
        with patch.object(E, "estimate", side_effect=LookupError(
                "area calculation worker returned no result (terminated by signal 11)")):
            estimate_id = store.start(["pilot"], background=False)
        record = store.get(estimate_id)
        assert record["status"] == "error"
        assert record["error"] == "area calculation worker returned no result (terminated by signal 11)"


if __name__ == "__main__":
    for test in (test_catalog_states_and_links, test_bbox_validation,
                 test_localities_drop_water_mark_homonyms_and_cache,
                 test_locality_errors_never_carry_the_key,
                 test_locality_feature_resolves_grid_parent_and_caches_geometry,
                 test_grid_cells_are_clipped_to_the_locality_and_configurable,
                 test_grid_selection_validation_identity_and_boundary_ownership,
                 test_grid_materialisation_is_parent_scoped_and_resumable,
                 test_empty_grid_selection_reports_actionable_worker_error,
                 test_dataset_timing_format_and_failure_record,
                 test_disjoint_raster_fetch_creates_a_resumable_parts_directory,
                 test_wide_dem_uses_only_disjoint_selection_coverage,
                 test_estimate_arithmetic_and_cache_effects,
                 test_calibration_uses_finished_runs_only,
                 test_estimate_store_lifecycle_and_blockers,
                 test_server_process_never_loads_proj,
                 test_geo_worker_keeps_the_key_out_of_arguments_and_errors,
                 test_geo_worker_uses_spawn_safe_launch_arguments,
                 test_estimate_store_preserves_safe_worker_errors):
        test()
    print("pipeline web data tests passed")
