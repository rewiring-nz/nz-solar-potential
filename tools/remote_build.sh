#!/bin/bash
# Build on the VM, compare with production, then publish to the selected target.
#
#   tools/remote_build.sh --regions "glenorchy kinloch"            # build + gate, no publish
#   tools/remote_build.sh --regions "glenorchy kinloch" --publish  # ...and publish if the gate passes
#   tools/remote_build.sh --fetch --regions "newtown" --publish    # fetch LiDAR/photos first
#   tools/remote_build.sh --publish --reviewed "why"               # publish although the gate failed
#   tools/remote_build.sh --yield-only --publish                   # the sun changed, not the roofs
#   tools/remote_build.sh --regions frankton_arm_lake --dev-publish alice
#   tools/remote_build.sh --regions frankton_arm_lake --dev-publish alice --parallel
#
# No --regions: an incremental build of every region (stale ones only).
#
# This is what the build-and-publish GitHub Action runs, and it works the same
# from a laptop. Everything is computed on the VM: it pulls the pushed code
# (tools/sync_vm.sh), builds (src/run_district_build.sh), checks against the
# live site (tools/predeploy_check.py), and publishes either a developer
# preview or, after a passing/reviewed gate, a production release.
#
# The VM is started if it is stopped, and stopped again at the end if this
# script started it (--keep-running to leave it up). A cut-off build resumes:
# run the same command again.
#
# Needs: gcloud signed in as someone who can start the VM and tunnel to it
# (the team's Google Group can), and push access to the repo for --publish.
set -u
cd "$(dirname "$0")/.."
if ! DEFAULTS=$(python3 -c '
import config
values = config.CLOUD_SHARED_BUILD
print("\t".join(values[key] for key in ("project", "vm", "zone", "ssh_user", "preview_bucket")))
'); then
  echo "ERROR: could not read shared cloud defaults from config.py."
  exit 1
fi
IFS=$'\t' read -r DEFAULT_PROJECT DEFAULT_VM DEFAULT_ZONE DEFAULT_VM_USER PREVIEW_BUCKET <<< "$DEFAULTS"
VM="${CLOUD_VM:-$DEFAULT_VM}"
ZONE="${CLOUD_ZONE:-$DEFAULT_ZONE}"
PROJECT="${CLOUD_PROJECT:-$DEFAULT_PROJECT}"
# The checkout lives in j's home; name the user so a runner or a teammate
# lands in the same place.
TARGET="${VM_USER:-$DEFAULT_VM_USER}@$VM"
SSH() {
  local output rc
  if output=$(gcloud compute ssh --tunnel-through-iap "$TARGET" --project="$PROJECT" --zone="$ZONE" \
    --ssh-flag="-o ConnectTimeout=60" --command "$1" 2>&1); then
    printf '%s\n' "$output" \
      | grep -v "^Warning\|^WARNING\|NumPy\|please see\|^Updating\|^Existing\|^$" || true
  else
    rc=$?
    printf '%s\n' "$output" \
      | grep -v "^Warning\|^WARNING\|NumPy\|please see\|^Updating\|^Existing\|^$" >&2 || true
    return "$rc"
  fi
}

if ! command -v gcloud >/dev/null 2>&1; then
  echo "ERROR: gcloud is not installed or not on PATH. Install the Google Cloud CLI, run 'gcloud auth login', then retry."
  exit 1
fi

REGIONS=""; FETCH=0; PUBLISH=0; REVIEWED=""; KEEP=0; MODE=""; REF=main
DEV_PUBLISH_USER=""; PARALLEL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --regions)      REGIONS="$2"; shift 2 ;;
    --fetch)        FETCH=1; shift ;;
    --publish)      PUBLISH=1; shift ;;
    --reviewed)     REVIEWED="$2"; shift 2 ;;
    --yield-only)   MODE="--yield-only"; shift ;;
    --force)        MODE="--force"; shift ;;
    --keep-running) KEEP=1; shift ;;
    --ref)          REF="$2"; shift 2 ;;
    --dev-publish)
      [ $# -ge 2 ] || { echo "--dev-publish needs a GitHub alias"; exit 2; }
      DEV_PUBLISH_USER="$2"; shift 2 ;;
    --parallel) PARALLEL=1; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
