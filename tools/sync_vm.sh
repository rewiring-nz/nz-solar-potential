#!/bin/bash
# Bring the build VM's code up to date from GitHub, safely.
#
# The VM's ~/solar-map is a git checkout of rewiring-nz/nz-solar-potential
# (since 14 Oct 2026; it used to be an scp'd copy that silently fell behind).
# It builds whatever is pushed: commit and push first, then run this. Region
# data, DEMs, point clouds and models are untracked and never touched.
#
# REFUSES TO RUN MID-BUILD. Each pipeline stage is a fresh process that reads
# the files at import, so replacing them while a district build is running
# mixes old and new code ACROSS REGIONS -- and the result looks like a normal
# build. That is unrecoverable without rebuilding, and undetectable afterwards.
# Use --force only if you are certain nothing is building.
#
# Builds rewrite a few tracked files (data/roof_cases.json gains check
# history); --autostash carries those edits across the pull. If one conflicts
# with an upstream change, the pull stops and says so; nothing is lost.
#
#   ./tools/sync_vm.sh                  # main, if the VM is idle
#   ./tools/sync_vm.sh --ref my-branch  # a pushed branch, to build it on the VM
#   ./tools/sync_vm.sh --force          # skip the busy check (know why)
set -u
cd "$(dirname "$0")/.."
VM=claude-doing-things
PROJECT=downloads-417521
ZONE=australia-southeast1-b
# The checkout lives in j's home; name the user so a runner or a teammate
# lands in the same place.
SSH="gcloud compute ssh --tunnel-through-iap ${VM_USER:-j}@$VM --project=$PROJECT --zone=$ZONE --command"

FORCE=0; REF=main
while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --ref)   REF="$2"; shift 2 ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
done

# The VM gets what is on GitHub, not what is on this machine.
if git rev-parse --git-dir >/dev/null 2>&1; then
  git fetch -q origin "$REF" 2>/dev/null
  if [ -n "$(git status --porcelain --untracked-files=no -- src tools tests config.py preview.html)" ]; then
    echo "NOTE: uncommitted code changes here will NOT reach the VM -- commit and push them first."
  fi
  if [ "$(git rev-parse --abbrev-ref HEAD)" = "$REF" ] && \
     [ -n "$(git log --oneline "origin/$REF..HEAD" 2>/dev/null)" ]; then
    echo "NOTE: local commits on $REF are not pushed; the VM will not have them."
  fi
fi

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

echo "=== pulling $REF on the VM ==="
$SSH "set -e; cd ~/solar-map
git config user.name >/dev/null || git config user.name 'build VM'
git config user.email >/dev/null || git config user.email 'vm@claude-doing-things'
git fetch -q origin '$REF'
git checkout -q '$REF' 2>/dev/null || git checkout -q -b '$REF' 'origin/$REF'
git pull -q --ff-only --autostash origin '$REF'
echo \"  VM at \$(git log --oneline -1)\"
d=\$(git status --porcelain --untracked-files=no | wc -l)
[ \"\$d\" = 0 ] || echo \"  (\$d tracked file(s) carry local build edits)\"" 2>&1 \
  | grep -v "^Warning\|^WARNING\|NumPy\|please see\|^$"
exit "${PIPESTATUS[0]}"
