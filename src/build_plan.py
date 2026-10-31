"""
The per-region build plan: which steps run, in which order, for every engine.

Three engines build a region: the district build (src/run_district_build.sh,
which tools/remote_build.sh and the build-and-publish workflow run on the build
VM), the fleet worker (src/build_region.sh) and the local executor
(tools/pipeline.py). Each used to carry its own stage list, and the lists
drifted: the fleet never ran outline_fit, repeat_roofs or demolished, and the
local executor ran register_imagery after the layout, so the markup frame and
heat-map registration it reads were missing when the layout ran. This module is
the one list. The engines read it; tests/test_build_plan.py fails when an
engine hard-codes a stage list again or orders its steps differently.

A region is built in three phases:

  PREPARE   per-region sidecars written before the layout and the incremental
            plan, invoked as `src/<name>.py <region>`. Each is switched off by
            its env var set to 0, never fails a build, and is skipped on
            yield-only builds.
  STAGES    the stage chain, each run through src/run_stage.py.
  FINISH    addresses (non-fatal: needs the network, patched in place later)
            and the region's own tiles and summary (emit_region).

Input preparation (fetching LiDAR, imagery and outlines; predicting roof faces)
is not part of the plan: the district build expects inputs fetched beforehand
(`tools/remote_build.sh --fetch`), the fleet worker restores a region pack or
fetches, and the local executor fetches as its first steps.

PAID STEPS. The demolished stage can ask a paid vision referee (Anthropic API,
about $0.004 a roof, stopped near SOLAR_DEMOLISHED_MAX_USD a region). It only
asks when SOLAR_DEMOLISHED_REFEREE=1. Every entry point decides that at the
start with a yes/no question that defaults to no; a non-interactive run
declines unless the caller already set the variable.

CLI (for the shell engines):

    python src/build_plan.py stages [--yield-only]   # one stage per line
    python src/build_plan.py prepare                 # name<TAB>switch<TAB>warning
    python src/build_plan.py finish                  # name<TAB>fatal(0|1)
    python src/build_plan.py confirm-paid --regions "a b" | --region-count N | --per-region
        # prints 1 (approved) or 0 (declined) for SOLAR_DEMOLISHED_REFEREE
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Callable, TextIO


@dataclass(frozen=True)
class PrepareStep:
    name: str
    switch: str
    warning: str


@dataclass(frozen=True)
class FinishStep:
    name: str
    fatal: bool


PREPARE: tuple[PrepareStep, ...] = (
    # An outline clearly offset from the roof the LiDAR sees is slid onto it
    # before the plan, so a building whose slide changed reads as stale.
    PrepareStep("outline_fit", "SOLAR_OUTLINE_FIT",
                "its outlines stay as surveyed"),
    # Sets of copies of one roof get their set's agreed reading before the
    # plan, so a changed agreement marks its buildings stale.
    PrepareStep("repeat_roofs", "SOLAR_REPEAT_ROOFS",
                "its buildings keep their own readings"),
)

# register_imagery comes first: markup traced on the map's photo is moved by
# each roof's lean as the layout reads it, and the heat map is drawn where the
# map's photo shows each roof. demolished comes after the gate and before
# everything that reads panels.
STAGES: tuple[str, ...] = (
    "register_imagery",
    "build_layout_geojson",
    "gate_panels",
    "demolished",
    "rerank_layouts",
    "derive_solar_potential",
    "patch_roof_confidence",
    "bake_building_horizons",
    "build_heatmap_raster",
)

# The sun changed, the roofs did not: every kWh is recomputed from stored
# geometry (src/apply_yield.py) and everything downstream of a kWh is redone.
YIELD_ONLY_STAGES: tuple[str, ...] = (
    "apply_yield",
    "rerank_layouts",
    "derive_solar_potential",
    "patch_roof_confidence",
    "bake_building_horizons",
    "build_heatmap_raster",
)

FINISH: tuple[FinishStep, ...] = (
    FinishStep("add_addresses", fatal=False),
    FinishStep("emit_region", fatal=True),
)

REFEREE_SWITCH = "SOLAR_DEMOLISHED_REFEREE"
STAGE_SWITCH = "SOLAR_DEMOLISHED"
REFEREE_CAP_SWITCH = "SOLAR_DEMOLISHED_MAX_USD"
REFEREE_DEFAULT_CAP_USD = 5.0
REFEREE_USD_PER_ROOF = 0.004


def stages(yield_only: bool = False) -> tuple[str, ...]:
    return YIELD_ONLY_STAGES if yield_only else STAGES


def region_steps(yield_only: bool = False) -> list[str]:
    """Every per-region step name in the order an engine runs it."""
    prepare = [] if yield_only else [p.name for p in PREPARE]
    return prepare + list(stages(yield_only)) + [f.name for f in FINISH]


def prepare_enabled(step: PrepareStep, env: dict[str, str] | None = None) -> bool:
    return (os.environ if env is None else env).get(step.switch, "1") == "1"


def referee_cap_usd(env: dict[str, str] | None = None) -> float:
    value = (os.environ if env is None else env).get(REFEREE_CAP_SWITCH)
    try:
        return float(value) if value else REFEREE_DEFAULT_CAP_USD
    except ValueError:
        return REFEREE_DEFAULT_CAP_USD


def referee_estimate_text(region_count: int | None, env: dict[str, str] | None = None) -> str:
    cap = referee_cap_usd(env)
    per_region = (
        f"The demolished stage can send survey and current photo crops of "
        f"screened roofs to a paid vision referee (Anthropic API), about "
        f"USD ${REFEREE_USD_PER_ROOF:.3f} a roof. Each region stops asking near "
        f"USD ${cap:.2f} ({REFEREE_CAP_SWITCH})."
    )
    if region_count is None:
        total = (" The number of regions is not known here, so the total is "
                 f"USD ${cap:.2f} times the regions built.")
    else:
        total = (f" Conservative estimate for {region_count} region(s): up to "
                 f"USD ${cap * region_count:.2f}.")
    return (per_region + total + " Cached verdicts are not asked again. This is "
            "not a spending cap: actual charges can be higher.")


def confirm_paid(
    region_count: int | None,
    env: dict[str, str] | None = None,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
    ask: Callable[[str], str] | None = None,
) -> bool:
    """Decide whether the paid referee may run. Default and non-interactive: no.

    A caller that already set SOLAR_DEMOLISHED_REFEREE has decided; a build
    with the demolished stage off spends nothing and is not asked.
    """
    env = os.environ if env is None else env
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stderr if out is None else out
    if ask is None:
        # The prompt goes to stderr: shell callers capture stdout for the answer.
        def ask(prompt: str) -> str:
            print(prompt, end="", file=out, flush=True)
            line = stdin.readline()
            if not line:
                raise EOFError
            return line
    if env.get(STAGE_SWITCH, "1") == "0":
        return False
    preset = env.get(REFEREE_SWITCH)
    if preset in ("0", "1"):
        return preset == "1"
    print(referee_estimate_text(region_count, env), file=out)
    if not stdin.isatty():
        print("Not interactive: the paid referee is skipped "
              f"(set {REFEREE_SWITCH}=1 to approve it).", file=out)
        return False
    try:
        answer = ask("Use the paid demolition referee? Type yes to approve [no]: ")
    except EOFError:
        answer = ""
    approved = answer.strip().lower() == "yes"
    print("Paid referee approved." if approved else
          "Paid referee skipped: only screening and cached verdicts apply.", file=out)
    return approved


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("stages")
    s.add_argument("--yield-only", action="store_true")
    sub.add_parser("prepare")
    sub.add_parser("finish")
    c = sub.add_parser("confirm-paid")
    scope = c.add_mutually_exclusive_group(required=True)
    scope.add_argument("--regions")
    scope.add_argument("--region-count", type=int)
    scope.add_argument("--per-region", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "stages":
        print("\n".join(stages(args.yield_only)))
    elif args.command == "prepare":
        for p in PREPARE:
            print(f"{p.name}\t{p.switch}\t{p.warning}")
    elif args.command == "finish":
        for f in FINISH:
            print(f"{f.name}\t{int(f.fatal)}")
    elif args.command == "confirm-paid":
        if args.regions is not None:
            count = len(args.regions.split())
        elif args.region_count is not None:
            count = args.region_count
        else:
            count = None
        print(int(confirm_paid(count)))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