done
[ $FETCH -eq 1 ] && [ -z "$REGIONS" ] && { echo "--fetch needs --regions"; exit 2; }
[ $PUBLISH -eq 1 ] && [ "$REF" != main ] && { echo "only main is published; drop --publish to test a branch"; exit 2; }
[ -n "$DEV_PUBLISH_USER" ] && [ -z "$REGIONS" ] && { echo "--dev-publish needs --regions"; exit 2; }
[ $PUBLISH -eq 1 ] && [ -n "$DEV_PUBLISH_USER" ] && { echo "developer preview and production publish are separate actions"; exit 2; }
[ -n "$REVIEWED" ] && [ -n "$DEV_PUBLISH_USER" ] && { echo "--reviewed is only for production publication"; exit 2; }
[ -n "$DEV_PUBLISH_USER" ] && ! printf '%s' "$DEV_PUBLISH_USER" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?$' && {
  echo "--dev-publish needs a 1-39 character alphanumeric/hyphen GitHub alias"; exit 2;
}
[ -n "$DEV_PUBLISH_USER" ] && ! printf '%s' "$REF" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/-]*$' && {
  echo "--dev-publish needs a simple branch name for --ref"; exit 2;
}
case "$REGIONS" in *[!a-z0-9_\ ]*) echo "region names are lower-case letters, digits and _"; exit 2 ;; esac

