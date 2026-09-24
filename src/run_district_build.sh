#!/bin/bash
# Full district build -- resumable, in the correct stage order.
#
# Replaces the pattern of hand-written one-off scripts scp'd to the VM. Those
# had no memory: the 31 Aug Queenstown rebuild was launched three times and
# each launch redid every completed region, because nothing on disk recorded
# what had already finished.
#
# Every stage goes through src/run_stage.py, which preflights the stage's
# inputs, records a completion marker on success, and (with --skip-done) skips
# work whose marker is newer than all of its inputs. So:
#
#   ./src/run_district_build.sh                  # incremental: rebuild only
#                                                # stale buildings, resume the
#                                                # rest (see the plan below)
#   ./src/run_district_build.sh --force          # rebuild everything
#   ./src/run_district_build.sh --yield-only     # the sun changed, the roofs
#                                                # did not: recompute every kWh
#                                                # from stored geometry, no LiDAR
#                                                # (src/apply_yield.py)
#   ./src/run_district_build.sh --regions "a b"  # just these regions
#
# Interrupting this and re-running it continues where it stopped.
#
# THERE IS NO MERGE ANY MORE. Each region ends by emitting its own tiles,
# cells, detail, heat-map tiles, addresses and a summary (src/emit_region.py),
# and src/combine_regions.py joins them into the served set. Nothing after the
# per-region stages reads the district into memory, which is what lets the
# same script build a town or a country (docs/scale-architecture.md). The
# terrain masks, deciles and panel shrink that used to run on the merged file
# run inside the emit, per region, at that region's own sun.
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python

# The selected-faces chain LEADS district builds. Without this export the build
# silently ignores every data/selected_faces/*.json the precompute wrote --
# there is no error, the old path just answers instead. Set
# SOLAR_SELECTED_FACES=0 explicitly to build old-path only.
export SOLAR_SELECTED_FACES="${SOLAR_SELECTED_FACES:-1}"
LOGDIR=data/build_logs
mkdir -p "$LOGDIR"

SKIP="--skip-done"
REGIONS=""
INCREMENTAL=0
YIELD_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --force)       SKIP=""; shift ;;
    --incremental) INCREMENTAL=1; shift ;;
    --yield-only)  YIELD_ONLY=1; SKIP=""; shift ;;
    --regions)     REGIONS="$2"; shift 2 ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
done

# The region list comes from all_areas(), which unions the config with what is
# actually on disk. A hard-coded list -- or the config alone -- is how a region
# silently never gets built: config.REGIONS held 23 entries while data/regions
# held 24, the missing one was `pilot`, and two district rebuilds skipped the
# town centre without erroring.
if [ -z "$REGIONS" ]; then
  REGIONS="$($PY -c 'from src.region_build import all_areas; print(" ".join(all_areas()))')"
fi

# Per-region stages, in dependency order.
STAGES="build_layout_geojson gate_panels rerank_layouts derive_solar_potential
        patch_roof_confidence bake_building_horizons build_heatmap_raster"
# After addresses: the region's own tiles, cells, detail and summary. This
# is what replaced the fan-in (docs/scale-architecture.md).
EMIT="emit_region"
if [ $YIELD_ONLY -eq 1 ]; then
  # The yield layer only: geometry, panels and shading factors stay as built;
  # every kWh is recomputed from them with the current solar model and
  # everything downstream of a kWh is redone. See src/apply_yield.py.
  STAGES="apply_yield rerank_layouts derive_solar_potential
          patch_roof_confidence bake_building_horizons build_heatmap_raster"
fi

echo "=== district build $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "regions: $(echo $REGIONS | wc -w | tr -d ' ')   resume: ${SKIP:-off}"

# SNAPSHOT THE BUILD WE ARE ABOUT TO REPLACE. The fan-in overwrites
# data/solar_potential.geojson, and once that is gone there is nothing left to
# compare the new build against -- every "did this help?" question becomes
# unanswerable. This was missing on 2 Sep: the only snapshot on the box predated
# the build that was actually deployed, so a comparison would have measured
# against the wrong baseline entirely, and it had to be taken by hand from the
# committed live file before the merge reached it.
#
# Deliberately non-fatal. A missing baseline is bad; losing eight hours of
# compute because the snapshot step tripped would be worse.
# The previous build's per-building ladders live in data/summaries/ now
# (written by combine). compare_builds reads them there, so the snapshot it
# diffs against is taken here as well: this branch only copied the folder to
# summaries_prev, which nothing reads, and the comparison ran against
# whatever stale build_snapshot_prev.json an older build had left behind.
if [ -d data/summaries ]; then
  rm -rf data/summaries_prev && cp -r data/summaries data/summaries_prev \
    && $PY src/compare_builds.py --snapshot \
    || echo "  WARN: could not snapshot the previous build -- comparison will be unavailable"
elif [ -f data/solar_potential.geojson ]; then
  $PY src/compare_builds.py --snapshot \
    || echo "  WARN: could not snapshot the previous build -- comparison will be unavailable"
else
  echo "  no existing build to snapshot (first run in this checkout)"
fi

# AUDIT THE INPUTS BEFORE SPENDING HOURS ON THEM. The 3 Sep run built 14
# regions LiDAR-only because their imagery mosaics were gone, and built
# arrowtown_hills as 50 buildings of zeros because its DSM described ground
# 340 m west of every building in it. Neither raised an error; both produced
# output indistinguishable from a real result, and both were found afterwards
# by hand. Ninety seconds of checking beforehand is the cheapest possible way
# to not repeat that.
#
# Non-fatal for the same reason as the snapshot above: a region with degraded
# inputs still builds, and refusing to start the district because one region is
# short of imagery would be a worse failure than the one being prevented. The
# point is that it is stated loudly at the top of the log rather than
# discovered days later.
if [ -f tools/audit_region_inputs.py ]; then
  echo "--- input audit ---"
  $PY tools/audit_region_inputs.py 2>/dev/null \
    | grep -E "PROBLEM|-> |only|regions with problems" \
    || echo "  (audit produced no findings)"
  echo "--- end input audit ---"
