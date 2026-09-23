# Backlog

Open work, in priority order. Each item names what it is based on. History is
in git.

## Ship

- **Full re-lay and deploy.** Every region needs rebuilding with the 23 Sep
  fixes (ridge snap, frame bearing convention, per-face frame-loss bound,
  one-plane gate on forced faces, balcony-rule majority guard, segmentation
  setback decoupling). Validated on Josh's cases, the 211 gate-flagged roofs
  (-1.5% vs no frame) and a 120-roof sample (+16.7% panels, none losing
  >30%). Then `tools/deploy_from_vm.sh --push` once `tools/predeploy_check.py`
  passes. The deploy carries the density heat map, corrected seasonal curves,
  and Kingston / Wanaka / Albert Town / Hawea.
- **Re-record goldens** after the last change (`tests/test_golden.py
  --record` on the VM), with the reason in the commit.
- **Verify after the re-lay:** 13 Plantation Rd (#4727237), Kingston counts,
  and the cases awaiting a verdict (`tools/cases.py check`).

## Geometry

- **Ridge snap reverts.** About a quarter of measurable off-crest ridges are
  refused by the footprint/overlap guard when a third facet shares a vertex
  just outside tolerance. `tools/ridge_offsets.py` measures it.
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
  location; re-check against the Queenstown Aero normal.
- **System derate** looks 4-5 points optimistic against PVGIS
  (`src/validate_against_pvgis.py`).

## Scale

- The build fleet (`tools/fleet.sh`, `src/gcs_queue.py`) has run as single
  workers but never as a fleet.
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
- `solar-map` and `solar-wellington` share code by hand: run
  `tools/check_repo_sync.py` before pushing shared files.