STARTED=0
finish() {
  if [ $STARTED -eq 1 ] && [ $KEEP -eq 0 ]; then
    active=$(SSH "pgrep -af '[s]rc/[a-z_]*\\.(py|sh)' || true")
    if ! runs_json=$(SSH "python3 ~/solar-map/tools/developer_runs.py list 2>/dev/null"); then
      echo "=== leaving the VM running; could not verify the remote run registry ==="
      return
    fi
    if ! registered=$(printf '%s' "$runs_json" | python3 -c '
import json,sys
rows=json.load(sys.stdin)
print(sum(1 for r in rows if r.get("status") in {"queued","running","gating","validating","publishing","cancelling"}))
'); then
      echo "=== leaving the VM running; the remote run registry was unreadable ==="
      return
    fi
    if [ -n "$active" ] || [ "$registered" -gt 0 ]; then
      echo "=== leaving the VM running; an active pipeline run is registered ==="
      [ -n "$active" ] && printf '%s\n' "$active"
    else
      echo "=== stopping the VM ==="
      gcloud compute instances stop $VM --zone $ZONE --project $PROJECT --async -q >/dev/null 2>&1
    fi
  fi
}
trap finish EXIT

echo "=== VM ==="
if ! status=$(gcloud compute instances describe "$VM" --zone "$ZONE" --project "$PROJECT" \
  --format="value(status)" 2>&1); then
  echo "ERROR: could not inspect VM $VM in project $PROJECT, zone $ZONE."
  echo "$status"
  echo "Check group membership and Compute Engine access, then retry."
  exit 1
fi
if [ "$status" != RUNNING ]; then
  echo "VM is $status; starting it may incur compute charges."
  if ! gcloud compute instances start "$VM" --zone "$ZONE" --project "$PROJECT" -q; then
    echo "ERROR: could not start VM $VM. Check Compute Instance Admin permissions and VM status."
    exit 1
  fi
  STARTED=1
fi
READY=0
for i in $(seq 1 20); do
  if SSH "echo up" | grep -q up; then
    READY=1
    break
  fi
  echo "IAP/SSH is not ready yet (attempt $i/20); retrying in 15 seconds."
  sleep 15
done
[ "$READY" -eq 1 ] || {
  echo "ERROR: VM did not become reachable through IAP/SSH after 5 minutes."
  echo "Check IAP tunnel and OS Login permissions, then retry."
  exit 1
}
echo "  running"

BUILD_DIR="~/solar-map"
RUN_ROOT=""
REMOTE_LOG="~/remote_build.log"
PARALLEL_ARG=""
[ "$PARALLEL" -eq 1 ] && PARALLEL_ARG="--parallel"
if [ -n "$DEV_PUBLISH_USER" ]; then
  echo "=== prepare isolated developer checkout and run ==="
  workspace=$(SSH "set -e
cd ~/solar-map
run_id=\$(python3 -c 'import datetime,uuid; print(datetime.datetime.now(datetime.timezone.utc).strftime(\"%Y%m%dT%H%M%SZ\")+\"-\"+uuid.uuid4().hex[:8])')
snapshot_ref='refs/developer-workspaces/$DEV_PUBLISH_USER/'\"\$run_id\"
git fetch -q --no-tags origin 'refs/heads/$REF:'\"\$snapshot_ref\"
commit=\$(git rev-parse \"\$snapshot_ref\")
helper_dir=\$HOME/.cache/nz-solar-potential
mkdir -p \"\$helper_dir\"
helper=\"\$helper_dir/developer_workspace_\$commit.py\"
git show \"\$commit:tools/developer_workspace.py\" > \"\$helper.\$run_id.tmp\"
mv \"\$helper.\$run_id.tmp\" \"\$helper\"
python3 \"\$helper\" create --repo \$HOME/solar-map \
  --base-root \$HOME/solar-map-developers --user '$DEV_PUBLISH_USER' \
  --ref '$REF' --commit \"\$commit\" --run-id \"\$run_id\"") || {
    echo "DEVELOPER WORKSPACE FAILED -- could not create a commit-pinned checkout"
    exit 1
  }
RUN_ID=$(printf '%s\n' "$workspace" | sed -n 's/^RUN_ID=//p' | tail -1)
RUN_ROOT=$(printf '%s\n' "$workspace" | sed -n 's/^RUN_ROOT=//p' | tail -1)
BUILD_DIR=$(printf '%s\n' "$workspace" | sed -n 's/^CHECKOUT=//p' | tail -1)
COMMIT=$(printf '%s\n' "$workspace" | sed -n 's/^COMMIT=//p' | tail -1)
[ -n "$RUN_ID" ] && [ -n "$RUN_ROOT" ] && [ -n "$BUILD_DIR" ] && [ -n "$COMMIT" ] || {
  echo "DEVELOPER WORKSPACE FAILED -- workspace details were incomplete"
  exit 1
}
REMOTE_LOG="$RUN_ROOT/remote_build.log"
echo "  developer: $DEV_PUBLISH_USER"
echo "  run: $RUN_ID"
echo "  commit: $COMMIT"
echo "  checkout: $BUILD_DIR"
echo "  output: $BUILD_DIR/data"
discard_new_workspace() {
  SSH "git -C ~/solar-map worktree remove --force '$BUILD_DIR' && rmdir '$RUN_ROOT'"
}
  active_json=$(SSH "cd '$BUILD_DIR' && .venv/bin/python tools/developer_runs.py list --user '$DEV_PUBLISH_USER'") || {
    echo "RUN REGISTRY FAILED -- could not inspect active developer runs"
    discard_new_workspace || true
    exit 1
  }
  active_rows=$(printf '%s' "$active_json" | python3 -c '
import json, sys
active = {"queued", "running", "gating", "validating", "publishing", "cancelling"}
for row in json.load(sys.stdin):
    if row.get("status") in active:
        print("|".join(str(row.get(k, "")) for k in ("run_id", "status", "regions", "commit")))
')
  if [ -n "$active_rows" ] && [ "$PARALLEL" -eq 0 ]; then
    echo "Active developer run(s):"
    printf '%s\n' "$active_rows" | while IFS='|' read -r id state regions commit; do
      echo "  $id  $state  regions=$regions  commit=${commit:0:12}"
    done
    if [ ! -t 0 ] || [ ! -r /dev/tty ]; then
      echo "A run is already active. Use --parallel to explicitly keep it and start another."
      discard_new_workspace || true
      exit 1
    fi
    echo "Choose one: (1) keep one existing run only (enter its ID);"
    echo "            (2) replace all existing runs with this run;"
    echo "            (3) keep all existing runs and start this one;"
    echo "            (q) cancel."
    printf "Choice: "
    IFS= read -r choice </dev/tty || { discard_new_workspace || true; exit 1; }
    case "$choice" in
      1)
        printf "Existing run ID to keep: "
        IFS= read -r kept </dev/tty || { discard_new_workspace || true; exit 1; }
        printf '%s\n' "$active_rows" | grep -q "^$kept|" || {
          echo "That run is not active for $DEV_PUBLISH_USER."
          discard_new_workspace || true
          exit 2
        }
        while IFS='|' read -r id state _; do
          [ "$id" = "$kept" ] || SSH "cd '$BUILD_DIR' && .venv/bin/python tools/developer_runs.py cancel --run-id '$id' --wait-seconds 60" || exit 1
        done <<< "$active_rows"
        discard_new_workspace || true
        echo "Kept $kept; no new run was started."
        echo "RUN_NOT_STARTED=1"
        echo "RUN_DASHBOARD_URL=https://storage.googleapis.com/${PREVIEW_BUCKET#gs://}/run-dashboard/index.html"
        exit 0 ;;
      2)
        while IFS='|' read -r id _; do
          SSH "cd '$BUILD_DIR' && .venv/bin/python tools/developer_runs.py cancel --run-id '$id' --wait-seconds 60" || exit 1
        done <<< "$active_rows" ;;
      3)
        PARALLEL=1
        PARALLEL_ARG="--parallel" ;;
      *)
        echo "No new run was started."
        discard_new_workspace || true
        exit 1 ;;
    esac
  fi
