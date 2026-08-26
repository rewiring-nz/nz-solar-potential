#!/bin/bash
# Parallel layouts-only re-run. Same steps as run_layouts_regate.sh, but the
# per-area work runs several areas at a time.
#
# The areas are independent until merge_regions -- each reads its own region
# files and writes its own outputs -- so the serial loop in the original was
# leaving 11 of 12 cores idle. gate_panels peaks around 250MB per area, so
# JOBS is bounded by cores, not memory; it defaults to half the cores to leave
# the machine usable while a rebuild runs.
#
# Everything after the fan-in (merge, deciles, shrink, tippecanoe) is a single
# pass over the combined file and stays serial.
#
# Usage: JOBS=6 bash src/run_layouts_regate_par.sh area1 area2 ...
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
JOBS="${JOBS:-$(( $(sysctl -n hw.ncpu 2>/dev/null || nproc) / 2 ))}"
[ "$JOBS" -lt 1 ] && JOBS=1
echo "running $# areas, $JOBS at a time"

# One area's three steps, as a unit -- exported so xargs can call it.
run_area() {
  r="$1"
  log="data/build_logs/${r}_regate.log"
  {
    .venv/bin/python src/build_layout_geojson.py "$r" &&
    .venv/bin/python src/gate_panels.py "$r" &&
    .venv/bin/python src/rerank_layouts.py "$r"
  } >"$log" 2>&1
  if [ $? -ne 0 ]; then echo "FAILED: $r (see $log)"; return 1; fi
  echo "done: $r"
}
export -f run_area

# printf %s\\n, not a bare list: area names go through xargs one per line so a
# missing quote cannot glue 24 names into a single argument. That exact bug
# ("File name too long") has bitten this pipeline before under zsh.
printf '%s\n' "$@" | xargs -P "$JOBS" -I{} bash -c 'run_area "$@"' _ {}
if [ $? -ne 0 ]; then echo "Skipping merge: at least one area failed"; exit 1; fi

$PY src/merge_regions.py && $PY src/bake_density_deciles.py &&
$PY src/shrink_panels_for_tiles.py &&
tippecanoe -o data/panel_layouts.pmtiles --force -l layout -Z13 -z16 \
  --drop-densest-as-needed --detect-shared-borders \
  -y kind -y building_id -y fill_rank -y fill_order -y array_id -y array_size \
  -y ac_kwh_year -y slope_deg -y aspect_deg \
  -y poa_kwh_m2_yr -y panel_count data/panel_layouts.geojson &&
echo REGATE_COMPLETE
