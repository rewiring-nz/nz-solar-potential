"""Fast contract checks for numbered quickstart run artifacts.

Run: .venv/bin/python tests/test_quickstart_reporting.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.pipeline import PipelineRun, _evaluate_fetch, _safe_area

QuickstartRun = PipelineRun


def test_area_names_are_path_safe():
    assert _safe_area("my-area_2") == "my-area_2"
    for unsafe in ("", "../other", "name/child", ".hidden", "a" * 65):
        try:
            _safe_area(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe area accepted: {unsafe!r}")


def test_report_and_logs_share_step_number_and_label():
    with tempfile.TemporaryDirectory(prefix="quickstart-report-", dir=ROOT) as tmp:
        run = QuickstartRun("test_area", Path(tmp) / "run")
        run.begin(2, ["python", "src/fetch_regions.py", "test_area"])
        run.event(2, "sample output")
        run.finish(2, "DEGRADED", "imagery unavailable; continue with reduced evidence", 0)
        report = run.report_path.read_text(encoding="utf-8")
        combined = run.log_path.read_text(encoding="utf-8")
        assert "| 02 | Fetch outlines, elevation, imagery, and point cloud |" in report
        assert "**DEGRADED**" in report
        assert "[QS-02] START Fetch outlines, elevation, imagery, and point cloud" in combined
        assert "[QS-02] sample output" in combined
        assert run.overall == "DEGRADED"
        assert not list(run.run_dir.glob("step-*.log"))
        assert not (run.run_dir / "run.json").exists()


def test_failed_step_updates_report():
    with tempfile.TemporaryDirectory(prefix="quickstart-report-", dir=ROOT) as tmp:
        run = QuickstartRun("test_area", Path(tmp) / "run")
        run.begin(4, ["python", "src/run_stage.py", "build_layout_geojson", "test_area"])
        run.finish(4, "FAIL", "command exited 1", 1)
        report = run.report_path.read_text(encoding="utf-8")
        assert run.overall == "FAIL"
        assert "**FAIL**" in report
        assert "command exited 1" in report
        assert not (run.run_dir / "run.json").exists()


def test_fetch_requires_wide_dem_after_fetch_stage():
    with tempfile.TemporaryDirectory(prefix="quickstart-fetch-", dir=ROOT) as tmp:
        import json
        import tools.pipeline as pipeline_module

        data_dir = Path(tmp) / "data"
        area_dir = data_dir / "regions" / "region"
        area_dir.mkdir(parents=True)
        outlines = area_dir / "building_outlines.geojson"
        outlines.write_text(json.dumps({"type": "FeatureCollection", "features": [{}]}))
        (area_dir / "dsm_mosaic.tif").write_bytes(b"dsm")
        original_data = pipeline_module.DATA
        pipeline_module.DATA = data_dir
        try:
            status, comment = _evaluate_fetch(area_dir, "")
        finally:
            pipeline_module.DATA = original_data
        assert status == "FAIL"
        assert "dem_wide_mosaic.tif" in comment


def test_child_output_redacts_api_key_before_logging():
    with tempfile.TemporaryDirectory(prefix="quickstart-report-", dir=ROOT) as tmp:
        run = QuickstartRun("test_area", Path(tmp) / "run")
        run.secrets.add("sensitive-test-key")
        code, output = run.run_command(
            1, [sys.executable, "-c", "print('sensitive-test-key')"])
        run.finish(1, "PASS", "redaction test", code)
        assert code == 0
        assert "sensitive-test-key" not in output
        assert "[REDACTED]" in output
        assert "sensitive-test-key" not in run.log_path.read_text(encoding="utf-8")
        assert "[REDACTED]" in run.log_path.read_text(encoding="utf-8")


if __name__ == "__main__":
    for test in (test_area_names_are_path_safe, test_report_and_logs_share_step_number_and_label,
                 test_failed_step_updates_report, test_fetch_requires_wide_dem_after_fetch_stage,
                 test_child_output_redacts_api_key_before_logging):
        test()
    print("quickstart reporting tests passed")