else
  VM_USER="${VM_USER:-j}" SYNC_VM="$VM" SYNC_ZONE="$ZONE" SYNC_PROJECT="$PROJECT" \
    tools/sync_vm.sh --ref "$REF" || exit 1
fi

echo "=== build on the VM ==="
if [ -n "$DEV_PUBLISH_USER" ]; then
  echo "=== start VM-owned developer run ==="
  SSH "cd '$BUILD_DIR' && .venv/bin/python tools/developer_runs.py start \
    --user '$DEV_PUBLISH_USER' --run-id '$RUN_ID' --regions '$REGIONS' \
    --commit '$COMMIT' --checkout '$BUILD_DIR' --run-root '$RUN_ROOT' \
    --source-data ~/solar-map/data $([ $FETCH -eq 1 ] && echo --fetch) \
    $([ -n "$MODE" ] && echo --mode "$MODE") $PARALLEL_ARG" || exit 1
  dashboard_url="https://storage.googleapis.com/${PREVIEW_BUCKET#gs://}/run-dashboard/index.html"
  echo "Public run dashboard: $dashboard_url"
  echo "RUN_DASHBOARD_URL=$dashboard_url"
  echo "=== follow run status ==="
  while :; do
    sleep 15
    status_json=$(SSH "cd '$BUILD_DIR' && .venv/bin/python tools/developer_runs.py list --user '$DEV_PUBLISH_USER'") || {
      echo "Could not reach the VM; the remote run continues and can be checked from the public dashboard."
      exit 1
    }
    details=$(printf '%s' "$status_json" | python3 -c '
import json, sys
rows=json.load(sys.stdin)
run=next((r for r in rows if r.get("run_id") == sys.argv[1]), {})
print("|".join(str(run.get(k) or "") for k in ("status","stage","preview_url","error")))
' "$RUN_ID")
    IFS='|' read -r run_status run_stage preview_url run_error <<EOF
$details
EOF
    echo "  $run_status: $run_stage"
    if [ "$run_status" = succeeded ] || [ "$run_status" = gate_failed ]; then
      echo "PREVIEW_URL=$preview_url"
      exit 0
    fi
    case "$run_status" in
      failed|cancelled|interrupted)
        echo "Developer run $run_status: $run_error"
        exit 1 ;;
    esac
  done
fi

steps="cd $BUILD_DIR"
[ $FETCH -eq 1 ] && steps="$steps && .venv/bin/python src/fetch_regions.py $REGIONS"
steps="$steps && bash src/run_district_build.sh $MODE${REGIONS:+ --regions \"$REGIONS\"}"
SSH "rm -f $REMOTE_LOG; nohup bash -c '($steps) >> $REMOTE_LOG 2>&1; echo REMOTE_BUILD_RC=\$? >> $REMOTE_LOG' >/dev/null 2>&1 &"
echo "  started: $steps"
seen=0
while :; do
  sleep 120
  s=$(gcloud compute instances describe $VM --zone $ZONE --project $PROJECT --format="value(status)")
  [ "$s" = RUNNING ] || { echo "VM is $s -- preempted or stopped mid-build; run the same command again to resume"; exit 1; }
  n=$(SSH "wc -l < $REMOTE_LOG" | tr -dc 0-9); n=${n:-0}
  if [ "$n" -gt "$seen" ]; then SSH "sed -n '$((seen+1)),${n}p' $REMOTE_LOG" | cut -c1-220; seen=$n; fi
  SSH "grep -q '^REMOTE_BUILD_RC=' $REMOTE_LOG && echo done" | grep -q done && break
done
rc=$(SSH "grep '^REMOTE_BUILD_RC=' $REMOTE_LOG | tail -1 | cut -d= -f2" | tr -dc 0-9)
[ "${rc:-1}" = 0 ] || { echo "BUILD FAILED (rc=$rc) -- nothing published"; exit 1; }

