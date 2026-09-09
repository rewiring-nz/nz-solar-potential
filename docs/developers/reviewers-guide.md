# Reviewer's guide — how to understand and check this project

Written for a human reviewer who wants to verify the logic, not just read
the code. It answers three questions per subsystem: *what does it claim to
do*, *where is that enforced*, and *how do I check it myself*. Companion to
[architecture.md](architecture.md), which describes the module boundaries.

## What the system claims

For every building in the Queenstown Lakes district (15,353 outlines from
LINZ), the pipeline estimates rooftop solar potential: roof facets with
slope/aspect, obstructions, physically placeable panels ranked sunniest
first, and annual generation. Everything derives from four public inputs —
LINZ building outlines, aerial imagery (0.1 m), LiDAR point cloud, and
DEM/DSM — plus hand-drawn roof markups for a subset of buildings used as
both ground truth and training data.

## The two geometry generations (read this first)

The single most confusing thing about this codebase is that TWO roof-
geometry systems coexist, and an environment flag decides which one leads.

**Old path** (always available, the fallback): LiDAR-driven recursive
partition (`src/roof_partition.py::_partition`, greedy RANSAC + cuts) and a
constructive straight-skeleton model (`src/roof_skeleton.py`), competing
inside `src/roof_segmentation.py::segment_building_best` under confidence
gates.

**Selected-faces chain** (leads when `SOLAR_SELECTED_FACES=1`): an offline
precompute (`tools/predict_faces.py`) builds up to three candidate readings
per building and an evidence scorer picks one:

- `sam_faces` — Segment Anything (vit_b), coverage-completion prompting
- `line_faces` — a U-Net line detector (`data/models/roof_lines_v5.pt`,
  trained on the hand-drawn markups) → line extraction → polygonisation
- `lidar_faces` — normal-based region growing on the point cloud,
  regularised to building axes; wins only by a +0.08 margin, on
  shadow-degraded imagery (mean in-footprint luminance < 110), or when
  both imagery readings are refused

The winner is written to `data/selected_faces/<building_id>.json`. The
build consumes it in
`src/roof_partition.py::facets_from_selected_faces` (line ~1258).

**Precedence during a build** (this ordering is the contract):

1. Josh's drawn markup for the building (`data/roof_labels.json`) — always
   wins, never second-guessed (no plane-fit tests, no confidence gates).
2. The selected-faces JSON, if present and above `SELECTED_MIN_SCORE`
   (0.30), each face passing a one-plane gate
   (`SELECTED_MIN_PLANE_INLIER` = 0.45). Uncovered residue is filled from
   the old path (RESIDUAL FILL) so coverage is guaranteed.
3. The old path.

A verdict panel entry saying "(old path)" means stages 1–2 produced
nothing for that roof.

## Data flow and file contracts

| artifact | written by | consumed by |
|---|---|---|
| `data/regions/<r>/building_outlines_dedup.geojson` | `src/region_build.py` (dedup/assignment) | everything region-aware |
| `data/selected_faces/<id>.json` | `tools/predict_faces.py` | `roof_partition.facets_from_selected_faces` |
| `data/regions/<r>/panel_layouts.geojson` | `src/build_layout_geojson.py`, then `src/gate_panels.py` (drops), then `src/rerank_layouts.py` (fill_rank bands) | merge, preview tools |
| `data/regions/<r>/solar_potential.geojson` | `src/derive_solar_potential.py` (+ `add_addresses` patch) | merge |
| `data/panel_layouts.geojson` (503 MB, untracked) | `src/merge_regions.py` | tiles, deciles |
| `data/solar_potential.geojson` (tracked, deployed) | `src/merge_regions.py` + `bake_density_deciles` | the live map's dashboard |
| `data/panel_layouts.pmtiles` (tracked, deployed) | tippecanoe over shrunk layouts | the live map's panel rendering |
| `data/roof_labels.json` | the markup tool (`mark_roofs.html`, GitHub Pages) via `tools/ingest_labels.py` | training, benchmarks, authority rules |

The deployed site IS this repository on GitHub Pages: pushing `main`
deploys. `tools/predeploy_check.py` diffs a candidate build against the
LIVE site before any push.

## The rule constitution — owner's rules → enforcement → check

These are standing rules from the project owner (Josh), each with the code
that enforces it and the command that verifies it. If you change enforcement
code, re-run the check.

| rule | enforced at | check |
|---|---|---|
| "His markup always wins" — drawn faces/obstructions are used verbatim, exempt from confidence and plane-fit gates | `roof_partition` (labels branch), `build_layout_geojson:447` (`drawn` exemptions), obstruction authority at `build_layout_geojson:421` (where he drew obstructions, ONLY his are used) | `src/verify_markup_rebuild.py`; verdict panel roofs with markup |
| "Sunny side first" — density slider strips shadiest panels first, whole arrays by MEAN per-panel yield | `panel_fitting._order_by_array` AND `rerank_layouts` (both must use mean — they diverged once, see commit `a61c81b`) | inversion audit: at 50% density, hidden panels must not out-yield visible ones (script in commit message `a61c81b`) |
| "100% density fills every possible area" — low-confidence panels are DEMOTED to ranks 81–100, never deleted | `build_layout_geojson` (`low_conf_fit` tagging), `panel_fitting.assign_fill_ranks`, `rerank_layouts` (straggler band) | `src/refit_one.py <id>` vs shipped counts |
| "No lines unless confident" — weakly-supported lines cost score; like-plane fill facets merge | `face_candidates.score_net` (0.45 line price), `roof_partition` residual-fill merge | drawn-roof benchmark (below) |
| "Equipment is not roof" | `face_candidates.lidar_faces` (region-median step test), obstruction detection, `gate_panels` (lumpy/sparse) | reference roof #5370338 in the dev-loop obstruction validation block |
| "Facets live on their footprint" | `roof_segmentation._attach_building_geometry` (clip, drawn exempt) | dev-loop audit |
| "Nothing ships from the laptop" (district scale) | district precompute + builds run on the VM (`run_district_build.sh`, `run_predict_all.sh` on the VM) | — operational rule |

