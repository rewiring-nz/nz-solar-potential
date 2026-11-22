# Backlog

Completed pipeline queue progress UX: shared queue/detail progress and timing,
current stage and area, readable locality titles, pending-job positions and
wait reasons, and the last three outcomes per lane for ten minutes. Unknown
cloud/preparation progress is indeterminate; only success reaches 100%, and
failed or cancelled work is not counted as complete. Overdue estimates and
disconnected updates are explicit, with last-known cards retained. Backend,
HTTP/SSE and JavaScript presentation tests cover the progress contract.

Completed local-queue imagery-option recovery: tasks persist and validate
`include_imagery`, forward both checkbox states to the pipeline worker, and
default older task records to including imagery. Real worker-process tests
cover both choices and validation rejects non-boolean values.

Completed web-job environment recovery: source-layer overrides merge into
the panel's environment instead of replacing it, retaining `PATH` and other
inherited settings for GDAL and subsequent stages. Regression tests exercise
default, empty and partial overrides and run source-choice pinning through
the job-test entry point.

Completed local web-build reliability follow-up: raster-export creation allows
120 seconds for LINZ and reports timeouts without automatically duplicating
export jobs. Layout workers close Rasterio handles before interpreter teardown.
Regression tests cover both paths; a Sunshine Bay grid selection is verified
through the HTTP estimate/job endpoints and final map-contract validation,
without a separate preview server.

Completed #48: roof facets are filled by facing direction, with pitch encoded
as saturation (full at 45°), and a distinct colour for missing/invalid geometry
data. The shared legend, catalogue styling and rooftop map use the same
expression; panels and obstructions stay above facets and 3D hides both facet
fills and outlines. No dataset rebuild or output-contract change is needed.

Completed #47: shared grouped map-layer legend in the pipeline panel and
separate data explorer, with independent overlay toggles, feature-attribute
queries, aerial/OpenStreetMap selection, cached-source extent overlays and
available intermediate layers. Analysis stacking is panels above building
outlines above the heat map. The simple map is `rooftop-solar.html`, with
`preview.html` retained as a compatibility redirect. Local cache layers are
isolated from cloud targets. Documentation renders from `docs/index.md`.
Remaining: remote source catalogues, valid-data masks rather than file
extents, and full raster viewing for intermediate datasets.
Follow-up baseline (superseded): the simple rooftop map linked to the explorer,
local pipeline panel and documentation through a collapsed navigation menu.
The map-user guide distinguished catalogue layers from the simple map's
analysis modes.

Completed Build data UX consolidation: renamed the pipeline page, integrated
area status with area selection, removed the separate explorer destination,
and replaced Regions/New build/Queue/Jobs navigation with expandable build,
queue and newest-first job-history sections. Shared map-layer controls remain
available in the workspace; the former explorer URL redirects to Build data.

Completed: national [area catalogue](docs/data-maintainers/areas.md), grouped
by council with LINZ ids and pipeline region ids. `tools/list_areas.py`
refreshes the snapshot from LINZ without fetching buildings or starting
builds. Candidates exclude lakes and inland bays; building presence and
survey readiness still need checking before scheduling.

Completed first local slice of #45: loopback task coordinator with JSON
persistence, subprocess workers, default pipeline dispatch, explicit conflict
and interruption reporting, and cloud-free multi-process tests.
Remaining: common cloud/local task/configuration contracts, regional/final
assembly separation and migration of the existing GCS worker path.

Completed second slice of #45: one shared per-region stage plan
(`src/build_plan.py`) read by the local, district and fleet engines, with a
drift test (`tests/test_build_plan.py`). Every engine now runs outline fit,
identical roofs, imagery registration before layout, demolition screening,
addresses and emit. The paid demolition referee is off unless approved by a
start-of-run yes/no question (or `--paid-referee` / workflow input).
Remaining: shared input preparation (fetch, prediction, dedupe, pack
restore), output-equivalence tests between engines, a VM cost confirmation
on `fleet.sh up`, and re-recording `tests/synthetic/reference.json` on Linux
(stale on HEAD since the 20 Oct record: 606 → 459 panels).

Completed local slice of #12: web pipeline panel (`tools/pipeline.py`,
`pipeline.html`, [guide](docs/data-maintainers/web-pipeline.md)). Loopback,
token-protected; picks LINZ suburbs/localities on a map, estimates buildings,
download, time and disk, offers source checkboxes that recalculate the
estimate, builds regions in sequence with live progress and cancel, then
combines all built regions. `tools/pipeline.py` writes `status.json` and
accepts `--no-preview-server`.
Cloud slice of #12: the panel's Local / Developer / Published toggle lists
local areas, every developer's previews and the live data version; starts
developer previews and gated publishes on the shared VM after an NZD cost
estimate acknowledged by checkbox; and shows selected and queued work per
lane (local, cloud). Cloud mode of `tools/pipeline.py` accepts several
space-separated regions. Remaining: a hosted backend with team auth (the
panel still runs on each developer's computer with their own `gcloud`
credentials).
Completed source-discovery follow-up to #12: covering LINZ elevation and
imagery layers are discovered from metadata and cached locally rather than
listed as regional surveys in `config.py`. Estimates pin the selected layers
for the job; where a raw point-cloud bulk source cannot be verified, the
estimate warns and the build uses the DSM.
Completed follow-up to #12: optional locality sub-area selection uses a
configurable 2×2–10×10 grid clipped to LINZ locality boundaries, with
deterministic whole-building ownership and nested selection work areas. It
uses the existing LINZ boundary and credentials; no additional dataset or
API key is required.

Completed: unattended quickstart and pipeline runs. A failed deploy gate on a
developer preview or build-only `tools/remote_build.sh` run now prints
`GATE WARNING` and continues (`GATE_RESULT=pass|flagged|not-run` for
callers); `--interactive` stops and asks instead. `tools/pipeline.py --yes`
accepts the printed, logged cloud cost estimate without a prompt and never
approves the paid referee or a gcloud install. Production `--publish` still
needs a passing gate or `--reviewed`.

Completed: LINZ locality regions. A region can be named by its LINZ NZ
Suburbs and Localities place, `<council>/<town>/<suburb>`, with nested
directories under `data/regions/` and `data/out/`; `tools/find_area.py` finds
ids, `src/localities.py` fetches attribute-matched outlines at preflight, and
[Choosing an area](docs/data-maintainers/choosing-an-area.md) guides
maintainers. Legacy `config.REGIONS` bbox regions still build and are not
migrated. Remaining: a full district build of a locality region on the build
VM (fleet queue, pack/restore and publish paths are untested with nested ids).

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

Live: data version 74 (published 9 Nov). 35 regions, 1,256,226 panels,
720.4 GWh, with the size-dependent edge gap on (`SOLAR_EDGE_GAP=1`,
`src/panel_fitting.py`).

v69 (2 Nov): 32,838 buildings, 35 regions, 1,300,366
panels (+1,123), 777.0 GWh. Carries the v68 review fixes below (weak shared
readings compete, copies keep their roof, a tiny sunny side does not bare
the roof). Built in 10 h 40 min. Gate run on the VM: 0 zeroed, 0 newly
withheld, 2 big drops (rendered, below); the laptop gate's 308 NEWLY
WITHHELD are its stale September reasons.

v68 (1 Nov): outline fit, identical-roof sharing, the Beach St roof fixes
and 216 demolished buildings withheld.

v68 review (gate, `tools/face_area_check.py` against the saved v67 layouts,
`tools/render_built.py` renders): flagged roofs improved or unchanged; 24
roofs still on the map lost face area, and about 10 big drops were rendered.
- Fixed for v69, each A/B'd and rendered on the photo. v69 district build
  (35 served regions, `--force`) started 1 Nov 04:27 UTC; v68 layouts saved
  on the VM in `~/v68_layouts` for the before/after review:
  - `SOLAR_REPEAT_KEEP_ROOF`: a copy given a neighbour's reading that leaves
    part of the roof unread (wanaka_town 4736995 15 -> 42, 5375073 14 -> 49).
  - `SOLAR_REPEAT_CONTEST`: a weak shared reading (under 0.60) competes with
    the LiDAR path (4749739 189 -> 248, 4749406 127 -> 165, 5370646 81 -> 140).
  - `SUN_SIDE_MIN_SHARE` / `SUN_SIDE_MIN_PANELS`: a tiny sunny face does not
    bare the roof (4734459 6 -> 32).
  - Lake Hayes 4731151 recovers with the two identical-roof rules (39 -> 77).
  - 600-roof sample, v68 code vs v69: 12 roofs change, panels +0.6%.
- v69 review (`tools/render_built.py --before ~/v68_layouts`): the review
  roofs render as the A/B did. `SOLAR_REPEAT_CONTEST` moves 184 of the 530
  roofs on the map with a weak agreed reading (under 0.60): 118 up (+1,065
  panels), 66 down (-354). The 12 biggest drops all render worse, and in one
  way: on a long plain shed or flat roof (wanaka_town 5375921, 5375984,
  5375975, 5375972, 5376055/5375061; frankton_arm 4739603 sawtooth, 72 -> 42)
  the LiDAR path wins with an extra split along the roof, leaving a bare or
  narrow strip the agreed reading did not have. Each goes back with
  `SOLAR_REPEAT_CONTEST=0` (4739603 42 -> 72, 5376055 24 -> 52).
  Tried and rejected (2 Nov): the LiDAR path replaces an agreed reading only
  with fewer faces. Every drop above had the LiDAR path at as many faces or
  more, every rendered gainer at fewer -- but across all 543 weak agreed
  readings (A/B on the VM, `~/ab70`) 73 change, 34 up and 33 down, panels
  19,320 -> 19,376, and the renders of the down side are worse on 10 of 12
  (4729804 63 -> 38 and 5373571 53 -> 30 lose sunny faces; the Wanaka gables
  4743318 and 4743327 26 -> 13 go half bare behind a jagged line). Gainers
  and drops have the same face counts and overlapping explained scores, so
  neither separates them. Several Wanaka drops look like the gables that
  gained; which reading is true needs markup. The blind check below
  settled most of these for v69.
- Blind check (2 Nov): none of the 64 roofs judged in the v69 review and the
  fewer-faces A/B has markup, so every better/worse above is an agent's
  reading of the photo. 48 of them are on a blind side-by-side page for the
  owner (https://claude.ai/artifact/AiBqdiPoePuuL9qrEJRBJq; verdicts in its
  `verdicts` collection; answer key and the agent's own calls, recorded
  first, in `data/blind_check/answer_key_2nov.json`; owner verdicts in
  `data/blind_check/owner_verdicts_2nov.json`).
  Scored (2 Nov): v68 vs v69 on 36 roofs, owner picks v69 on 22, v68 on 9,
  same on 5. The agent agreed on 20 of the 31 decided, and called v68 on 14
  where the owner backed it on 5: render review leans against change. Three
  of the four plain-shed drops the agent flagged (4739603, 5376055,
  5375921) are better at v69 in the owner's eye; 5375984 is better at v68.
  v69's contest stays. The fewer-faces rule loses 10 of 12 (owner backs it
  only on 4724517 and 5371948, neither of which the agent picked): the
  rejection holds. Roofs where v68 was better, to draw in the roof set:
  4725214, 4736995, 4735193, 4749406, 5375061, 5370646, 5375984, 5375975,
  4731151. Owner notes repeat two faults: panels across a ridge (5375073)
  and stray landscape rows at a face edge (5374556, 5373571, 4731151).
- Tried and rejected (2 Nov): drop a single landscape row on a pitched
  face (A/B on 984 roofs, VM `~/ab71`: 492 change, 37,484 -> 35,111
  panels, 103 roofs to zero). Owner's blind review of 40 movers
  (https://claude.ai/artifact/ExStV2iwLSfDEEzPtvfCwy; answers in
  `data/blind_check/owner_v70_sideways.json`): v69 picked 24, v70 3, same
  10; mean score 5.3 vs 3.2. Roofs that went to zero: v69 on 11 of 12
  (6.1 vs 1.2). Roofs that kept panels: v69 13, v70 3, same 10. Only two
  of the three roofs that prompted it improved. A sideways row on a strip
  is better than an empty strip; the "weird" rows in the 2 Nov notes are
  a symptom of wrong roof lines, not of racking ("racking" was tagged on
  2 of 40). The agent's prior agreed on 14 of 27.
- Fault tags from that review, both versions (40 roofs): roof lines wrong
  16, empty space unused 8 (v69), outline wrong 7, panels across a ridge
  7, obstructions 2, off the roof 1, racking 2. Roof lines are the main
  fault; ridge-tagged roofs: 4741990, 4731151, 4746845, 4725292, 4720823,
  5373571, 1560393. 4746515: lines trace a tree's shadow instead of the
  roof edge behind it. 4719462: a diagonal wing's panels are not square
  to its eave.
- Panels across a ridge, 5375073 (wanaka_town): about 70% of the returns
  inside its face are at ground height, so the house postdates the LiDAR.
  Its faces come from the photo (a repeat reading of 5375075 via SAM), and
  with no heights the roof reads as one 1.5 deg face across the ridge.
  How many roofs postdate the LiDAR, and what a photo-only roof should
  get, is open; the review's ridge tag will say how often it shows.
- Kept as is: 5370587 (Arrowtown terrace, 132 -> 73) loses one 2-3 panel
  array per unit to the scrap rule. Relaxing the rule to keep them brought
  the same scattered pairs back on complex houses across the sample, which
  is what the rule removes.
- Still open: 4749146 (town_west_fernhill, 90 -> 51): its slid outline
  breaks its faces into jagged pieces, with or without its saved reading.
  Readings in `data/selected_faces` predate the outline slide (20 Sep) and
  `tools/predict_faces.py` does not read fitted outlines; slid buildings
  (853) may need their readings recomputed.
- Completed #52: fixed `tests/test_xref.py` to resolve nested package modules
  and scope-local module aliases, with regression cases for shadowing and
  genuinely missing imports. CI now runs the deterministic pipeline-web and
  map-layer catalogue tests, accumulates Python and JavaScript failures, and
  runs the JavaScript checks independently of Python failures.
- Partial on the flagged Beach St roofs: 48 Beach St still has panels on the
  dark plant box's east half; 44 Beach St's second strip stays landscape.