echo "=== gate against live ==="
SSH "cd $BUILD_DIR && .venv/bin/python tools/predeploy_check.py; echo GATE_RC=\$?" | tee /tmp/remote_gate.$$
gate=$(grep '^GATE_RC=' /tmp/remote_gate.$$ | cut -d= -f2); rm -f /tmp/remote_gate.$$
if [ "$gate" != 0 ]; then
  if [ -n "$DEV_PUBLISH_USER" ]; then
    echo "PRODUCTION GATE FAILED (rc=${gate:-unknown}) -- continuing with developer preview only"
  else
    [ -n "$REVIEWED" ] || { echo "GATE FAILED -- not publishing"; exit 1; }
    echo "GATE FAILED -- publishing anyway, reviewed: $REVIEWED"
  fi
elif [ -n "$DEV_PUBLISH_USER" ]; then
  echo "PRODUCTION GATE PASSED"
fi

if [ -n "$DEV_PUBLISH_USER" ]; then
  echo "=== validate developer preview map contract ==="
  for region in $REGIONS; do
    SSH "cd $BUILD_DIR && .venv/bin/python tools/validate_quickstart_map.py --region-out 'data/out/$region' --map-data data" || {
      echo "DEVELOPER PREVIEW FAILED -- map output contract validation failed for $region"
      exit 1
    }
  done
  echo "=== publish temporary developer preview ==="
  preview_out=$(SSH "cd $BUILD_DIR && .venv/bin/python tools/publish_preview.py --user '$DEV_PUBLISH_USER' --regions '$REGIONS' --run-id '$RUN_ID'") || {
    echo "DEVELOPER PREVIEW FAILED -- the preview was not updated"
    exit 1
  }
  printf '%s\n' "$preview_out"
  preview_url=$(printf '%s\n' "$preview_out" | sed -n 's/^PREVIEW_URL=//p' | tail -1)
  lower_user=$(printf '%s' "$DEV_PUBLISH_USER" | tr '[:upper:]' '[:lower:]')
  case "$preview_url" in
    https://storage.googleapis.com/*/devel/"$lower_user"/preview.html) ;;
    *) echo "DEVELOPER PREVIEW FAILED -- publisher returned an invalid preview URL"; exit 1 ;;
  esac
  echo "PREVIEW_URL=$preview_url"
fi
[ $PUBLISH -eq 1 ] || { echo "built and gated; re-run with --publish to publish"; exit 0; }

echo "=== publish from the VM ==="
git pull -q --ff-only origin main || { echo "could not update this checkout"; exit 1; }
v=$(grep -oE 'dataVersion: "[0-9]+"' site-config.js | grep -oE '[0-9]+'); nv=$((v+1))
out=$(SSH "cd ~/solar-map && .venv/bin/python tools/publish_served.py $nv --keep 3 --protect $v && echo PUBLISHED_OK")
echo "$out" | grep -v "^Removing\|^Copying\|^\.*$" | tail -5
echo "$out" | grep -q PUBLISHED_OK || { echo "PUBLISH FAILED -- site unchanged"; exit 1; }
perl -pi -e "s/dataVersion: \"$v\"/dataVersion: \"$nv\"/; s#(dataBase: \"https://storage.googleapis.com/rewiring-solar-tiles/)v[0-9]+/#\${1}v$nv/#" site-config.js
grep -q "rewiring-solar-tiles/v$nv/" site-config.js || { echo "site-config.js has no dataBase to switch -- set it by hand"; exit 1; }
summary=$(SSH "cd ~/solar-map && .venv/bin/python -c 'import json; s=json.load(open(\"data/build_summary.json\")); t=s[\"totals\"]; print(\"%d regions, %s panels, %.1f GWh\" % (len(s[\"regions\"]), format(int(t[\"panel_count\"]), \",\"), t[\"kwh\"] / 1e6))'" | tail -1)
note=""; [ -n "$REVIEWED" ] && note="
Gate failed and was reviewed: $REVIEWED"
git add site-config.js
git commit -q -m "District build v$nv: $summary

Data at https://storage.googleapis.com/rewiring-solar-tiles/v$nv/
Built on the VM by tools/remote_build.sh${REGIONS:+ (regions: $REGIONS)}.${COMMIT_NOTE:+
$COMMIT_NOTE}$note" && git push -q origin main && echo "PUSHED (data v$nv)"
