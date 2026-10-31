#!/bin/bash
# One region, start to finish, on any machine: fetch -> predict -> prepare ->
# build -> emit -> publish. This is the unit the worker loop runs and the unit the
# queue hands out (docs/maintainers/scale-architecture.md).
#
#   ./src/build_region.sh <region> [--no-publish]
#
# Resumable at every stage: fetches skip what is on disk, face prediction is
# recorded per region, build stages use run_stage markers, and the emit is
# atomic. Re-running after a preemption continues from where it stopped.
#
# STOPS ON A FAILED FETCH, on purpose. The first expansion script made every
# stage non-fatal, so a 400 on the Wanaka DSM and 165 missing point-cloud
# tiles scrolled past and the build started on nothing.
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
REGION="${1:?region name}"
# A LINZ locality id holds '/'; logs and markers are flat files.
RSLUG="${REGION//\//__}"
PUBLISH=1
[ "${2:-}" = "--no-publish" ] && PUBLISH=0
[ -f .env ] && { set -a; . ./.env; set +a; }
. tools/cloud_profile.sh
if [ -n "${SOLAR_CLOUD_PROFILE:-}" ]; then
  PROFILE_BUCKET=$(cloud_profile_value bucket) || exit 2
  PROFILE_PREFIX=$(cloud_profile_value build_prefix) || exit 2
  if [ -z "$PROFILE_BUCKET" ] && [ -z "${SOLAR_BUCKET:-}" ]; then
    echo "Cloud profile '$SOLAR_CLOUD_PROFILE' must define bucket for region publishing." >&2
    exit 2
  fi
  export SOLAR_BUCKET="${SOLAR_BUCKET:-$PROFILE_BUCKET}"
  export SOLAR_BUILD_PREFIX="${SOLAR_BUILD_PREFIX:-${PROFILE_PREFIX:-build}}"
fi
export SOLAR_SELECTED_FACES="${SOLAR_SELECTED_FACES:-1}"
SHARDS="${SOLAR_PREDICT_SHARDS:-8}"
LOGDIR=data/build_logs; mkdir -p "$LOGDIR"
STATE=data/build_state; mkdir -p "$STATE"
log() { echo "[$REGION] $(date -u +%H:%M:%S) $*"; }

# The task file from the queue carries the region's bbox, so a worker whose
# config does not list this region can still build it.
mkdir -p "data/regions/$REGION"
gcloud storage cat "${SOLAR_BUCKET:-gs://solar-potential-map-data}/${SOLAR_BUILD_PREFIX:-build}/queue/$REGION.json" \
  > "data/regions/$REGION/task.json" 2>/dev/null || rm -f "data/regions/$REGION/task.json"

# A region built before keeps a PACK in the bucket: the points and pixels
# near its buildings and its DSM (src/pack_region.py), enough to rebuild it
# byte-identically. Restoring it is minutes and no LINZ or OpenTopography
# traffic -- the difference between re-laying the country after a geometry
# fix and re-downloading it. Only a region never built before fetches.
if $PY src/pack_region.py "$REGION" --restore >>"$LOGDIR/$RSLUG.log" 2>&1; then
  log "restored inputs from the region pack"
  $PY src/fetch_regions.py "$REGION" || { log "FATAL: fetch_regions rc=$?"; exit 1; }
else
  log "fetch"
  $PY src/fetch_regions.py "$REGION" || { log "FATAL: fetch_regions rc=$?"; exit 1; }
  $PY src/fetch_pointcloud_regions.py "$REGION" || { log "FATAL: pointcloud rc=$?"; exit 1; }
fi

# Ownership. A region from the national planner is a grid cell that overlaps
# nothing, so this is a no-op there; hand-drawn regions overlap and need it.
log "dedupe outlines"
$PY src/region_build.py "$REGION" >>"$LOGDIR/$RSLUG.log" 2>&1 || log "WARN: dedupe rc=$? (continuing)"

if [ ! -f "$STATE/$RSLUG.predict.done" ]; then
  log "predict faces ($SHARDS shards)"
  rc=0
  for i in $(seq 0 $((SHARDS - 1))); do
    OMP_NUM_THREADS=2 $PY tools/predict_faces.py --region "$REGION" --shard "$i/$SHARDS" \
      >>"$LOGDIR/predict_$RSLUG.log" 2>&1 &
  done
  for job in $(jobs -p); do wait "$job" || rc=1; done
  [ $rc -eq 0 ] || { log "FATAL: a predict shard failed (see $LOGDIR/predict_$RSLUG.log)"; exit 1; }
  date -u +%FT%TZ > "$STATE/$RSLUG.predict.done"
else
  log "predict: already done"
fi

# The steps after input preparation come from the shared plan
# (src/build_plan.py), the same list the district build runs: prepare
# sidecars, the stage chain, then addresses and the emit.
STAGES="$($PY src/build_plan.py stages)" || { log "FATAL: could not read the build plan"; exit 1; }
PREPARE="$($PY src/build_plan.py prepare)" || exit 1
FINISH="$($PY src/build_plan.py finish)" || exit 1
# The paid demolition referee runs only when the fleet launch approved it
# (tools/fleet.sh up passes SOLAR_DEMOLISHED_REFEREE); a worker has no one to
# ask, so it declines otherwise.
SOLAR_DEMOLISHED_REFEREE="$($PY src/build_plan.py confirm-paid --regions "$REGION" </dev/null 2>>"$LOGDIR/$RSLUG.log")" \
  || SOLAR_DEMOLISHED_REFEREE=0
export SOLAR_DEMOLISHED_REFEREE

while IFS=$'\t' read -r step switch warning; do
  [ -n "$step" ] || continue
  [ "${!switch:-1}" = "1" ] || continue
  log "$step"
  $PY "src/$step.py" "$REGION" </dev/null >>"$LOGDIR/$RSLUG.log" 2>&1 \
    || log "WARN: $step failed -- $warning"
done <<< "$PREPARE"

log "build"
for s in $STAGES; do
  $PY src/run_stage.py --skip-done "$s" "$REGION" >>"$LOGDIR/$RSLUG.log" 2>&1 \
    || { log "FATAL: $s failed (see $LOGDIR/$RSLUG.log)"; exit 1; }
done

while IFS=$'\t' read -r step fatal; do
  [ -n "$step" ] || continue
  log "$step"
  if ! $PY src/run_stage.py --skip-done "$step" "$REGION" </dev/null >>"$LOGDIR/$RSLUG.log" 2>&1; then
    [ "$fatal" = "1" ] && { log "FATAL: $step failed (see $LOGDIR/$RSLUG.log)"; exit 1; }
    log "WARN: $step failed -- patch later"
  fi
done <<< "$FINISH"

if [ $PUBLISH -eq 1 ]; then
  log "publish"
  $PY src/publish_region.py "$REGION" || { log "FATAL: publish failed"; exit 1; }
fi
log "DONE"
