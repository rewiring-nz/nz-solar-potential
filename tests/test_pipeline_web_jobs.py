"""Pipeline web panel: the job runner, with a fake pipeline script.

Run: .venv/bin/python tests/test_pipeline_web_jobs.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

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
if os.environ.get("SOLAR_SURVEY_OVERRIDES"):
    print("SURVEY_OVERRIDES=" + os.environ["SOLAR_SURVEY_OVERRIDES"], flush=True)
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
    # lock_poll_s keeps the workspace-lock wait test fast.
    (tmp / "fake.py").write_text(FAKE)
    (tmp / "combine.py").write_text(COMBINE)
    data = tmp / "data"
    data.mkdir()
    return JobManager(
        data, ROOT, sys.executable,
        command_builder=lambda py, region, paid: [py, str(tmp / "fake.py"), str(data), region],
        combine_builder=lambda py, regions: [py, str(tmp / "combine.py"), *regions],
        cancel_grace_s=grace, lock_poll_s=0.05)


def _estimate(*regions):
    return {"estimate_id": "0123456789abcdef", "regions": [{"region": r} for r in regions],
            "totals": {"total_s": 100}, "paid_referee": {"usd_upper": 1.0},
            "options": {"include_imagery": True, "paid_referee": False}}


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
        second = m.start(_estimate("c"))
        assert m.load(second)["status"] == "queued"
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
        later = _wait(m, second)
        assert later["status"] == "succeeded" and later["started"] >= job["finished"]


def test_single_region_success_needs_no_extra_combine():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        m = _manager(Path(tmp))
        est = _estimate("a")
        est["options"]["paid_referee"] = True
        job = _wait(m, m.start(est, paid_referee=True))
        assert job["status"] == "succeeded" and job["progress"] == 1.0 and job["eta_s"] == 0
        assert job["final_combine"]["status"] == "not needed"
        assert job["paid_referee"] is True and job["approved"]["paid_referee_usd_upper"] == 1.0


def test_source_choice_is_pinned_to_job_and_command():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        m = _manager(Path(tmp))
        commands = []
        original = m.command_builder

        def capture(*args):
            command = original(*args)
            commands.append(command)
            return command

        m.command_builder = capture
        estimate = _estimate("a")
        estimate["options"]["include_imagery"] = False
        estimate["regions"][0]["source_layers"] = {
            "dsm_layer": 123194, "imagery_layer": 123118,
        }
        job = _wait(m, m.start(estimate))
        assert job["include_imagery"] is False
        assert "--no-imagery" in commands[0]
        assert job["survey_overrides"]["a"] == estimate["regions"][0]["source_layers"]
        assert '"dsm_layer": 123194' in m.log(job["job_id"])["text"]


def test_job_environment_overrides_preserve_parent_environment():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        m = _manager(Path(tmp))
        probe = [
            sys.executable, "-c",
            "import json, os; print(json.dumps({key: os.environ.get(key) "
            "for key in ('PATH', 'SOLAR_TEST_PARENT', 'SOLAR_TEST_OVERRIDE', "
            "'PYTHONUNBUFFERED')}))",
        ]
        with patch.dict(os.environ, {"PATH": tmp, "SOLAR_TEST_PARENT": "inherited",
                                     "SOLAR_TEST_OVERRIDE": "parent"}):
            for overrides in (None, {}, {"SOLAR_TEST_OVERRIDE": "child"}):
                process = m._popen(probe, None, overrides)
                stdout, _ = process.communicate(timeout=10)
                assert process.returncode == 0, stdout
                environment = json.loads(stdout)
                assert environment == {
                    "PATH": tmp,
                    "SOLAR_TEST_PARENT": "inherited",
                    "SOLAR_TEST_OVERRIDE": "child" if overrides else "parent",
                    "PYTHONUNBUFFERED": "1",
                }, environment


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


def test_workspace_lock_makes_local_jobs_wait_and_stale_jobs_recover():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        m = _manager(Path(tmp))
        lock = m.data / "pipeline-workspace.lock"
        lock.mkdir()
        write_json_atomic(lock / "owner.json", {"pid": 4242})
        job_id = m.start(_estimate("a"))
        deadline = time.time() + 10
        while not m.load(job_id).get("waiting") and time.time() < deadline:
            time.sleep(0.02)
        assert "4242" in m.load(job_id)["waiting"]
        assert m.load(job_id)["runs"][0]["status"] == "queued"
        (lock / "owner.json").unlink()
        lock.rmdir()
        job = _wait(m, job_id)
        assert job["status"] == "succeeded" and job["waiting"] is None
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


CLOUD_FAKE = r'''
import sys, time
target, regions = sys.argv[1], sys.argv[2]
print("building " + target + " " + regions, flush=True)
if "slow" in regions:
    time.sleep(30)
print("GATE_RESULT=pass", flush=True)
print("PREVIEW_URL=https://example.test/devel/me/preview.html", flush=True)
if target == "published":
    print("PUSHED (data v68)", flush=True)
'''


def _cloud_manager(tmp: Path) -> JobManager:
    m = _manager(tmp)
    (tmp / "cloud.py").write_text(CLOUD_FAKE)
    m.cloud_builder = lambda target, py, regions, paid: [py, str(tmp / "cloud.py"), target,
                                                         " ".join(regions)]
    m.cloud_env = lambda: dict(os.environ, CLOUD_VM="fake")
    return m


def _cloud_estimate(target, *regions):
    est = _estimate(*regions)
    est["target"] = target
    est["cloud"] = {"cost_nzd": "5.00", "cost_text": "up to NZD 5.00; not a spending cap",
                    "project": "p", "vm": "v", "zone": "z", "ref": "main",
                    "preview_user": "me", "preview_url": "https://example.test/x"}
    return est


def test_cloud_lane_runs_beside_local_and_records_outputs():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        m = _cloud_manager(Path(tmp))
        local = m.start(_estimate("slow"))
        dev = m.start(_cloud_estimate("developer", "a", "b/c/d"))
        pub = m.start(_cloud_estimate("published", "a"))
        q = m.queue()
        assert [j["job_id"] for j in q["local"]["jobs"]] == [local]
        assert [j["job_id"] for j in q["cloud"]["jobs"]] == [dev, pub]
        assert m.view(pub)["queue_position"] in (1, 2, None)
        job = _wait(m, dev)
        assert job["status"] == "succeeded" and job["lane"] == "cloud"
        assert job["gate_result"] == "pass"
        assert job["preview_url"] == "https://example.test/devel/me/preview.html"
        assert job["approved"]["cloud_cost_nzd"] == "5.00"
        assert "building developer a b/c/d" in m.log(dev)["text"]
        assert m.load(local)["status"] in ("queued", "running"), "the local lane runs independently"
        published = _wait(m, pub)
        assert published["published_version"] == "68"
        m.cancel(local)
        assert _wait(m, local)["status"] == "cancelled"


def test_queued_job_can_be_removed_and_cloud_needs_a_builder():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        m = _cloud_manager(Path(tmp))
        first = m.start(_cloud_estimate("developer", "slow"))
        second = m.start(_cloud_estimate("developer", "a"))
        m.cancel(second)
        assert m.load(second)["status"] == "cancelled"
        assert second not in [j["job_id"] for j in m.queue()["cloud"]["jobs"]]
        m.cancel(first)
        assert _wait(m, first)["status"] == "cancelled"
        try:
            m.cancel(second)
        except JobError as exc:
            assert exc.status == 409
        else:
            raise AssertionError("cancelled a finished job")
        plain = JobManager(m.data, ROOT, sys.executable)
        try:
            plain.start(_cloud_estimate("developer", "a"))
        except JobError as exc:
            assert exc.status == 409
        else:
            raise AssertionError("cloud job without a cloud builder")
        try:
            plain.start(_cloud_estimate("elsewhere", "a"))
        except JobError as exc:
            assert exc.status == 400
        else:
            raise AssertionError("unknown target accepted")


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
                 test_source_choice_is_pinned_to_job_and_command,
                 test_job_environment_overrides_preserve_parent_environment,
                 test_cancel_sends_sigint_and_skips_the_rest,
                 test_workspace_lock_makes_local_jobs_wait_and_stale_jobs_recover,
                 test_cloud_lane_runs_beside_local_and_records_outputs,
                 test_queued_job_can_be_removed_and_cloud_needs_a_builder,
                 test_progress_weights_finished_steps):
        test()
    print("pipeline web job tests passed")
