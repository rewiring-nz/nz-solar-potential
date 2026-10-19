#!/bin/bash
# Build on the VM and, if the gate passes, publish -- from anywhere gcloud runs.
#
#   tools/remote_build.sh --regions "glenorchy kinloch"            # build + gate, no publish
#   tools/remote_build.sh --regions "glenorchy kinloch" --publish  # ...and publish if the gate passes
#   tools/remote_build.sh --fetch --regions "newtown" --publish    # fetch LiDAR/photos first
#   tools/remote_build.sh --publish --reviewed "why"               # publish although the gate failed
#   tools/remote_build.sh --yield-only --publish                   # the sun changed, not the roofs
#   tools/remote_build.sh --regions frankton_arm_lake --preview-user alice
#
# No --regions: an incremental build of every region (stale ones only).
#
# This is what the build-and-publish GitHub Action runs, and it works the same
# from a laptop. Everything is computed on the VM: it pulls the pushed code
# (tools/sync_vm.sh), builds (src/run_district_build.sh), gates against the
# live site (tools/predeploy_check.py) and publishes the served set to a new
# gs://solar-potential-map-tiles/v<N>/ itself (tools/publish_served.py). This
# machine then switches site-config.js to it and pushes that one-line change.
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
print("\t".join(values[key] for key in ("project", "vm", "zone", "ssh_user")))
'); then
  echo "ERROR: could not read shared cloud defaults from config.py."
  exit 1
fi
IFS=$'\t' read -r DEFAULT_PROJECT DEFAULT_VM DEFAULT_ZONE DEFAULT_VM_USER <<< "$DEFAULTS"
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

REGIONS=""; FETCH=0; PUBLISH=0; REVIEWED=""; KEEP=0; MODE=""; REF=main; PREVIEW_USER=""
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
    --preview-user) PREVIEW_USER="$2"; shift 2 ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
done
[ $FETCH -eq 1 ] && [ -z "$REGIONS" ] && { echo "--fetch needs --regions"; exit 2; }
[ $PUBLISH -eq 1 ] && [ "$REF" != main ] && { echo "only main is published; drop --publish to test a branch"; exit 2; }
[ $PUBLISH -eq 1 ] && [ -n "$PREVIEW_USER" ] && { echo "developer preview and production publish are separate actions"; exit 2; }
[ -n "$PREVIEW_USER" ] && ! printf '%s' "$PREVIEW_USER" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?$' && {
  echo "--preview-user must be a 1-39 character alphanumeric/hyphen alias"; exit 2;
}
case "$REGIONS" in *[!a-z0-9_\ ]*) echo "region names are lower-case letters, digits and _"; exit 2 ;; esac

STARTED=0
finish() {
  if [ $STARTED -eq 1 ] && [ $KEEP -eq 0 ]; then
    echo "=== stopping the VM ==="
    gcloud compute instances stop $VM --zone $ZONE --project $PROJECT --async -q >/dev/null 2>&1
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

VM_USER="${VM_USER:-j}" SYNC_VM="$VM" SYNC_ZONE="$ZONE" SYNC_PROJECT="$PROJECT" \
  tools/sync_vm.sh --ref "$REF" || exit 1

echo "=== build on the VM ==="
steps="cd ~/solar-map"
[ $FETCH -eq 1 ] && steps="$steps && .venv/bin/python src/fetch_regions.py $REGIONS"
steps="$steps && bash src/run_district_build.sh $MODE${REGIONS:+ --regions \"$REGIONS\"}"
SSH "rm -f ~/remote_build.log; nohup bash -c '($steps) >> ~/remote_build.log 2>&1; echo REMOTE_BUILD_RC=\$? >> ~/remote_build.log' >/dev/null 2>&1 &"
echo "  started: $steps"
seen=0
while :; do
  sleep 120
  s=$(gcloud compute instances describe $VM --zone $ZONE --project $PROJECT --format="value(status)")
  [ "$s" = RUNNING ] || { echo "VM is $s -- preempted or stopped mid-build; run the same command again to resume"; exit 1; }
  n=$(SSH "wc -l < ~/remote_build.log" | tr -dc 0-9); n=${n:-0}
  if [ "$n" -gt "$seen" ]; then SSH "sed -n '$((seen+1)),${n}p' ~/remote_build.log" | cut -c1-220; seen=$n; fi
  SSH "grep -q '^REMOTE_BUILD_RC=' ~/remote_build.log && echo done" | grep -q done && break
done
rc=$(SSH "grep '^REMOTE_BUILD_RC=' ~/remote_build.log | tail -1 | cut -d= -f2" | tr -dc 0-9)
[ "${rc:-1}" = 0 ] || { echo "BUILD FAILED (rc=$rc) -- nothing published"; exit 1; }

echo "=== gate against live ==="
SSH "cd ~/solar-map && .venv/bin/python tools/predeploy_check.py; echo GATE_RC=\$?" | tee /tmp/remote_gate.$$
gate=$(grep '^GATE_RC=' /tmp/remote_gate.$$ | cut -d= -f2); rm -f /tmp/remote_gate.$$
if [ "$gate" != 0 ]; then
  [ -n "$REVIEWED" ] || { echo "GATE FAILED -- not publishing"; exit 1; }
  echo "GATE FAILED -- publishing anyway, reviewed: $REVIEWED"
  [ -z "$PREVIEW_USER" ] || { echo "GATE FAILED -- developer previews require a passing gate"; exit 1; }
fi

if [ -n "$PREVIEW_USER" ]; then
  echo "=== publish temporary developer preview ==="
  preview_out=$(SSH "cd ~/solar-map && .venv/bin/python tools/publish_preview.py --user '$PREVIEW_USER' --regions '$REGIONS'") || {
    echo "PREVIEW FAILED -- build and gate passed, but the public preview was not updated"
    exit 1
  }
  printf '%s\n' "$preview_out"
  preview_url=$(printf '%s\n' "$preview_out" | sed -n 's/^PREVIEW_URL=//p' | tail -1)
  lower_user=$(printf '%s' "$PREVIEW_USER" | tr '[:upper:]' '[:lower:]')
  case "$preview_url" in
    https://storage.googleapis.com/*/devel/"$lower_user"/preview.html) ;;
    *) echo "PREVIEW FAILED -- publisher returned an invalid preview URL"; exit 1 ;;
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
perl -pi -e "s/dataVersion: \"$v\"/dataVersion: \"$nv\"/; s#(dataBase: \"https://storage.googleapis.com/solar-potential-map-tiles/)v[0-9]+/#\${1}v$nv/#" site-config.js
grep -q "solar-potential-map-tiles/v$nv/" site-config.js || { echo "site-config.js has no dataBase to switch -- set it by hand"; exit 1; }
summary=$(SSH "cd ~/solar-map && .venv/bin/python -c 'import json; s=json.load(open(\"data/build_summary.json\")); t=s[\"totals\"]; print(\"%d regions, %s panels, %.1f GWh\" % (len(s[\"regions\"]), format(int(t[\"panel_count\"]), \",\"), t[\"kwh\"] / 1e6))'" | tail -1)
note=""; [ -n "$REVIEWED" ] && note="
Gate failed and was reviewed: $REVIEWED"
git add site-config.js
git commit -q -m "District build v$nv: $summary

Data at https://storage.googleapis.com/solar-potential-map-tiles/v$nv/
Built on the VM by tools/remote_build.sh${REGIONS:+ (regions: $REGIONS)}.${COMMIT_NOTE:+
$COMMIT_NOTE}$note" && git push -q origin main && echo "PUSHED (data v$nv)"
