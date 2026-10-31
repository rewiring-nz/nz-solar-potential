# Reviewer's guide

For a reviewer who wants to check the logic, not just read the code. For
each part: what it claims, where that is enforced, and how to check it.
Module boundaries are in [architecture.md](architecture.md).

## What the system claims

For buildings in the served regions, the pipeline estimates roof faces with
slope and aspect, obstructions, panels that physically fit (ranked sunniest first) and
yearly generation. Inputs: LINZ building outlines, aerial imagery, the LiDAR
point cloud, DSM and DEM, plus hand-drawn roof markup for some buildings,
used as ground truth and training data.

## Roof geometry: two paths

Two roof-geometry systems exist side by side.

**LiDAR path** (the fallback): a recursive RANSAC partition
(`src/roof_partition.py`) and a straight-skeleton model
(`src/roof_skeleton.py`) compete in
`src/roof_segmentation.py::segment_building_best` under confidence gates.

**Selected faces** (on by default, `SOLAR_SELECTED_FACES=1`): an offline step
(`tools/predict_faces.py`) builds up to three readings per building and a
scorer picks one:

- `sam_faces`: Segment Anything (vit_b) on the photo.
- `line_faces`: a U-Net line detector, then line extraction and
  polygonising. The v5 model drives extraction; v6 adds only its hip channel
  to the evidence map.
- `lidar_faces`: normal-based region growing on the point cloud, squared to
  the building axes. It wins only by a 0.08 margin, on dark (shadowed)
  imagery, or when both photo readings are refused.

The winner is written to `data/selected_faces/<building_id>.json`. A
district pre-plan can also write an agreed reading for matching LiDAR roof
surfaces to `data/repeat_faces/<building_id>.json`.

**Precedence in a build:**

1. Hand-drawn markup (`data/roof_labels.json`). Always wins; no plane-fit or
   confidence gates.
2. The repeated-roof reading, when enabled and present; otherwise the
   ordinary selected reading. `roof_partition.selected_doc` resolves this.
   The reading must score at least `SELECTED_MIN_SCORE`
   (0.30), and each face must pass its one-plane gate (normally 0.45 of
   returns; large complex faces have a stricter gate). An ordinary reading
   below `SELECTED_TRUST` (0.60) competes with the whole LiDAR path and loses
   if that explains the returns clearly better. An agreed repeated-roof
   reading skips that per-building competition. Uncovered roof is filled
   from the LiDAR path.
3. The LiDAR path.

A verdict-panel entry marked "(old path)" means steps 1 and 2 gave nothing.

Off by default: ridge snapping (`SOLAR_RIDGE_SNAP=0`) and the plant-deck
rule (`SOLAR_PLANT_DECKS=0`).

## Files and who writes them

| file | written by | read by |
|---|---|---|
| `data/regions/<r>/building_outlines_dedup.geojson` | `src/region_build.py` | everything region-aware |
| `data/selected_faces/<id>.json` | `tools/predict_faces.py` | `roof_partition.facets_from_selected_faces` |
| `data/repeat_faces/<id>.json` | `src/repeat_roofs.py` | `roof_partition.selected_doc` |
| `data/regions/<r>/outline_fit.json` | `src/outline_fit.py` | `outline_fit.fitted_outlines`, build keys |
| `data/regions/<r>/panel_layouts.geojson` | layout, gate, optional district demolition screen, rerank | `src/derive_solar_potential.py`, `src/emit_region.py` |
| `data/regions/<r>/solar_potential.geojson` | `src/derive_solar_potential.py` (+ `src/add_addresses.py`) | `src/emit_region.py` |
| `data/out/<r>/` (not in Git) | `src/emit_region.py` | `src/combine_regions.py` |
| served set under `data/` (not in Git) | `src/combine_regions.py` | `tools/publish_served.py`, local preview |
| `data/roof_labels.json` | `mark_roofs.html` via `tools/ingest_labels.py` | training, benchmarks, precedence |

