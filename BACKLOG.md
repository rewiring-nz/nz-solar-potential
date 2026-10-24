# Backlog

Open work, in priority order. Each item names what it is based on. History is
in git. Open items are also GitHub Issues (#16-36) on the project board
(https://github.com/orgs/rewiring-nz/projects/5), which is where to pick up
and assign work.

## Ship

- **#44: LERC dependency checks and automatic reader selection.** Local pipeline
  preflight tests actual decoding before acquisition and reports repair guidance.
  The default automatically selects `GTiff` or `LIBERTIFF` without requiring a
  per-run environment setting. S3 mosaics use strict, atomic VRT creation;
  offline tests cover decoding, crop/resampling, and preservation after failed inputs.

- **v52 LIVE 20 Oct 18:45 NZT: panels in clean racked arrays** (gate reviewed:
  panels 2,067,057 -> 2,008,902, -2.8%; no roof lost face area; the five big
  drops rendered -- scattered and mixed-orientation panels gone, clean arrays
  kept; four tiny roofs with garbage layouts withheld). (Josh's verdicts on v51, 20 Oct: 0 fixed,
  12 better, 13 still wrong). His rule: panels go on racks -- rows along the
  eave, portrait up the slope, clear arrays, never a landscape panel beside a
  portrait one, never a lone panel; 100% density = every clean array, not a
  clean array plus a rotated edge strip. Rules now (src/panel_fitting.py
  RACKING, enforce_arrays): one orientation per face, no mixed-orientation
  "pockets", no lone panel, 3+ panels on a face, a clean pair only on a face
  under 12 m2 (small, angular sections); blocks split by a vent or skylight on
  one face count together (rails span a gap of up to 1.3 m). Measured on 400
  sample roofs (tools/layout_ab.py; health checks mixed_orientation and
  small_array use the same rule) against racking off: mixed orientation
  70-74% -> 0% of roofs, lone/small groups 71% -> 0-0.5%, panels -10.8%
  (Arrowtown) / -10.3% (CBD), empty good faces 2% -> 5% / 7%. Renders: the
  losses are scattered fill on cluttered roofs and rotated edge strips
  (4735172 went from portrait block + landscape strip to one clean 2x2).
  Getting there: the first strict cut (4 touching panels) cost 20% and
  emptied 12-14% of good faces; every newly empty face traced to the array
  rule (a row 2 + gap + 3 split at a 1.1 m join; 3 + 3 around a skylight; a
  roof of small triangular faces 138 -> 12), fixed one by one. Also in v52:
  each face's plane in the layouts, and the health check's seam_off_ridge.
  Goldens re-recorded (were from v41).
  Roof lines next: all nine "lines wrong" roofs run on offline face readings
  scored 0.32-0.56 (some photo-based, shadow-prone).

- **v53 LIVE 21 Oct 06:40 NZT (built as v53b).** Gate reviewed: panels
  2,008,902 -> 2,010,966 (+0.1%); 12 roofs lost >8% face area, each 35 m2 or
  less; 14 big drops rendered -- on most the LiDAR faces explain clearly more
  of the returns than the reading they replaced (4733741 0.77 -> 0.97), but
  narrower faces and new obstructions cost panels (4741996, 4733741,
  4746337, 4735304's blob); judged by Josh's verdicts before any rule
  changes. Verdicts page refreshed: v51 (as judged) beside v53, 27 roofs,
  previous verdicts shown, new verdicts saved separately (verdicts_v53).
- **Sunlight data, 22 Oct (Josh: "more granular"; low-solar runs for battery
  resilience).** Live: low-solar runs on every building card (low_solar.js)
  -- longest run of days under X% of the roof's average day (X = 20,
  editable), the once-a-decade run (90th percentile of yearly longest) and a
  typical year's (median), with frequencies, from ERA5 daily radiation
  1940-2025 (Copernicus via Open-Meteo, CC BY 4.0, packed in climate/era5 by
  tools/pack_era5_daily.py) scaled to each roof's own seasonal curve. At
  20%: north roofs in Queenstown max 3 days (Apr 1953), typical 1; south
  roofs "low" all winter (said on the card). On main, ships next build:
  ERA5 differences between places scale the NIWA calibrations
  (era5_spatial_ratio) -- Wakatipu basin within 0.3% (one sky at 25 km),
  Wanaka +2.0%, Hawea +1.8%, Glenorchy -2.7%. ERA5 checked against SolarView
  (1391 vs 1365 kWh/m2/yr, months within 10%). Needs Josh: NIWA CliFlo
  daily radiation (Queenstown Aero, Auckland) to check how much reanalysis
  smooths the darkest days; optionally an NREL API key for Himawari 2 km
  satellite solar (2016-2020) -- the only route to suburb-level cloud.

- **24 Oct: installer layouts, ready for v57 (Josh: 29 York St "panels on the
  shady side", a lone panel, landscape on a face; 40 Youghal St landscape).**
  Committed, judged on 400 sample roofs (tools/layout_ab.py) and the markup
  benchmark: (1) sunny side only -- a face more than 112.5 deg off north is
  bare when the roof has a sunnier side (under 80% of its sunniest face's
  sun share); under 70% of the site's best no face takes panels
  (build_layout_geojson.away_from_sun); panels -16% central Queenstown, -23%
  Arrowtown, the south halves of gables; (2) the panel gate's ground test uses
  the terrain plane, not the 20 m median (hillside roofs read as ground: York
  lost 10 of 11 sunny-face panels; 1,262 "sparse" drops in pilot); (3) the gate
  re-racks each face it touched (105 lone panels and 904 pairs shipped in
  pilot); (4) strips narrower than a portrait row whose returns lie on the
  neighbouring slope's plane join it (face_straighten.absorb_slivers); (5) a
  detected obstruction hugging a face edge goes unless its returns stand up
  (obstruction_planarity.drop_edge_hugging): precision 0.41 -> 0.47, recall
  0.39 -> 0.36; (6) FLAT_SLOPE_DEG 10 -> 5: 40 Youghal St's 9.9 deg gable was
  racked as flat (rows along the building, landscape); landscape on 5-10 deg
  faces 63% -> 1% Wanaka, 49% -> 2% CBD, 65% -> 8% Arrowtown, panels +0.2-0.4%.
  Goldens need re-recording at the v57 build.
- **Markup benchmark (tools/markup_benchmark.py, 24 Oct).** Josh's drawn roofs
  built blind (SOLAR_HIDE_LABELS=1) and scored against his drawing. Baseline,
  131 roofs: outline IoU 0.91, face IoU 0.63 (32 at 0.8+), roof-line F 0.54
  (48 at 0.7+), obstruction precision 0.41 / recall 0.39 on roofs marked
  complete, panels 12% under the drawn. The colour detector draws 2/3 of the
  false obstruction area (1,170 of 1,749 m2): next target. Tried: keeping a
  corroborated colour blob whole only up to 4 m2 (was 15) -- precision 0.468
  -> 0.470, recall 0.361 -> 0.358: not the source; the false colour area is
  elsewhere. Colour-only blobs (no height overlap, passing _lidar_signature)
  switched off: precision 0.47 -> 0.51, recall 0.36 -> 0.32, panels +2% --
  a trade, not a win; kept. The detector needs a better test of what an
  object looks like, not a threshold. NEXT (after v57): the trained photo
  obstruction model (data/models/photo_obstruct_v1.pt) is only used in towns
  without LiDAR, yet on held-out towns it scores object recall 0.68 /
  precision 0.36 against the colour detector's 0.30 / 0.30. Josh's markup is
  in its training labels, so retrain with the benchmark roofs held out, then
  try it as the photo path in LiDAR towns (in place of, or as referee for,
  colour blobs) and judge on tools/markup_benchmark.py. Roof lines: a
  vision-model referee needs Josh's decision on an API key.
- **Resilience audited and fixed (live 24 Oct).** Days were scaled by total
  sunlight, so a tilted roof's overcast days came out ~2x too bright; beam and
  diffuse now scale apart (Erbs daily diffuse fraction; the roof's own diffuse
  share from the chart). ERA5 checked: homogeneous across decades, r 0.94 with
  NASA POWER daily (same mean), about half POWER's very dark winter days
  (POWER's early record has artefacts, e.g. 18-24 Jul 1984 near zero). Needs
  Josh: NIWA CliFlo daily radiation (Queenstown Aero, Auckland) for a station
  check. Lightbox redrawn to fit the window; darker blue, red-orange highlight.
- **v55 LIVE 21 Oct 18:45 NZT:** portrait first, whole-row partial systems,
  clean hips (with the fewer-faces rule), two-level flat roofs, roof-change
  notes (photo_changed; 45 roofs in pilot). Gate reviewed: panels
  2,010,966 -> 1,988,394 (-1.1%, mostly portrait); 16 roofs lost >8% face
  area (each under 75 m2); 16 big drops rendered -- portrait rows on about
  half, hip readings that fit the LiDAR better on the rest (5372095,
  4727160 crooked). 12/16 Kent St 294 -> 347. Verdicts page refreshed
  (v51 vs v55, 29 roofs, saved as verdicts_v55); Josh emailed.
- **v54 BUILT 21 Oct, NOT PUBLISHED.** The VM stopped at its 40 h spot limit
  mid-build (resumed with --force for the last 7 regions). Gate passed (panels
  -336, 0 zeroed, 12 roofs lost face area, each under 75 m2) but the 9 big
  drops rendered badly: the straighter, jog-free hips made an 8-face hip
  viable on 8-corner gables and near-flat roofs, and it replaced 2-4 face
  readings it explained slightly WORSE, inside the near-tie allowance
  (4746263 0.75 -> 0.73, 4719229, 4730948). Fixed (16da3be4): a form with
  more faces than the reading must explain at least as well. Ships in v55.
- (history) **v53 built 21 Oct, NOT PUBLISHED (face-area check: 586 roofs lost more
  than 8% of their face area; 4725177 1,918 -> 252 m2, 563 -> 61 panels; 51
  big drops).** Cause: a weak reading that lost the face competition was
  replaced by the LiDAR path, whose faces then failed downstream gates the
  reading's faces are exempt from (4725177's 1,250 m2 face at "authored +
  kept"). The 213-roof sample had no face-area check, so it never showed.
  Fix (195ca155): the competition compares roof area AFTER the attach stage
  on both sides and the LiDAR path must keep 92% of it; every A/B now runs
  ~/ab/facecmp.py against the live layouts. Rebuilding as v53b, together
  with the v54 photo-lean fix. What v53 was meant to ship: Josh, 20 Oct: 34 and 14 Ballarat St, 3 Rees St "astonishingly
  bad". All three ran on precomputed face readings (data/selected_faces)
  explaining 32-36% of their LiDAR returns; the pipeline trusted any reading
  over 0.30 (half of all 47,302 score under 0.54), and racking cannot rescue
  a wrong face. What ships:
  - a reading scored under 0.60 COMPETES with the whole LiDAR path
    (partition, then skeleton / plain-roof competitors) and loses when that
    explains 5+ points more of the returns, or explains as much while the
    reading's own faces cover under 85% of the roof (the rest residual
    fill), or nearly as much with 40% fewer faces (src/roof_segmentation.py
    _partition_facets; SELECTED_TRUST etc. in roof_partition.py). A hard bar
    at 0.60 was tried first: it fixed the bad roofs but threw away good
    low-scored readings (4735292's pyramid hips) and moved seams off the
    ridges (31% -> 33%); points-explained alone could not referee (34
    Ballarat's padded two-strip reading 0.63 vs 0.67);
  - installer rows (panel_fitting.shape_arrays, SOLAR_RECT_ARRAYS=0 off):
    each row a continuous run, a panel alone in its row goes, steps on hip
    faces stay, and a face is never emptied to tidy it (that first cut took
    empty good faces 5.6% -> 8.0%);
  - a raised roof level marked as an obstruction is roof when its returns
    lie on a plane of their own parallel to the face (obstruction_planarity).
    Tested; it did not fire on the case that prompted it (4735304's blob is
    rough, 0.30 m median off any plane -- likely a real deck).
  Measured against v52's rules: 213 CBD roofs -- panels 17,394 -> 17,408,
  clean roofs 35.2% -> 41.8%, ragged faces 55.9% -> 42.3%, empty good faces
  7.5% -> 5.6%, seams off the ridge unchanged at 31.0%; 200 Arrowtown roofs
  -- panels -1.6%, ragged faces 58% -> 46%, empty good faces 5.0% -> 3.5%.
  Renders: 3 Rees 29 -> 55 panels in rows, 34 Ballarat 19 -> 37 in three
  clean rows, 4725580's triangle patchwork -> clean faces (31 -> 68),
  4735371 43 -> 97, 5371072's townhouse rows clean. Still wrong: 14
  Ballarat (flat roof cut by skylight strips), 4725584 (keeps its 148-piece
  reading: 0.85 vs the LiDAR path's 0.64), big obstruction blobs on some
  flat roofs. Obstructions: of 2,124 on those roofs 28% stand up out of the
  roof in the LiDAR, 22% are flat on it (skylights are flat and real), 50%
  have too few returns to judge -- next, a photo check.

- **In v53 (live): panels "over the edge" -- the photo's lean.**
  Josh's edge complaints (13 Douglas Ave 4746298, 42 Suburb St, 12/16 Kent
  St): their outlines sit 98-100% on the LiDAR roof and no shift improves
  that, so faces and panels are where the survey says; what is off is the
  map photo's lean, which src/register_imagery.py measures per building and
  moves the drawing by. Its neighbour check overrode correct answers: a
  taller roof leans further than the houses round it, so 13 Douglas Ave's
  sharp 1.6 m match (steady at every window size) shipped its neighbours'
  0.8 m -- panels a metre off the roof in the photo. Now a match that would be
  replaced gets a second look over a 12 m window; steady (within 0.3 m) at a
  trusted sharpness, it keeps its own answer, unless that is over 2.5 m from
  the neighbours. On 300 Arrowtown roofs the wide window reproduced trusted
  matches within 0.5 m 97% of the time. Dry run on arrowtown_millbrook: 275
  second looks kept, 74 roofs move more than 0.5 m; rendered on the photo, 10
  of the 12 biggest moves sit visibly better (one jumped 3.9 m -- hence the
  2.5 m cap). Also confirmed on v52: per-face orientation is right -- the
  pyramid (4735292) racks portrait up every face.

- **v54 (building): plain-form roofs keep their lines; hips with straight
  valleys.** Ranking every roof in live v52 by the health
  checks: the worst 40 big roofs and the worst 40 houses are almost all roof
  lines (seams off the ridge 39/40, ragged faces 39/40 and 34/40); panel-level
  faults no longer top the list. Two causes found on the worst houses:
  (1) where a plain hip form wins (src/structured_roof.py), the later stages
  (ridge snap, seams to plane lines, straightening) moved its exact lines
  onto noisy plane crossings -- 4718459's T-shaped hip came out with a
  doubled ridge and curved seams; those stages now skip a plain-form roof
  (SOLAR_KEEP_FORMS=0 off); (2) the hip form itself (synthesize_dsm.hip_faces)
  labelled each point with its nearest wall SEGMENT, a circle round every
  inside corner, so T and L houses had curved valleys; it now uses each
  wall's swept zone between the bisectors at its ends (SOLAR_HIP_BISECTORS=0
  off) -- an equal-armed L test house's two valley faces went 39.5 / 52.1 m2
  to 47.0 / 47.0. On 150 Shotover/Lake Hayes houses: ragged faces 47.3% ->
  45.3%, sliver faces 2.0% -> 1.3%, clean 26.7% -> 27.3%, no face area lost;
  Arrowtown unchanged (few roofs there take a plain form). (3) A hip built on
  every shallow wall step fanned into slivers (4726934: 16 faces); hips are
  now built on the outline with steps under 1 m straightened, their faces
  still covering the real outline (a notched house: 8 -> 4 faces). On the 40
  worst houses against v52 (renders): 4747647's web of crooked lines became a
  clean hip, 4730928 / 4722969 / 4728389 / 4719781 / 4726934 cleaner, none
  worse. Why plain forms reach few houses (150 Shotover houses): the hip form
  is rejected on 86 because a face fits at an impossible pitch -- most houses
  are several wings, not one hip. Prototype src/composite_roof.py (a house as
  its wings, each hip/gable/mono/flat, joined as solids with straight
  valleys; overlapping wings; faces refitted on the returns they win) beats
  the current faces on some worst houses (4726934 0.62 -> 0.83 of returns)
  but on 139 Shotover houses it explains a median 51 points FEWER returns
  than the current faces (within 0.03 on 6 of 82) -- the wing fits are wrong,
  not the referee; no face-count weighting changes the outcome. OFF
  (SOLAR_COMPOSITE_ROOFS=1 on); needs proper wing fitting before it can help.

- **In v55 (live), also:** (1) partial systems are whole
  rows in one block -- the fill order ranked panels by their own yield
  stretched to the array's range (shading noise on an open roof), so 284 of
  12 Brunswick St's 562 panels were a blob full of holes; now coarse sun
  bands (3%), then rows outward from the best row (panel_fitting
  _block_order; SOLAR_BLOCK_ORDER=0 off); (2) roofs changed since the 2021
  survey are flagged (photo_changed) and the card says so -- survey photo
  vs map photo inside the outline, brightness change > 60 and edge
  correlation < 0.25; 35 Brecon St was a deck under construction in 2021
  and has rooftop plant now; CBD top ten all rebuilt/demolished, Arrowtown
  ~8/12 (rest re-roofed), so a note, not a withheld layout; (3) portrait
  first: live layouts were 36-51% landscape on pitched roofs (a 10% margin
  and one orientation lock per building); landscape must now fit 40% more
  (SOLAR_LANDSCAPE_MARGIN): on 200 Arrowtown roofs landscape 47% -> 7% of
  pitched panels, panels -4.7%, empty good faces 4% -> 5%. Live already: the 14 kW household cap only for
  one house (a residential roof over 350 m2 is uncapped; 12 Brunswick St
  was capped at 14 kW).
- **In v55 (live): obstruction "blobs" that are roof levels.** 12/16 Kent St (4735304, Josh: "no panels on the right wing's
  empty flat area") had a 160 m2 obstruction whose returns are two flat
  levels at the face's pitch (50% and 37%, 0.4 m apart) -- one 961 m2 face
  spans two wings. obstruction_planarity now calls a big patch roof when up
  to three planes parallel to the face hold 80% of its returns (plant and
  ducts sit at every height and do not). 7 flat-roof cases 722 -> 812
  panels (4735304 285 -> 358, right wing filled); 213 CBD roofs +1% panels,
  no face area lost. Watch: 4735290 now places panels over dark rectangles
  that were inside its blob (skylights or existing panels).
- **Front end, 21 Oct:** sun simulation cross-fades between frames and
  follows a drag at most ~7 times a second (Josh: "glitchy when dragging").
- **3D buildings (live 21 Oct; Josh: the old 3D "looks very bad"):** the 3D
  button shows each building as a solid (buildings3d.js, three.js as a
  MapLibre custom layer) instead of draping the map over the DSM, which
  turned every roof edge into a smeared slope with the panels hanging down
  it. Flat ground; walls vertical; each roof face drawn on the tilt the
  tiles carry (slope_deg/aspect_deg) at the height the DSM gives it (median
  inside the face, outliers rejected); panels on their face, filtered by the
  page's own panelVisible; heat-map mode colours the faces by annual sun.
  Checked on 603 Fernhill faces: the tile tilt and a free least-squares fit
  of the DSM agree (median 4 deg aspect, 2.2 deg slope; median residual
  0.13 m vs 0.12 m). About 1 ms per building, time-sliced; three.js loads on
  the first switch to 3D, so the 2D page is unchanged (pixel-identical after
  a 3D on/off cycle). The DSM terrain stays as the fallback. Open: the
  ground is flat, so a hillside house stands on the 25th-percentile height
  of a ring 2-5 m outside it; trees are not drawn; the sun simulation
  shades the ground, not the meshes.

- **Front end, 20 Oct (live):** a building opens with its economic system --
  the panels that pay for themselves (merit order, priced by the bill model
  at the full system), at most 14 kW for a home, no cap for a business -- and
  a System size slider on the card (map and live view follow; "Back to the
  economic size" restores it; the default rounds down to whole arrays). Four money figures: Bill savings, Total bill
  savings, Net savings, Return on investment. The all-buildings dashboard
  starts collapsed to the Heat Map / Panel Layout buttons (hidden on phones).
  Sun simulation (sun_shadows.js): shadows cast from the LiDAR surface for
  any date and time, in workers; hills outside the survey cast none; needs
  Safari 16.4+ for the worker path.

- **v51, Queenstown first -- LIVE 20 Oct 10:05** (built at dfacd0de as v51c; gate: +13,080 panels, 0 big drops, 1 borderline roof withheld -- #4720962, confidence on the 0.45 line in v50 too; face-area check: 0 buildings lost area; panels +0.6..+2.6% in 23 of 25 Wakatipu regions, clean share flat). Four layout
  changes, each judged with tools/layout_ab.py on 200 Arrowtown and 200 CBD
  roofs (half the worst on the health check, half random) and by
  side-by-side renders:
  - faces rebuilt as a clean tiling first (src/face_straighten.py
    as_coverage): faces overlapped by cm2 and left hairline gaps, so every
    tidy-up step silently stood aside -- the first A/B was identical to the
    decimal;
  - coplanar neighbours merged, seams moved to where their two planes cross
    (pitched seams more than 15 cm off: 68.5% -> 56.9%), remaining edges
    straightened (coverage_simplify tolerance is a VW area, not a distance);
  - an "obstruction" whose returns lie on this face's or a neighbour's plane
    is roof (src/obstruction_planarity.py): pitched faces more than 10%
    "obstructed" 24% -> 19% (Arrowtown), 18% -> 14% (CBD); panels +2.4% /
    +1.4%; clean roofs 34.5% -> 39.5% / 38.5%.
  The health check's "ragged face" rule barely moves and is the wrong
  measure: most faces it flags are flat roofs on L-shaped buildings, drawn
  right. Seam offset from the plane crossing is the honest measure of "lines
  not following the ridges"; it needs planes in the layout output to become
  a health check (not there yet). Wanaka, Hawea, Cromwell, Glenorchy and
  Auckland stay on v50 layouts until Josh has seen Queenstown.
  The first full build was NOT published; the gate and a face-area check
  caught three bugs the 400-roof samples never hit: (1) tidying split a face
  at a narrow neck and kept the bigger piece -- 12/16 Kent St lost its
  230 m2 wing, 7 Panorama Tce its middle; (2) "coplanar" was tested only
  along the shared edge, where gable halves always agree, so low-pitched
  commercial roofs merged their halves and the other half became one huge
  obstruction (4749386: 750 -> 454 panels); (3) seams moved between poorly
  fitted planes against the LiDAR (two plain houses withheld). Fixed in
  5b98e986, dfacd0de and the seam-evidence commits; rebuilt as v51c. Lesson:
  check face area per building between builds (~/ab/facearea_regions.py;
  belongs in the gate).

- **Glenorchy, Kinloch and the Rees Valley -- live in v43 (13 Oct).** No
  LiDAR reaches the head of the lake: synthetic-surface path like Cromwell,
  photos Otago 0.3m Rural 2017-2019 (layer 104460). Regions glenorchy (528
  outlines), kinloch (30), glenorchy_north (142, Rees Valley / Paradise road).
- **Photo-only roof reading -- measured 13 Oct, not adopted for houses.**
  tools/research/photo_only_roofs.py on 150 Queenstown houses with LiDAR:
  sunshine per m2 of roof, median error -- hip 2.5%, photo gable/hip form
  2.6%, SAM photo faces 3.9%, true layout at 20 degrees 2.0%. The photo form
  names the roof right more often (61/100 vs 35/100; 59 at 0.3 m), but
  whole-roof sunshine barely depends on it at 20 degrees. LiDAR pitch median
  20.1 degrees. Where it could still matter: which face takes the panels
  first (north share error 12 vs 13 points) -- worth retesting on panel
  counts, not sunshine, if the no-LiDAR towns grow.
- **Small no-LiDAR buildings now gabled (57a9eb49) -- live in v45 (13 Oct).**
  Gate: +10,090 panels (+0.6%), none zeroed or down 30%. Under 60 m2, 1 roof
  in 141 is a hip in the LiDAR; gables leave faces big enough for panels.
- **Cromwell and Bannockburn -- live in v42 (29 Sep).** No LiDAR
  reaches the Cromwell basin, so they build on a synthetic surface
  (src/synthesize_dsm.py): 8 m DEM ground, hip roofs at 20 degrees under
  400 m2, flat above; every building flagged `pitch_guessed` and labelled on
  the map. Next for this path: tell gables from hips with the roof-line model
  on the photo; pick the pitch per building type; add Pisa Moorings (~450
  outlines north of Cromwell) if wanted.

- **v41 live 27 Sep** (48d1991a): every 26 Sep fix plus full z16 panel
  tiles. Gate: +15.6% panels on the buildings live had, none zeroed.
- **Data hosting moved to Google Cloud (30 Sep).** The map reads
  gs://rewiring-solar-tiles/v<N>/ (public, CORS, year-long cache);
  deploy_from_vm.sh --push publishes there, keeps the newest three versions
  and commits only site-config.js. Open: the git history still holds 2.3 GB
  of old data (rewrite needs Josh and Cameron to agree, everyone re-clones);
  the VM cannot upload (storage read-only scope), so publishing still goes
  through the laptop.
- **Over-carving left in v41:** colour blobs on a clean white flat roof
  (#4725716, 32 -> 12) and a 19.5 m2 "height" obstruction only 0.15-0.23 m
  proud (#4731172). Both detectors, not geometry.

- **v40 deployed 26 Sep** past a gate that failed only on two small roofs
  that were misread before and still are: #4728022 (a 7 m2 triangle face on
  a large flat roof; 11 panels live) and #5372844 (two stray triangles; 6
  live). Both are face-reading failures, not obstruction ones.
- **The 13 BIG DROPs -- fixed in 44979ac7, in the v41 build.** The sunken
  detector had been dormant on clean faces until adbcc978 (24 Sep); once it
  ran everywhere, single low returns built jagged strips. Now majority cells,
  plane refresh, and roof-coloured lower strips as levels. Still carved, by
  design: real terraces (#4725197) and darker lower roofing (#4728664).
- **Fixed 26 Sep for v41, each with an off switch, measured on pilot:**
  staircase faces of narrow balcony floors dropped (#4740503: 550 -> 526,
  west terraces clear; `SOLAR_STAIRCASE_DROP`); unclipped faces exempt from
  the corridor test (42 Suburb St 9 -> 19; `SOLAR_CORRIDOR_WIDTH`);
  plant-deck rule may not drop most of a roof (#4734932 kept, #4728022
  0 -> 27); edge drops judged by thickness (22 Earl St's 25 red eave
  strips gone). Pilot, laptop: +579 panels, none zeroed or down 30%.
- **Still open from Josh's cases:** facade-top strips on #4740503's east
  block; 4 Kent St reads as a 14.6-degree gable in the LiDAR (Josh: flat);
  32 Frankton Rd layout order; 13 Plantation Rd (region not on the laptop).
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
- **LiDAR frame for markup and image steps -- measured, left off.** Bench
  on all 98 complete marked roofs (26 Sep, VM):

  | | area recall | area precision | small marks found |
  |---|---|---|---|
  | A: as shipped | 0.350 | 0.328 | 137/365 |
  | B: markup moved onto the LiDAR (`SOLAR_MARKUP_FRAME=lidar`) | 0.350 | 0.325 | 134/365 |
  | C: B + survey-year photo (`SOLAR_PIPELINE_PHOTO=reference`) | 0.350 | 0.335 | 111/365 |

  C is more precise but loses a fifth of the small objects: the vent rule's
  contrast threshold was tuned on pilot's photo, and the 2021 capture is
  older than the markup. Worth retrying with the contrast re-tuned per photo.

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