## How to check each stage yourself

- **Face selection quality** (the number that licenses the design):
  `.venv-sam/bin/python tools/select_faces.py` — measures every candidate
  family against the hand-drawn benchmark roofs and prints
  picked-vs-oracle agreement. Current: picked 0.785, oracle 0.811.
  Any change to `face_candidates.py` must not lower the picked number.
- **One building, end to end**: `python src/refit_one.py <building_id>`
  re-fits with current code and diffs against the shipped layout.
- **One building through the real build path**:
  `SOLAR_SELECTED_FACES=1` + `build_layout_geojson._build_one(<id>)`
  (see `src/patch_buildings.py`, which wraps exactly this for production
  patches).
- **Panel gates**: the dev loop (`bash src/run_dev_loop.sh pilot`) ends
  with a layout-quality block and an obstruction-validation block over
  named reference roofs, each annotated with the owner's original
  complaint and the previous build's numbers.
- **Whole-region eyeball**: `tools/preview_sample.py --ids ... --out x.html`
  renders imagery + facets + panels per building; the standing "verdict
  panel" is this over the ~29 roofs the owner has ruled on.
- **Against the live site**: `python tools/predeploy_check.py` — compares
  a candidate merged build against production, listing zeroed buildings
  and >30% panel drops. Nothing deploys without reading this.
- **Detector**: `tools/train_line_model.py --epochs N` prints held-out
  ridge/valley/cliff F1 per epoch (hash-based split, pinnable via
  `data/bench_ids.txt`). v5: ridge 0.446. 150 epochs measured no better
  than 90 at current data size.

## Environment flags

| flag | meaning |
|---|---|
| `SOLAR_SELECTED_FACES=1` | build consumes `data/selected_faces/` (the new chain). Off = old path only |
| `SOLAR_PREDICT_RESUME=1` | `predict_faces` skips buildings with existing output (preemptible VM runs) |
| `SOLAR_VISION_DIR` | override the vision-lines directory (`roof_line_source`) |
| `SOLAR_MIN_SCORE`, `SOLAR_MIN_LEN_FRAC` | vision-line acceptance overrides |
| `SOLAR_LINES_LEAD` | vision lines lead the partition (older experiment path) |
| `SOLAR_DEBUG_NET`, `SOLAR_DEBUG_SPLIT` | per-family score prints in the line-network contest / split debugging |
| `SOLAR_KEEP_EXPORTS`, `SOLAR_LAZ_SINGLE` | fetch/export behaviour tweaks |
| `LINZ_API_KEY` | data fetching |

## Comprehension debt (known, prioritised)

1. **`roof_segmentation.py` (3,173 lines) and `roof_partition.py` (2,630)**
   each accumulated a year of rule-per-incident growth. Their comments are
   good locally but there is no internal table of contents; a reviewer
   cannot find "the balcony rule" without grepping. A split is STAGED (not
   done — the VM currently builds from a synced copy of this tree, and
   module moves mid-flight would desync it): `roof_segmentation` →
   attach/gates vs competition vs vision-lines lead;
   `roof_partition` → partition core vs selected-faces consumption vs
   top-surface utilities.
1b. **Golden snapshots vs markup precedence** (resolved 2026-09-09): two
   golden buildings "collapsed" from 4 and 10 facets to 1 and 2 -- that is
   the markup-wins rule working correctly (the owner drew 1 and 2 faces on
   those roofs; the snapshots predate the markup). When a golden building
   fails with a facet-count DROP, check `data/roof_labels.json` for that id
   before suspecting the gates.
2. **Dead standalone scripts**: `src/compare_reconstruct.py`,
   `src/scan_defects.py`, `src/label_sheet.py`, `src/triage_sheet.py`
   are imported by nothing and belong to finished arcs. Deletion staged
   with the split.
3. **Two repos, hand-synced**: `solar-map` (Queenstown, deployed) and
   `solar-wellington` share most code by copy. `tools/check_repo_sync.py`
   diffs them and must be run before pushing shared-code changes.
   `site-config.js` is the ONLY intended per-deployment divergence point.
4. **`preview.html` (4,200 lines)** is one file by design (static Pages
   deploy, no build step). Its sections are ordered: CSS, DOM, map setup,
   data loading, selection/panel UI, search, economics hooks.

## Glossary of project-specific terms

- **facet** — one planar roof face as shipped (a `kind: "facet"` feature)
- **face** — a candidate facet inside the selection chain, before gates
- **drawn / authored / from_labels** — geometry from the owner's markup
- **selected** — geometry from `data/selected_faces/` (the new chain)
- **old path** — RANSAC partition + skeleton competition
- **straggler / confetti** — panels banded to ranks 81–100: real
  placements hidden at default density, visible at 100%
- **fill_rank / fill_order** — percentile band and exact sequence used by
  the client-side density slider (tiles are static; the slider filters)
- **verdict panel** — the ~29-roof render the owner rules on; the
  regression gate for geometry changes (`data/verdicts.json`)
- **defer** — the precompute refusing to write a selected-faces file so
  the building falls to the old path deliberately
- **lumpy / sparse** — `gate_panels` drop reasons: surface roughness under
  a panel / too few LiDAR returns to trust the surface
