"""Smoke tests for the local quickstart map's HTTP and PMTiles range contract."""

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.pipeline_serve import RangeHandler
from tools.pipeline import PipelineRun, RUNS, QuickstartRun


def test_range_server_serves_static_and_partial_content():
    with tempfile.TemporaryDirectory(prefix="quickstart-map-") as temp:
        root = Path(temp)
        (root / "preview.html").write_text("<!doctype html><title>map</title>")
        payload = bytes(range(256)) * 3
        (root / "buildings.pmtiles").write_bytes(payload)
        handler = lambda *args, **kwargs: RangeHandler(*args, directory=str(root), **kwargs)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/preview.html") as response:
                assert response.status == 200
                assert b"<title>map</title>" in response.read()
            request = urllib.request.Request(base + "/buildings.pmtiles",
                                             headers={"Range": "bytes=16-47"})
            with urllib.request.urlopen(request) as response:
                assert response.status == 206
                assert response.headers["Content-Range"] == f"bytes 16-47/{len(payload)}"
                assert response.read() == payload[16:48]
            request = urllib.request.Request(base + "/buildings.pmtiles",
                                             headers={"Range": "bytes=999999-"})
            try:
                urllib.request.urlopen(request)
            except urllib.error.HTTPError as exc:
                assert exc.code == 416
            else:
                raise AssertionError("out-of-range PMTiles request should return 416")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


def test_map_report_lists_new_steps_and_output_paths():
    from tools.pipeline import STEPS
    assert [step["number"] for step in STEPS] == list(range(1, 19))
    assert STEPS[13]["label"] == "Emit regional map tiles"
    assert STEPS[14]["label"] == "Combine map dataset"
    assert STEPS[16]["label"] == "Validate map contract"
    assert RUNS.name == "pipeline_runs"
    assert STEPS[14]["outputs"].startswith("data/ map contract")
    assert STEPS[15]["outputs"].startswith("data/terrain/")


def test_brewfile_installs_tippecanoe_cli_suite():
    brewfile = (ROOT / "Brewfile").read_text(encoding="utf-8")
    assert 'brew "tippecanoe"' in brewfile


def test_combine_regions_cli_parses_options():
    import subprocess

    result = subprocess.run(
        [sys.executable, "src/combine_regions.py", "--help"],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--skip-markup-lines" in result.stdout


def test_pipeline_run_directory_structure():
    with tempfile.TemporaryDirectory(prefix="pipeline-run-", dir=ROOT) as temp:
        run_dir = Path(temp) / "run"
        run = QuickstartRun("test_area", run_dir)
        assert run.log_path.is_file()
        assert run.report_path.is_file()
        assert set(p.name for p in run_dir.iterdir()) == {"run.log", "report.md"}
        assert not (run_dir / "run.json").exists()
        assert not list(run_dir.glob("step-*.log"))


def test_pipeline_preview_targets_shared_data_map():
    from tools.pipeline import _start_preview_server

    with tempfile.TemporaryDirectory(prefix="pipeline-preview-", dir=ROOT) as temp:
        data_dir = Path(temp) / "data"
        data_dir.mkdir()
        (data_dir / "buildings.pmtiles").write_bytes(bytes(range(64)))
        run = QuickstartRun("test_area", Path(temp) / "run")
        run.metadata["bbox"] = [168.6, -45.1, 168.7, -45.0]
        ok, url = _start_preview_server(
            "test_area", run, Path(sys.executable), preferred_port=0, open_browser=False)
        try:
            assert ok
            assert "/preview.html?lat=" in url
            assert run.metadata["map_preview_url"] == url
            assert (run.run_dir / "map-preview-server.pid").is_file()
            assert (run.run_dir / "map-preview-server.log").is_file()
            assert not (run.run_dir / "run.json").exists()
            assert not list(run.run_dir.glob("step-*.log"))
        finally:
            pid = int((run.run_dir / "map-preview-server.pid").read_text())
            import os
            import signal
            os.kill(pid, signal.SIGTERM)


if __name__ == "__main__":
    test_range_server_serves_static_and_partial_content()
    test_map_report_lists_new_steps_and_output_paths()
    test_combine_regions_cli_parses_options()
    test_pipeline_run_directory_structure()
    test_pipeline_preview_targets_shared_data_map()
    test_brewfile_installs_tippecanoe_cli_suite()
    print("quickstart map tests passed")
