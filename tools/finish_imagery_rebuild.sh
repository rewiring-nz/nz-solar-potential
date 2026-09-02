#!/usr/bin/env bash
# Finish the 3 Sep district build properly, once imagery is back.
#
# THE SITUATION. 14 of 24 regions built LiDAR-only because their
# imagery_mosaic.tif had been deleted (the parts and the vision-line
# predictions are still on disk, which is how we know imagery existed when
# predict_roof_lines last ran). Measured across the district that cost roughly
# 31% of obstruction detections: 2.70 obstructions per building in regions with
# imagery against 1.86 without. Regions differ in building stock so that is
# directional, not a controlled comparison -- but the direction is not in doubt.
#
# WHAT THIS DOES, in order, each step waiting on the last:
#   1. wait for the running district driver to exit (never two drivers at once)
#   2. wait for tools/repair_imagery.sh to finish restoring mosaics
#   3. re-run the line model for regions whose predictions are missing
#   4. rebuild only the affected regions
#   5. fan in
#
# Deliberately does NOT stop the running build. That pass is producing a
# complete district; degraded in 14 regions, but complete, and it is the
# fallback if any of this fails.

set -u
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
LOG=data/build_logs/finish_rebuild.log
mkdir -p data/build_logs

AFFECTED="arrowtown_east arrowtown_hills arrowtown_millbrook arthurs_point
          arthurs_point_east dalefield frankton_arm frankton_east_lake
          jacks_point kelvin_heights kelvin_south shotover_lakehayes
          town_south_lake tucker_beach"

say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

say "=== finish_imagery_rebuild waiting ==="

while pgrep -f run_district_build.sh > /dev/null; do sleep 120; done
say "district driver has exited"

while pgrep -f repair_imagery.sh > /dev/null; do sleep 120; done
say "imagery repair has finished"

still=""
for r in $AFFECTED; do
  [ -f "data/regions/$r/imagery_mosaic.tif" ] || still="$still $r"
done
if [ -n "$still" ]; then
  say "WARNING: still no imagery for:$still"
  say "  they will rebuild LiDAR-only again; everything else proceeds"
fi

# Predictions are per building and persist on disk, so only regions that never
# got them (or got them for a fraction of their buildings) need the model run.
say "--- re-running the line model where predictions are missing ---"
for r in $AFFECTED; do
  [ -f "data/regions/$r/imagery_mosaic.tif" ] || continue
  $PY tools/predict_roof_lines.py --region "$r" >> "$LOG" 2>&1 \
    && say "  $r: predictions done" \
    || say "  $r: prediction FAILED (continuing)"
done

say "--- rebuilding the affected regions ---"
./src/run_district_build.sh --force --regions "$AFFECTED" >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  say "rebuild exited $rc -- check $LOG"
else
  say "rebuild finished"
fi

say "=== done ==="
