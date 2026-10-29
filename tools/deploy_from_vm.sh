#!/bin/bash
# Pull the VM's combined served set, gate it against live, and push.
#
#   tools/deploy_from_vm.sh            # pull + compare + gate, then stop and show
#   tools/deploy_from_vm.sh --push     # ...and commit + push if the gate passes
#   tools/deploy_from_vm.sh --push-reviewed "why"   # push although the gate
#       failed, after rendering what it listed; the reason goes in the commit
#
# Relays files the VM built: nothing here is computed on the laptop. The VM
# can now publish by itself (tools/remote_build.sh, the build-and-publish
# GitHub Action); this laptop path remains for reviewing a build locally.
set -u
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
PY=.venv/bin/python
VM=${DEPLOY_VM:-claude-doing-things}
# The VM's project comes from config, not gcloud's default project: a laptop
# whose default is another project reaches a stopped VM of the same name there.
PROJECT=${DEPLOY_PROJECT:-$($PY -c 'import config; print(config.CLOUD_SHARED_BUILD["project"])')}
ZONE=${DEPLOY_ZONE:-australia-southeast1-b}
. tools/cloud_profile.sh
if [ -n "${SOLAR_CLOUD_PROFILE:-}" ]; then
  VM=${DEPLOY_VM:-$(cloud_profile_value instance_name)} || exit 2
  PROJECT=${DEPLOY_PROJECT:-$(cloud_profile_value project)} || exit 2
  ZONE=${DEPLOY_ZONE:-$(cloud_profile_value zone)} || exit 2
fi
PUSH=0; REVIEWED=""
[ "${1:-}" = "--push" ] && PUSH=1
if [ "${1:-}" = "--push-reviewed" ]; then
  PUSH=1; REVIEWED="${2:-}"
  [ -n "$REVIEWED" ] || { echo "--push-reviewed needs the reason the gate's list is acceptable"; exit 1; }
fi

echo "=== pull the served set ==="
for f in panel_layouts.pmtiles buildings.pmtiles building_cells.pmtiles addresses.json \
         assumptions.json seasonal_curves.json build_summary.json markup_lines.geojson; do
  gcloud compute scp --tunnel-through-iap --project="$PROJECT" --zone "$ZONE" --quiet "$VM:~/solar-map/data/$f" "data/$f" || { echo "FATAL: could not pull $f"; exit 1; }
done
for d in building_detail heatmap_tiles summaries seasonal_curves addresses sun; do
  rm -rf "data/$d.pull" && mkdir -p "data/$d.pull"
  if gcloud compute scp --tunnel-through-iap --project="$PROJECT" --zone "$ZONE" --quiet --recurse "$VM:~/solar-map/data/$d" "data/$d.pull/" 2>/dev/null; then
    rm -rf "data/$d" && mv "data/$d.pull/$d" "data/$d"
  else
    # the gate reads summaries, the page reads detail: stale copies of either
    # would ship last build's data under this build's tiles
    # sun: the page's workings read data/sun/<region>.json; the gate refuses a
    # build without them
    case $d in summaries|building_detail|sun) echo "FATAL: could not pull $d"; rm -rf "data/$d.pull"; exit 1 ;; esac
  fi
  rm -rf "data/$d.pull"
done
[ -f data/addresses.json ] && [ -d data/addresses ] && rm -rf data/addresses   # flat file wins when both exist

echo "=== what the VM built ==="
$PY -c "import json; s=json.load(open('data/build_summary.json')); t=s['totals']; print(len(s['regions']), 'regions,', int(t['n']), 'buildings,', int(t['panel_count']), 'panels,', round(t['kwh']/1e6,1), 'GWh; by type:', {k:int(v['n']) for k,v in s['by_type'].items()})"

echo "=== gate against live ==="
if ! $PY tools/predeploy_check.py; then
  if [ -z "$REVIEWED" ]; then echo "GATE FAILED -- not pushing"; exit 1; fi
  echo "GATE FAILED -- pushing anyway, reviewed: $REVIEWED"
fi

if [ $PUSH -eq 1 ]; then
  # THE DATA GOES TO THE TILES BUCKET, NOT TO GIT (tools/publish_served.py).
  # A new version folder, the site pointed at it, the newest three kept so
  # any of them is a one-line rollback. Git gets only the site-config switch.
  v=$(grep -oE 'dataVersion: "[0-9]+"' site-config.js | grep -oE '[0-9]+'); nv=$((v+1))
  $PY tools/publish_served.py "$nv" --keep 3 --protect "$v" || { echo "PUBLISH FAILED -- site unchanged"; exit 1; }
  sed -i '' "s/dataVersion: \"$v\"/dataVersion: \"$nv\"/" site-config.js
  sed -i '' -E "s#(dataBase: \"https://storage.googleapis.com/solar-potential-map-tiles/)v[0-9]+/#\1v$nv/#" site-config.js
  grep -q "solar-potential-map-tiles/v$nv/" site-config.js || { echo "site-config.js has no dataBase to switch -- set it by hand"; exit 1; }
  git add site-config.js
  summary=$($PY -c 'import json; s=json.load(open("data/build_summary.json")); t=s["totals"]; print("%d regions, %s panels, %.1f GWh" % (len(s["regions"]), format(int(t["panel_count"]), ","), t["kwh"] / 1e6))')
  note=""; [ -n "$REVIEWED" ] && note="
Gate failed and was reviewed: $REVIEWED
"
  git commit -q -m "District build v$nv: $summary

Data at https://storage.googleapis.com/solar-potential-map-tiles/v$nv/
$note
Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" && git push -q origin main && echo "PUSHED (data v$nv)"
else
  echo "dry: re-run with --push to publish"
fi
