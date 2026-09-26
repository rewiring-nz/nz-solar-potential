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

from tools.quickstart_serve import RangeHandler
from tools.quickstart_run import QuickstartRun, _prepare_preview_bundle


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


def test_map_report_lists_new_steps_and_isolated_output_paths():
    from tools.quickstart_run import STEPS
    assert [step["number"] for step in STEPS] == list(range(1, 19))
    assert STEPS[13]["label"] == "Emit regional map tiles"
    assert STEPS[14]["label"] == "Combine isolated map dataset"
    assert STEPS[16]["label"] == "Validate map contract and prepare preview"


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


def test_preview_bundle_isolated_data_base():
    with tempfile.TemporaryDirectory(prefix="quickstart-map-bundle-", dir=ROOT) as temp:
        run_dir = Path(temp) / "run"
        run = QuickstartRun("test_area", run_dir)
        run.metadata = {"bbox": [168.6, -45.1, 168.7, -45.0]}
        preview = _prepare_preview_bundle("test_area", run, run_dir / "map-data")
        assert preview.is_file()
        site = (run_dir / "map-preview" / "site-config.js").read_text(encoding="utf-8")
        relative = (run_dir / "map-data").relative_to(ROOT).as_posix()
        config = json.loads(site.removeprefix("window.SITE = ").removesuffix(";\n"))
        assert config["dataBase"] == f"/{relative}/"
        assert abs(config["defaultView"]["center"][0] - 168.65) < 1e-9
        assert abs(config["defaultView"]["center"][1] + 45.05) < 1e-9


if __name__ == "__main__":
    test_range_server_serves_static_and_partial_content()
    test_map_report_lists_new_steps_and_isolated_output_paths()
    test_combine_regions_cli_parses_options()
    test_preview_bundle_isolated_data_base()
    test_brewfile_installs_tippecanoe_cli_suite()
    print("quickstart map tests passed")
