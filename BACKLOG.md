# Backlog

Open work, in priority order. Each item names what it is based on. History is
in git.

## Ship

- **Full re-lay and deploy.** Every region needs rebuilding with the 23 Sep
  fixes (ridge snap, frame bearing convention, per-face frame-loss bound,
  one-plane gate on forced faces, balcony-rule majority guard, segmentation
  setback decoupling) and the 24 Sep ones: skylights detected on clean faces,
  pocket panels at 100%, hip ridges snapping, the gate's deterministic order,
  rerank tie-breaks. Validated on Josh's cases, the 211 gate-flagged roofs
  (-1.5% vs no frame) and a 120-roof sample (+16.7% panels, none losing
  >30%); the 24 Sep changes on the synthetic region (tests/synthetic). Then
  `tools/deploy_from_vm.sh --push` once `tools/predeploy_check.py` passes. The
  deploy carries the density heat map, corrected seasonal curves, and
  Kingston / Wanaka / Albert Town / Hawea. It is the first build with build
  keys, so every region plans as `full` once.
- **Re-record goldens** (`tests/test_golden.py --record` on the VM). The
  24 Sep geometry changes move real roofs on purpose; look at the render of
  any golden whose count moved, and give the reason in the commit.
- **Verify after the re-lay:** 13 Plantation Rd (#4727237), Kingston counts,
  and the cases awaiting a verdict (`tools/cases.py check`).

## Geometry

- **Ridge snap reverts: re-measure.** Plain hip roofs were ALWAYS reverted
  (the re-cut strip ran past the apex); fixed 24 Sep by sliding the vertices of
  a ridge whose ends are both interior. After the re-lay, run
  `tools/ridge_offsets.py` to see what share is still refused and why.
- **Eaves.** 6.6-18.8% of roof-height returns fall outside the LINZ
  footprint, by up to 2 m, so roof AREA is understated everywhere; panels
  stay inside the footprint, which is right. Both earlier attempts are in
  git history (roof_outline, _extend_to_eave). Needs per-edge treatment
  judged against the drawn outlines, on real roofs.
- **Imagery alignment.** Aerial imagery sits up to 5 m from the LiDAR on
  some roofs (relief displacement). `src/register_imagery.py` measures a
  per-building shift but it is held off (`SOLAR_IMAGE_SHIFT=0`): neighbour
  coherence of the shifts was near chance. Needs a roof-only edge mask and a
  coherence gate before it ships.
- **arrowtown_hills** builds 50 buildings and estimates none.
- **Glenorchy** has no LiDAR coverage; it cannot be built.
- **Open-ended drawn lines:** nine marked roofs need patching for the
  line-extension rule (`tools/patch_labelled.py`).

## Model

- **Calibration point.** The SolarView cloud factor is derived at the pilot
  location; re-check against the Queenstown Aero normal. (Elsewhere the model
  already calibrates to the nearest of 28 NIWA measured-radiation stations.)
- **System derate** looks 4-5 points optimistic against PVGIS
  (`src/validate_against_pvgis.py`, needs network). A change is now a
  yield-only rebuild (`run_district_build.sh --yield-only`), minutes and no
  LiDAR.

## Scale

- **Dress rehearsal on one city**: docs/national-rehearsal.md. The fleet
  (`tools/fleet.sh`, `src/gcs_queue.py`) has never run as a fleet; nobody
  knows what LINZ and OpenTopography sustain (`tools/measure_fetch_rate.py`);
  the real region-pack ratio is unmeasured (`src/pack_region.py` prints it).
- Serving tiles from the bucket (`site-config.js` `dataBase`) is built and
  untested at scale; Pages still serves everything.

## Product

- Email updates (Buttondown) wait until the leaderboard has real change data.

## Standing rules

- Hand-drawn markup always wins over fitted geometry.
- Judge changes by measurement against the markup or by reviewing the render,
  never by aggregate counts alone.
- Nothing at district scale is built on a laptop; the VM builds, the laptop
  only relays through `tools/deploy_from_vm.sh`.