fi

# ------------------------------------------------------ incremental by default
#
# EVERY BUILDING IS REBUILT ONLY IF WHAT IT IS BUILT FROM HAS CHANGED: its
# selected reading, its drawn markup, or the geometry code (src/build_keys.py
# records all three per building). tools/patch_stale_selected.py plans each
# region:
#   clean  nothing stale -- layout and gate are skipped, the rest resumes
#   patch  a few stale -- patch_buildings rebuilds just them (layout, gate,
#          rerank, derive, confidence), byte-identical to a full build
#   full   no layouts, no keys, or over a tenth stale -- the full layout, which
#          fans across every core and is the faster way past that point
# The stages after the layout run with --skip-done, whose markers now record
# the code that wrote them, so a code change re-runs them and a preempted run
# resumes where it stopped.
#
# --force still rebuilds every stage of every region; --yield-only skips the
# geometry altogether (see src/apply_yield.py). --incremental is accepted for
# old scripts and changes nothing: this is what it used to ask for.
PLAN=data/build_state/incremental_plan.json
mkdir -p data/build_state
if [ -n "$SKIP" ] && [ $YIELD_ONLY -eq 0 ]; then
  echo "=== plan ==="
  $PY tools/patch_stale_selected.py --regions $REGIONS --plan "$PLAN" || exit 1
  echo "=== patch ($(date -u +%H:%M:%S)) ==="
  $PY tools/patch_stale_selected.py --regions $REGIONS --patch >>"$LOGDIR/_patch.log" 2>&1 \
    || { echo "FAILED: patching (see $LOGDIR/_patch.log)"; exit 1; }
fi
mode_of() {
  if [ -z "$SKIP" ] || [ $YIELD_ONLY -eq 1 ] || [ ! -f "$PLAN" ]; then echo full; return; fi
  $PY -c "import json,sys; print(json.load(open('$PLAN')).get(sys.argv[1], {}).get('mode', 'full'))" "$1"
}

fail=0
for r in $REGIONS; do
  mode=$(mode_of "$r")
  echo "=== $r: $mode ($(date -u +%H:%M:%S)) ==="
  for s in $STAGES; do
    flag=$SKIP
    if [ "$s" = "build_layout_geojson" ] || [ "$s" = "gate_panels" ]; then
      # the keys, not the markers, say whether the layout is current
      if [ "$mode" != "full" ]; then continue; fi
      if [ "$s" = "build_layout_geojson" ]; then flag="--force"; fi
    fi
    if ! $PY src/run_stage.py $flag "$s" "$r" >>"$LOGDIR/$r.log" 2>&1; then
      echo "  FAILED: $s for $r (see $LOGDIR/$r.log)"
      fail=1
      break
    fi
  done
  # Addresses need the network, and are a patch-in-place post-process. A
  # failure here must not discard the offline compute around it -- re-run
  # later with: python src/run_stage.py add_addresses <region>
  $PY src/run_stage.py $SKIP add_addresses "$r" >>"$LOGDIR/$r.log" 2>&1 \
    || echo "  WARN: addresses failed for $r -- patch later"
  # Where the photo sits relative to the LiDAR, per building; a failure here
  # only means the drawing stays where the LiDAR is.
  $PY src/run_stage.py $SKIP register_imagery "$r" >>"$LOGDIR/$r.log" 2>&1 \
    || echo "  WARN: image registration failed for $r -- drawing unshifted"
  if ! $PY src/run_stage.py $SKIP $EMIT "$r" >>"$LOGDIR/$r.log" 2>&1; then
    echo "  FAILED: $EMIT for $r (see $LOGDIR/$r.log)"
    fail=1
  fi
done

if [ $fail -ne 0 ]; then
  echo "=== stopping before the fan-in: at least one region failed ==="
  echo "Fix it, re-run this script, and completed regions will be skipped."
  exit 1
fi

echo "=== combine ($(date -u +%H:%M:%S)) ==="
# NO MERGE. Each region emitted its own tiles, cells, detail, heat-map tiles
# and addresses under data/out/<region>/; combine_regions joins them into the
# served set without ever reading the district into memory. The merged
# solar_potential.geojson and panel_layouts.geojson are no longer produced by
# the build -- src/merge_regions.py still exists for debugging, and nothing
# in the ship path reads its output.
$PY src/combine_regions.py || { echo "FAILED: combine_regions"; exit 1; }


# DID THE BUILD ACTUALLY USE ITS INPUTS? On 10 Sep a resumed district run
# skipped every layout stage on stale markers and shipped the previous
# geometry with fresh mtimes -- zero errors, bit-identical totals. A green
# build that ignored its inputs must FAIL here, not deploy quietly. The count
# now comes from the region summaries the emit stage wrote, summed by combine.
if [ "${SOLAR_SELECTED_FACES}" = "1" ] && [ "$(ls data/selected_faces 2>/dev/null | wc -l)" -gt 100 ]; then
  n_sel=$($PY -c 'import json; print(int(json.load(open("data/build_summary.json"))["totals"].get("from_selected_facets", 0)))')
  if [ "${n_sel:-0}" -lt 50 ]; then
    echo "FAILED: selected-faces enabled but only ${n_sel} from_selected facets across the build -- it did not use its inputs"
    exit 1
  fi
  echo "guard: ${n_sel} from_selected facets across the build"
fi

echo "=== complete $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
