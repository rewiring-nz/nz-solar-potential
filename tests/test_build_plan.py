"""The build engines run one stage plan (src/build_plan.py).

Three engines build regions: tools/pipeline.py (local), run_district_build.sh
(build VM, via remote_build.sh and the GitHub workflow) and build_region.sh
(fleet workers). Each once kept its own stage list, and the lists drifted:
stages ran on one path and not another. These checks fail when an engine
stops reading the plan or hard-codes a list again, and pin the paid-referee
question's default of no.
"""
import io
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import build_plan  # noqa: E402

SHELL_ENGINES = ("src/run_district_build.sh", "src/build_region.sh")
PAID_CALLERS = ("src/run_district_build.sh", "src/build_region.sh",
                "tools/remote_build.sh", "tools/fleet.sh")


class _Stdin(io.StringIO):
    def __init__(self, text: str = "", tty: bool = True):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _confirm(text="", tty=True, env=None):
    out = io.StringIO()
    return build_plan.confirm_paid(3, env=env or {}, stdin=_Stdin(text, tty), out=out), out.getvalue()


def test_shell_engines_read_the_plan_and_hard_code_no_stage_list():
    stage_pair = re.compile(r"\b(\w+)\s+(\w+)\b")
    for rel in SHELL_ENGINES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "build_plan.py stages" in text, f"{rel} does not read the stage plan"
        assert "build_plan.py prepare" in text, f"{rel} does not read the prepare steps"
        assert "build_plan.py finish" in text, f"{rel} does not read the finish steps"
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        for a, b in zip(build_plan.STAGES, build_plan.STAGES[1:]):
            assert not any(m.groups() == (a, b) for m in stage_pair.finditer(code)), \
                f"{rel} hard-codes the stage sequence '{a} {b}'"


def test_paid_step_callers_ask_at_the_start():
    for rel in PAID_CALLERS:
        assert "confirm-paid" in (ROOT / rel).read_text(encoding="utf-8"), \
            f"{rel} starts builds without the paid-referee question"


def test_local_pipeline_runs_every_plan_step_in_order():
    from tools.pipeline import STEPS
    keys = [step["key"] for step in STEPS]
    plan = build_plan.region_steps()
    assert [k for k in keys if k in plan] == plan
    assert [step["number"] for step in STEPS] == list(range(1, len(STEPS) + 1))


def test_synthetic_harness_runs_the_plan():
    import importlib.util
    spec = importlib.util.spec_from_file_location("synthetic_run", ROOT / "tests/synthetic/run.py")
    synthetic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(synthetic)
    assert synthetic.STEPS == [s for s in build_plan.region_steps() if s not in synthetic.OFFLINE_SKIP]
    assert synthetic.OFFLINE_SKIP == {"add_addresses"}


def test_plan_shape():
    assert "demolished" in build_plan.STAGES
    assert build_plan.STAGES.index("gate_panels") < build_plan.STAGES.index("demolished") \
        < build_plan.STAGES.index("rerank_layouts")
    assert build_plan.YIELD_ONLY_STAGES[0] == "apply_yield"
    assert [f.name for f in build_plan.FINISH][-1] == "emit_region"
    assert build_plan.region_steps(yield_only=True)[:1] == ["apply_yield"]


def test_cli_lists_match_the_module():
    def cli(*args):
        return subprocess.run([sys.executable, "src/build_plan.py", *args], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.split()
    assert cli("stages") == list(build_plan.STAGES)
    assert cli("stages", "--yield-only") == list(build_plan.YIELD_ONLY_STAGES)


def test_cli_confirm_paid_declines_without_a_terminal():
    r = subprocess.run([sys.executable, "src/build_plan.py", "confirm-paid", "--regions", "a b"],
                       cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and r.stdout.strip() == "0"
    assert "not a spending cap" in r.stderr


def test_paid_referee_defaults_to_no():
    assert _confirm("\n")[0] is False          # Enter
    assert _confirm("")[0] is False            # end of input
    assert _confirm("y\n")[0] is False         # only an explicit yes approves
    assert _confirm("yes\n")[0] is True
    assert "not a spending cap" in _confirm("yes\n")[1]


def test_paid_referee_declines_when_not_interactive():
    assert _confirm("yes\n", tty=False)[0] is False


def test_paid_referee_honours_a_decision_already_made():
    assert _confirm(env={build_plan.REFEREE_SWITCH: "1"}, tty=False)[0] is True
    assert _confirm("yes\n", env={build_plan.REFEREE_SWITCH: "0"})[0] is False
    assert _confirm("yes\n", env={build_plan.STAGE_SWITCH: "0"})[0] is False


if __name__ == "__main__":
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_") and callable(f)]
    for test in tests:
        test()
    print(f"build plan: {len(tests)} checks passed")
