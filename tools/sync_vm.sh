#!/bin/bash
# Ship the current local code to the build VM -- both trees, safely.
#
# The VM's ~/solar-map and ~/solar-wellington are scp'd payload copies, not git
# clones, so they do not update themselves and can silently fall behind. That
# matters more than it sounds: on 31 Aug the Island Bay rebuild was staged
# against a tree whose panel_fitting was missing the gap-fill pass, so the
# rebuild would have reproduced the bug it was meant to fix.
#
# REFUSES TO RUN MID-BUILD. Each pipeline stage is a fresh process that reads
# the files at import, so replacing them while a district build is running
# mixes old and new code ACROSS REGIONS -- and the result looks like a normal
# build. That is unrecoverable without rebuilding, and undetectable afterwards.
# Use --force only if you are certain nothing is building.
#
#   ./tools/sync_vm.sh              # both trees, if the VM is idle
#   ./tools/sync_vm.sh --force      # skip the busy check (know why)
set -u
cd "$(dirname "$0")/.."
LOCAL_ROOT="$(pwd)"
VM=claude-doing-things
PROJECT=downloads-417521
ZONE=australia-southeast1-b
SSH="gcloud compute ssh $VM --project=$PROJECT --zone=$ZONE --command"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

if [ $FORCE -eq 0 ]; then
  echo "checking whether the VM is mid-build..."
  BUSY=$($SSH 'pgrep -f "src/[a-z_]*\.py" | wc -l' 2>/dev/null | tr -d " \r\n")
  if [ "${BUSY:-0}" != "0" ]; then
    echo "REFUSING TO SYNC: $BUSY pipeline process(es) running on the VM."
    $SSH 'pgrep -af "src/[a-z_]*\.py" | head -3' 2>/dev/null
    echo ""
    echo "Replacing code mid-build mixes old and new across regions and the"
    echo "result looks normal. Wait for it to finish, or pass --force."
    exit 1
  fi
  echo "  VM idle."
fi

sync_tree() {
  local name="$1" local_dir="$2"
  [ -d "$local_dir" ] || { echo "  no local $local_dir -- skipping $name"; return; }
  echo "=== $name ==="
  $SSH "mkdir -p ~/$name/src ~/$name/tools ~/$name/tests" >/dev/null 2>&1
  gcloud compute scp --project=$PROJECT --zone=$ZONE --quiet \
    "$local_dir"/src/*.py "$VM:~/$name/src/" >/dev/null || return 1
  gcloud compute scp --project=$PROJECT --zone=$ZONE --quiet \
    "$local_dir"/src/*.sh "$VM:~/$name/src/" >/dev/null 2>&1 || true
  for f in config.py requirements.txt requirements.lock.txt preview.html; do
    [ -f "$local_dir/$f" ] && gcloud compute scp --project=$PROJECT --zone=$ZONE \
      --quiet "$local_dir/$f" "$VM:~/$name/$f" >/dev/null 2>&1
  done
  for d in tools tests; do
    ls "$local_dir/$d"/* >/dev/null 2>&1 && gcloud compute scp \
      --project=$PROJECT --zone=$ZONE --quiet \
      "$local_dir/$d"/* "$VM:~/$name/$d/" >/dev/null 2>&1
  done
  echo "  synced"
}

sync_tree solar-map "$LOCAL_ROOT"
sync_tree solar-wellington "$LOCAL_ROOT/../solar-wellington"

# Confirm the fix that motivated all of this actually landed, in both trees.
echo ""
echo "=== verifying ==="
$SSH '
for t in solar-map solar-wellington; do
  if [ -f ~/$t/src/panel_fitting.py ]; then
    if grep -q gap_fill ~/$t/src/panel_fitting.py; then g=yes; else g=NO; fi
    if [ -f ~/$t/src/preflight.py ]; then p=yes; else p=NO; fi
    echo "  $t: gap-fill=$g preflight=$p"
  fi
done' 2>/dev/null
echo ""
echo "Done. Region data, DEMs and point clouds are NOT touched by this script."