For exact stage order and differences between engines, use the
[execution reference](tooling-execution.md#regional-workflow-differences);
for serving and release steps, use
[dataset operations](../data-maintainers/dataset-operations.md#7-publish).

## Rules, where they are enforced, how to check

| rule | enforced in | check |
|---|---|---|
| Markup wins: drawn faces and obstructions used as drawn, exempt from confidence and plane-fit gates; where obstructions are drawn, only those count | `roof_partition` (labels branch), `build_layout_geojson` (drawn exemptions, obstruction authority) | `tools/verify_markup_rebuild.py`; verdict-panel roofs with markup |
| Sunny side first: lowering density hides the shadiest panels first, whole arrays by mean panel yield | `panel_fitting._order_by_array` and `rerank_layouts` (both must use the mean) | at 50% density, no hidden panel should out-yield a visible one |
| 100% density shows every feasible panel: low-confidence panels move to ranks 81-100, not deleted | `build_layout_geojson` (`low_conf_fit`), `panel_fitting.assign_fill_ranks`, `rerank_layouts` | `src/refit_one.py <id>` against shipped counts |
| No lines without evidence: weak lines cost score; like-plane fill faces merge | `face_candidates` (line price 0.45 in the net score), `roof_partition` residual fill | drawn-roof benchmark (`tools/markup_benchmark.py`) |
| Equipment is not roof | `face_candidates.lidar_faces` (step test), obstruction detection, `gate_panels` (lumpy, sparse) | obstruction block of the dev loop |
| Faces stay on their footprint | `roof_segmentation._attach_building_geometry` (drawn exempt) | dev-loop audit |
| District builds run on the build VM, not a laptop | `src/run_district_build.sh` on the VM | operating rule |

## Check each stage yourself

Choose the check for the concept you changed; use the full-suite checklist
below before publishing. Historical benchmark figures here are regression
context, not an independent accuracy estimate.

- **Face selection:** `.venv-sam/bin/python tools/select_faces.py` scores
  each candidate family against the drawn roofs and prints picked-vs-best
  agreement (last recorded: picked 0.785, best 0.811). A change to
  `face_candidates.py` must not lower the picked figure.
- **Geometry against the markup:** `python tools/markup_benchmark.py`
  builds the drawn roofs with labels hidden and scores them.
- **One building:** `python src/refit_one.py <building_id>` refits with
  current code and diffs against the shipped layout.
- **One building through the real build:** `src/patch_buildings.py` wraps
  `build_layout_geojson._build_one(<id>)`.
- **Panel gates:** `bash src/run_dev_loop.sh pilot` ends with layout-quality
  and obstruction checks on named reference roofs.
- **By eye:** `tools/preview_sample.py --ids ... --out x.html` renders photo,
  faces and panels per building. The verdict panel is this over the roofs
  with a recorded verdict (`data/verdicts.json`).
- **Against the live site:** `python tools/predeploy_check.py` lists zeroed
  buildings and big panel drops. Nothing deploys without reading it.
- **Line detector:** `tools/train_line_model.py --epochs N` prints held-out
  F1 per channel (ridge, valley, cliff, hip); pin the split with
  `data/bench_ids.txt`. Do not use v6 for extraction (line agreement falls
  from 0.754 to 0.42), and do not feed LiDAR step blobs to the extractor;
  steps belong to the scorer. Pretraining: `tools/export_rid_training.py`,
  then the trainer's `--extra-dir/--init/--lr`.

## Checks before publishing

```sh
bash tests/run_all.sh --fast   # unit, JavaScript and repository checks
bash tests/run_all.sh          # also synthetic-region and golden-building checks
```

Golden-building checks require local region data and skip without it.
JavaScript checks require Node; without it the runner skips them. CI's
[test workflow](../../.github/workflows/tests.yml) provides the data-free checks.
The existing diagram check validates a curated symbol/constants list, not
the Markdown Mermaid diagrams; see the
[execution-reference checklist](tooling-execution.md#keeping-this-reference-accurate).

Add targeted tests for deterministic algorithms and data contracts, and
update the matching guide when behaviour changes. For a regional build,
also complete the [operator review](../data-maintainers/dataset-operations.md#5-check)
and deploy gate; passing unit tests alone does not establish layout quality.

## Environment flags

| flag | meaning |
|---|---|
| `SOLAR_SELECTED_FACES` | `1` (default) enables selected/repeated readings; `0` bypasses them, but markup still wins |
| `SOLAR_REPEAT_ROOFS` | `1` (default) prefers an available repeated-roof reading within the selected-faces path |
| `SOLAR_PREDICT_RESUME=1` | `predict_faces` skips buildings already done |
| `SOLAR_VISION_DIR` | vision-lines directory (`roof_line_source`) |
| `SOLAR_MIN_SCORE`, `SOLAR_MIN_LEN_FRAC` | vision-line acceptance |
| `SOLAR_LINES_LEAD` | vision lines lead the partition (older experiment) |
| `SOLAR_DEBUG_NET` | per-family scores in the line-network contest |
| `SOLAR_KEEP_EXPORTS`, `SOLAR_LAZ_SINGLE` | fetch and point-cloud behaviour |
| `LINZ_API_KEY` | data fetching |

Layout switches for A/B tests (`SOLAR_*`) are named in the module that reads
them; `tools/layout_ab.py` runs a sample with a switch off and on.

## Known comprehension debt

1. `roof_segmentation.py` and `roof_partition.py`
   grew one rule per incident and have no table of contents. A split is
   planned: segmentation into attach/gates, competition and vision-lines;
   partition into core, selected-faces and top-surface utilities.
2. A golden building whose face count drops may be the markup rule working:
   check `data/roof_labels.json` for that id before blaming the gates.
3. One repo. The Wellington copy was retired on 24 Sep 2026.
   `site-config.js` holds the per-deployment settings.
4. `preview.html` is one file by design: static Pages
   deploy, no build step.

## Glossary

- **facet**: one planar roof face as shipped (`kind: "facet"`).
- **face**: a candidate facet in the selection chain, before gates.
- **drawn / authored / from_labels**: geometry from the markup.
- **selected**: geometry from `data/selected_faces/`.
- **old path**: the LiDAR partition and skeleton.
- **straggler**: a panel in ranks 81-100, hidden at the default density,
  shown at 100%.
- **fill_rank / fill_order**: band and exact order used by the density
  slider.
- **verdict panel**: the set of roofs with recorded verdicts; the
  regression check for geometry changes.
- **defer**: the precompute declines to write a selected-faces file, so the
  building uses the LiDAR path.
- **lumpy / sparse**: `gate_panels` drop reasons: rough surface under a
  panel; too few LiDAR returns to trust.
