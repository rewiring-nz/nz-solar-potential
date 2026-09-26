"""Fast contract checks for numbered quickstart run artifacts.

Run: .venv/bin/python tests/test_quickstart_reporting.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.quickstart_run import QuickstartRun, _safe_area


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
        record = run.begin(2, ["python", "src/fetch_regions.py", "test_area"])
        step_log = run.run_dir / record["log"]
        with step_log.open("a", encoding="utf-8") as f:
            f.write("[QS-02] sample output\n")
        run.finish(2, "DEGRADED", "imagery unavailable; continue with reduced evidence", 0)
        report = run.report_path.read_text(encoding="utf-8")
        combined = run.log_path.read_text(encoding="utf-8")
        detail = step_log.read_text(encoding="utf-8")
        assert "| 02 | Fetch outlines, elevation, imagery, and point cloud |" in report
        assert "**DEGRADED**" in report
        assert "step-02-fetch-outlines-elevation-imagery-and-point-cloud.log" in report
        assert "[QS-02] START Fetch outlines, elevation, imagery, and point cloud" in combined
        assert "[QS-02] sample output" in detail
        assert run.overall == "DEGRADED"


def test_failed_step_updates_machine_record_and_report():
    with tempfile.TemporaryDirectory(prefix="quickstart-report-", dir=ROOT) as tmp:
        run = QuickstartRun("test_area", Path(tmp) / "run")
        run.begin(4, ["python", "src/run_stage.py", "build_layout_geojson", "test_area"])
        run.finish(4, "FAIL", "command exited 1", 1)
        report = run.report_path.read_text(encoding="utf-8")
        import json
        state = json.loads(run.state_path.read_text(encoding="utf-8"))
        assert state["status"] == "FAIL"
        assert state["steps"][3]["exit_code"] == 1
        assert "**FAIL**" in report
        assert "command exited 1" in report


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
        assert "sensitive-test-key" not in (run.run_dir / run.records[0]["log"]).read_text(encoding="utf-8")


if __name__ == "__main__":
    for test in (test_area_names_are_path_safe, test_report_and_logs_share_step_number_and_label,
                 test_failed_step_updates_machine_record_and_report,
                 test_child_output_redacts_api_key_before_logging):
        test()
    print("quickstart reporting tests passed")
