# Backlog

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
Follow-up baseline: the simple rooftop map now links to the explorer,
local pipeline panel and documentation through a collapsed navigation menu.
The map-user guide distinguishes independent explorer layers and attribute
queries from the simple map's analysis modes.

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

Completed local slice of #12: web pipeline panel (`tools/pipeline_web.py`,
`pipeline.html`, [guide](docs/data-maintainers/web-pipeline.md)). Loopback,
token-protected; picks LINZ suburbs/localities on a map, estimates buildings,
download, time and disk, needs a typed `yes`, builds regions in sequence with
live progress and cancel, then combines all built regions. `tools/pipeline.py`
now writes `status.json` and accepts `--no-preview-server`.
Cloud slice of #12: the panel's Local / Developer / Published toggle lists
local areas, every developer's previews and the live data version; starts
developer previews and gated publishes on the shared VM after an NZD cost
estimate and typed `yes`; and shows selected and queued work per lane (local,
cloud). Cloud mode of `tools/pipeline.py` accepts several space-separated
regions. Remaining: a hosted backend with team auth (the panel still runs on
each developer's computer with their own `gcloud` credentials).
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
  finds.
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

- **Buildings with no outline: pressed flat in the 3D ground (11 Nov, code
  only, not yet in a build).** The large block on Beach St / Rees St,
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
  Goes live with the next terrain_ground tile build
  (`tools/build_terrain_tiles.py --ground`, VM). Next: draw such a
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
