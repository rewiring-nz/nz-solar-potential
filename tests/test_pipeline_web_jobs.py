"""Pipeline web panel: the job runner, with a fake pipeline script.

Run: .venv/bin/python tests/test_pipeline_web_jobs.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.pipeline_webui.jobs import JobError, JobManager, progress, run_fraction
from tools.pipeline_webui.common import write_json_atomic

# A stand-in for tools/pipeline.py: announces its run directory, writes
# status.json, then exits 0, or 1 for region "bad", or waits for SIGINT for "slow".
FAKE = r'''
import json, os, signal, sys, time
from pathlib import Path
data, region = Path(sys.argv[1]), sys.argv[2]
run = data / "pipeline_runs" / ("run-" + region.replace("/", "_"))
run.mkdir(parents=True, exist_ok=True)
print("PIPELINE_RUN_DIR=" + str(run), flush=True)
def status(overall, s):
    (run / "status.json").write_text(json.dumps({"overall": overall, "area": region,
        "steps": [{"key": "a", "status": s, "seconds": 1}, {"key": "b", "status": "PENDING"}]}))
status("RUNNING", "PASS")
if region == "slow":
    def stop(*_):
        print("interrupted cleanly", flush=True)
        status("FAIL", "INTERRUPTED")
        sys.exit(130)
    signal.signal(signal.SIGINT, stop)
    for _ in range(600):
        time.sleep(0.05)
    sys.exit(0)
if region != "bad":
    out = data / "out" / region
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text("{}")
print("built " + region, flush=True)
sys.exit(1 if region == "bad" else 0)
'''

COMBINE = r'''
import sys
print("combined " + " ".join(sys.argv[1:]), flush=True)
'''


def _manager(tmp: Path, grace: float = 5.0) -> JobManager:
    (tmp / "fake.py").write_text(FAKE)
    (tmp / "combine.py").write_text(COMBINE)
    data = tmp / "data"
    data.mkdir()
    return JobManager(
        data, ROOT, sys.executable,
        command_builder=lambda py, region, paid: [py, str(tmp / "fake.py"), str(data), region],
        combine_builder=lambda py, regions: [py, str(tmp / "combine.py"), *regions],
        cancel_grace_s=grace)


def _estimate(*regions):
    return {"estimate_id": "0123456789abcdef", "regions": [{"region": r} for r in regions],
            "totals": {"total_s": 100}, "paid_referee": {"usd_upper": 1.0}}


def _wait(manager, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if manager.load(job_id)["status"] in ("succeeded", "failed", "cancelled"):
            return manager.view(job_id)
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_regions_run_in_order_failures_continue_and_combine_runs():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        m = _manager(Path(tmp))
        job_id = m.start(_estimate("a", "bad", "b"))
        try:
            m.start(_estimate("c"))
        except JobError as exc:
            assert exc.status == 409
        else:
            raise AssertionError("second job started while one is running")
        job = _wait(m, job_id)
        assert [r["status"] for r in job["runs"]] == ["succeeded", "failed", "succeeded"], job["runs"]
        assert job["status"] == "failed"
        assert job["runs"][0]["run_dir"].endswith("run-a")
        assert job["runs"][0]["status_json"]["overall"] == "RUNNING"
        assert job["final_combine"]["status"] == "succeeded"
        assert job["final_combine"]["regions"] == ["a", "b"]
        log = m.log(job_id)
        assert "built a" in log["text"] and "combined a b" in log["text"] and log["eof"]
        tail = m.log(job_id, offset=log["next_offset"] - 5)
        assert len(tail["text"]) == 5
        assert job["progress"] == 1.0 or job["status"] == "failed"


def test_single_region_success_needs_no_extra_combine():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        m = _manager(Path(tmp))
        job = _wait(m, m.start(_estimate("a"), paid_referee=True))
        assert job["status"] == "succeeded" and job["progress"] == 1.0 and job["eta_s"] == 0
        assert job["final_combine"]["status"] == "not needed"
        assert job["paid_referee"] is True and job["approved"]["paid_referee_usd_upper"] == 1.0


def test_cancel_sends_sigint_and_skips_the_rest():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        m = _manager(Path(tmp))
        job_id = m.start(_estimate("slow", "a"))
        deadline = time.time() + 10
        while not (m.load(job_id)["runs"][0].get("run_dir")) and time.time() < deadline:
            time.sleep(0.05)
        m.cancel(job_id)
        job = _wait(m, job_id)
        assert job["status"] == "cancelled", job
        assert [r["status"] for r in job["runs"]] == ["cancelled", "cancelled"]
        assert "interrupted cleanly" in m.log(job_id)["text"], "SIGINT must reach the pipeline first"
        try:
            m.cancel(job_id)
        except JobError as exc:
            assert exc.status == 409


def test_workspace_lock_blocks_and_stale_jobs_recover():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        m = _manager(Path(tmp))
        lock = m.data / "pipeline-workspace.lock"
        lock.mkdir()
        write_json_atomic(lock / "owner.json", {"pid": 4242})
        try:
            m.start(_estimate("a"))
        except JobError as exc:
            assert exc.status == 409 and "4242" in str(exc)
        else:
            raise AssertionError("started past the workspace lock")
        write_json_atomic(m.dir / "20260101T000000-abcdef" / "job.json",
                          {"job_id": "20260101T000000-abcdef", "status": "running", "created": "x",
                           "regions": ["a"], "runs": [{"region": "a", "status": "running"}]})
        again = JobManager(m.data, ROOT, sys.executable)
        job = again.load("20260101T000000-abcdef")
        assert job["status"] == "interrupted" and job["runs"][0]["status"] == "interrupted"
        for bad in ("../x", "", "a" * 50):
            try:
                again.load(bad)
            except JobError as exc:
                assert exc.status == 404
            else:
                raise AssertionError(bad)


def test_progress_weights_finished_steps():
    status = {"steps": [{"key": "a", "status": "PASS"}, {"key": "b", "status": "RUNNING"}]}
    assert run_fraction(status, {}) == 0.5
    assert abs(run_fraction(status, {"a": 30, "b": 90}) - 0.25) < 1e-9
    job = {"regions": ["x", "y"], "status": "running",
           "runs": [{"status": "succeeded"}, {"status": "running"}]}
    assert progress(job, [None, status], {}) == 0.75


if __name__ == "__main__":
    for test in (test_regions_run_in_order_failures_continue_and_combine_runs,
                 test_single_region_success_needs_no_extra_combine,
                 test_cancel_sends_sigint_and_skips_the_rest,
                 test_workspace_lock_blocks_and_stale_jobs_recover,
                 test_progress_weights_finished_steps):
        test()
    print("pipeline web job tests passed")