- `tests/test_local_queue.py` interrupt test fails on the Mac ("Operation
  not permitted" signalling the worker); passes elsewhere.

## Ship

- **Finding bad layouts automatically: not solved.** `tools/layout_faults.py`
  measures each served roof against LINZ's open LiDAR (panels off their
  array's plane, over open air, faces that misfit the surface) and for
  implausible face shapes. Against the owner's blind scores on the 40 random
  v72 roofs, the LiDAR measures predict nothing useful (the build fits to
  the same LiDAR; the faults seen are against the photo). Face shape looked
  promising on those 40 but failed on the 25 frame-fix roofs.
  A vision referee (`tools/layout_referee.py`, Opus 5.5 at low effort,
  about $0.006 a render) tracks the owner better and held up on the
  second set: rank correlation 0.47 and AUC 0.71 on the 40, AUC 0.65 on
  the 50 frame-fix drawings. It is harsher than the owner and over-tags
  outline and missed. Next: show it the plain photo beside the drawing,
  try medium effort, then decide whether to screen the district with it.

- **Line detector retrained on every town (10 Nov).** The build's
  detectors (`roof_lines_v5`/`v6`, Sep) learned from Queenstown-central roofs
  only: the other towns' photos are not on the Mac. Exported on the VM from
  all 164 usable drawn roofs, the 14 drawn held-back roofs pinned as
  validation (`--val-ids`), trained on the Mac. Mean F1 (ridge, valley,
  cliff) on those 14: v5 0.090, v6 0.071, v8 0.143; new model on 149 roofs
  0.252 (ridge 0.46, valley 0.24, cliff 0.06, hip 0.26),
  `data/models/roof_lines_v9_eval.pt`. Same recipe on subsets: the 64
  Queenstown-central roofs alone 0.088; 37 random roofs 0.20/0.14; 75
  random 0.21/0.19. Variety of towns is what carried over; more random
  drawings still help, more gently. Not yet in the build: v5 drives the
  calibrated line extractor and v6 the evidence map, so v9 needs the blind
  benchmark (predict_faces on the VM, then markup_benchmark) before it
  replaces either. Cliffs stay near zero: steps are a LiDAR question.
  Blind benchmark (11 Nov, 179 drawn roofs, VM): v9 as the evidence map
  only ties v6 (line_f 0.573 both, 79 lines right, face IoU 0.614 vs
  0.613; 2 better, 2 worse); v9 for both roles is worse (line_f 0.561, 74
  right, face IoU 0.617; 16 better, 30 worse, change -0.012 [-0.029,
  +0.004]). Pixel F1 on held-back roofs did not carry over to faces: the
  build stays on v5/v6. The faces' errors are not in the line detector.

- **The roof set: get 100 roofs right, then scale.** `data/roof_set.json`
  (`tools/make_roof_set.py`): 100 development roofs (35 flagged, 9 from the
  v68 render review, 25 new of the failing kinds, 31 already drawn) and 30
  held back at random from the whole map. Marking page:
  https://claude.ai/artifact/RroCQEBj9kA7BntDryKt4V (saves each roof online
  as it is drawn; B shows the build's faces as a reference). 91 roofs need
  drawing, development roofs first. Next:
  - ingest the drawn roofs into `data/roof_labels.json`: done 10 Nov for
    the 53 drawn on 4-5 Nov (197 roofs held; 14 of the held-back 30 now
    drawn and usable). Faces come from the page's own facesFor, run on
    the 130-roof bundle's outlines;
  - give `tools/markup_benchmark.py` a `--set dev|holdout` option and add
    panel checks (installer rules: margins, no panel across a line, whole
    groups, orientation);
  - baseline the 100 on the VM, then fix one general rule at a time;
  - score the 30 once, at the end, beside the 600-roof sample.
  The detector models (3-18 Sep) trained on 72 of the old drawn roofs, so
  only the held-back 30 are a fair final test. Blind runs now slide drawn
  outlines like any other (`outline_fit._marked` honours `SOLAR_HIDE_LABELS`).

- **Roofs Josh flagged on 31 Oct** (all in `data/roof_cases.json`):
  - *Demolished buildings still drawn.* Built: the `demolished` stage
    (`src/demolished.py`) screens every outline against both photos and asks
    a vision referee about the 987 it keeps; "gone" or "replaced" withholds
    the roof with reason `demolished`. In every build since v68 (v68
    withheld 216):
    - All 987 answered ($4.60 in all). 247 would be withheld (194 gone,
      53 replaced), all reviewed on contact sheets and judged right
      (`demolished-review/`, outside the repo). Borderline but kept:
      4736289, 4739163, 4736316. 4728200 and 5370424 have a blank survey
      photo and were judged on today's photo alone.
    - The deploy gate will list them under NEWLY WITHHELD; that drop is
      expected.
  - *Three "nothing is there" labels show a standing building* in both
    photos: 4718482, 4718539, 4744515 (in `config.DEMOLISHED_BUILDING_IDS`,
    so off the map entirely). Check them in the labelling tool.
  - *Identical roofs, different answers.* Fixed in code
    (`src/repeat_roofs.py`, `SOLAR_REPEAT_ROOFS`, on by default); in every
    build since v68.
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
    (`src/outline_fit.py`, `SOLAR_OUTLINE_FIT`, on by default); in every
    build since v68. 41 Ballarat
    St's outline sits 1.25 m south-east of its LiDAR roof; slid onto it, the
    hips meet at the photo's apex and no panel is over the yard. District:
    853 of 24,138 outlines slide (3.5%, median 0.9 m); 18% in
    town_west_fernhill, where the survey photo agrees with the slide. The
    3D view and the map outline follow the slid outline. Eaves (outline
    the right place, roof wider) are not changed.
  - *Commercial roofs on Beach St.* Fixed in code and in every build
    since v68; needs Josh's look.
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
- **`tools/research/render_cases.py` cannot read the per-region build**
  (it rebuilds each roof and gets 0 facets). Use `tools/render_built.py`,
  which draws the built layouts before and after (`--cases`, `--movers N`,
  `--ids`, `--before <copy of data/regions>`). `cases.py check --render`
  still calls the old path.
  `cases.py check` (no render) also fails on the VM after v72: every case
  roof gives `KeyError(<building id>)`, so it looks the roof up in a table
  the per-region build no longer fills. The deploy gate's case list
  (36 tracked, 7 changed in v72) still works and is what to read meanwhile.

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

**23 Nov, Sunrise Lane street rebuilt with the ViT-B all-roofs reader (`J_all.pt`, street L11 against L10).**
- None of the 19 street roofs is in the training sets. Sheets in
  `data/preview/cases/street_L11/`. Not judged by the owner yet; the agent's
  reading (not a measurement): hips and the missing faces now found on
  5371242, 4729434, 4729334 and 4729764; 4729286 gains lines but they
  wobble; 4729767 and 4729284 still wrong. Lines from the regions are not
  straight: straightening is the next visible loss.
- Folds 1-4 of the ViT-B reader are training for the 168-roof score.

**22 Nov (late night), ViT-B/14 reader ahead on fold 0; what the reader misses is valleys and drop edges.**
- Fold 0 (35 held-out drawn roofs), ViT-B (`J_fold0.pt`, batch 4, 16 epochs,
  no vision-only start) against ViT-S (run I): drawn edge given, face IoU
  0.795 against 0.759, faces right 21 against 15, line F 0.821 against 0.807;
  own edge 0.741 against 0.709, faces right 14 and 14. Validation line F1
  0.832 against 0.786 (ridge 0.80/0.74, valley 0.51/0.41, drop edge
  0.51/0.43, outline 0.73/0.71). One fold: the other four are to train.
- Missed drawn faces (run I, 1354 drawn, 637 found at IoU 0.6): of 548
  missed faces of 5 m2 or more, 356 are merged into a drawn neighbour, 210
  of those with a line map under 0.3 on the missing edge (the reader does
  not see the line). Drawn line pixels the reader sees (0.5 or more within
  2 px): ridge 81%, valley 68%, drop edge 59%.
- Line completion (`SOLAR_LINE_COMPLETE`, `line_graph.complete_lines`):
  74 -> 76 roofs right of 167, 7 better and 3 worse at the best setting.
  Off. Loose line ends are not the main loss.
- Sharp LiDAR channels (`SOLAR_LINES_SHARP`: unsmoothed height and the
  height step within 0.6 m): samples re-cut on the VM
  (`data/line_set/typed_sharp`, 9 channels); fold 0 on ViT-B
  (`K_fold0.pt`): no gain. Drawn edge given, face IoU 0.782 against 0.795
  and faces right 18 against 21; own edge 0.718 against 0.741, 14 and 14;
  validation line F1 0.820 against 0.832 (drop edge 0.54 against 0.51).
  Off. The ViT-B reader on the 7-channel set is the one trained on: an
  all-roofs model (`J_all.pt`), then folds 1-4.
- `data/line_set/lines_set/points` holds 4322 LiDAR plane-fit readings never
  used in the typed sets: a ready self-training set to test.

**22 Nov (late), five quick tests on the held-out drawn roofs (run I maps, drawn edge supplied).**
Default: face IoU 0.741, faces right 73-74 of 166-167. Scripts in the session
scratchpad (oracle.py, judge.py, repair.py), not in git.
- Candidates: 27 per roof (runs G, I, their mean; merge barrier 0.2/0.35/0.5;
  basin sigma 1/2/3.5). No single setting beats the default. Best per roof
  (a perfect judge): 0.789, 96 right. Houses 59 -> 71, larger 15 -> 25.
- Two readers averaged (G and I maps): 0.737, 74. No gain.
- LiDAR judge picking among the 27 (plane fit of the returns in each face,
  returns slid up to 1.5 m): 0.750, 79 right; better on 48 roofs, worse on
  25. About a quarter of what picking could give. The per-face penalty made
  no difference.
- LiDAR joining faces one plane fits (joint fit worse by under 0.015 m RMS),
  from the default or an over-split reading: 0.742, 76. It joins too much
  (863-1031 faces against 1347 drawn; 0.03 m and looser score worse).
- L10 street (`I_all.pt`): not visibly better than L9 on 13 and 5 Sunrise
  Lane; not sent.
- Where houses go wrong (4-11 drawn faces, IoU under 0.8, sheet by eye): the
  main ridges are read sharply; faces are lost where a line fades before it
  reaches the edge or another line, and the watershed then closes the face
  with a wobbly or slanted boundary. Houses by drawn faces: 2-3 faces 0.87,
  4-6 0.75, 7-11 0.72, 12+ 0.65.
- ViT-B/14 backbone (`SOLAR_LINES_ARCH=vitb14`): batch 8 needs 13.6 GB on
  MPS and stalls a 19 GB Mac; batch 4 runs at 0.87 s a step. Fold 0 training
  (`J_fold0.pt`, no vision-only start).

**22 Nov (night), outline pass complete; run I level with run G; the reader is the limit.**
- The owner checked the outline of all 150 earlier drawn roofs (91 changed,
  59 confirmed); lines reconciled (`tools/reconcile_outline_pass.py`; 42
  line ends on 27 roofs more than 1.5 m short were left). 178 of 211 drawn
  roofs carry a checked outline; all 168 in the training set teach the edge
  (`data/line_set/typed_set9`, samples re-cut on the VM).
- Fair baseline: run G read on the re-cut samples and scored against the
  corrected markup (`heldout_G9`): drawn edge given, face IoU 0.725, faces
  right 69, lines right 124; own edge 0.664, 54, 118; roof map against the
  taught roof 0.875 (0.9 or better on 98). Against the old markup the same
  reader scored 0.700 / 66 / 117: part of the counted error was the markup.
- Run I (`I_fold*.pt`, all 168 teaching the edge; `heldout_I`): drawn edge
  0.737, 70, lines right 132; own edge 0.666, 54, 125; roof map 0.867 (97).
  Lines a little better, faces and edge level. Teaching the edge on 168
  roofs instead of 35 did not move the edge; 11 more roofs of lines moved
  the lines a little. More markup of this kind is not what limits the reader.
- Sample framing against the build's reading, 167 roofs, G folds on the VM
  (ablation script kept in the session scratchpad, not in git): framed on
  the drawn roof, face IoU 0.727 / own edge 0.677 / edge IoU 0.90; framed
  on the build's edge 0.727 / 0.662 / 0.873; re-framed on the reader's edge
  0.729 / 0.662 / 0.869. The build's reading is not broken; re-framing is
  now off (`SOLAR_LINE_READER_REFRAME`). Build edge against drawn: 0.856.
- Larger roofs read in tiles at 0.07 or 0.1 m/px with the same reader: no
  gain (85 roofs 0.648 -> 0.653; 38 roofs 0.605 -> 0.585). Not trained on
  part-roofs, so untested rather than ruled out.
- The worst roof maps are flat commercial roofs with plant, car-park decks
  and multi-building blocks in the town centre, not houses (houses: roof
  map 0.89, faces right on about half).
- Next: a stronger reader (larger backbone is a download and needs the
  owner's OK); `I_all.pt` (all drawn roofs) for the street.

**22 Nov (evening), run G scored: the 11 new hard roofs help a little.**
- Run G (`data/line_set/runs/G_fold*.pt`, `typed_set5`, fixed folds): each
  roof read by the fold model that never saw it, faces by
  `tools/experiments/face_score.py` on `data/line_set/heldout_G`.
- The 157 earlier roofs, against run D (`heldout_D8`): drawn edge supplied,
  face IoU 0.694 (0.680), lines right 106 (103), near perfect 59 (56); the
  reader's own edge, 0.630 (0.620), 102 (103), 52 (48). A small gain,
  within what a retrain alone can move.
- The 11 new roofs, held out, against the reader trained without any of
  them: drawn edge 0.778 (0.743), faces right 7; own edge 0.734 (0.688),
  faces right 6. Lines right on all 11.
- Larger roofs remain the weak group (56 roofs: face IoU 0.618, faces right
  12). Still unexplained: the same 11 roofs read in the wild scored 0.54.
- Run H (17 corrected outlines, 35 roofs teaching the edge) follows.

**22 Nov (later), 11 hard roofs drawn; the outline was not being taught.**
- The owner drew 11 of the 20 hard roofs (3 more flagged not a building, 6
  not done: very complex roofs). In `data/roof_labels.json`; samples in
  `data/line_set/typed_set5` (168 drawn roofs), fixed folds in
  `data/line_set/folds.json`, the new ones listed in `new11.json`.
- Before (the reader that never saw them, framed on the build's edge, as
  the map would read them): outline IoU 0.77, face IoU 0.54, faces right on
  1 of 11, outline 0.9 or better on 2.
- The same reader given the picture centred on the drawn roof (the training
  framing, drawn edge supplied) scores face IoU 0.74 and lines right on 9
  of 11: much of the loss in the wild is not the lines. Reading again in a
  picture framed on the reader's own edge (`line_reader.REFRAME`) recovers
  little of it (face IoU 0.554 to 0.578, outline 0.753 to 0.774 on the 11,
  VM): the rest is unexplained and is the next thing to find.
- The owner, 22 Nov: on the roofs drawn earlier the outline was generally
  left as surveyed. So 144 of the 168 drawn roofs were teaching the surveyed
  outline (balconies and decks included) as the roof's edge. `line_targets`
  now marks the edge and roof maps as known only where the outline was drawn
  or corrected (24 roofs); the 0.86 edge overlap measured on the 157 was
  partly agreement with the survey, not with the roof.
- A no-panel face is not roof in the targets (67 faces on 37 roofs).
- Outline pass (`build_label_bundle.py --outline-pass`, page in
  `data/preview/outline_pass/`, 150 earlier roofs, local only: 33 MB): the
  owner checked 17, changed the outline on 14. Earlier drop edges were drawn
  where the roof really ends (in front of a balcony). Lines and faces are
  reconciled to the new outline by `tools/reconcile_outline_pass.py`
  (lines along the new outline dropped, ends carried to it up to 1.5 m,
  faces rebuilt; 4 line ends over 1.5 m short were left). Set
  `data/line_set/typed_set7`: 35 drawn roofs now teach the edge. Run H
  (`runs/H_fold*.pt`) follows run G on it. 133 roofs of the pass remain.
- Running: five folds on typed_set5 (`runs/G_fold*.pt`), then held-out maps
  for all 168 and the face score, old 157 against run D and the new 11
  against the before.
- Marking page: a second LINZ flight of each roof on V (`reference_imagery`),
  for looking only; the main photo is the map's (`imagery` equals the
  display tiles, checked by eye on two roofs).

**22 Nov, like-for-like benchmark; the reader's own roof edge; owner's street verdicts.**
- Build benchmark (179 drawn roofs, VM, each roof read by the fold model
  that never saw it, the build's outline, `~/ab/bench_L7`): face IoU 0.629,
  line F 0.65, lines right 96, faces right 34. The build before it: 0.627,
  92, 43. The typed reader with region faces ties the old build; it is not
  a step up on this score.
- Where the loss is: the faces step given the DRAWN maps scores face IoU
  0.929, 137 of 157 faces right. Given the reader's maps, 0.68. The reader's
  maps are the limit, not the conversion. Basins-then-merge
  (`line_graph._basins`), 8-view reading (`line_reader.maps`), and every
  threshold tried move the held-out score by under 0.01.
- Roof edge: the reader's roof map, carried onto its edge map and
  straightened (`line_graph.roof_outline`, `line_reader.reader_edge`),
  overlaps the drawn edge 0.86 on the 157 held-out roofs with no slide
  (0.9 or better on 61). It leaves out balconies and decks the build's edge
  takes in. End to end with it (`face_score.py ... regions_own`): face IoU
  0.620, lines right 103.
- Street L8 (reader's edge), owner's verdicts (logged per roof in
  `data/roof_cases.json`): better on 15, 7, 6 (#4729573), 4 Sunrise and both
  sheds (3 Sunrise #4729554 "seems perfect"); worse on 84 Panorama,
  2 Sunrise (obstruction too big, line not straight), 3 Sunrise #4729550,
  6 Sunrise #4729764 (balcony taken in); 13 Sunrise "still terrible";
  5 Sunrise lines wrong on both; chamfered corners on #5371242.
  L9 (short slanted edges dropped, shared edges simplified to 8 px): not
  visibly better by eye, not shown as progress.
- Benchmark with the reader's edge and the straighter faces (L9 code,
  `~/ab/bench_L9`): line F 0.67, lines right 99, faces right 38, face IoU
  0.613, outline IoU 0.87 (the build's edge, slid by the benchmark: 0.897).
  Lines a little better than L7's run (0.65, 96), faces no better.
- 784 px training on the Mac did not finish one epoch in 47 minutes
  (stopped); 588 px (42 x 42 patches) is running instead
  (`F588_fold0.log`).
- 588 px on fold 0, from the 392 px start: 0.660 then 0.640 after two
  epochs against 0.691 and 0.708 at 392 px, at ten times the time per epoch
  (1,400 s). Stopped: more detail in the reader is not the cheap gain.
- 20 hard roofs out for marking (`data/label_set/mark20_picked.json`, the
  reader's own edge and faces kept in `mark20_seed.json` for a before and
  after): picked from 545 houses read across the district, by the reader's
  uncertainty, drop edges, and disagreement with the surveyed outline.
- Was running: the reader retrained at 784 px (56 x 56 patches, `SOLAR_LINES_SIZE`)
  on fold 0, against D_fold0's 0.755 (`data/line_set/runs/F784_fold0.log`).
  If it helps: all folds, held-out face score, street. If not, the remaining
  levers are more drawn roofs (126 drawn roofs took fold 0 from 0.687 to
  0.755) and a larger backbone (a download: needs the owner's OK).
- `tests/test_call_sites` fails on `src/face_regions.py:152,162` (net()
  called with an argument) before and after this work: not from it.

**21 Nov (night), faces seeded from the face-interior map.** All five folds
trained (`D_fold0-4.pt`); every drawn roof read by a model that never saw it
(`data/line_set/heldout_D`, `tools/experiments/heldout_maps.py`) and scored
by FACES (`tools/experiments/face_score.py`, drawn outline supplied):

| 157 held-out drawn roofs | face IoU | faces right | lines right | near perfect |
|---|---|---|---|---|
| joins-and-lines (`faces_from_maps`) | 0.516 | 20 | 53 | 21 |
| regions (`faces_from_regions`) | 0.678 | 53 | 99 | 48 |

Regions, by kind: houses outside the town centre 60 roofs, lines right 47,
near perfect 22, faces right 18; town-centre houses 42: 27, 17, 22; larger
55: 25, 9, 13. The three thresholds barely move the score (0.669-0.678).
The reader finds 962 faces where 1,296 are drawn: missed faces are now the
reader's, not the conversion's. Street L7 (`data/preview/cases/street_L7`):
mixed by eye, not judged by the owner. Not comparable with the 179-roof
build benchmark (0.627, 43, 92) until run through it with the build's own
outline: do that next, on the VM.


**21 Nov (late), LiDAR alignment in the training set; street not yet better.**
- The first export left the LiDAR channels off the photo: few buildings
  outside the pilot have a recorded drawing shift. Aligned by the label
  (`tools/align_lidar_channels.py`, `data/line_set/typed_set3`): 100 of 157
  drawn roofs and 392 of 716 readings moved more than 0.5 m.
- Fold 0 (32 unseen drawn roofs, 0.75 m), reader retrained on the aligned set
  (`D_fold0.pt`): raw line map 0.743 with the LiDAR as it comes, 0.775 read
  twice (pass 1 photo alone, LiDAR slid onto its lines, pass 2), 0.776 aligned
  by the label. Drawing F 0.757; 0.9+ on 12 of 32 (reader A: 6).
- Final model on all labels: `data/line_set/runs/E_all.pt`. Build-side reader:
  `src/line_reader.py`, `SOLAR_LINE_READER=graph tools/line_reader_faces.py`.
- Street (L2-L6, `data/preview/cases/street_L*`): not better than L1 and not
  sent. Three causes found: (1) a slide estimate moved the roof edge off 1
  Sunrise Lane; the edge is no longer moved by the slide; (2) the
  joins-and-lines drawing leaves lines that close no face: on fold 0 it reads
  247 faces where 419 are drawn and gets the face count right on 9 of 32
  roofs, although its line F is 0.69-0.76. Line F hid this: score FACES
  (count, IoU) on held-out roofs before the next street run; (3) the old
  region conversion with the new model (L6) is no better by eye.
- Next: closed faces from the maps (the core map seeds every face, the line
  and join maps place straight edges between them), judged by a face score
  on the five folds, then the street.


**21 Nov, reader learns everything the markup records.** The markup gives each
line a kind (ridge, valley, cliff), the outer edge and the join points; the
first reader trained on "a line between faces" only, with the outer edge
erased. `tools/line_targets.py` now writes eight target maps
(`data/line_set/typed_set2`), `tools/train_roof_lines.py` trains them, and
`src/line_graph.py` rebuilds a roof as joins connected by straight typed
lines (from the markup's own maps: line F 0.91 at 0.75 m, 128 of 157 at 0.9+).
Fold 0 of 5 (32 drawn roofs the model never saw, 0.75 m): reader A 0.687 ->
with drawn roofs in training 0.716 (first-target scoring); with the corrected
target, raw line map 0.735, joins-and-lines drawing 0.729. Typed maps at 2 px:
ridge 0.62, cliff 0.47, valley 0.37, outline 0.55, join 0.49. Adding 4,322
LiDAR-derived roofs to training (run B) did not help (0.620 vs 0.637).
Open: outline from the roof map is wobbly (needs straight edges between
corner joins); missed joins; folds 1-4; final model on all labels; street.


- **21 Nov: owner's direction: roof lines first, building outlines second,
  everything else after.** "It's relatively easy to see and mark these up
  visually as a human, so I see no reason we can't make AI good at that."
  A new reader is being trained: DINOv2 ViT-S/14 photo encoder (weights
  already on the Mac) with a LiDAR side branch, boundary and core maps at
  392 px (`tools/train_roof_lines.py`), on `data/line_set/lines_set`
  (`tools/export_roof_lines.py`, map-photo frame, 384 px): 716 paid readings
  rated 0.5 or over, 4,322 LiDAR point-roof readings whose planes hold 95%
  of their returns within 0.15 m (of 9,800 tried), 157 hand-drawn roofs kept
  out as the test; the 30 held-back roofs are not read. Open: scores; maps
  to faces through `face_regions.straight_faces`; the street side by side.
- **21 Nov: three panel-side fixes from the street marks, parked behind the
  line work.** (1) Obstructions: `CRISP_PEAK` and `CRISP_LARGE`
  (`src/obstruction_detection.py`), on by default after the bench (103
  marked roofs: small marks found 37.8% -> 64.6%, small detections on a
  mark 29.2% -> 32.5%, area recall 0.322 -> 0.355, precision 0.338 ->
  0.344); the edge rule (`CRISP_EDGE_M`) added after 15H Sunrise Lane
  over-fired is not yet benched. (2) The shady side is laid out and ranked
  after the sunny side (`SHADY_SIDE_PANELS`). (3) Readings get
  `straighten_faces` after the weld: neutral on 179 drawn roofs (line F
  0.638, 92 right, 43 faces right). Dropped: `SOLAR_LINE_MERGE_OFF=0.8`
  (faces right 43 -> 39). Street `street_r7` against all marks: missed
  obstructions 13 of 32 (was 2), panels should go here 10 of 12 (was 4),
  over an obstruction 2 of 4. Not sent as a round.
- **21 Nov: placement on the photo checked; it was not the fault.** The
  entry below compared the map's photo with the SURVEYED outline. Against the
  drawn faces the recorded shifts hold: on the 19 street roofs the
  photo-to-photo match (roof only) is about (-0.8, +0.7) m and the LiDAR
  roof's lean in the survey-year photo about (+0.6, -0.6) m, which sum to
  the small recorded shifts; drawn at the recorded shift the point faces sit
  on the roof in the map's photo (#4729434, #4729764; agent's eye;
  `tools/experiments/survey_vs_map_photo.py`, scratch `place.py`). So on the
  13 unchanged roofs the faults are in the lines. #4729434: a real
  two-ridge roof read nearly right, with a jogged seam where two parallel
  fold lines 0.5 m apart both survived, and a hip end read as a sliver.
  Trial `street_r5`, `SOLAR_LINE_MERGE_OFF=0.8` (parallel candidate lines
  within 0.8 m are one line; was 0.35): 3 roofs change (#4729329 4 -> 3
  faces, #4729434 jog gone, #4729292 15 -> 14 faces); on #4729434 panels
  went 25 -> 38 and some look to sit across the gutter strip, not checked.
  Not adopted, not benchmarked. `SOLAR_POINT_FACES_DIR` lets a trial write
  and read its own readings. Open: why the hip end is lost (plane too small
  to grow, or merged by the seam cost), the panels on #4729434 in r5, and
  the 6 changed roofs.
- **21 Nov: the 19 test-street roofs, survey-year photo against the map's
  photo.** Sheets in `data/preview/cases/change_r4/` (2021 photo, current
  photo, returns by height, surveyed outline, the owner's real-edge clicks;
  script `tools/experiments/survey_vs_map_photo.py`). Read by the agent's
  eye, not measured: 6 of 19 are not the roof the survey saw (#4729767 and
  #5371242 rebuilt larger, #4729554, #4729550 and #4729287 a different roof,
  #4729452 under scaffolding in 2021). `roof_change` does not separate them:
  edge correlation is under 0.25 on all but one of the 19, and brightness
  change over 60 on 3. On the other 13 the 2021 photo sits on the outline and
  the returns, and the current photo shows the roof up to about 1.5 m away
  (#4729434, #4729288, #4729334, #4729764, #4729435), while the recorded
  drawing shifts on the street are all under 0.5 m (`image_shift.json`; the
  point-roof lean replaces the photo-to-photo match). Open: (1) compare the
  photo-to-photo match (`register_imagery.measure`) with the point-roof lean
  on these 13 and on the drawn roofs; (2) a changed-roof test that works
  (footprint of the current photo against the returns), and what a changed
  roof is built from.
- **21 Nov: round 4 judged not fixed; what the first roofs show.** Owner on
  round 4: nothing visibly fixed (marks `data/fault_marks/street_r4.json`).
  The weld moves lines by centimetres; the faults are metres. Evidence
  renders (photo beside every return by height, surveyed outline, fitted
  edge, faces, the owner's real-edge clicks: `data/preview/cases/evidence_r4/`):
  13 Sunrise Lane (#5371242): the returns show a low roof the size of the
  surveyed outline (32 m2); the map's photo shows a hip roof about twice
  that, and the owner's four corner clicks sit 1-3 m outside the outline over
  ground-height returns. The 2021 survey and the 2026 photo are not the same
  roof. 1 Sunrise Lane (#4729286): faces follow the returns closely but not
  the photo roof; moving the drawing (+0.9, -0.8) m did not line it up, so it
  is not a slide. Recorded leans on the street are all under 0.5 m. Not yet
  checked: the survey-year photo against the map's photo for the 19 roofs
  (`register_imagery.roof_change`), which would say how many were built or
  extended after the survey. Street rebuilt with the point roof off
  (`SOLAR_POINT_ROOF=0`, `street_photo`: the photo-reading model leads on
  the 10 roofs that have one): real-edge clicks met 4 of 7 (round 4 marks)
  and 1 of 14 (round 3), both roofs once ticked right changed; by the
  agent's eye not clearly better on #4729286. Not sent as a round.
- **21 Nov: round 3 marks (106 spots, 46 comments) and round 4: one line
  between faces.** Marks in `data/fault_marks/street_r3.json`. What the
  comments say, by count of roofs: doubled or tangled lines (7), outline off
  the photo roof (6), balcony or deck drawn as roof (4), missed hips and
  ridges that are plain in the photo (4), obstructions missed (15 spots) or
  far too large (1). Measured: a "doubled line" is two near-parallel edges of
  different faces 0.08-1.0 m apart over 1 m or more; 47 pairs on the round 3
  street, on every roof marked for it, none on five roofs not marked for it.
  `src/face_weld.py` (on by default, `SOLAR_WELD_FACES=0` off): a lapped
  edge is cut, a gap between faces under 0.9 m goes to the face beside it.
  Street round 4 (`street_r4`, built with `SOLAR_KEEP_UNREAD_FACE=1` like
  round 3): 47 pairs -> 10; panels 474 -> 506. Drawn-roof benchmark (179,
  blind, run before the spike tidy): line F 0.629 -> 0.639, roofs with lines
  right 88 -> 92, faces right 43 -> 42, panels 20,335 -> 20,586 (drawn
  19,370). Against the marks nothing else moved: ridges 2 of 6, obstructions
  over 0 of 4, missed obstructions 0 of 17 and 2 of 15, real roof edge 2 of
  14. Seen on #4729288, not measured: the oversized obstruction is gone and
  a panel now sits on the small object it was drawn around. Not touched:
  outline against the photo, balconies (flat faces well below the main roof),
  missed hips, sliver faces under 1.7 m (the 10 pairs left are mostly these),
  obstructions. Sheet `data/preview/cases/street_r4/`, marking page
  `data/preview/cases/mark_street_r4/`.
- **21 Nov: how fault marks become rules.** `tools/mark_faults.py` now
  has `measure` (every panel on the marked roofs measured the same way,
  marked or not; per fault, the measurement and threshold that takes every
  marked panel and the fewest unmarked) and `score-all` (a rebuilt street
  against every marks file so far). The page groups faults into five
  categories, adds "real roof edge is here" (scored: the drawn outer edge
  within 0.5 m of the click) and comments pinned to a spot. On the v85
  marks (484 panels, 35 marked): "should not be here" separates on face
  width (<= 1.63 m takes all 18 and 5 of 449 unmarked), which is the round 1
  rule; over a ridge, over an obstruction and off the roof do not separate
  on anything the snapshot holds (best: 104, 155 and 62 unmarked caught), so
  those need the returns and the photo measured at each mark before a rule.
  55 of the 68 line marks sit within 0.5 m of the outer edge. Open: add
  LiDAR and photo measurements to `measure` (runs on the VM); a second
  street of another kind as the check that rules hold where they were not
  tuned.
- **20 Nov: test street round 3 and the drawn-roof benchmark.** Benchmark
  (179 drawn roofs, blind): rounds 1-2 change no face on any of them (line
  F 0.629, 88 roofs with lines right, both runs; panels 20,422 -> 20,335):
  no harm, and no measured gain, because the form swap never met a reading
  there. `SOLAR_KEEP_UNREAD_FACE=1` (a point-reading face that is not one
  plane stays bare instead of being recut by residual fill): line F 0.629,
  lines right 88 -> 87, panels 20,335 -> 19,605 (drawn total 19,370), 18
  roofs changed, two large roofs lose most panels (#5372220 858 -> 481,
  #4747637 419 -> 101). On the street it changes four roofs, clears the
  stray triangles at #4729452 and the deck row at #4729292 (6 of 7 "off
  the roof" marks). Left OFF in the code: mixed on the drawn roofs. Shown
  to the owner as round 3 with a fresh marking page
  (`data/preview/cases/mark_street_r3/`, before/after
  `data/preview/cases/street_r3/`); it goes on, perhaps for homes only,
  if the owner's marks say so. Still open: outer edges (35 marks: outline
  against the photo), ridge and obstruction marks, missed obstructions.

- **20 Nov: test street round 2 (roof lines).** Cause found for the
  staircase ridge at #4750879: `structured_roof.maybe_structured` swapped
  the point reading's nine straight faces for a "wings" form with 26- and
  30-corner faces. A reading now keeps its lines there as it does in the
  reshape stages (60c476d3; `SOLAR_FORM_OVER_READING=1` swaps as before).
  On the street it changes #4750879 and #5371242 only. The 68 line marks
  split 35 on the outer edge (outline off the roof in the photo: decks and
  pergolas inside the outline, and the photo's lean), 30 on inner lines,
  3 away from any line. Seen in the LiDAR evidence renders
  (scratch `evidence.py`: photo, candidate lines, returns by plane),
  unjudged: #4729452's reading has two faces the build drops (plane inlier
  0.15 and 0.22 over a flat roof and pergola) and residual fill replaces
  them with stray triangles; #4729286 and #4729550 have the outline 1 m or
  more off the photo roof. `mark_faults.py score` cannot judge a moved
  line (a line 0.3 m off still lies within 0.5 m of the mark): line
  changes go back to the owner on a fresh marking page. Drawn-roof
  benchmark before/after running on the VM (`~/mb_before.score`,
  `~/mb_after.score`). Renders `data/preview/cases/street_r2/`.

- **20 Nov: test street round 1, scored against the owner's marks.** Marks
  (`data/fault_marks/street_v85.json`, 127 on 17 of 19 roofs, 2 roofs marked
  right): roof line or edge wrong 68, should not be here 18, obstruction not
  marked 17, off the roof 7, panels should go here 7, over a ridge 6, over
  an obstruction 4. Loop: `tools/street_loop.py <label> ~/street_cases.json`
  on the VM (reproduces v85 exactly at baseline), then
  `tools/mark_faults.py score`. Round 1 (fa93a3d9): no panels on a face
  under 1.7 m wide (`NARROW_FACE_M`), and height-rule drops that lie on
  their face's plane go back (`gate_panels.restore_on_plane`). Score: 18 of
  18 "should not be here", 1 of 7 "panels should go here", both right roofs
  unchanged; nothing else moved. Unmarked side effects for the owner to
  judge: #4729550 -2 and #4729292 -3 panels (faces 1.4 and 1.5 m wide).
  Renders `data/preview/cases/street_r1/`. Not in a build yet. Open, in
  order of marks: roof lines (68; the reading itself), missed obstructions
  (17; vents, skylights, a ridge strip), off the roof (7; six on a 2.2 m
  face over a deck at #4729292), ridge (6), remaining "panels should go
  here" (6; faces left empty at #4729287, #4729764).

- **20 Nov: fault marking page.** `tools/mark_faults.py page <out> <snapshot>`
  draws every panel of a snapshot as a clickable shape on the 0.05 m photo;
  the owner tags panels (over a ridge, over an obstruction, off the roof,
  wrong way round, stray, other) and pins spots (panels should go here,
  obstruction not marked, marked obstruction is not one, line or edge
  wrong). Marks carry the panel index and NZTM coordinates; save pasted
  marks as `data/fault_marks/<label>.json` (`mark_faults.py read`). First
  page: the 19-roof test street, `data/preview/cases/mark_street_v85/`
  (serve with the `solar-map-static` preview, port 8010). Next: a scorer
  that re-lays the street (`tools/layout_ab.py --ids`) and counts marks
  resolved, so each rule change is judged against the marks.

- **20 Nov: small test street for panel placement.** Sunrise Lane (17 roofs,
  frankton_arm) plus 26 Earnslaw Terrace and 84 Panorama Terrace: 19 roofs,
  refined until the owner calls each right, then widened. Baseline renders
  (v85): `data/preview/cases/street_v85/`. VM working folder `~/streettool`
  (cases file `~/street_cases.json`); per-roof re-layout with
  `tools/layout_ab.py frankton_arm --ids ...`. Four v85 reports logged as
  cases: #4740972, #4750879, #4729452, #4729434. Faults seen in the
  baseline, unjudged: arrays left ragged where the LiDAR gate and the
  obstruction mask remove single panels; panels across ridges where face
  lines sit off the photo ridge; a portrait row on a narrow strip face;
  panels past the eave where the outline is wider than the roof; vents not
  marked.

- **20 Nov: v85 published past the failed gate, on the owner's go-ahead.**
  `gs://solar-potential-map-tiles/v85/`, main 252e05d2. Still open from the
  gate: point-roof faces that end with no panels (#4732192, #4751244 and
  seven more zeroed roofs), 77 roofs down over 30% not yet judged.

- **19 Nov: Queenstown rebuilt (v85 candidate), gate FAILED, not published.**
  Build 01:23 to 07:44 UTC at 2e081948, no stage failed; VM up 6 h 51 min,
  stopped. Gate against v84: 9 roofs lost every panel, 77 lost over 30%,
  2 newly withheld, panels on the map -0.4%. Renders, v84 beside v85, one
  3600 px image per roof: `data/preview/cases/cases_v85/` (54 reported
  roofs; 7 changed layout) and `data/preview/cases/gate_v85/` (the 9 zeroed
  and the 15 largest drops). Seen in four of the gate renders, unjudged:
  two big drops come from a reader roof that marks skylight strips and a
  terrace as no-go; two zeroed roofs (#4732192, #4751244) have point-roof
  faces drawn and no panels placed. Next: find why a point-roof face ends
  with no panels (all nine zeroed have no reader file on the four checked),
  fix generally, rebuild only the affected regions, then ask about
  publishing.

- **RULE: the paid reader is not the production method (owner, 19 Nov).**
  Reading every Queenstown house (about US$1,800) is ruled out, and the
  method must scale nationally at compute cost only. The reader supplies
  training labels and evaluation samples in small batches. The roughly
  1,050 readings already bought are the label set for a local model
  (`data/models/face_regions_v3.pt` was trained on far fewer and is
  untested outside central Queenstown). Next: retrain the local model on
  all of them, hold some out, and put it beside the reader and the point
  roof on the reported roofs (`tools/cases_sheet.py`).

- **Compute costs reviewed (19 Nov).** Both stored 600 GB disk images
  deleted on the owner's instruction (`solar-vm-20261019`, `solar-worker`;
  about US$35-45 a month at list price): `tools/vm_start.sh --clone` needs
  `tools/fleet.sh image` run again first. Open, largest first: rebuild only
  the roofs a change touches (any hashed-code change marks all 23 regions
  full, 6.5 h); a small machine type for light jobs through the capped
  start script; the one-core stages before the first region (plan took 38
  min on 18 Nov); a cheaper reader (cheaper model, or only roofs whose
  point roof fails a check), to be trialled on about 50 houses; idle stop
  30 to 10 min. Buckets are under 9 GB in all.

- **Reported roofs are logged and redrawn every version (19 Nov).**
  `data/roof_cases.json` now holds 54 roofs (16 added from the owner's v83
  and v84 reviews; it had lapsed since 8 Nov). After every build:
  `tools/cases_sheet.py snap <label>` then `sheet <out> <previous> <label>`
  gives one 3600 px image a roof, previous version beside new, over the
  map's photo at zoom 21, and an index page; send it with the gate result.
  Baseline: v84, `data/preview/cases/cases_v84/index.html` (53 roofs; the
  Wanaka case #4727237 is outside the kept Queenstown layouts). Snapshots
  are on the VM under `~/casetool/data/case_snapshots` until the build
  ends and the checkout can be synced. A v84 roof led by a photo reading is
  drawn at the shift on file, which can differ from the map by its
  reading's slide.

- **Queenstown build with the reader roofs launched (19 Nov, VM
  `~/qt_build3.out`).** The 23 point-face regions at 2e081948: 394
  confident reader readings copied into the VM's `data/vision_faces`
  (379 from the 502-house batch, 15 from the two streets; 658 to 1,051
  files), the reader leading, the slide bound and the thin-point rule.
  v84's layouts and shifts are kept in `~/v84_layouts` for the before/after.
- **OPEN: the slider reaching every face needs the map page too.** Laying
  panels on away-from-sun faces and ranking them last (as pocket panels
  are) is not enough on its own: the map's default for a home is
  `fill_order <= 28` and for anything else every panel
  (`rooftop-solar.html` `withinSize`, `emit_region._panel_areas`), so a home
  with fewer than 28 sunny-side panels and every non-home would show the
  shady-side panels by default, and `derive_solar_potential` and
  `bake_density_deciles` would count them in the default totals. It needs
  a shipped flag on those panels that the default sizing, the totals and
  the card's slider all read. Not in this build.

- **The 502-house reader batch is back (19 Nov).** 489 readings, 13
  failed, US$85.56 ($0.175 a reading returned; the 29-house batch was
  $0.14). The reader rated 387 (79%) at 0.5 or over, median 0.55, 4 under
  0.3; 379 became a roof on ingest (`~/reader_q500_faces` on the VM, not
  yet in `data/vision_faces`). 40 of those, drawn at random, beside v84:
  `docs/review/reader_q500_sample_1..5.jpg` (awaiting the owner's
  verdict). Agent's reading of 16 of the 40: the reader's outline sits on
  the eaves and its faces follow the ridges on all 16, and it is at least
  as good as v84 on each; v84 was already close on about half. It marks
  existing solar arrays and skylights as obstructions. At this rate all
  10,280 eligible Queenstown houses would cost about US$1,800 and give
  about 8,000 confident roofs.

- **Owner's verdict on the two-street trial (19 Nov): the reader looks
  better than live.** Acted on: (1) `vision_roof.reader_leads` again lets
  the reader lead wherever it has a reading (18ebc5f0; the 12-face limit
  stays as `SOLAR_READER_LEADS_OVER_FACES`, off); (2) 502 random
  Queenstown houses sent to the reader as 23 batches (outline 80-400 m2,
  not held back, no markup, no reading yet, shared over the 23 regions by
  their number of such houses, seed 41; 10,280 houses are eligible in
  all). Crops, `picked.json` and each folder's `batch.json` are in
  `data/reader_trials/reader_q500/` on this Mac; collect with
  `src/vision_roof.py batch-collect <folder>`. Estimate US$85 at the
  tool's $0.17 a roof; the 29-house batch came to $0.14 a roof. Not yet
  done: ingest on the VM (the slide onto the LiDAR needs the returns),
  copy the 14 confident two-street readings and these into
  `data/vision_faces` there, rebuild. Reading all 10,280 eligible houses
  would be about US$1,450 at $0.14: owner's decision. Also waiting for the
  next build: the slide limit, the thin-reading rule, and panels on every
  face for the slider (23 Wakatipu Heights; not written).

- **Paid reader on two streets, beside v84 (19 Nov;
  `docs/review/reader_vs_live_19nov_1..4.jpg`,
  `tools/experiments/reader_vs_live.py`; awaiting the owner's verdict).**
  All 29 buildings of 40 m2 or more on Wakatipu Heights and Bolton Lane
  (none held back), `claude-fable-5-2`, effort high, Batch API: 29
  readings, US$4.01 (about $0.14 a roof). The reader rated 14 at 0.5 or
  over and 15 under (deep shadow, trees). Readings kept at any confidence
  for the sheets (`--min-confidence 0`), in a trial folder on the VM
  (`~/reader_streets_faces`), not in `data/vision_faces`; crops and raw
  readings in `data/reader_trials/reader_streets/`. Agent's reading:
  reader clearly better than v84 on about 17 (1, 3, 5, 6, 8, 10, 12, 15,
  17, 20, 22, 23, 25 = 33 Wakatipu Heights, 26 = 35, 27 = 37, 28, 29);
  poor on about 10 (2 = 5 Bolton Lane, half the building unread; 4, 7,
  9, 11, 14, 16, 18, 19, 21, 24), 8 of them rated under 0.5 by the reader
  itself. So its confidence sorts good from bad here, and a reading at 0.5
  or over beat the point faces on simple roofs too: the "reader leads only
  over 12 faces" rule of 18 Nov (`vision_roof.reader_leads`) needs
  revisiting once the owner has judged these.

- **Owner's review of v84 on the live map (19 Nov): seven more failures on
  two streets, none fixed.** Wakatipu Heights 23 (#4751005: the south face
  bare, the slider stops at 27 panels; owner: the default may sit near
  14 kW but the slider must reach every place a panel fits), 33 (#4751072:
  panels off the roof and across a step), 35 (#4751237: panels the wrong
  way round across the hips), 37 (3 panels on a large roof); 1/3 Bolton
  Lane (outline and roof lines well off the photo); 5 Bolton Lane (panels
  over obstructions and empty space, scattered). The three Wakatipu
  Heights roofs traced are point readings of 2-3 faces with a fitted edge
  (frankton_arm_lake); the others are not traced. One street, nearly every
  house wrong: the rules added since v82 have not made ordinary houses
  right.
- **The v84 losses traced (19 Nov): thin scans.** #4747990, #4738759 and
  #4738589 have a one-face point reading with no fitted edge that v84 put
  first; v83's other reading (8, 2 and 4 faces) drew them well
  (`docs/review/thin_point_19nov_v83.jpg` against `thin_point_19nov.jpg`).
  #4723372 was slid (2.5, 1.5) m, further than a photo can lean on a
  single-storey house, and the photo check kept it. Two rules, committed
  (c4356a60), fast tests pass, not in a build: a slide longer than
  0.4 m per m of roof height plus 0.5 m is dropped
  (`point_roof.write_faces`; #4723372 now unslid); a one-face point
  reading with no fitted edge gives way to a later reading of two faces or
  more (`roof_partition.selected_doc`; the three now lead with the other
  reading). Tried and dropped: choosing between the two readings by which
  agrees with the photo better, right on 7 of 12 known cases
  (scratch `pick.py`).

- **v84 is live (19 Nov): published on the owner's instruction although
  the gate failed.** Next: trace the three clear losses (#4723372,
  #4747990, #4738759) and look at a sample of the leans the neighbours'
  median replaced. The entry as written before publishing:
- **Queenstown build with the checks and the new reading order is built and
  NOT published (19 Nov, `point-roof` 7f9ac465; served set on the VM and
  pulled to this Mac's `data/`).** 23 regions in 6 h 28 min; panels
  1,327,680 -> 1,329,719. Gate against v83 FAILED: 2 zeroed (5 and 6
  panels: one under trees, one on a cleared site), 16 big drops, none
  newly withheld, 10 of 38 case roofs changed
  (`docs/review/gate_v84_{drops,cases}.jpg`). Agent's reading of the drops:
  clear losses on good roofs #4723372 (77 -> 17, faces drawn off the
  roof), #4747990 (58 -> 21), #4738759 (57 -> 26); partial on #4738589,
  #4742844; the rest more faces or a building site. Causes not traced.
  Same houses, v83 beside this build: `docs/review/build84_v83_1..6.jpg`,
  `build84_new_1..6.jpg`: 3 Kerry Drive, 7 Leeds Lane and #4721959 now on
  their roofs; 46 Goldfield Heights and 50 Andrews Road better; 9 Kent
  Street loses its curved corner. The lean check replaced 60 leans in the
  town centre and 118 in Frankton Arm (about one roof in ten there): few
  of those were looked at. To publish: `.venv/bin/python
  tools/publish_served.py 84`, then `site-config.js` on main to v84
  (owner's go-ahead needed past a failed gate).

- **Slide check, lean check and a new reading order are in the build
  (18 Nov, `point-roof` 7f9ac465; Queenstown build running, not live).**
  (1) `point_roof.write_faces`: a slide onto the returns of 1 m or more is
  kept only when its roof matches the photo at least as sharply as the
  unslid roof; the 1.5 returns/m2 cut-off is removed. (2)
  `register_imagery._point_leans`: a lean at the search limit, or over
  0.2 m per m from the median of 4 or more sharp neighbours within 150 m,
  takes that median. (3) `vision_roof.reader_leads`: the paid reader leads
  only a roof it reads as more than 12 faces; `selected_doc`,
  `build_keys.reading_hash` and the drawing shifts follow it. Fast tests
  pass. NOT built in from the experiment: each corner moved by its own
  height (the map still moves a building as one piece; `emit_region.py`
  has someone else's uncommitted work in this checkout) and walls snapped
  to photo edges (moved few walls, little). Not done: the owner's
  lone-panel rule (the cut is by `fill_order` in the map page and
  `emit_region`), balconies, roofs built since the survey.

- **Fused roof edge: two checks added after the owner's verdict (18 Nov).**
  Owner on the 40-house sheets: fused looks better, some way off (#4721959,
  2.6 m off its roof). Cause: its lean came out at the corner of the search
  (0.4, -0.4 m per m), on the building's own shadow edge. Added in
  `tools/experiments/fused_edge.py`: (1) a slide onto the returns of 1 m or
  more is kept only when the roof it gives matches the photo at least as
  sharply as the unslid roof; (2) a lean at the search limit, or over
  0.2 m per m from the median lean of the sharp roofs within 150 m, takes
  that median (no lean when there are too few neighbours). Rerun on the
  same 40 plus 3 Kerry Drive (sheets replaced, `fused_vs_live_18nov_1..6`;
  numbering moved by one after house 5): 7 of 41 caught: slides dropped on
  5, 6 (3 Kerry Drive, 2.9 m), 8 and 11; lean replaced on 13, 20
  (#4721959) and 22. Agent's reading: 20, 6, 5 and 8 now sit on their
  roofs. Not yet in the build.

- **Fused roof edge beside v83 on 40 random unmarked houses (18 Nov,
  `tools/experiments/fused_vs_live.py`, eight regions, five each,
  `docs/review/fused_vs_live_18nov_1..5.jpg`; awaiting the owner's
  verdict).** 38 of 40 got a fused edge; 33 and 38 have no faces from the
  returns (built since the 2021 survey). Agent's reading: fused clearly
  better on about 10 (3, 7, 12, 13, 16, 22, 23, 34, 39 and 2: v83 drew
  nothing on 3, 22, 23 and 34), much the same on about 24 (v83 already has
  point faces and the roof edge there), fused worse or absent on about 6
  (33 and 38 nothing; 19 slid off its roof by the slide onto the returns,
  as 3 Kerry Drive; 10, 37 and 5 odd faces or edge). Open: a guard on the
  slide onto the returns; what a roof with no point faces falls back to;
  balconies and other no-go areas; panels on the fused roof.

- **Fused roof edge: surveyed outline + returns + photo (18 Nov,
  experiment, `tools/experiments/fused_edge.py`,
  `docs/review/fused_edge_18nov_{1,2}.jpg`; awaiting the owner's verdict).**
  Owner's direction: the LiDAR roof edge reconciled against the photo and
  the outline together. Per building: point faces, each wall moved to where
  the returns stop (`roof_edge`), every corner moved by the lean times its
  own height (`photo_lean.leaned`), then each wall slid up to 0.8 m onto
  the photo edge along it when one stands out by 3 robust standard
  deviations. On the three houses and nine of the owner's v83 failures,
  agent's reading: on the roof with straight edges and sensible ridges on 8
  (36 Stanley Street, 15B Frankton Road, 1 Shotover Street, 7 Leeds Lane,
  9 Kent Street, 9 Vancouver Drive, 32 Highview Terrace, 50 Andrews Road);
  poor on 4: 3 Kerry Drive (the slide onto the returns, -1.5, 2.5 m, is
  wrong), 46 Goldfield Heights and #4729407 (changed since the 2021
  survey; balconies not separated), #5371259 busy. The photo snap moved
  few walls and little: most of the gain over the live map is point faces
  + roof edge + lean per corner being used at all, where v83 used a photo
  reading or markup placed by a photo-to-photo shift.

- **Method survey and a photo-outline prototype (18 Nov; nothing in the
  build).** Owner asked for a better way that scales.
  *Survey (agent web research, primary sources read by the agent, not
  re-checked here):* (1) `roofer`, the 3DBAG engine (GPL-3.0, v1.0.0 Apr
  2026, CLI + Python), reconstructs roof planes from classified LAS plus
  one roof polygon per building; run on about 10.8 M Dutch buildings at
  about 10 pts/m2; it does NOT correct a polygon that is off the point
  cloud, and reports per-building RMSE, no-data share and a
  point-cloud-unusable flag. No published run outside the Netherlands.
  (2) Google Solar API: Queenstown, Frankton, Arrowtown, Wanaka, Hawea
  and Cromwell are in none of its coverage files (6 May 2026); roof
  segments are bounding boxes; results may be cached 30 days: not usable.
  (3) Otago Regional Council (June 2026): region-wide LiDAR, first
  Queenstown-Lakes datasets public from late 2026. (4) Roof planes from
  photos alone: best published AP50 about 0.57, no released weights.
  (5) Outline-from-photo models with open weights and licences: Pix2Poly,
  HiSup (MIT), FFL (BSD-3).
  *Prototype (`tools/experiments/photo_outline.py`, SAM ViT-B already on
  the Mac, box prompt from the surveyed outline, untuned;
  `docs/review/photo_outline_18nov_{1,2}.jpg`), agent's reading on 12
  roofs:* good on about 7 (9 Kent Street with its curve, 36 Stanley Street
  on the eaves, 50 Andrews Road without its deck, 46 Goldfield Heights
  with the part the surveyed outline lacks); leaks onto ground-level
  surfaces on 4 (car parks, paths, decks); balconies not separated; edges
  ragged. Leaks are all onto surfaces the returns put at ground height.
  *Design this points to:* roof polygon = photo mask cut by the returns'
  roof-height footprint (photo wins where the two disagree, flagged);
  planes from the returns inside it (`roofer` or `point_roof`); the
  surveyed outline only names the building. Next: `roofer` on the three
  houses against `point_roof` (needs the owner's OK to install it).

- **Owner's review of v83 on the live map (18 Nov): ten failures reported,
  none fixed yet.** What the build held for each (VM, 18 Nov):
  - *3 Kerry Drive #4735154* (outline and panels off the roof): point
    faces, slid onto the returns by (-1.5, 2.5) m by `point_roof.register`
    on a multi-level building; lean sharpness 0.8. The surveyed outline,
    unslid, fits the photo better. Same on v82.
  - *7 Leeds Lane #4750959* (outline, ridges and panels wrong, a diagonal
    run of panels): led by a paid photo reading, placed by the
    photo-to-photo shift (1.3, 0.1) m. Its point faces with the roof edge,
    at their own lean (0.69, -0.49), sit on the roof with the hips right
    (agent's reading of `roof_edge_sheet`).
  - *9 Kent Street #4735373* and *9 Vancouver Drive #4751255* (a lone
    panel): the map shows panels by `fill_order` up to the slider or the
    14 kW home cap; the order does not keep the shown set in whole arrays,
    so the cut strands one panel. Owner's rule: when the cap would add a
    single panel, stop one short or go one over, to a whole array.
  - *32 Highview Terrace #4729348* (outline about 1.5 m off): point faces,
    no slide; not traced further.
  - *#4729407 Kelvin Heights* (panels over balconies; flagged "roof may
    have changed"): two point faces, 362 + 57 panels, 2 obstructions.
    Owner: read what a person sees in the photo, LiDAR or not.
  - *50 Andrews Road #4729783* (one face panelled, room for more): not
    traced.
  - *46 Goldfield Heights* (outline nowhere near the building) and
    *#5371259* (a long roof cut into dozens of slivers): not traced.
  Owner's direction (18 Nov): rely less on the surveyed outline and more
  on the photo itself for the outline and for where panels cannot go.
  That is what the paid photo reader does (`src/vision_roof.py`; 13 Nov
  pinned notes had point-roof houses right on 2 of 24); it has read 563
  roofs, costs about US$0.17 a roof by batch, and its API credit is spent.
- **The 26 big drops on roofs with the new edge are the panel packer's
  thresholds, not the edge.** #4721870 (flat, 9.0 x 19.7 m): the surveyed
  rectangle packs 70 at the tight edge gap; the fitted edge is 0.12 m
  shorter, ten columns no longer fit, and the wide edge gap (0.4 m,
  `EDGE_GAP_KEEP_FRACTION` 0.75) then keeps 48 of 63 = 0.76 and wins.
  Searching both grid phases together changed nothing and was not kept.
- **Round joins in `extend_to_edge` replaced by mitred ones** (a
  rectangular face had come out with 104 vertices); not in a build.

- **v83 is live (18 Nov): published on the owner's instruction, to judge
  on the map, although the gate failed.** Hour cap: the owner said the
  monthly hours may be raised (18 Nov); not yet raised. The entry as
  written before publishing:
- **Queenstown build with the roof edge is built and NOT published (18 Nov,
  `point-roof` a41dc41e; the served set is on the VM and pulled to this
  Mac's `data/`).** 23 regions rebuilt in 6 h 12 min on one VM; the map's
  panels 1,287,611 -> 1,327,680. Gate against v82 FAILED: 5 zeroed (5-7
  panels each: small buildings under trees), 38 big drops, 1 newly
  withheld, 13 of 38 case roofs changed. Renders, before and after:
  `docs/review/gate_v83_{zeroed,drops1,drops2,cases}.jpg`. Of the 38
  drops, 26 are point roofs with the new edge and 12 are roofs under 1.5
  returns/m2 that lost their point faces and fell back to another reading
  (#5371543 74 -> 12, #4718798 78 -> 42 are clear losses on good roofs).
  Publishing a failed gate needs the owner's go-ahead. To publish:
  `.venv/bin/python tools/publish_served.py 83`, then `site-config.js` on
  main to v83. The sparse-roof rule wants replacing before the next build:
  keep the point faces and drop only the slide onto the returns.

- **Roof edge from the returns is in the build (17-18 Nov, `point-roof`
  a41dc41e; Queenstown build running, not live).** `src/roof_edge.py`
  (moved from experiments): point faces run out to the fitted edge, the
  doc carries it as `outline`, `outline_fit.fitted_outlines` uses it.
  Three things found on the way, each from a four-suburb build (town
  centre, Fernhill, Kelvin Heights, Hanley's Farm):
  (1) the outline now follows the reading that gives the faces: a
  photo-traced outline had clipped point faces on about 350 of 1,270
  town-centre buildings; (2) a roof under 1.5 returns/m2 (built since the
  2021 survey) is not read from the returns; (3) walls are not fitted on
  sparse roofs (strip expects under 10 returns), so about one point roof
  in three still has the surveyed outline (roof edge used: town centre
  605 of 818 point roofs, Hanley's 437 of 551, Kelvin Heights 554 of
  1,058, Fernhill 1,192 of 1,278). Same 20 houses before and after:
  `docs/review/roof_edge_before_1..3.jpg`, `roof_edge_after_1..3.jpg`.
  Agent's reading: better on about six (edge reaches wings and eaves the
  surveyed outline missed; one house 2 m off now on its roof), same on
  most, five town-centre roofs unchanged because a photo reading leads
  them, one rebuilt-since-the-scan house wrong both times. Awaiting the
  owner's verdict.
- **While this is worked on, only Queenstown is rebuilt** (owner, 17 Nov):
  the 23 regions with point faces. Wanaka, Hawea, Cromwell and the rest
  stay on the map as v82 built them.
- **Still flat:** the map moves a whole building by its lean at mean
  height; the three-house images moved each corner by its own height. On
  a house the difference is about 0.1-0.2 m; on a tall building more.
- **Not looked into:** why Kelvin Heights fits an edge on only half its
  point roofs when 57 of 64 random houses over 60 m2 fitted.

- **40 random unmarked houses as v82 draws them (17 Nov,
  `docs/review/placement_v82_1..5.jpg`, `tools/experiments/placement_sheet.py`;
  awaiting the owner's verdict).** Yellow surveyed outline, red faces, cyan
  panels, at the shift on file. Agent's reading, not a measurement: about
  half sit on the roof with sensible lines; the largest group of misses
  (about a quarter: 11, 12, 14, 15, 19, 21, 22, 31, 34, 36, 40) is a roof
  whose real edge is not the surveyed outline (eaves beyond it, a wing
  missing, a different shape), which no slide can fix; about six are the
  right shape slid by 0.5-1 m (5, 9, 17, 26, 28, 38); about four have
  wrong inner lines (18, 20, 32, 39). **The roof edge from the returns and
  `extend_to_edge` (owner preferred it on the three houses) is still not
  in the build: that is the next thing to build.**
- **Regions with no point faces at all:** Cromwell east and west,
  Bannockburn, Arrowtown hills, Glenorchy (both), Kingston, Kinloch: about
  6,500 buildings on the older method; Arrowtown East has them for 167 of
  416. Cause not looked into.
- **Photo placement now runs for every roof with point faces**
  (`register_imagery._lean_pass`, not yet in a build). It had run only over
  buildings the photo-to-photo match measured. On 15 random Wanaka and
  Hawea houses the lean reads 0.0-0.2 m and the roofs already sat right, so
  this was not the cause of the misses there.

- **Placement on the photo, checked 17 Nov (v82 live).** By eye on the live
  map, four suburbs: most roofs carry their panels, but in every view one or
  two houses have panels over an eave or across a ridge by 1-2 m. Test
  (`tools/experiments/lean_neighbours.py`, 37 marked pilot roofs whose point
  lines match the markup at F >= 0.6, truth = best slide of lines onto drawn
  lines): no slide 0.71 m median error; the roof's own lean (the build's
  rule) 0.21 m, 59% within 0.3 m, 75% within 0.5 m, 2% over 1 m; the
  neighbours' median lean x own height 0.57 m, 18% within 0.3 m; own
  reading with the prior centred on the neighbours' lean, same as own.
  Pooling a lean over neighbours is DROPPED (second time it has lost). Where
  the lines are right the placement is within 0.5 m on three roofs in four,
  so the misses seen on the map are more likely roofs whose lines are wrong
  than roofs put in the wrong place. Not yet measured: what share of
  unmarked houses that is. Next: a sheet of ~40 random unmarked houses,
  faces and panels over the map photo, each miss sorted into wrong lines /
  wrong place / panel over the edge.
- **Point fallback is NOT live.** The full re-lay was held on 17 Nov: 178 of
  205 VM hours used, one re-lay costs 12-16, and any further `src` fix needs
  its own re-lay, so the fallback (3 roofs regained) waits to ship with the
  next roof fix.

- **v82 is live (17 Nov 01:40 UTC): marked town-centre roofs that borrowed
  a neighbour's shift are placed by their own lean.** 19 Camp Street
  (#5372566) was drawn 2.3 m off its roof on v81: its photo-to-photo match
  was weak, it took its neighbours' 1.0 m east, 0.7 m south, and its own
  leans give 0.98 m west, 0.56 m north (`register_imagery._point_leans`).
  Seven marked pilot roofs moved; the gate passed against v81 with nothing
  zeroed, dropped or withheld; checked on the live map. Applying the lean
  difference to every marked pilot roof was tried first and dropped: of the
  8 that moved most, 3 looked clearly worse by render (leans under 3.5
  sharpness in one photo). Other regions built with the commit before; the
  rule changes nothing there (their markup is drawn on the map's photo).
- **The 36 roofs v81 left with no panels are three groups, none fixed.**
  6 are under the 0.45 confidence gate on point-roof faces (#4718821 0.25,
  #4738149 0.42, #5372248 0.37, #4749869, #5374014, #5372361); 7 are
  `too_small`; 23 carry `low_confidence` only as the fallback label: the
  layout places a few panels and the panel gate drops them (#4737116 placed
  7, #4748214 placed 3, rebuilt alone with `tools/layout_ab.py`). **The
  first group has a rule, built and not yet in a district build
  (17 Nov, `SOLAR_POINT_FALLBACK`, build_layout_geojson POINT FALLBACK):**
  a roof whose point faces fall under the confidence gate is built again
  from the next reading and that build kept when it clears the gate. The
  six roofs rebuilt alone on the VM, rule off and on (panels before the
  panel gate): #4738149 0 -> 85 (confidence 0.42 -> 0.85), #4718821 0 -> 49
  (0.25 -> 0.76), #5372248 0 -> 39 (0.37 -> 0.81); #5372361 clears the gate
  (0.86) but is 25 m2 and takes no panel; #4749869 and #5374014 have no
  other reading and stay withheld. How many roofs it changes district-wide
  is unmeasured until the next full re-lay. A roof that falls back keeps
  the photo lean of its point faces as its drawing shift
  (register_imagery), not its fallback reading's slide: unchecked. The
  second and third groups have no rule yet.
- **v81 is live (17 Nov 00:09 UTC): the point roof and the photo lean on
  all 35 regions.** 32,838 buildings, 1,287,610 panels (v79: 1,238,166).
  Built from `point-roof` at a7acbaeb on four machines in 4 h 6 min
  (19:29 to 23:35 UTC 16 Nov): the build VM on the four Wanaka-area regions,
  three n2-standard-16 spot clones (`tools/vm_start.sh --clone`, branch
  `vm-spending-limits`, image `solar-worker`) on 9, 9 and 13 regions; each
  clone uploaded what it wrote to
  `gs://solar-potential-map-data/split_v81/<clone>/` and deleted itself; the
  shares were unpacked on the build VM, then `combine_regions.py`.
  - **Gate failed and was reviewed by render**, not judged against markup:
    36 roofs lose all panels, 140 lose over 30%, 14 case roofs changed
    (`docs/review/gate_v81_*.jpg`: 14 zeroed, 54 drops, 14 cases rendered).
    The zeroed roofs looked at (4718821, 4738149) have well-drawn faces and
    are withheld by the confidence stage as `low_confidence` (0.25, 0.42):
    the confidence score has not been checked against point-roof faces.
    Drops looked at lose a whole face (5370629: an 18 degree face toward
    155 degrees gets none). Both are open.
  - **The clones had no LINZ key** (`add_addresses` failed on all 31 of
    their regions); addresses and emit were re-run on the build VM. A clone
    job needs the key from Secret Manager or the `.env`.
  - **Limits found.** The fleet (`tools/fleet.sh`) could not be used: no
    queue exists in the bucket, `up` asks for a 400 GB pd-balanced disk
    (image is 600 GB, SSD quota 500 GB), and `src/build_region.sh` runs the
    layout with `--skip-done`, which skips a re-lay on old markers. c2d spot
    had no capacity in zone -a. The CPU quota (`CPUS_ALL_REGIONS`) was 32
    and was raised by the owner. Clone limits (6 alive, 12 h each, monthly
    cap counting live clones) are in `data/vm_budget.json` on that branch;
    the VM-start hook also blocks `instances create`, the fleet's `up`,
    bulk create and instance groups.
  - **Still to do:** merge `point-roof` into main (main serves data its code
    did not build; draft PR #67 is behind the branch); re-record the golden
    and synthetic references on the VM; delete `split_v81/` and the
    `solar-worker` image when no longer wanted; v80 was never served.
  - The first launch of this build was lost for ten hours to a watcher
    whose `pgrep -f` matched its own ssh command; `tools/watch_vm_build.sh`
    replaces it.

- **The lean holds on the map's own photo, on regions it was never tuned
  on (16 Nov, VM, `tools/experiments/lean_check_regions.py`).** 75 marked
  roofs in 22 regions outside pilot, traced on the map's photo, point roof
  scored against the markup's faces and strokes at 0.3 m: as placed
  median 0.31, 12 roofs at 0.7 or over; leaned 0.64, 32-33 roofs (prior
  0.5 / 1.0 / 1.5: 33 / 32 / 33); with the markup's own best slide 0.69,
  37. The slide there is a median 0.56 m, p90 1.05 m. Pilot on the same
  run: 5 -> 28 (prior 1.0), 32 with the best slide. `SOLAR_LEAN_PRIOR`
  stays 1.0. The VM had no PyMaxflow (`point_roof`'s graph cut): installed
  1.3.2, the Mac's version, and declared it in `requirements.txt` and the
  lock. `point_roof` gives one roof 180 s before it keeps its other
  readings.
- **v80 uploaded, site not switched (16 Nov).** The reading-fix build
  (35 regions, 1,239,069 panels, +0.1%) is at
  `gs://solar-potential-map-tiles/v80/`. Gate listed 15 of 32,838 roofs
  (2 zeroed, 11 big drops, 2 case roofs changed), rendered in
  `docs/review/gate_v80_flagged_roofs.jpg`: about five are worse, a large
  obstruction patch taking a face's panels (5372246, 5370362, 5374095,
  4740583, 4751063). The push of `site-config.js` to main was refused by
  the session's permission check; main still serves v79. v77-v79 are
  still in the bucket (no `--keep`).

- **Point roof and the lean wired into the district build (16 Nov, code on
  `point-roof`, on by default; no region built with it yet).**
  `build_plan.PREPARE` runs `point_roof` first for every region (16 jobs,
  a roof rewritten only when `point_roof.py` changed; faces carry their
  planes). `register_imagery._point_leans` replaces the shift of every roof
  built from point faces with `photo_lean.drawing_shift` read off the
  map's own tiles (0.2 s a roof; `SOLAR_PHOTO_LEAN=0` turns it off;
  `SOLAR_LEAN_PRIOR` 1.0: no cost on the pilot check, 28 of 50, and fewer
  far misses on the tiles). A free-model reading no longer moves the
  drawing of a roof that point faces lead. `build_keys.reading_hash`
  follows the new order. Reader against point roof on equal terms
  (`tools/experiments/fair_compare_reader.py`, 23 marked roofs with a
  reader reading): reader better on 12, point roof on 5, same 6; on the 17
  with 12 faces or fewer 6 / 5 / 6. The reader keeps first place.
  1 Shotover Street on the map's tiles: the lean gives a move of (0.0,
  0.37) m where the map applies (-0.2, 1.1) today. Before the district
  build: the lean check on the 68 marked roofs outside pilot, on the VM.

- **Faces from the returns lead the free model in the build (16 Nov, code
  on `point-roof`; `SOLAR_POINT_ROOF` still off by default, no region
  built with it).** Fair comparison first
  (`tools/experiments/fair_compare.py`: 29 benchmark roofs the free model
  never trained on, each method's faces slid as one piece to its best
  match with the markup's faces and strokes): line F at 0.3 m median 0.82
  point roof against 0.69 free model, roofs at 0.7 or over 18 against 14,
  point roof better on 15 and worse on 6. `roof_partition.selected_doc`
  now reads reader, then point faces, then free model; point faces are
  kept through the build like a reading (`from_reading`);
  `point_roof.py --jobs`. Through the build itself, 50 marked pilot roofs
  built blind (`SOLAR_HIDE_LABELS=1`, `tools/layout_ab.py`), scored the
  same way (scratchpad `three/builtscore.py`): as now 24 roofs right at
  0.3 m (median 0.69), 3,783 panels, 30% clean; with
  `SOLAR_POINT_ROOF=1` 32 roofs (0.79), 3,980 panels, 50% clean. To ship:
  point faces for every region (`point_roof.py <region> --jobs 16` on the
  VM), the switch on, a district re-lay (the geometry hash changes), and
  the lean for the drawing (entries above) or the roofs are right but
  drawn off the photo. Reader against point roof on equal terms is
  unmeasured.

- **Lean prior and the check for the map's photo (16 Nov).** The misses on
  the map's tiles are jumps of 2-3 m onto another edge (4735146: -2.5,
  -2.1 m). `SOLAR_LEAN_PRIOR` (default 0.5): on the map's tiles against
  the weak truth above, prior 0.5 / 1.0 / 1.5 / 2.5 leaves 31 / 22 / 14 /
  11% of 36 roofs over 1 m off (median 0.43 / 0.41 / 0.35 / 0.35 m); on
  the survey-year photo against the markup directly, roofs right at 0.3 m
  28 / 28 / 24 / 21 of 50. Sharpness of the peak does not separate the
  misses. `tools/experiments/lean_check_regions.py` scores every marked
  roof whose region is on the machine; the 68 marked roofs outside pilot
  (2-20 faces) were traced on the map's own photo and are the direct
  check: run it on the VM after the district build, before choosing the
  prior. VM cap for November raised 165 -> 205 h on the owner's word
  (`data/vm_budget.json`, branch `vm-spending-limits`, 5eeb19e3).

- **Lean on the MAP's photo: not ready (16 Nov).** The live map shows the
  LINZ Basemaps photo, not the survey-year photo the pilot markup was
  drawn on, so the lean has to be read off the map's tiles
  (`register_imagery._Tiles`). Scratchpad `three/maplean.py`, 36 marked
  pilot roofs, against the markup's slide plus `image_shift.json` (a sum of
  two measurements, a third of the shifts borrowed from neighbours, so a
  weak truth): what the map applies now is a median 0.72 m off (19% within
  0.5 m, 17% over 1 m); the lean read off the tiles 0.43 m (56% within
  0.5 m) but 31% over 1 m. Better typically, worse in the tail: do not
  ship as is. Next: accept the photo's lean only on a sharp peak and keep
  the present shift otherwise; a direct truth exists outside the pilot,
  where marked roofs were traced on the map's own photo, but those
  regions' LiDAR is on the VM only. 1 Shotover Street with the edge
  extended: 84% of drawn line length has a built line within 0.3 m, 98%
  within 0.75 m; what is left is the west side of the box on the
  south-west wing and the top 1.5 m of the long valley, 0.3-0.75 m off.

- **Roof edge from the returns: owner prefers it; faces extended, not
  re-partitioned; tree rule (16 Nov, experiment).** Owner's verdict on
  `docs/review/three_houses_16nov_roof_edge.jpg`: the edge from the LiDAR
  is better than the surveyed outline. Three rules added to
  `tools/experiments/roof_edge.py` since the entry below: (1) a wall is
  fitted only when its strip expects 10 or more returns (1 Shotover
  Street's 2.4 m wall expected 5 and moved 1 m inwards on noise); unfitted
  walls take the roof's median move. (2) `extend_to_edge`: the faces found
  on the surveyed outline are kept and run out to the fitted edge, the
  eave strip cut by each seam carried straight on; re-partitioning on the
  moved outline redrew a hip end on Shotover that was right. (3) Owner: a
  tree over the roof edge makes the returns a bad guide. A wall with
  vegetation returns at or above its roof height over 20% of its length,
  on the roof side, is not fitted: 46 of 525 walls on 14 of the 50 marked
  roofs (counting every vegetation return within reach stopped 192 of 365:
  garden shrubs). The tree rule is untested against a known overhang.
  Inner lines on the 50, surveyed outline / extended to the fitted edge:
  right at 0.3 m 28 / 24, at 0.75 m 33 / 33; the drop is seams carried
  through the eave strip, which the markup (outer edge = surveyed outline)
  does not have. Not in the build or the map.

- **Roof edge wall by wall from the returns (16 Nov, experiment; owner
  confirmed the markup's outer edge is the surveyed outline the tool gave,
  so the markup cannot judge roof edges).** `roof_edge.fit_edges`: the
  surveyed outline keeps its corners and directions, each wall of 1.5 m or
  more moves along its normal (1 m in to 2 m out) to where this roof's
  returns stop (last 0.4 m strip holding 0.4 of the roof's return density,
  then the 95th percentile of the returns in it, plus 0.1 m). 36 Stanley
  Street: 696 m2 against the 688 m2 drawn there by hand (surveyed outline
  561 m2). Walls on 1 Shotover Street move -1.0 to +0.9 m, on 15B Frankton
  Road -0.3 to +0.5 m. Inner lines on the 50 roofs, surveyed outline /
  fitted edge: right at 0.3 m 28 / 26, at 0.75 m 33 / 35
  (`docs/review/three_houses_16nov_roof_edge.jpg`). Agent's reading of
  that render: Stanley's edge and hips sit on the roof; Shotover's eaves
  are covered on the north and west but the east side gains a jog and a
  notch; Frankton gains a small extra face on the south side. A photo
  judge (strongest brightness change within 1.5 m of the leaned edge) was
  tried and dropped: on Stanley it locks on the gutter line 1 m inside the
  edge. Also in `photo_lean.py`: shared corners lean by one height (seams
  no longer bend), `absorb_slivers` (no effect on these three: the
  doubled lines on Shotover's south-west wing are the two sides of a step,
  which the lean separates as the photo does). Open on Shotover: lines
  (a)-(d) in the entry below.

- **1 Shotover Street line by line, and the roof edge (16 Nov).**
  `tools/experiments/line_by_line.py` (leaned point roof against the
  markup, 0.5 m pieces): 85% of the drawn line length has a built line
  within 0.3 m, 9% within 0.3-0.75 m, 6% none; of built lines 81 / 10 / 9%
  (`docs/review/three_houses_16nov_1_shotover_line_by_line.jpg`). The
  differences: (a) the long valley from the middle junction stops 1.5 m
  short of the top edge; (b) the small north-east gable has a built line
  along its lower side, where the returns are two planes, that the markup
  does not have; (c) the west side of the drawn box on the south-west wing
  is not built, the box's long sides run on to the roof edge instead; (d)
  line ends at the roof edge miss by up to 0.75 m; (e) leaning each face by
  its own plane bends a seam where two planes disagree at a shared corner:
  lean shared corners by one height. **Roof edge:** drawn edge within 0.3 m
  of the built edge on 44%. `tools/experiments/roof_edge.py` takes the edge
  from the returns of the planes that belong to the roof, straightened
  with `_regularise_machine_face`: 36 Stanley Street edge F at 0.3 m 0.00
  -> 0.74, area against drawn 0.81 -> 1.01, every hip and the top box on
  the markup (`..._36_stanley_lidar_edge.jpg`; what is left there is the
  gutter ring drawn 1 m inside the edge, not built). On the 50 marked
  roofs (`tools/experiments/roof_edge_check.py`) the edge from the returns
  scores under the surveyed outline (edge F at 0.3 m median 0.45 against
  0.69; lines unchanged, 27 against 28 roofs), but the comparison is not
  independent: the built-on-outline area is 1.00 of the drawn area at the
  median (p10 0.97), so on most marked roofs the markup's outer edge is the
  surveyed outline. The returns' edge is 1.01 of drawn at the median, p10
  0.86, p90 1.14: too ragged as it stands. Needs a judge for the edge that
  is not the markup (the photo's gradient across the leaned edge, or the
  owner's eye), then per-edge fitting.

- **Lean read off the photo, no markup: 5 -> 28 of 50 roofs right at 0.3 m
  (16 Nov).** `tools/experiments/photo_lean.py` (rule and measurement in its
  header): the built roof's seams and edge, each point moved by k times its
  height, are laid over the photo for every k within 0.4 m per metre, and
  the k where the photo's brightness changes across the lines is kept. End
  to end on the same 50 marked pilot roofs
  (`tools/experiments/photo_lean_check.py`; the markup scores, it does not
  place): line F at 0.3 m median 0.21 -> 0.73 (0.82 with the markup's own
  slide), roofs at F >= 0.7 5 -> 28 (33); at 0.75 m 24 -> 32. The three
  study houses, as placed / leaned: 1 Shotover Street 0.31 / 0.83, 15B
  Frankton Road 0.61 / 0.81, 36 Stanley Street 0.05 / 0.58
  (`docs/review/three_houses_16nov_leaned.jpg`). 36 Stanley Street is a
  truncated pyramid the point roof had right, drawn 2 m off; the note
  above calling it a miss is wrong. PRIOR and SIGMA_PX were picked on 36
  of these roofs; unmarked roofs and other towns are unmeasured. Left on
  the three (agent's reading of the render): the roof edge stops at the
  LINZ outline, 0.5-1 m inside the eave the photo shows (see Imagery,
  Eaves), and one sliver on 1 Shotover Street. Not in the build or the
  map. Next: roof edge from the returns; then the lean in the build's
  drawing step in place of `image_shift` where a roof has faces, for the
  map's photo.

- **The LiDAR roof is right far more often than scored; it is drawn off the
  photo (16 Nov).** 50 marked pilot roofs with 2-20 faces, none held back,
  `src/point_roof.py` against the markup's faces and strokes, line F at
  0.3 m (`tools/experiments/roof_photo_slide_from_markup.py`): median 0.21
  as built, 0.82 after one slide of the whole roof chosen per roof on the
  lines; roofs at F >= 0.7: 5 -> 33 of 50. The slide is a median 0.72 m,
  p90 1.42 m. `src/register_imagery.py` assumes the survey-year photo (the
  one the markup was drawn on) sits on the LiDAR; by this measure it does
  not. The slide is not predicted by neighbours: the median slide of the 6
  nearest marked roofs leaves 0.46 m (25% within 0.3 m), their lean per
  metre of height times the roof's own height 0.47 m (19%). Reading it off
  the photo with no markup, first attempt
  (`tools/experiments/roof_photo_slide_from_photo.py`: built seams and roof
  edge slid over the photo, keeping the place where the photo's gradient
  runs across them): median 0.34 m from the markup's slide, 42% within
  0.3 m, 58% within 0.5 m; the misses are jumps to another edge up to 3 m
  away. The per-roof slide is fitted on the markup, two numbers per roof,
  so 33 of 50 is an upper figure for an unmarked roof. Next: make the
  photo slide dependable (prefer the small slide, require a sharp peak,
  roof edge and seams agreeing), then draw the three study houses with it
  and check the map's own photo, which leans differently
  (`image_shift.json`). Likely the reason photo readings win blind picks:
  they are traced on the photo, so they sit on it.

- **Three houses first (16 Nov, owner's instruction): get 15B Frankton Road
  (4725546), 1 Shotover Street (4734696) and 36 Stanley Street (4735292)
  right before widening.** `src/point_roof.py` (planes grown on the returns,
  lines from plane intersections) against the markup's faces and strokes,
  drawn lines found / built lines real within 0.3 m
  (`tools/experiments/three_house_lean.py`, `three_house_view.py`):
  as compared until now 4734696 33 / 29%, 4725546 64 / 60%, 4735292 3 / 8%;
  after one slide of the whole roof chosen on the lines (0.25, -0.88 m;
  0.25, 0.50; 1.62, -1.25) 84 / 79%, 91 / 88%, 39 / 93%. A lean per metre of
  height fits as well (0.12-0.17 m/m). So on the first two the geometry is
  close and the comparison was misplaced: the markup is in the photo's
  frame, the build in the LiDAR's, and the census slide (best outline
  overlap) gave (0, 0) for both. `image_shift.json` holds (-0.2, 1.1) for
  4734696, the same slide with the sign turned, but (0.1, 0.0) for 4735292,
  a 12 m roof that needs 2 m. The 24 census roofs re-scored with the slide
  chosen on the lines (scratchpad `census/census_aligned.py`, after-fix
  build): found 52/64/72%, real 73/85/90% within 0.3/0.75/1.5 m (38/60/71
  and 57/84/90 before). Next: the same line-based slide in
  `tools/markup_benchmark`; check what the map draws for these three
  (lean applied or not); the remaining misses on the first two; why
  4735292 misses 61% (near-flat faces at 12 m). Render:
  `docs/review/three_houses_16nov_1_shotover_st.jpg` (its 3D row draws the
  red wireframe at the wrong height: view bug, not the roof).

- **The 24-roof yardstick under-counts, and where the real gap is (16 Nov).**
  The census (scratchpad `census/census.py`) and `tools/markup_benchmark._lines`
  score against the markup's closed `faces` only. The markup also holds
  open `lines` strokes (ridges, valleys): on the 24 census roofs 375 m of
  3,033 m of strokes lie on no face edge, so a built line on them counted
  as invented (4725546: 2 faces, 56 of 67 m of ridge strokes unscored;
  4735292 89 m; 5371183 99 m; 4726056 53 m). Counting the strokes
  (after-fix build): built lines that are real within 0.3/0.75/1.5 m
  57/84/90%; drawn lines found 38/60/71%. So invention is small; the gap is
  lines missed (29% of drawn length has nothing within 1.5 m) and placement
  (half the lines found are 0.3-1.5 m off). LiDAR check
  (`tools/experiments/line_evidence.py`: plane fitted to the returns within
  1.5 m each side of a line): 78% of drawn line samples show a fold over 8
  degrees or a step over 0.3 m; on 5372585, 5371176 and 4725546 the lines
  scored as invented show folds of 29, 19 and 22 degrees on 99-100% of
  samples. Render: `docs/review/census_16nov_lines_scored_invented.jpg`.
  To do: score strokes in `markup_benchmark`; a label-free score from the
  LiDAR (line on a fold or step, face on one plane, fold with no line) run
  on every roof; test whether placement error grows with height above the
  eave (photo lean is per metre of height, the census aligns outlines only).
- **Free model retrain on 714 roofs: worse, not adopted (16 Nov).** 146 new
  readings added to the 568: held-out line F / lines right / face IoU /
  faces right 0.528 / 16 / 0.643 / 10 against 0.626 / 21 / 0.697 / 13 for
  `face_regions_v3` (re-scored the same day, reproduced). Second run of
  each: 714 roofs 0.507 / 18 / 0.648 / 7; 568 roofs 0.559 / 18 / 0.655 /
  13. So one data set moves 0.07 in line F between runs, v3 was the better
  of two draws, and 146 more readings did not help (both 714 runs sit
  under both 568 runs). Parked: the three-house study comes first, and
  these scores use the outline slide shown above to be misplaced.

- **Reader readings for 234 more pilot homes, and why they hold fewer panels
  (16 Nov).** 299 pilot homes the free model had drawn were read by the paid
  reader (60 in session, 237 by batch, US$15.13); 234 rated 0.4 or over are
  in `data/vision_faces/` on the Mac, **not yet on the VM** (copy after the
  running build, then a patch build). Built both ways on the Mac
  (`tools/layout_ab.py`): free model 8,634 panels, reader 7,343. The split:
  reader lines without reader obstructions 8,137 (-6%, the traced roof edge
  holds 6% less roof area: 43,238 -> 40,571 m2); reader obstructions take
  the other 10% (obstructions on these roofs 735 -> 1,523). The reader's
  obstructions were scored against hand-drawn ones on the 15 census roofs
  that have both: 113 of 154 touch a drawn obstruction, median size 0.92 of
  the drawn one, total area 372 m2 against 628 m2 drawn. So they are
  mostly real and not oversized; the earlier count placed panels over
  skylights and vents the LiDAR detector does not see. Renders:
  `docs/review/reader_vs_free_model_16nov_random.jpg` (12 at random) and
  `..._most_panels_lost.jpg`. Open: (a) `vision_roof.ingest` drops a traced
  part more than 3 m from the rest as "another building" even when it lies
  inside the building's own outline (4750940, 4740468: 2 of 234, 43% of
  each roof lost) -- keep a part that sits inside the surveyed outline;
  (b) reader obstruction positions overlap the drawn ones by only 174 of
  372 m2, so placement is a few pixels off; (c) not judged by the owner yet.

- **Photo-first reading wired into the layout (14 Nov, behind
  `SOLAR_VISION_ROOF=1`, off; branch `point-roof`; awaiting the owner's
  blind picks).** `src/vision_roof.py`: `prep` writes gridded crops, a
  reader (PROMPT) traces faces, obstructions and not-roof areas, `ingest`
  turns a reading into `data/vision_faces/<id>.json`. The reading leads
  every machine reading (`roof_partition.selected_doc`), its traced roof
  edge replaces the surveyed outline (`outline_fit.fitted_outlines`), its
  obstructions are added to the detector's (`build_layout_geojson`), and it
  is slid onto the LiDAR for pitch; hand markup still wins. Run on the 24
  noted houses with readers in the session: 19 readings used, 5 rated under
  0.4 fell back to point faces. The 0.5 bar from the benchmark test did not
  carry over: this run's prompt asked for an honest confidence and the
  ratings came out about 0.1 lower, so 0.4 was used; the bar needs setting
  against markup with the final prompt. Blind per-aspect page against
  production, each drawn where the map would draw it:
  https://claude.ai/artifact/L5GMXXbg9tANuwgZCC7rV6 (key
  `data/blind_check/answer_key_14nov_photo_first_houses.json`; the agent
  called photo-first better on lines on 17, same on 6, production on 1).
  **Owner's blind picks (14 Nov,
  `data/blind_check/owner_14nov_photo_first_houses.json`), photo-first /
  production / same of 24: roof lines 20 / 3 / 1, obstructions 16 / 3 / 5,
  panel layout 21 / 3 / 0** (point faces against production on the same
  houses, 13 Nov: 12 / 8 / 4, 3 / 4 / 13, 8 / 9 / 7). On the 19 houses built
  from a photo reading: lines 16 / 2 / 1, panels 17 / 2. Production won all
  three on 4740401 and 5371209. The 5 point-face fallbacks also won 4 on
  lines, where they had not on 13 Nov: the only change for them is the
  drawing moved back onto the photo. The agent's lines call matched the
  owner on 15 of 24. This says better than production, not right: the
  count of roofs with nothing wrong (2 of 24 for point faces) is not yet
  measured for photo-first, and these 24 houses were the development set.
  **Fresh houses (14 Nov, awaiting picks):** 24 random pilot `home`
  outlines over 60 m2, none used before, same prompt and 0.4 bar: 16 photo
  readings, 8 fell back to point faces. Page
  https://claude.ai/artifact/RqdBtssi2zt7n8TVgVMvMA, key
  `data/blind_check/answer_key_14nov_photo_first_fresh.json`; the agent
  called lines photo-first 10, same 13, production 1. Seen in the renders:
  4750932 takes no panels on its main roof under the photo reading.
  **Owner's picks on the fresh 24
  (`data/blind_check/owner_14nov_photo_first_fresh.json`), photo-first /
  production / same: roof lines 20 / 0 / 4, obstructions 16 / 0 / 7 (1
  unsure), panel layout 16 / 0 / 7 (1 unsure).** On the 16 built from a
  photo reading: lines 16 of 16, obstructions and panels 15 of 16. The 8
  point-face fallbacks: lines 4 better, 4 same; six of them noted "both
  terrible" or "both bad" — the unread photos are the open gap. Also noted:
  panels not lined up with each other across faces (4725495), empty space
  left without panels (4750932). Agent's lines call matched 12 of 24.
  **Which reader, and what it costs (14 Nov, the same 24 hand-drawn dev
  roofs, line F / lines right of 24):** through the API
  (`src/vision_roof.py read`, effort high): `claude-fable-5-2` 0.727 / 16 at
  $0.335 a roof (4.7k tokens in, 7.4k out); `claude-opus-5-5` 0.563 / 13 at
  $0.062. As agents in the session: Fable 0.664 / 15, Sonnet 0.487 / 11,
  Haiku 0.489 / 9. Production 0.575 / 11, point faces 0.621 / 11. Only the
  top model reads roofs better than production; the Batch API halves its
  price to about $0.17 a roof. Spent on this test: $9.64.
  **Does the small model want more labels?** `tools/train_face_regions.py`
  from scratch, 80 epochs, on 24 / 48 / 96 hand-drawn roofs, same 35 held
  out: boundary F1 0.146 / 0.216 / 0.246. Still rising, slowly, from a low
  base: a from-scratch U-Net is not the student.
  **Open-model survey (agent web research, 14 Nov, not verified here):** no
  published SAM fine-tune or open weights for roof planes at this
  resolution; the best published RGB-only roof-plane result is AP50 0.57
  (DINOv3 backbone, 2.1M Dutch 3DBAG masks). Suggested student: a pretrained
  DINOv2/DINOv3 backbone with instance and boundary heads and a LiDAR side
  branch, pretrained on free Dutch 3DBAG roof planes with 8 cm photos, then
  fine-tuned on a few thousand teacher labels; estimated 30-60 GPU hours.
  **A pretrained encoder lifts the student (14 Nov, scratchpad experiment,
  not in the repo yet).** Same data, loss, metric and 35 held-out roofs as
  `tools/train_face_regions.py`; ResNet-18 (ImageNet weights already on
  the Mac) as the photo encoder at 512 px, U-Net decoder, LiDAR channels in
  a side branch, 60 epochs: held-out boundary F1 0.413 on 96 hand-drawn
  roofs (0.349 on 24, 0.313 on 48: noisy at these sizes), against 0.280
  for the same network without pretrained weights and 0.246 for the old
  U-Net. Photo only: 0.400, so the LiDAR branch adds little as built.
  **500 pilot houses sent to the teacher (14 Nov):** `vision_roof.py
  batch-submit`, `claude-fable-5-2`, estimate $85 (batches
  msgbatch_01X5MiLBYLx8Dv2HKMp84mGA, msgbatch_019WPqbxFVTcvYQEtE2mJent).
  Result: 492 readings for $84.98; 318 rated 0.5 or over became training
  samples (photo-frame faces through `export_face_regions.roof_sample`).
  **Student scored as roof faces on the 35 held-out hand-drawn pilot roofs
  (maps -> `face_regions.learned_faces` -> benchmark arithmetic), line F /
  lines right / face IoU:** hand 96 only 0.434 / 12 / 0.613; teacher 318
  only 0.528 / 16 / 0.707; hand 96 + teacher 318 0.534 / 18 / 0.677.
  Production on the same roofs 0.562 / 18 / 0.687, point faces 0.609 / 19 /
  0.720. Teacher labels train the student as well as hand labels do, and
  318 of them moved it 0.10; it is still under production. 30 minutes of
  training on the Mac. Not yet tried: more labels (the pilot has only ~630
  houses; other towns' photos are on the VM), a stronger open backbone
  (DINOv2/DINOv3 need a download), the Dutch 3DBAG pretraining, the 174
  lower-confidence readings. Spend to date on this work: $94.62.
  **Direction channels fixed in augmentation, all paid labels used (14 Nov,
  no further spend).** `tools/train_face_regions.dihedral` flips and rotates
  the pixels but leaves the LiDAR downhill-direction channels (5, 6)
  pointing the old way, so 7 of 8 augmented copies teach a wrong slope
  direction. Fix (scratchpad `curve/pre.py`, mode `prefix`; not yet in the
  repo trainer): a flip sets c5 = 1 - c5, each quarter turn sets
  (c5, c6) = (1 - c6, c5). With the fix, same 35 held-out roofs, line F /
  lines right / face IoU / faces right: hand 96 + teacher 318 0.546 / 18 /
  0.662 / 11; hand 96 + 461 teacher readings rated 0.4 or over + session
  readings (568) **0.626 / 21 / 0.697 / 13**. That is above production
  (0.562 / 18 / 0.687) and point faces (0.609 / 19 / 0.720) on lines, between
  them on face IoU. Boundary F1 on the maps did not move (0.438, 0.430), so
  map F1 is a poor guide to the polygons. One training run each, 35 roofs:
  a difference of 3 roofs is within what a rerun could move; the owner's
  blind page decides. 40 minutes of training on the Mac, free to run per
  roof.
  **Blind page out, awaiting the owner (14 Nov):** 24 pilot homes the
  student never saw (131 unseen homes, seed 14), production against the
  student's faces through the vision-roof path (union of faces as roof edge,
  slid onto the LiDAR, no obstructions from the student; trial readings read
  through `SOLAR_VISION_FACES_DIR`). https://claude.ai/artifact/7w1gfP3rhP3vQUyajZ8hJq,
  key `data/blind_check/answer_key_14nov_free_model_houses.json`. Agent's
  line calls recorded first (`source_14nov_free_model_houses.json`): student
  14, same 9, production 1. Layout health on the 24, production -> student:
  panels 920 -> 875, clean roofs 50% -> 25% (12.6% of student panels count
  as off the building because the health check measures against the
  surveyed outline and the reading's roof edge is slid onto the LiDAR; an
  earlier note here had the two columns swapped). Student lines are raster-traced and wobble; they
  are not yet straightened or snapped to plane intersections. The student
  lives in the scratchpad (`curve/pre.py`, `pre_prefix_568.pt`); it moves
  into `tools/train_face_regions.py` and `src/face_regions.py` if the owner
  prefers it. DINOv2 ViT-S student: stopped unscored. At 644 px on the Mac
  it had not finished 5 of 40 epochs in 4 hours; it needs a GPU.
  **Owner's blind picks (14 Nov,
  `data/blind_check/owner_14nov_free_model_houses.json`), student /
  production / same: lines 13 / 6 / 5, obstructions 12 / 6 / 6, panels
  13 / 6 / 5.** The agent's line calls matched 13 of 24. For comparison the
  paid reader on its own fresh page won lines 20 / 0 / 4. So the free
  student is ahead of production but well short of its teacher, and the
  owner's notes say "not great" or "terrible" on 9 of the 24, including
  houses the student won. Production won 4725572, 4735022, 4735041, 4735396,
  5370893, 5371131: on the renders (agent's reading, not a measurement) the
  student's lines there wobble, or it split a plain face or merged two.
  More teacher labels are the paid route to close the gap to the reader.
  **Student in the build (14 Nov, uncommitted on `point-roof`).**
  `tools/train_face_regions.py`: `EncoderNet` and the direction-channel fix
  in `dihedral` (the training loop for EncoderNet is still the scratchpad
  `curve/pre.py`). `src/face_regions.py`: loads
  `data/models/face_regions_v3.pt` (57 MB, untracked; not yet on the VM or
  in a bucket), `straight_faces` (header there: 35 held-out drawn roofs,
  raster 0.626 / 21 / 0.697 / 13, straight 0.619 / 20 / 0.716 / 16, so
  neutral on lines and better on faces; a loose plane-intersection rule
  scored 0.560 and averaging over symmetries 0.553, both rejected).
  `tools/student_faces.py <region>` reads every home (zone class home,
  unmarked, no vision reading; 2.2 core-seconds a roof) into
  `data/student_faces/`. `SOLAR_VISION_ROOF` now defaults on: a vision
  reading, else a student reading, leads `selected_doc`, the roof edge and
  the build key, and `register_imagery.drawing_shifts` moves such a roof's
  drawing back by its own recorded slide. The 318 paid readings rated 0.5 or
  over are in `data/vision_faces/` on this Mac. Fast tests pass. Not judged:
  the straightened faces by the owner; any town but central Queenstown;
  non-homes (left on production).
  **District rebuild launched (14 Nov, 10:40 UTC):** the owner raised the
  month's VM cap from 120 to 150 hours (`data/vm_budget.json` on branch
  `vm-spending-limits`, not merged to main; 131 used at the start). Estimate
  about 15 h, NZ$15 (10.7 h re-lay measured for v72, readings, margin).
  `point-roof` pushed (bec030ce) and synced to the VM; `face_regions_v3.pt`
  and the 329 files of `data/vision_faces/` copied up. On the VM
  `/tmp/chain.sh` (log `~/solar-map/photo_roof_chain.out`) runs
  `tools/student_faces.py` on every served region, then
  `run_district_build.sh --force --regions "<data/out less akl_*>"`.
  **First launch failed (3.3 VM hours):** the VM's checkout had
  `src/face_rectify.py` deleted in its working tree, so
  `build_layout_geojson` failed at import in every region; the build stopped
  before the fan-in and nothing was combined. The readings finished (7,289
  student files). File restored with `git checkout`, build relaunched at
  13:58 UTC (`/tmp/chain2.sh`, log `photo_roof_chain2.out`). Check
  `git status` on the VM for deleted tracked files before a build.
  **Student coverage gaps seen in that run:** every home fails with "need at
  least one array to concatenate" in arrowtown_hills, glenorchy,
  glenorchy_north, kingston and kinloch, and many in arrowtown_east (176),
  wanaka_albert_town (121), hawea (67) and wanaka_town (85): cause not
  traced (no point-cloud returns for the roof is the likely one). Cromwell
  and Bannockburn have no buildings zoned "home", so none are read. All of
  these stay on the selected faces.
  **Build finished, gate failed, not published (15 Nov 00:31 UTC):** 35
  regions, 32,838 buildings, 1,236,935 panels (live v78: 1,256,226, -1.5%).
  Gate against v78: 21 roofs zeroed, 165 big drops, 3 newly withheld, 34 of
  38 case roofs changed. Of the zeroed, 19 use a student reading; of the big
  drops, 115 student, 19 vision, 31 neither (7,289 student and 329 vision
  readings in the build). Renders of all 21 zeroed and 30 of the drops:
  `docs/review/gate_15nov_zeroed.jpg`, `gate_15nov_drops_sample.jpg`. Agent's
  reading, not a measurement: several zeroed roofs are plain, well-lit
  houses with faces drawn and no panels (5374704, 4735665, 4720927); on
  others the student's faces sit off the building (4738118, 4735619,
  5372972) or are missing (4720448, 4720457); a share of the drops show the
  same off-building faces (4725815, 4727777, 4736995). Cause not traced: the
  recorded slide (`shift`) is the first suspect. The student reading scores
  1.0 always, so nothing competes with a bad one. The VM's served set is
  pulled into `data/` on this Mac: the whole build is viewable at
  `http://localhost:8000/solar-map/rooftop-solar.html?localdata=1`. VM
  stopped at 00:58 UTC after 14.4 h this start; month at about 145.5 of 150
  h.
  **Owner: fix first (15 Nov); a panel drop is not in itself bad, some
  roofs carried too many.** Three causes traced, three rules in
  `tools/student_faces.py` (no `src/` change, so only roofs whose reading
  changes rebuild):
  (1) the reading was slid by `point_roof.register`, which under trees slid
  it off the building (4735042, 4735619: a face over the wall at 57-67
  degrees); it now takes the build's accepted outline-fit slide. Pilot: 71
  of 368 homes change, renders in
  `docs/review/slide_fix_15nov_pilot_movers.jpg`; several remaining drops
  are south faces at 26-36 degrees that rightly hold no panels.
  (2) a reading gives way when its plane-fit share is over 0.20 below the
  incumbent reading's (68 of 368 pilot homes; on the owner's 24 blind homes
  5: 2 production wins, 2 same, 1 model win).
  (3) no reading under 1 building-class return per m2 (three zeroed Wanaka
  homes held 0.03-0.46; Kinloch has none at all, so it is not read).
  Month's cap raised to 165 h on the owner's word. Re-read and incremental
  build launched 01:42 UTC (`/tmp/chain3.sh`, log
  `photo_roof_chain3.out`); the 15 Nov layouts are kept in
  `~/v79a_layouts` on the VM for before/after renders. Still
  to do: `tools/deploy_from_vm.sh`, render the gate's list, `--push`, stop
  the VM, record the hours.
  **Second gate, 15 Nov 09:10 UTC: failed again, not published; live is
  still v78.** Build finished 08:50 UTC (10 regions clean, 12 patched,
  13 full): 32,838 buildings, 1,238,166 panels (v78 1,256,226, -1.4%);
  5,658 student readings kept (7,289 before the three rules). Gate:
  11 zeroed (was 21; 9 student, 2 no reading), 120 big drops (was 165;
  69 student, 19 vision, 32 no reading; 110 of the 120 are the same roofs
  as the first gate), 1 newly withheld (4721200, a glasshouse). So the three
  rules fixed the zeroed roofs they targeted but barely touched the drops.
  Renders against `~/v68_layouts` (the nearest saved layouts to v78; no v78
  layouts were kept): `docs/review/gate2_15nov_zeroed.jpg` (all 11) and
  `docs/review/gate2_15nov_drops_sample.jpg` (every fifth drop, 24). Not
  yet judged by the owner. Unmeasured reading of the sheets: several zeroed
  roofs are overgrown or a glasshouse, but 4720927 (plain hip garage),
  4734693, 5371115 lose panels a plain roof should hold; many drops keep
  the same faces and place fewer panels on them (4742404 52->22, 4735028
  44->21, 4733218 48->26), cause not yet found; 32 drops have no photo
  reading at all, so something else on this branch changed them since v78.
  **Published as v79 on the owner's word (15 Nov), gate failed and
  reviewed;** `site-config.js` on main points at v79 (commit 24c44ff3), roll
  back by pointing it at v78. Main does not yet hold the code that built
  v79 (branch `point-roof`).
  **Where the lost panels went (measured on this Mac, 15 Nov).** On the 120
  flagged roofs the face area is unchanged from v78 (median ratio 1.00) but
  panels cover a median 27% of it against 44%. 19 flagged pilot roofs with
  a paid reading, rebuilt three ways (`tools/layout_ab.py`): no reading
  1,310 panels; the reading's faces without its obstructions 1,010; the
  full reading 749. So about half the loss is the reading's face shapes and
  half the obstructions it names (`vision_obstructions`, which the LiDAR
  checks never test). Two mechanisms seen in renders, not yet judged by the
  owner: (a) v78 packs rows to within 0.11-0.15 m of the face edge, so a
  ridge drawn 0.4 m off drops a whole row (4735028: a 4.0 m face became
  3.59 m, 22 -> 11); (b) one small obstruction mid-face leaves the far half
  of the face empty (4735028 other face 22 -> 10; 4735398 three named
  strips, 35 -> 21, and the photo shows nothing under two of them). Not the
  cause: height-step splitting (SOLAR_STEP_SPLIT 0 and 1 give the same
  counts on four no-reading roofs). Still unexplained: the 32 drops with no
  reading; 10 of them carry hand markup, and 4725599 builds 199 here but
  132 on the VM. Next: decide whether photo-named obstructions need a LiDAR
  or size check, and why the fitter abandons the far side of an obstruction.
  **Owner's verdict on v79 (16 Nov): panel placement is still not an order
  of magnitude better.** Examples: 9 Melbourne St (4725494) has a staggered
  array and an empty far side; 7 Melbourne St has staggered panels on its
  sawtooth bays ("solar installers would never install staggered like
  this"). Traced on 4725494: the 183 m2 face carries 15 obstructions
  totalling 62 m2 and the grid keeps every free slot around them, hence the
  stagger (55 panels on v78, 38 now); the 133 m2 south-east face (25 deg,
  aspect 133) is emptied by `away_from_sun` at 0.74 of the roof's best 0.96,
  as it was on v78. Also measured: on 329 paid-reading roofs the drawn seam
  sits a median 0.35 m from where the two LiDAR planes meet (343 of 922
  seams over 0.5 m). The two small fitter fixes were stopped before any
  code changed; a plan for panel placement was put to the owner instead
  (array-first placement, rows on each face's own eave, obstructions held
  to a measured standard, seams on plane intersections, and a benchmark of
  owner-drawn layouts to iterate against on this Mac).
  **Error census on the drawn roofs (16 Nov, this Mac, pilot only: the
  other regions hold no imagery here; held-back roofs excluded).** Each
  drawn roof built blind (SOLAR_HIDE_LABELS=1) and sorted by what is wrong;
  "right" is line F >= 0.7 at 0.75 m and face IoU >= 0.8.
  LiDAR path alone, 70 roofs: 19 right; 20 have the right lines placed
  0.3-1.5 m off; 13 invent lines; 6 miss lines; 12 mixed or other. 35% of
  its line length matches no drawn line within 1.5 m. By size: 1-2 faces 9
  of 25 right, 3-4 faces 6 of 12, 5-8 faces 3 of 17, 9+ faces 1 of 16.
  Paid reader on the 24 it read on 14 Nov, same roofs three ways: the
  reading as read 10 right (lines within 0.3 / 0.75 / 1.5 m: 45 / 68 / 78%;
  87% of its lines real); the LiDAR path 6 right; **the same readings
  after the build 4 right** (35 / 58 / 71%). Of the 10 read right, 6 come
  out of the build worse: 4735378 and 5371118 are two-face roofs whose
  straight ridge (line F 1.0 and 0.89) is rebuilt as a zig-zag (0.03 and
  0.41); 5370320 goes from 2 faces to 5. Renders:
  `docs/review/census_16nov_reader_as_read.jpg`,
  `docs/review/census_16nov_reader_after_build.jpg`. Caveats: the markup
  was traced on a leaning photo, so some of the 0.3 m misses are the
  ruler; the free model was trained on these roofs so it cannot be scored
  on them; these 70 are mostly central-town buildings, not houses. The paid
  reader could not be run on the other 46: the API key's credit is spent
  (no charge made). Next: find the build stage that bends a reading's
  seams and let a reading through intact; then the reader on all 70.
  **Readings now pass through the build intact (16 Nov, code on
  `point-roof`, not yet in a district build).** Three stages were damaging
  them, each traced on the six roofs above. (1) The LiDAR reshape stages in
  `build_layout_geojson` (`split_steps`, `split_lower_levels`, then
  `merge_coplanar_faces`) cut a face where the returns step and merged the
  piece into the face across the ridge: 4735378's two 52 m2 faces left as
  63 and 40 m2. A roof holding reading faces now skips them, as a plain
  form does; planes are still refreshed. (2) The like-plane merge at the end
  of `_attach_building_geometry` merged faces the reader drew apart
  (5371137: 20 faces read, 13 left). Reading faces now stand through it as
  drawn faces do. (3) The trimmed plane fit starts from least squares, so
  plant or a tree over a third of a flat face tilts it and the face is
  thrown away as "not one plane" and re-cut (5370320: 9 deg, 23% on plane,
  where a 1 deg plane holds 64% and the rest stand above). A reading face
  that fails is retried on the plane most returns lie on, and kept when
  under 15% of returns fall below it (`_dominant_plane`); a missed ridge
  still fails (4735104: 41% below). `SOLAR_KEEP_READINGS=0` turns (1) and
  (2) off. Same 24 roofs, built blind: **4 right before, 9 after; the
  readings as read score 10.** Lines within 0.3 / 0.75 / 1.5 m: 35 / 58 /
  71% before, 42 / 67 / 78% after (as read 45 / 68 / 78%). Panels on the 24:
  2,413 to 2,598. The one still lost is 4735104, where the reader missed a
  ridge and residual fill places it badly. Renders:
  `docs/review/readings_16nov_before_fix.jpg`,
  `docs/review/readings_16nov_after_fix.jpg`. Not measured: the 5,658
  student-read homes on the live map (trained on these roofs, so no fair
  score here; needs a district build and a blind check), and whether the
  student's readings deserve the same trust as the paid reader's.
  Tests: fast suite passes. The full suite fails on this branch with or
  without this change, identically (28 golden buildings and the synthetic
  region, 606 -> 415 panels): the references predate the branch's
  panel-fitting and reading work and have not been re-recorded. To do:
  re-record on the VM once the branch's layouts are accepted.
  **District build with the fix launched (15 Nov 23:12 UTC = 16 Nov midday
  NZ)** on the owner's word. VM started 23:09 UTC through `vm_start.sh`
  (about 153.4 of 165 h used before). `point-roof` at cc9334eb synced. Every
  changed line acts only on faces marked `from_reading`, so
  `tools/rekey_unaffected.py --from-hash 0cd1aeb44b88 --apply` carried the
  build keys of the 26,851 buildings with no photo reading to the new code
  hash (bfbc016ffcd4); the 5,987 with a reading rebuild. Keys before the
  carry are in `~/v79b_keys` on the VM; the published layouts in
  `~/v79b_layouts`. `run_district_build.sh --regions "<data/out less
  akl_*>"`, log `~/readings_fix_build.out`. Regions over a tenth stale build
  in full (about 20,700 buildings), so the estimate is 6-8 h. Still to do:
  the deploy gate against v79, renders of what it flags, the owner's word
  before any publish, stop the VM, record the hours.
  **Reader extended to the pilot homes the student drew (16 Nov, in
  progress).** The owner's blind picks on fresh houses put the reader's
  lines ahead of production on 20 of 24 and the student's on 13 of 24, so
  the student's homes are being re-read by the reader, run as agents in the
  session (no API charge; about 19k tokens a roof, six roofs to an agent).
  First 60 of the 299 pilot homes holding a student reading: 45 rated 0.4 or
  over and ingested, 15 under and left on the student. Built both ways
  (student -> reader): panels on the 45, 1,535 -> 1,353; sample render
  `docs/review/reader_vs_free_model_16nov_sample.jpg` (agent's reading, not
  a measurement: the reader's lines are cleaner on most, and it finds
  ridges and hips the student misses; some of its obstruction outlines are
  too large). The other 239 are being read in waves. Readings are in the
  scratchpad (`teach1/`, `teach2/`) until all are in; then into
  `data/vision_faces/`, up to the VM, and a patch build. Tried and dropped
  the same day: moving a reading's seams onto the LiDAR plane crossings
  (`seams_to_plane_lines` on the 24 census roofs: lines within 0.3 / 0.75 /
  1.5 m unchanged at 42 / 67 / 78%).
  New layouts kept in `~/v79b_layouts` on the VM. VM stopped 09:29 UTC:
  7.9 h this start, about 153.4 of the month's 165.
  **Pilot built locally for a preview (14 Nov):** every stage of the plan
  run through `src/run_stage.py`, then `emit_region` and `combine_regions`
  (23 minutes; `tools/pipeline.py` stops at preflight here because the GDAL
  command-line tools are not installed on this Mac). 318 homes from vision
  readings, 368 from the student, the rest as before. View with the
  `solar-preview` server at
  `http://localhost:8000/solar-map/rooftop-solar.html?localdata=1`: on a
  local server `?localdata=1` reads the data beside the page instead of the
  published release (`rooftop-solar.html`, DATA_BASE). The other 23 regions
  in that local set are older local copies, not the live v78.
  **Cost rules out a reader call per building** (owner, 14 Nov: millions of
  buildings nationally; roughly 5-10 cents each, unchecked against the
  price list). Direction: the reader labels a few thousand roofs once and a
  small local model is trained on those labels and run free on the VM;
  whether the small model keeps the reader's quality is untested.
  Not done: lines snapped to LiDAR plane intersections; `emit_region` moving
  a vision roof's drawing back by its stored slide (the review renders do);
  a reader that calls the API instead of session agents (paid; estimate
  before any district run); other towns. Fast tests pass.
- **Roof read from the photo by a vision model (14 Nov, first test).** Owner's pinned notes on the 24 point-roof houses
  (`data/blind_check/owner_notes_13nov_point_roof_houses.json`): 2 of 24
  right; wrong roof lines on 15, an obstruction missed or a panel over one
  on 12, panels over empty space, a balcony, a valley or a ridge on 9, the
  outline off the photo on 8. Two causes found for the outline: the review
  page drew the layout in the LiDAR frame without moving it back onto the
  photo (emit does this from `image_shift.json`; `tools/layout_ab.py` output
  does not), and `point_roof.register` slid 4734808 1.5 m where the measured
  lean is 0.7 m. Test of the photo-first route: a Claude vision reader traced
  24 hand-drawn pilot dev roofs from a gridded photo crop alone (never shown
  the markup), scored with the benchmark's arithmetic: line F 0.664 (lines
  right 15) against point faces 0.621 (11) and production 0.575 (11); face
  IoU 0.655 / 0.665 / 0.644. The reader's own confidence separates its
  results: at 0.5 or over (15 roofs) line F 0.81, 11 right; under 0.5 (9
  roofs: dark, blurred, shadowed or overexposed photos) 0.41, where point
  faces score 0.56. Vision where confident and point faces elsewhere: 0.72,
  14 right. One untuned prompt, one read per roof, no snapping to LiDAR or
  image edges. Next: snap traced lines to the LiDAR plane intersections and
  take pitch from the returns inside each traced face; use the reader's
  obstructions and not-roof areas; wire as a reading and judge blind on
  houses. A district-wide run is a paid API cost to confirm first.

- **Faces straight from the raw LiDAR points (13 Nov, behind
  `SOLAR_POINT_ROOF=1`, off; branch `point-roof`).** `src/point_roof.py`:
  slide the outline onto the roof-height returns; grow planes on the returns
  (not the rasterised grid); roof lines are the planes' exact intersections
  plus fitted step lines; cut the outline by every line; one graph cut gives
  each cell a plane or "no roof here"; flat planes that meet without a step
  are one deck. No trained model, no photo, about 0.3 s a roof for the
  faces. `python src/point_roof.py <region> --ids ...` writes
  `data/point_faces/<id>.json` (selected-faces schema plus the slide);
  `roof_partition.selected_doc` reads it first and
  `outline_fit.fitted_outlines` applies its slide. The survey supports it:
  9 returns/m2, about 2 cm of noise across a plane. Blind on the 70 drawn
  pilot roofs through `tools/layout_ab.py`, production -> switch on: line F
  0.584 -> 0.614, lines right 33 -> 36, faces right 25 -> 24, face IoU
  0.651 -> 0.674; by roof, line F better on 21, worse on 21. Layout of the
  70: 335 s -> 87 s. A wash on the benchmark. Its smoothness weight was
  picked on these 70, and production's detectors trained on many of them;
  other towns and the held-back 30 are unscored (their point clouds are on
  the VM). Blind side-by-side for the owner, the 30 roofs whose seams differ
  most: https://claude.ai/artifact/JZUYsLVV85GS4yQuX95t8b (answers in its
  `answers` collection; key and the agent's calls, recorded first, in
  `data/blind_check/answer_key_13nov_point_roof.json`: points 19,
  production 5, same 6).
  Owner's verdict (13 Nov, `data/blind_check/owner_13nov_point_roof.json`):
  points better on 14 (2 of them from the scores, no pick made), production
  on 3, same on 12, unsure 1. Mean score out of 10 on the 24 roofs scored
  both ways: production 2.3, points 4.1; points scored higher on 16, lower
  on 1, equal on 7. The agent agreed on 13 of the 17 decided. On r30
  (5371108) the pick says production and the scores say points (3 vs 5).
  Fault tags, production / points, of 24: obstructions 14 / 10, roof lines
  14 / 8, panels across a ridge 3 / 5, outline 3 / 3, over empty area 2 / 0.
  So the owner prefers the point faces where the benchmark called a wash
  (line F better on 21, worse on 21), and at 4.1 out of 10 they are still
  far from good: obstructions are now the most common fault, then lines.
  Second page, as the owner asked (the first had too many commercial roofs,
  and one overall pick was hard where one drawing had the better panels and
  the other the better lines): 24 random pilot houses (zone class home,
  70-350 m2, seed 1311), one pick each for roof lines, obstructions and
  panel layout (`tools/review_page.py --aspects`):
  https://claude.ai/artifact/A38LDAsCTByYVNhBz5xXni (key
  `data/blind_check/answer_key_13nov_point_roof_houses.json`; picks in
  `owner_13nov_point_roof_houses.json`). Owner's picks, points / production
  / same: roof lines 12 / 8 / 4; obstructions 3 / 4 / 13 (4 can't tell);
  panel layout 8 / 9 / 7. On houses the point faces have a small edge on
  lines and none on panels. The agent called 20 of the 24 "same" from a
  contact sheet and matched the owner's lines pick on 6: thumbnails do not
  show what the owner sees.
  A pick does not say what is wrong, so the same 24 houses are on a page
  where the owner pins the fault on the point build's drawing and describes
  it (`tools/annotate_page.py`, pins as image fractions, map bounds in
  `data/blind_check/notes_key_13nov_point_roof_houses.json`):
  https://claude.ai/artifact/DbWrnP9P7qBQfBpUux872M (`notes` collection).
  The 8 houses where production's lines won come first. Awaiting notes.
- **The benchmark's truth drops drawn lines (13 Nov).** `markup_benchmark`
  takes the roof's lines from the label's derived faces, and a drawn line
  that closes no face is not in them. Over 150 drawn roofs (held-back
  excluded) 22% of drawn line length is missing from the truth; 45 roofs
  lose over 20%, 17 over half (15B Frankton Rd 4725546: 10 lines drawn, 2
  faces). A build that finds those lines is scored down. Score against the
  lines as drawn as well (`evalrecon.py` `dl_f`: production 0.620, prototype
  0.671 on the 70), and repair face derivation for open-ended lines.

- **Faces messier than real roofs (11 Nov, five Wakatipu Heights houses).**
  All five readings scored under the 0.60 trust line (four ended on the
  LiDAR path, one on SAM), so faces follow DSM pixels: jagged edges,
  slivers, ridges off the walls, where the photo shows plain gables and
  hips. Benchmark, 178 drawn roofs (lines right = line F >= 0.7, faces
  right = face IoU >= 0.8):

  | Arm | line F | lines right | faces right |
  | --- | --- | --- | --- |
  | production | 0.573 | 79 | 43 |
  | simple form where confident | 0.575 | 79 | 44 |
  | simple form forced everywhere (`SOLAR_HYP_FORCE=1`, trust 0) | 0.504 | 52 | 36 |

  Straight seams (`src/face_rectify.py`, 11 Nov, off: SOLAR_RECTIFY_FACES=1).
  Stage probe on the five: the faces leave segmentation already traced,
  `fit_seams` multiplies their vertices (36 -> 528 on 4750978) and
  `straighten_faces` refuses three of the five outright. The new last stage
  paints the faces on a 0.1 m grid, fits each shared seam with straight
  lines (split only at a real bend, snapped to the walls or 45 deg),
  re-cuts the roof along them, and keeps the result only if every face
  stays near its area and the LiDAR fits no worse. It straightens two of
  the five (4751237: 55 -> 27 vertices, one straight ridge); on the other
  three the faces themselves are wrong and it refuses. Benchmark, same
  178 roofs, off -> on: line F 0.590 -> 0.591, lines right 82 -> 82, faces
  right 43 -> 42; it changed 104 roofs and cut their vertices 29% (5,865
  -> 4,149). Line F counts a seam within 0.75 m as right, so it does not
  see wobble; whether it reads as less messy needs a blind side-by-side.
  The larger error is face structure, not seam wobble.

  Ceiling of per-wall roof forms (12 Nov, drawn roofs less the 30 held
  back, 155 scored). Each outline wall either rises as an eave or ends as a
  gable, every slope at one pitch, the roof the lower envelope of those
  slopes; the eave/gable choice is made per roof by an oracle that sees the
  markup, so this is the best such a model can do. Lines right / faces
  right, production vs ceiling:

  | Drawn roofs | n | production | ceiling |
  | --- | --- | --- | --- |
  | one level (no drawn step) | 46 | 25 / 24 | 36 / 25 |
  | 1-3 steps | 33 | 17 / 11 | 4 / 6 |
  | 4+ steps | 76 | 29 / 4 | 11 / 6 |

  So forms fitted to the outline can add at most ~11 roofs, all single-level;
  109 of 155 drawn roofs have steps between levels, where production already
  beats any outline form. On the 4+ step roofs production is under-split
  (22 of 76 built with under two-thirds of the drawn faces; face IoU 0.53).
  Drawn line found within 0.75 m by production, by kind: ridge 64% of
  7,224 m, valley 53% of 3,401 m, step 47% of 5,275 m (a step drawn on the
  outline counts as missed here, so the step figure is a floor). Steps are
  a third of all drawn line and are height jumps the LiDAR sees directly.

  Step split (`src/face_steps.py`, 12 Nov, on by default, SOLAR_STEP_SPLIT=0
  off): a face whose returns lie on two planes at least 0.45 m apart along
  their boundary is cut there. Blind, 178 drawn roofs, vs without: line F
  0.590 -> 0.595, faces right 43 -> 46, lines right 82 -> 81; 37 roofs moved
  (line F up 20 / down 8, face IoU up 20 / down 6). Folds as well
  (SOLAR_STEP_SPLIT=2) add nothing. Where the drawn steps go (drawn roofs
  less the held-back, 0.5 m samples, built roof tested 0.75 m each side):
  found 2,354 m; roof both sides but no seam 1,592 m; roof on one side only
  676 m; no roof either side 295 m. Of the steps inside one built face
  (1,098 m measured), half show under 0.3 m of height change in the LiDAR
  (each side's local plane carried to the line): invisible in this survey.
  Gate counts on the stepped drawn roofs: 660 faces read as one plane, so
  faces rarely span a visible step. One-sided steps: 523 m border drawn
  roof the build leaves out, but drawn roof missing from the build is 4.4%
  of drawn area (3,892 of 88,223 m2) and half of it is three large pilot
  roofs (4725584, 4740503, 4726050); 374 m are drawn on the outline. So no
  step rule gives a large gain; the larger measured lever is still choosing
  the better reading (below: best reading already drawn 77 roofs right vs
  the pick's 48).
  Golden roofs (`tests/test_golden.py`, recorded for v52 on Linux) fail 28 of
  28 with or without this change: stale since before 12 Nov, re-record on
  the VM at the next build.

  One flat/gable/hip/pyramid per rectangular part is too crude to lead:
  real roofs join forms (gable plus hip ends, dormers, L and T ridges) that
  it does not model. Next: keep the readings and regularise their faces
  (straight edges, ridges and valleys snapped to wall directions, slivers
  merged), and extend the simple form to joined parts, judged one rule at a
  time on the benchmark and renders.
- **Flat commercial roofs cut into many faces (13 Beach St, #4734679,
  case opened 8 Nov: "This panel layout is terrible").** v72 has 19 faces
  on it: a SAM reading scored 0.37 (under the 0.60 trust line) and the
  LiDAR path both cut the decks along roofing seams, parapets and plant,
  plus slivers at 9-46 degrees over walls. Flat faces already rack along
  the building's walls (`panel_fitting._edge_aligned_axes`), so the angles
  agree; the damage is that each face is packed alone with its own
  setbacks, so one deck becomes 10 arrays of 4-22 panels with stepped
  edges, some in strips between walls and plant. Same family as 24, 48 and
  93 Beach St. Next: on flat decks at the same level (within
  `MERGE_MAX_STEP_M`), pack one grid across the deck and clip it by the
  obstructions instead of by face seams; judge on the case roofs and the
  CBD drawn roofs with `tools/layout_ab.py`, then blind.

- **Use the better reading.** On Josh's 131 drawn roofs the pipeline's pick
  scores line F 0.537 (48 roofs right); the best candidate it already drew
  scores 0.688 (77). Trusting the photo reading plus overlap-clip-only
  coherence (switches `SOLAR_SELECTED_TRUST`/`MIN_SCORE`,
  `SOLAR_FACET_COHERENCE`) scores 64 roofs / 0.601, but breaks messy
  commercial roofs (12 Stanley St, 42 Suburb St, 4734994, 5372596). No score
  or shape statistic separates good readings from bad. Vision referee
  (`tools/vision_referee.py`, photo + LiDAR relief, choosing among up to 4
  readings), scored on 130 drawn roofs (roofs with line F >= 0.7): pipeline
  pick 47, Sonnet 5.5 58 (35 better, 19 worse), Opus 5.5 at low effort 60
  (35 better, 13 worse; mean line F 0.536 -> 0.594; $1.79, about $0.014 a
  roof), best reading shown 73. Gating on its stated confidence makes it
  worse. In the build behind `SOLAR_READING_REFEREE=1`
  (`src/reading_referee.py`; `tools/vision_referee.py apply` writes the
  picks to `data/referee_faces/`): built blind with today's code on the
  drawn roofs, it makes no gain (line F 0.586 -> 0.582, 63 roofs right
  both ways, faces right 33 -> 36); of the 64 roofs it changes, 14 better,
  20 worse. The picks were made from candidates dumped on 24 Oct, and
  today's own pick (63) already beats that day's (47), so the referee was
  choosing against an older final. Re-asked on candidates from today's
  build ($1.79): its pick scores 62 roofs right against the build's own 63
  (best shown 78); built blind with the switch on, line F 0.586 -> 0.591,
  63 roofs right both ways, faces right 33 -> 36; of the 48 roofs it
  changes, 14 better, 15 worse. No gain: the switch stays off. The build's
  own pick has caught up with what the referee adds; the gap left is
  between the pick and the best drawing shown (63 vs 78), which neither
  finds. Head to head (`vision_referee.py pair`, Opus 5.5 at medium effort):
  on the 47 roofs where the four-way pick differed from the build's, the
  build's faces against that pick, asked twice with the order swapped, the
  pick taking the roof only if it wins both ($1.93, $0.045 a roof). Scored
  on the same candidates: line F 0.586 -> 0.598, roofs right 63 -> 64; of
  the 33 roofs it changes, 18 better, 11 worse (four-way: 19 better, 19
  worse). The two orders agreed on 42 of 47, and the first-shown drawing
  won 46 of 94, so there is no order lean; where they split, the challenger
  was worse on 3 of 4. Kept as a tool, not wired in: one roof gained, at
  about $0.03 a roof across a district. The gap to the best drawing shown
  (64 vs 78) is not one a referee closes.
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
- **Seam fixes behind switches, judged on the Mac.** The dangle join and
  DSM box tops are on; the empty-face refit and curved split are off.
  - `SOLAR_DANGLE_JOIN_M` (default 1.0, on; `src/roof_line_source.py`): a drawn end within 1 m of another drawn line
    is joined to it; each part of the cut must fit its own plane (70% of
    returns within 0.15 m). On the 54 marked roofs with such an end, 9
    change: drawn lines found rise on 8, seam steps 12 -> 10, panels
    2,096 -> 2,123. 19 Camp St's triangle becomes its own 1.1 deg face.
  - DSM box tops in the 3D view (on in this deployment: `site-config.js`
    `obstTopFromDsm: true`; `?SOLAR_OBST_TOP_DSM=1` elsewhere): over 6,254 boxes in three regions the
    box top's distance from the DSM falls from 0.80 to 0.49 m (area-weighted).
    Still to be looked at in the 3D view.
  - `SOLAR_EMPTY_FACE_REFIT=1` (default off): no panel change; 3 of 450 sampled roofs change
    before the 20 m2 floor, seam steps 10 -> 9. Low value; fine to turn on.
  - `SOLAR_CURVED_SPLIT=1` (default off): fires on 19 Camp St only, of about 456 roofs; the
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
- **System derate.** Recalibrated to 13.79% (5 Nov, glass reflection
  folded in; 16.4% total with the inverter, `config.PV_ASSUMPTIONS`). The
  map sits about 5% above PVGIS (1,276 vs 1,215 kWh/kWp,
  `src/validate_against_pvgis.py`), deliberately: PVGIS uses generic
  temperature and system losses. A derate-only change is a `--yield-only`
  rebuild; it leaves each panel's stored shading_factor valid.
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

- **At the next build: detailed building types from OpenStreetMap (16 Nov;
  code done, not built).** `classify` now reads
  `data/regions/<r>/building_uses.json` (churches, marae, emergency, sport,
  accommodation, retail, office, farm) ahead of the zone and the size, and
  the video offers one building type on its own. No region shows any of it
  until, on the VM: `python tools/fetch_osm_uses.py` (every region; it
  needs internet, retries the public Overpass servers and keeps a day's
  answer), then a re-emit and combine. Do it as part of the next build
  that re-emits anyway; no VM run was started for this on its own. Measured
  on the Wakatipu regions only: 11 places of worship, 8 emergency, 12
  community, 187 accommodation, 237 shops; Wanaka and Upper Clutha are
  unmeasured. After the build, check the churches by name against the map
  (one "place of worship" is named Literacy Aotearoa) and that the type
  chips and the "Other" dashboard row read sensibly with fourteen types.
- **At the next major build: panels one zoom level further out (16 Nov;
  open).** Below zoom 13 the map says "Zoom in to see panel layouts"
  because the panel-area tiles start at z13 (`src/emit_region.py`, the
  `_layout_lo` pass, 13-14). Showing them from z12 means cutting that pass
  from 12, re-emitting every region, and moving the front end's matching
  limits (`panel-areas-fill` minzoom, the zoom hint; the totals switch at
  `BUILDING_MIN_Z` is separate). Expected gain is small: at z12 a pixel is
  about 13 m and a home's panels are 7-8 m across, so homes are specks and
  only large roofs read as panels. Not worth a re-emit on its own; do it
  with a build that re-emits everything anyway, check the z12 tile sizes
  against the 200 kB per-region budget, and judge it by a render.
- **Panels skewed on the map by tile rounding (11 Nov; fixed, v78 live).** Built panels are square in the layouts, but the map drew
  many as rhombi. Cause: the z15-16 panel tiles were cut at tippecanoe's
  default detail (12), about 0.1 m per tile unit at z16, and the map
  overzooms z16, so each corner was rounded and simplified by up to
  0.1 m. On frankton_arm_lake 71% of panels came out more than 2 deg off
  square (up to 10 deg). `src/emit_region.py` now cuts that pass at
  `--full-detail=16` (0.007 m): over the 35 re-emitted regions 2.9% of
  panels are off by more than 2 deg (panels cut at a tile edge), for 13%
  more bytes (panel_layouts.pmtiles 80.1 MB). Served from v78 (11 Nov).
- **Buildings with no outline: pressed flat in the 3D ground (11 Nov; v77
  live).** The large block on Beach St / Rees St,
  Queenstown CBD (NZTM ~1258205, 5004375; a finished flat roof with a glass
  skylight today) has no LINZ outline: the outlines come from the 2021
  photos, which show it under construction with a tower crane. No outline,
  no solid, and `flatten_buildings` pressed only outlined footprints, so
  the 3D view draped the 2021 survey there, crane and scaffold included, as
  spikes. Not glass: the skylight did not exist when the survey was flown.
  `unoutlined_structures` (tools/build_terrain_tiles.py) now finds such
  structures by a top that is flat at 5 m, walled, raised, unoutlined and
  not green in the photo, and presses it to the ground beside it. On pilot
  it finds 6, all real (two construction sites, three houses, a moored
  boat); without the photo test it found 61, mostly tree crowns.
  `flatten_buildings` also falls back to ground up to 15 m out when a
  footprint's 2-5 m ring is all other buildings (none on pilot).
  v76 (live 11 Nov) carried the first version: 341 structures over the 35
  served regions, but on the Beach St block it pressed only the flat
  patch, leaving the rim and the crane core 8-15 m up, still spiky in 3D,
  and one patch's 1-4 m ground ring sat on the deck's own rim (ground
  324.7 m, street ~316 m). The structure now grows from the patch through
  connected raised, unoutlined, non-green cells, widened 12 m to 48 m in
  6 m steps until a step adds under 5% (walled; a slope keeps growing and
  keeps the 12 m shape), and its ground is read 3-10 m outside the whole
  shape. On pilot the block is pressed whole to 315.3 m; the other five
  sites are unchanged in kind. Tests: tests/test_terrain_ground.py (9).
  v77 (live 11 Nov) is v76 with terrain_ground rebuilt this way
  (`tools/build_terrain_tiles.py --ground --out <dir> <served regions>`):
  335 structures over the 35 served regions; the block samples flat at
  315.3 m in the served tiles (324.7-329.7 m in v76) and renders flat in
  3D. Next: draw such a
  structure as a plain box solid rather than leave it flat; an estimate
  needs an outline (the roof may have changed since the survey).
- **Zoomed-out savings: live in v75 (11 Nov).** Cells carry money groups
  (`mg`); the total prices them below zoom 13. Live: $14.95M/yr over ~14,966
  roofs at zoom 11.5 over Queenstown; $37.2M (zoom 8.5) and $37.3M (zoom 5.5)
  over the district's 31,611 roofs. Each cell size ships only at its band's
  zooms (building_cells.pmtiles 7.9 MB); one zoom-9 tile over central
  Queenstown is 780 kB, the largest. v75 re-emitted v74's layouts: no roof
  changed. Re-emit directly (emit_region per region, then combine_regions),
  not through run_district_build.sh, while every building is stale.
- **Waiting for the next full rebuild (not yield-only)**: the sunlight
  calibration fix (flat roof now reproduces SolarView's 1,362.5 kWh/m2;
  v71 is 1.9% high flat, 3.5% high on north faces at 25 deg, in the
  Queenstown district), the heat map shading like panels, sys_kwh_N by fill
  order, and the PV notes' 16.4% losses. A yield-only rebuild would keep
  each panel's stored shading_factor, whose beam share comes from the old
  sun model; rebuild in full. Since v74 (8 Nov) the geometry code changed
  too (edge gap and both frame-search fixes on by default, the drawn-outline
  markup), so on 10 Nov tools/patch_stale_selected.py marked every building
  in all 35 served regions stale: the next build is a full re-lay of every
  region, many VM hours, not a patch. Plan it at the start of a month's VM
  budget (data/vm_budget.json).
- **Judgement calls from the independent review, not yet acted on:**
  terrain shading is nearly inert in the yield model (pilot horizon on vs
  open: -0.4% flat, -0.8% north at 25 deg) because one monthly cloud factor
  and Erbs put ~95% of low-sun light in diffuse; per-town pricing for
  Wanaka and Cromwell (EA region 35) where the map now uses Queenstown's;
  the coverage ladder uses plan area against true panel density (about 10%
  conservative on a 25 deg roof); the three horizon products reach 20 or
  30 km; colours and economic size ignore a saved battery.
- **Browser checks before a front-end release:** tools/contrast_check.js,
  overflow_check.js, econ_check.js, text_check.js (run each on the page).

- **3D view.** Solids are drawn over the map, so a hill or tree between the
  camera and a building does not hide it. Cars in the DSM stand as blobs.
  The sun simulation shades the ground, not the building meshes.

## Scale

- **National, with fast roof updates: the plan (11 Nov).** Measured on the
  Queenstown district (35 regions, 32,838 buildings, one 16-core VM): a full
  re-lay took 10.7 h (v72), about 14 core-seconds a building. Wanaka
  (8,565 buildings) by stage: layout 2,400-2,900 s, gate_panels ~1,800 s,
  heat-map raster ~2,300 s, emit ~550 s. At that rate New Zealand's ~2M
  buildings are ~7,800 core-hours a full re-lay, about NZ$490 of spot VMs,
  plus the face readings (predict_faces, ~10 s a building in one process)
  once. Two things make a roof change take hours, not days:
  1. **Re-run only what a change touches.** Today one build key per
     building covers all the geometry code, so any change re-lays everything
     (on 10 Nov every building was stale). The heat-map raster reads only
     the LiDAR and the building's shading, not the faces (its header), so a
     roof-line change should not re-run it; the panel gate's LiDAR checks
     belong at fit time. Give each stage its own key per building and skip a
     building whose new faces equal its stored faces: a roof-line change then
     re-lays only the roofs whose faces moved, and only layout and emit run.
  2. **Run it on the fleet.** `tools/fleet.sh` + `src/gcs_queue.py` spread
     regions over spot workers; the regional CPU quota (100) caps it at six
     16-core workers, ~80 h for a national full re-lay. A quota request (e.g.
     800 CPUs) brings that to ~10 h at the same cost. The fleet has never
     run (dress rehearsal below).
  Rollout of a roof change: the drawn roofs blind (minutes), one district
  (hours), then national in the background, busiest areas first; each
  building already carries its model version (`mv`), so a half-rolled-out
  map stays consistent per building.
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
