#!/bin/bash
# Every check this repo has, in one command. Run before a push.
#
#   ./tests/run_all.sh          # everything
#   ./tests/run_all.sh --fast   # skip the synthetic-region build and the
#                               # golden tests (a minute or more each)
#
# Exit status is non-zero if any check fails, so it works as a pre-push hook.
#
# What each one is for:
#   pure         arithmetic you cannot see -- aspect conventions, the horizon
#                codec, lookup binning, the derate. Mutation-checked.
#   economics    the money maths: self-consumption split, savings, payback.
#                Untestable until 1 Sep, when it was pulled out of preview.html
#                -- which is how a 2.4x error in the yearly figure survived
#                long enough to be spotted on the map.
#   deprecations the class that nearly removed shapely.vectorized from under
#                the geometry core on a routine dependency upgrade.
#   imports      every src module imports, and every positional call matches
#                the def it resolves to. On 22 Sep a signature change left a
#                caller passing four arguments to a two-argument function.
#   ridge snap   the shared-ridge snap on synthetic gables and hips.
#   roof levels  lower roof levels become faces; decks and clutter do not.
#   small obj    vents read as crisp contrast, stains do not; edge-drop width.
#   xref         every import of a repo name, including inside functions and
#                in tools/, still resolves -- what a deletion breaks first.
#   synthetic    the whole district build on a made-up eight-roof region,
#                fingerprinted against tests/synthetic/reference.json. Runs
#                anywhere (no LiDAR needed); about a minute.
#   diagram      the architecture page names 78 functions and constants. This
#                fails if any has moved or changed value, because a diagram
#                that drifts is worse than none -- a reviewer trusts it.
#   golden       pins what segmentation currently produces for the 28
#                ground-truth buildings, so a refactor cannot move geometry
#                unnoticed. Needs local region data; skips without it.
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

fail=0
run() {
  echo ""
  echo "=== $1 ==="
  shift
  "$@" || fail=1
}

run "pure functions"        $PY tests/test_pure.py
if command -v node >/dev/null 2>&1; then
  run "economics"           node tests/test_economics.mjs
else
  echo ""; echo "=== economics: SKIPPED (no node) ==="
fi
run "deprecated APIs"       $PY tests/test_no_deprecations.py
run "imports and arity"     $PY tests/test_imports_and_arity.py
run "ridge snap"            $PY tests/test_ridge_snap.py
run "roof levels"           $PY tests/test_roof_levels.py
run "small obstructions"    $PY tests/test_small_obstructions.py
run "geometry stages"       $PY tests/test_geometry_stage.py
run "image lean"            $PY tests/test_register_imagery.py
run "plane refresh"         $PY tests/test_plane_refresh.py
run "sunken regions"        $PY tests/test_sunken.py
run "xref"                  $PY tests/test_xref.py
run "output contract"       $PY tests/test_output_contract.py
run "quickstart reporting"   $PY tests/test_quickstart_reporting.py
run "quickstart map preview" $PY tests/test_quickstart_map.py
run "diagram vs code"       $PY tools/check_diagram.py
if [ $FAST -eq 0 ]; then
  run "synthetic region"    $PY tests/synthetic/run.py
  run "golden buildings"    $PY -W ignore tests/test_golden.py
else
  echo ""
  echo "=== synthetic region + golden buildings: SKIPPED (--fast) ==="
fi

echo ""
if [ $fail -eq 0 ]; then
  echo "all checks passed"
else
  echo "SOME CHECKS FAILED -- see above"
fi
exit $fail
