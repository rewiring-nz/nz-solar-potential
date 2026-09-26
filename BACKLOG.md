# Backlog

Open work, in priority order. Each item names what it is based on. History is
in git.

## Ship

- **v40 deployed 26 Sep** past a gate that failed only on two small roofs
  that were misread before and still are: #4728022 (a 7 m2 triangle face on
  a large flat roof; 11 panels live) and #5372844 (two stray triangles; 6
  live). Both are face-reading failures, not obstruction ones.
- **The 13 BIG DROPs -- fixed in 44979ac7, in the v41 build.** The sunken
  detector had been dormant on clean faces until adbcc978 (24 Sep); once it
  ran everywhere, single low returns built jagged strips. Now majority cells,
  plane refresh, and roof-coloured lower strips as levels. Still carved, by
  design: real terraces (#4725197) and darker lower roofing (#4728664).
- **Balconies panelled on #4740503** (Josh's case) -- in v40 and before.
  The terrace row along the south facade takes panels between the carved
  pieces. Not fixed.
- **Verify after the re-lay:** 13 Plantation Rd (#4727237), Kingston counts,
  and the cases awaiting a verdict (`tools/cases.py check`).

## Imagery

- **Drawings follow the photo's lean -- live in v40.** The map shows
  LINZ Basemaps' aerial (2026 over Queenstown), orthorectified to the ground,
  so roofs lean 0.5-1.5 m off the LiDAR; outlines, faces, panels, the heat
  map and Josh's markup overlay looked misplaced (32 Frankton Rd, 10 Stanley
  St). `src/register_imagery.py` measures the lean per building against the
  LiDAR year's own photo (SURVEYS `reference_imagery_layer`), photo to photo;
  pilot: 1,001 of 1,066 shifted, median 0.57 m, p90 1.28 m. Markup traced on
  the map's own photo (every Queenstown region but pilot) is left where it
  was drawn. `SOLAR_IMAGE_SHIFT=0` turns it off.
- **Detect obstructions on the reference photo, not the map's.** Outside
  pilot the colour detectors read the 2026 photo, whose roofs lean off the
  LiDAR, so colour blobs land up to 1-2 m from the object. The reference
  photo sits on the LiDAR. Measure with `tools/obstruction_bench.py`.
- **Markup traced on a leaning photo sits off the LiDAR.** ~110 labelled
  roofs outside pilot were drawn on the 2026 photo; their faces drive the
  layout but are offset from the planes by the lean. Un-shifting them into
  the LiDAR frame when read would fix both; measure against face-IoU first.
- **One shift per building is not enough on big roofs.** 32 Frankton Rd
  (4,000 m2) leans unevenly; its match was weak and it borrowed 0.4 m.

## Geometry

- **Hips and valleys: overreach, and what is left of it.** Faces that ran
  past a hip or valley shipped the neighbour's slope as an obstruction (the
  synthetic L roof, two plain gables: 25 m2 of "objects"). Since 24 Sep
  `src/plane_seams.py` hands such roof back when the returns vouch for it
  (off their own plane by > 0.3 m, on a neighbour's within 0.15 m), cut on
  the planes' intersection. On the L roof that removed 6 m2 of phantom
  height obstruction. Two things remain there, both worth checking on real
  roofs with `tools/explain_obstructions.py <id>`: an overreach separated
  from its true face by roof with no returns cannot be judged; and
  COLOUR-only blobs sitting exactly on the plane at a fold (8.8 m2, median
  +0.01 m), which is shading, not an object -- but a flush skylight looks the
  same to the LiDAR, so any fix must use the blob's shape or position on the
  fold, not its height.
- **Lower roof levels -- fixed 25 Sep.** One face spanning two roof levels
  had its lower level carved by the sunken detector (9 Marine Parade: 168 of
  193 m2 of obstructions). `src/roof_levels.py` gives a wide, planar lower
  level its own face; decks narrower than 4 m and clutter stay obstructions.
- **Small obstructions (vents) and edge drops -- fixed 25 Sep.** Vents are
  kept when crisp against the roof around them; narrow sunken strips at a
  face's edge are keepouts, not obstructions. `tools/obstruction_bench.py`
  (42 complete marked roofs with imagery): small marks found 47 -> 66 of 163,
  small detections on a mark 15% -> 29%, area recall 0.281 -> 0.298,
  precision 0.261 -> 0.295. Still open: 97 of 163 small marks missed (many
  are invisible in the photo -- 54 have contrast under 5), and 254 small
  detections touch no mark. Run the bench on the VM for all 98 roofs.
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
