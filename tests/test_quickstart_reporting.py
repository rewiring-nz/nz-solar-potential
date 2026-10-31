"""Fast contract checks for numbered quickstart run artifacts.

Run: .venv/bin/python tests/test_quickstart_reporting.py
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.pipeline import (
    PipelineRun,
    _argument_parser,
    _dispatch_cloud_action,
    _evaluate_fetch,
    _remote_pipeline_command,
    _report_preview_port,
    _safe_area,
)

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


def test_cloud_pipeline_step_offset_keeps_labels_sequential_after_prechecks():
    with tempfile.TemporaryDirectory(prefix="quickstart-report-", dir=ROOT) as tmp:
        run = PipelineRun("test_area", Path(tmp) / "run", step_offset=6)
        run.begin(1)
        run.finish(1, "PASS", "remote pipeline preflight", 0)
        report = run.report_path.read_text(encoding="utf-8")
        combined = run.log_path.read_text(encoding="utf-8")
        assert "| 07 | Preflight: validate area and runtime |" in report
        assert "[QS-07] START Preflight: validate area and runtime" in combined


def test_failed_step_updates_report():
    with tempfile.TemporaryDirectory(prefix="quickstart-report-", dir=ROOT) as tmp:
        run = QuickstartRun("test_area", Path(tmp) / "run")
        from tools.pipeline import STEP
        layout = STEP["build_layout_geojson"]
        run.begin(layout, ["python", "src/run_stage.py", "build_layout_geojson", "test_area"])
        run.finish(layout, "FAIL", "command exited 1", 1)
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


def test_pipeline_cli_defaults_to_local_and_keeps_default_region():
    parser = _argument_parser("town_gorge_north")
    args = parser.parse_args([])
    assert args.mode == "local"
    assert args.region is None
    assert args.port is None


def test_cloud_lifecycle_action_parses_through_pipeline_entry_point():
    parser = _argument_parser("town_gorge_north")
    args = parser.parse_args([
        "--mode", "cloud", "--profile", "external-poc",
        "--cloud", "create", "--confirm",
        "solar-demo-12345/australia-southeast1-b/solar-quickstart",
    ])
    assert args.mode == "cloud"
    assert args.profile == "external-poc"
    assert args.cloud == "create"
    assert args.confirm == "solar-demo-12345/australia-southeast1-b/solar-quickstart"


def test_cloud_action_dispatches_selected_region_and_profile():
    from cloud_profile import CloudProfile

    profile = CloudProfile("external-poc", {"preview_port": 8765})
    with patch("tools.pipeline._run_pipeline", return_value=0) as run:
        assert _dispatch_cloud_action(
            profile, "run", "frankton_arm_lake", 8765, None,
        ) == 0
    run.assert_called_once_with(profile, "frankton_arm_lake", 8765, False)


def test_cloud_remote_command_tracks_only_new_pipeline_runs():
    import tempfile

    command = _remote_pipeline_command("frankton_arm_lake", 8765)
    assert "tools/pipeline.py frankton_arm_lake --port 8765 --no-open-browser" in command
    assert "--step-offset 6" in command
    # The remote run never asks again: the start-of-run answer travels with it.
    assert "SOLAR_DEMOLISHED_REFEREE=0 .venv/bin/python tools/pipeline.py" in command
    assert "SOLAR_DEMOLISHED_REFEREE=1 " in _remote_pipeline_command("frankton_arm_lake", 8765, True)
    assert "CLOUD_RUN_ID=%s" in command
    assert "before=$(find data/pipeline_runs" in command
    assert "data/pipeline_runs" in command
    with tempfile.TemporaryDirectory(prefix="quickstart-cloud-report-", dir=ROOT) as tmp:
        report = Path(tmp) / "report.md"
        report.write_text(
            "- **Map preview:** [http://127.0.0.1:9123/preview.html?lat=1&lng=2](url)\n",
            encoding="utf-8",
        )
        assert _report_preview_port(report) == 9123


if __name__ == "__main__":
    for test in (test_area_names_are_path_safe, test_report_and_logs_share_step_number_and_label,
                 test_cloud_pipeline_step_offset_keeps_labels_sequential_after_prechecks,
                 test_failed_step_updates_report, test_fetch_requires_wide_dem_after_fetch_stage,
                 test_child_output_redacts_api_key_before_logging,
                 test_pipeline_cli_defaults_to_local_and_keeps_default_region,
                 test_cloud_lifecycle_action_parses_through_pipeline_entry_point,
                 test_cloud_action_dispatches_selected_region_and_profile,
                 test_cloud_remote_command_tracks_only_new_pipeline_runs):
        test()
    print("quickstart reporting tests passed")
