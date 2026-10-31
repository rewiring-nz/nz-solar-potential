# Backlog

Completed first local slice of #45: loopback task coordinator with JSON
persistence, subprocess workers, default pipeline dispatch, explicit conflict
and interruption reporting, and cloud-free multi-process tests.
Remaining: common cloud/local task/configuration contracts, regional/final
assembly separation and migration of the existing GCS worker path.

Open work, in priority order. Finished work is in git history. Open items
are also GitHub Issues on the project board
(https://github.com/orgs/rewiring-nz/projects/5); pick up and assign work
there.

Completed documentation: [tooling and execution architecture](docs/developers/tooling-execution.md)
maps launchers, machine boundaries, stage differences and run lifetimes;
the linked architecture and operating guides distinguish current behaviour
from the proposed common pipeline interface.
The [documentation index](docs/index.md) now routes readers by persona;
setup, access administration, release procedures and execution comparisons
have primary guides rather than repeated walkthroughs.
Guides now live in matching persona subdirectories, with historical notes
under `docs/ai-agents/archive/`; references follow the moves. Documentation
maintenance includes removing obsolete code paths and redundant explanations.

Live: data version 67 (31 Oct; roof-line join on). 32,838 buildings, 35
regions, 1,334,824 panels.

Building: v68, launched 31 Oct 07:28 UTC on the VM (`--force`, the 35 served
regions, about 6 h). Carries outline fit, identical-roof sharing, the Beach St
roof fixes and the 247 approved demolished withholds. Then gate, render the
movers, publish.

## Ship

- **Roofs Josh flagged on 31 Oct** (all in `data/roof_cases.json`):
  - *Demolished buildings still drawn.* Built: the `demolished` stage
    (`src/demolished.py`) screens every outline against both photos and asks
    a vision referee about the 987 it keeps; "gone" or "replaced" withholds
    the roof with reason `demolished`. Not yet in a build. Before the next
    one:
    - All 987 answered ($4.60 in all). 247 would be withheld (194 gone,
      53 replaced), all reviewed on contact sheets and judged right
      (`demolished-review/`, outside the repo). Borderline but kept:
      4736289, 4739163, 4736316. 4728200 and 5370424 have a blank survey
      photo and were judged on today's photo alone. Ready for the next
      district build.
    - The deploy gate will list them under NEWLY WITHHELD; that drop is
      expected.
  - *Three "nothing is there" labels show a standing building* in both
    photos: 4718482, 4718539, 4744515 (in `config.DEMOLISHED_BUILDING_IDS`,
    so off the map entirely). Check them in the labelling tool.
  - *Identical roofs, different answers.* Fixed in code
    (`src/repeat_roofs.py`, `SOLAR_REPEAT_ROOFS`, on by default); waits for
    the next district build.
    - 6/60 Hallenstein St: the LiDAR shows six of the eight townhouses with
      an 8–10° strip along the north-east edge, 0.7 m higher there and
      falling south-west, so the size slider is right to drop it first. The
      two corner units are flat with no strip. The six had been read five
      ways; all six now share one reading (2° membrane + strip).
    - Panel counts still differ (33–47) because LINZ outlines for the one
      design range 143–186 m² and obstructions are detected per roof. Next:
      let a set agree on its outline and obstructions too.
    - District: 592 sets of identical roofs, 1,547 buildings. 204 sets keep
      their own readings because the shared one fit the LiDAR worse.
  - *Outline outside the roof edge.* Fixed in code
    (`src/outline_fit.py`, `SOLAR_OUTLINE_FIT`, on by default); waits for
    the next district build. 41 Ballarat
    St's outline sits 1.25 m south-east of its LiDAR roof; slid onto it, the
    hips meet at the photo's apex and no panel is over the yard. District:
    853 of 24,138 outlines slide (3.5%, median 0.9 m); 18% in
    town_west_fernhill, where the survey photo agrees with the slide. The
    3D view and the map outline follow the slid outline. Eaves (outline
    the right place, roof wider) are not changed.
  - *Commercial roofs on Beach St.* Fixed in code, not yet built; needs
    the next district build and Josh's look.
    - 48 Beach St `5370368`: the plant room (3.9 m up, 22 m2) is now an
      obstruction (`STRUCTURE_MIN_M`); 10 panels came off it. Still open:
      small colour blobs on the clear white roof (the small-object
      contrast path). Gating colour blobs on returns *above* the deck was
      tried and reverted: it also dropped real vent hoods on 5370326,
      whose LiDAR reads as flat as a shadow.
    - 93 Beach St `4725633`: scraps under 4 panels that belong to no
      array are dropped (`drop_scraps`), so they leave the economic size.
    - 24 Beach St `4735237`: the layout respected the markup; the map
      drew it with a borrowed lean 1.5 m short, so rows sat on the ridge
      skylights. A weak match is now measured on the roof alone before
      borrowing (`ROOF_LOOK`): 3.6 m, vents on vents. 61 of the pilot's
      169 borrowed leans change. Needs `register_imagery` re-run.
    - 44 Beach St `5370317`: the main slope is portrait now. The narrow
      second strip stays landscape (portrait does not fit 1.8 m); the
      strips may be mis-split (the map photo shows two light strips).
    - 40 commercial pilot roofs, A/B: panels 7,506 -> 7,295, all from the
      100%-only band (255 -> 57); ranks 1-80 unchanged (6,678 -> 6,677).
- **`tools/render_cases.py` cannot read the per-region build.** At v67,
  most case tiles render "0 facets, 0 panels" and four raise KeyError. Three
  cases show "regressed" from a broken 25 Sep check run that recorded zero
  for everything.
- **`data/build_snapshot_prev.json` on the VM is stale.** It covers 15,346
  buildings, so `src/render_top_movers.py` ranks the wrong movers. Compare
  against the live summaries, as `tools/predeploy_check.py` does.

- **Re-record the synthetic reference** on the VM: v66 packs panels at
  their true size (1.134 x 1.961 m), so the synthetic region's panel counts
  no longer match its reference.
- **The laptop deploy gate reads stale withheld reasons.** NEWLY WITHHELD
  takes `no_estimate_reason` from the laptop's own
  `data/regions/<r>/solar_potential.geojson` (September copies), so it lists
  hundreds of roofs the VM build does not withhold. Run
  `tools/predeploy_check.py` on the VM until the relay pulls those reasons.
- **Audit: still to do in the pipeline.**
  - Plane-intersection and reflex cuts drift after the first cut
    (`roof_partition.py:1066, 2134`; needs `_reanchor`).
  - Frame rules: a wing under 20 deg gets snapped anyway; `frame_group`
    reassigns faces; `register_frame` votes with faces that stay empty;
    `centre_panels` breaks the shared grid.
  - Coverage kWp uses plan area x kW/m2: low by cos(slope).
  - The region-centre horizon is still a floor under every building's beam
    (the lookup table is built with it).
- **Install cost falls at each size tier** (`economics.js:124`): 7.99 kW
  costs more than 8.0 kW. Needs a pricing call: marginal tiers raise every
  quote; interpolated rates keep the levels.
- **Misread faces may carry the wrong aspect.** v64 racks panels square to
  the walls, but the face's direction used for sun is unchanged. The same
  misread faces (low 6–13° faces read 21–46° across the walls) likely have
  the wrong aspect for yield and the sunny-side rule.
- **Add the face-area check to the deploy gate.** A building should not lose
  roof-face area between builds; today it is a separate run
  (`tools/face_area_check.py`).
- **Re-record the goldens.** Last recorded at v52; the installer-layout
  changes since then are not in them.

## Roof lines

- **Use the better reading.** On Josh's 131 drawn roofs the pipeline's pick
  scores line F 0.537 (48 roofs right); the best candidate it already drew
  scores 0.688 (77). Trusting the photo reading plus overlap-clip-only
  coherence (switches `SOLAR_SELECTED_TRUST`/`MIN_SCORE`,
  `SOLAR_FACET_COHERENCE`) scores 64 roofs / 0.601, but breaks messy
  commercial roofs (12 Stanley St, 42 Suburb St, 4734994, 5372596). No score
  or shape statistic separates good readings from bad. Next: the vision
  referee (`tools/vision_referee.py`) only where the photo and LiDAR readings
  disagree (a few hundred roofs, a few dollars).
- **Faces that don't meet in 3D.** `tools/layout_health.py` `seam_step`
  flags shared edges where the two faces' planes step over 0.4 m apart and
  the DSM shows one continuous roof. On the live Queenstown layouts: 6.8% of
  edges (20% of roofs) in the LiDAR frame, 14.5% (37%) at the faces the
  map draws, because `buildings3d.js` samples the DSM at faces moved by the
  photo lean (`img_dx`/`img_dy`) without taking it off. Next: sample at the
  unshifted position. Moving flagged edges to where the planes cross makes
  roof lines worse against the markup (line F 0.656 -> 0.593, 1 roof better,
  15 worse), and the step score does not separate good readings from bad
  (Spearman 0.06 vs line F); re-test with the point cloud and
  `SOLAR_MARKUP_FRAME=lidar` before using either.
- **Seam fixes behind switches, judged on the Mac (all off).**
  - `SOLAR_DANGLE_JOIN_M=1.0`: a drawn end within 1 m of another drawn line
    is joined to it; each part of the cut must fit its own plane (70% of
    returns within 0.15 m). On the 54 marked roofs with such an end, 9
    change: drawn lines found rise on 8, seam steps 12 -> 10, panels
    2,096 -> 2,123. 19 Camp St's triangle becomes its own 1.1 deg face. Turn on.
  - `?SOLAR_OBST_TOP_DSM=1` (3D view): over 6,254 boxes in three regions the
    box top's distance from the DSM falls from 0.80 to 0.49 m (area-weighted).
    Not yet checked in the browser. Turn on after a look in 3D.
  - `SOLAR_EMPTY_FACE_REFIT=1`: no panel change; 3 of 450 sampled roofs change
    before the 20 m2 floor, seam steps 10 -> 9. Low value; fine to turn on.
  - `SOLAR_CURVED_SPLIT=1`: fires on 19 Camp St only, of about 456 roofs; the
    cut is a straight line across the face that nobody drew. Leave off.
- **Composite roofs.** `src/composite_roof.py` (a house as its wings, joined
  with straight valleys) is off (`SOLAR_COMPOSITE_ROOFS`). On 139 Shotover
  houses it explains a median 51 points fewer returns than the current faces.
  Needs proper wing fitting first.
- **Still wrong:** 14 Ballarat St (flat roof cut by skylight strips);
  4725584 (keeps a 148-piece reading); facade-top strips on 4740503's east
  block; 4 Kent St reads as a 14.6° gable in the LiDAR (Josh: flat);
  32 Frankton Rd layout order; 4728022 and 5372844 (stray small faces).
- **Verify:** 13 Plantation Rd (4727237), Kingston counts, and the cases
  awaiting a verdict (`tools/cases.py check`).
- **Open-ended drawn lines:** nine marked roofs need patching for the
  line-extension rule (`tools/patch_labelled.py`).
- **arrowtown_hills** builds 50 buildings and estimates none.

## Obstructions

- **Photo obstruction model in LiDAR towns.** On held-out towns the trained
  model (`data/models/photo_obstruct_v1.pt`) scores object recall 0.68 /
  precision 0.36 against the colour detector's 0.30 / 0.30. It is only used
  where there is no LiDAR. Retrain with the benchmark roofs held out, try it
  as the photo path (or referee for colour blobs) in LiDAR towns, and judge
  on `tools/markup_benchmark.py`. The colour detector draws 2/3 of the false
  obstruction area.
- **Small marks missed.** 97 of 163 small marks are missed (54 have contrast
  under 5 in the photo) and 254 small detections touch no mark. Run
  `tools/obstruction_bench.py` on the VM for all 98 complete roofs.
- **Over-carving:** colour blobs on a clean white flat roof (4725716) and a
  19.5 m² "height" obstruction only 0.15–0.23 m proud (4731172).
- **Obstructions with too few returns to judge** (50% on the CBD sample)
  need a photo check.
- **Watch:** 4735290 places panels over dark rectangles (skylights or
  existing panels).
- **Hips and valleys.** An overreach separated from its true face by roof
  with no returns cannot be judged. Colour-only blobs sitting on the plane at
  a fold are shading, but a flush skylight looks the same to the LiDAR: any
  fix must use the blob's shape or position, not its height. Check real
  roofs with `tools/explain_obstructions.py <id>`.

## Imagery

- **Survey-year photo for markup and image steps.** Measured on 98 roofs:
  more precise (0.335 vs 0.328) but finds a fifth fewer small objects. Retry
  with the vent contrast threshold tuned per photo
  (`SOLAR_MARKUP_FRAME=lidar`, `SOLAR_PIPELINE_PHOTO=reference`).
- **One shift per building is not enough on big roofs.** 32 Frankton Rd
  (4,000 m²) leans unevenly.
- **Eaves.** 6.6–18.8% of roof-height returns fall outside the LINZ
  footprint, by up to 2 m, so roof area is understated. Needs per-edge
  treatment judged against the drawn outlines.

## Model

- **Calibration point.** Re-check the SolarView cloud factor against the
  Queenstown Aero normal.
- **System derate** looks 4–5 points optimistic against PVGIS
  (`src/validate_against_pvgis.py`). A change is a `--yield-only` rebuild.
- **Station mapping outside the Wakatipu.** Low-solar days are mapped onto
  the Queenstown Aero station record only for Queenstown, Glenorchy and
  Kingston. Wanaka and Cromwell are not mapped.
- **NREL Himawari sunlight (optional).** The NSRDB Himawari files are public
  on AWS (2015–2020); range reads of the Queenstown pixels may avoid needing
  an API key. The only route to suburb-level cloud.
- **No-LiDAR towns** (Cromwell, Glenorchy): tell gables from hips with the
  roof-line model on the photo; pick pitch per building type; add Pisa
  Moorings (~450 outlines) if wanted. If these towns grow, retest photo-only
  roof reading on panel counts rather than sunshine.

## Map

- **3D view.** Solids are drawn over the map, so a hill or tree between the
  camera and a building does not hide it. Cars in the DSM stand as blobs.
  The sun simulation shades the ground, not the building meshes.

## Scale

- **Dress rehearsal on one city** (docs/ai-agents/archive/national-rehearsal.md). The fleet
  (`tools/fleet.sh`, `src/gcs_queue.py`) has never run as a fleet. LINZ and
  OpenTopography fetch rates are unknown (`tools/measure_fetch_rate.py`). The
  real region-pack ratio is unmeasured (`src/pack_region.py` prints it).
- **Bucket serving** is untested at national scale.
- **Git history** still holds 2.3 GB of old data. A rewrite needs Josh and
  Cameron to agree, and everyone re-clones.
- **The VM cannot upload** (read-only storage scope), so publishing runs
  from a laptop or the "Build and publish the map" GitHub Action.

## Product

- Email updates (Buttondown) wait until the leaderboard has real change data.

## Standing rules

- Hand-drawn markup always wins over fitted geometry.
- Judge changes by measurement against the markup or by the render, never by
  totals alone.
- Nothing at district scale is built on a laptop. The VM builds; the laptop
  only relays through `tools/deploy_from_vm.sh`.
