# Agent instructions for nz-solar-potential

This document holds verified, version-controlled facts, operational constraints,
architecture boundaries, and maintenance guidelines for AI-assisted work on
`nz-solar-potential`. Treat these facts as current unless code contradicts them,
and update this file in the same change that alters a fact, constraint, or workflow.

## Maintenance protocol

- **What belongs here**: Verified architectural decisions, operational constraints,
  stable source-data/CRS facts, output contracts, supported workflows, and clearly
  labelled open questions.
- **What does not belong here**: Secrets, credentials, personal data, transient logs,
  verbatim chat transcripts, duplicated walkthroughs maintained elsewhere in [docs/](docs/),
  or claims not traceable to code, configuration, or reproducible validation.
- **How to update**: State the fact, its evidence, and the source location. Replace
  superseded statements rather than appending competing versions. Keep uncertain items in
  [Open questions](#open-questions) and resolve or delete them when evidence arrives.
- **Tone**: State the rule, the measurement, and the reason. No conversational attribution
  ("X said", quotes from chat, dates of conversations); write the fact itself.

## Architecture

Local Python geospatial pipeline (LINZ outlines, LiDAR, imagery →
per-building solar estimates and panel layouts) plus a static MapLibre map
frontend. See [docs/developers/architecture.md](docs/developers/architecture.md)
for module boundaries and the [docs README](docs/README.md) for the full
documentation map (data maintainers, web-map users, ADRs).

## Build and test

Set up per [docs/data-maintainers/local-setup.md](docs/data-maintainers/local-setup.md).
- `bash src/run_dev_loop.sh pilot`: Fast pilot-region check.
- `bash src/run_district_build.sh`: District build orchestrator. Incremental by
  default (only stale buildings rebuild), `--force` for everything, `--yield-only`
  when only the solar model changed.
- `tools/pipeline.py [region]`: (Also invoked by `quickstart.sh`) Runs regional
  estimates, then emits and combines the map contract directly under `data/`.
  Diagnostic logs are recorded under `data/pipeline_runs/<run-id>/run.log` and
  `report.md` (plus preview-server PID/log when the preview starts). Combining
  replaces the current map contract with the selected region. `tools/pipeline_serve.py`
  serves a preview with PMTiles byte-range support on loopback.
- `bash tests/run_all.sh`: Local automated check entry point running pure Python,
  economics, deprecated-API, repository-sync, architecture-diagram, and synthetic
  region checks (`tests/synthetic`). Add targeted tests under `tests/` when changing
  deterministic algorithms or data contracts.
- Change `src/output_contract.py` deliberately whenever what the map reads changes.

## Conventions

- Comments and docs state the rule, the measurement, and the reason. No
  conversational attribution ("X said", quotes from chat, dates of
  conversations); write the fact itself.
- Processing CRS is EPSG:2193 (NZTM2000); web-map output is EPSG:4326. Don't
  mix them.
- Generated datasets are pipeline contracts. The current map build emits
  per-region artifacts and map tiles; published map data is stored outside Git.
  The legacy root-level `data/solar_potential.geojson` is ignored and is not a
  current frontend input. Preserve active output fields and assumptions unless
  updating producer and consumer together.
- `config.PV_ASSUMPTIONS` is the single source of truth for PV model
  assumptions shown in the UI; don't hardcode assumption values elsewhere.
- Update the matching doc in `docs/` in the same change as a command, output,
  or operating-limit change.

## Documentation rules

- Treat executable scripts, `config.py`, and committed data contracts as the
  implementation source of truth. Link to them instead of copying volatile
  details.
- Mark future cloud work, unverified claims, and experiments clearly. Current
  documented operations run locally.
- Keep platform-specific setup in the data maintainer guides under
  [docs/data-maintainers/](docs/data-maintainers/). macOS is the primary
  platform; Ubuntu and Windows guidance is included where it differs.
- Keep agent context factual, concise, and reviewable in Git. Remove duplicate
  or superseded statements when adding new evidence.
- Update the matching doc or guide in [docs/](docs/) in the same change as a
  changed command, input, output, operating limit, or externally visible estimate.

## Verified project facts

Last verified: 2026-09-26

### Pipeline execution and orchestration
- Full regional builds isolate each area in its own Python process because
  decoded point-cloud tiles are retained in process memory.
- `src/run_district_build.sh` is the district orchestrator. It plans each
  region from per-building build keys (clean / patch / full), runs per-area
  layout, gating, reranking, derivation, confidence, horizon and raster
  stages, emits each region's tiles (`emit_region.py`) and combines them
  (`combine_regions.py`). There is no merged-file path any more.
- `src/region_build.py` treats `pilot` as a region-aware build area whose
  inputs and outputs resolve under `data/regions/pilot/`, while pilot source
  acquisition starts under `data/`. Pilot source inputs are symlinked into the
  region tree.
- Per-area generated outputs are under `data/regions/<area>/`; merged map-facing
  outputs are under `data/`, including GeoJSON, heatmap artifacts, and
  `panel_layouts.pmtiles`.
- `src/run_stage.py` is the stage wrapper used by the district build. It runs
  preflight checks, records completion markers under `data/build_state/`, and
  skips only stages whose declared inputs are older than their marker.
- `config.PIPELINE_REGION` selects the default region for `tools/pipeline.py`;
  the CLI can override it with any configured `config.REGIONS` name. Per-machine
  JSON area configuration has been removed.
- Pipeline run failures are triaged from each run's `report.md`, then the
  matching numbered step log. [docs/data-maintainers/troubleshooting.md](docs/data-maintainers/troubleshooting.md)
  is the indexed guide for those diagnostics, LINZ permissions, missing Python
  dependencies, map-build CLI prerequisites, and local preview failures.

### Data sources, CRS, and storage
- The processing CRS is EPSG:2193 (NZTM2000); web-map output is EPSG:4326.
- LINZ WFS access in the current fetch code uses WFS 1.0.0 with `typeName` and
  `maxFeatures`. WFS 2.0.0 returned empty results during prior validation.
- Missing aerial imagery does not block builds: the regional fetcher logs a
  warning and the build runs without imagery-based obstruction detection.
- `data/dem_wide_mosaic.tif` is a required root-level input for several current
  stages, including building horizons and terrain masks. `src/fetch_dem_wide.py`
  fetches LINZ layer `51768` over the configured `config.DEM_WIDE_BBOX`, which
  includes a 30 km EPSG:2193 buffer; LINZ describes that source as
  cartographic rather than suitable for precision terrain analysis.
- `data/` is not wholly disposable: it contains tracked curated labels,
  benchmark/truth/verdict assets, and trained roof-line models, alongside
  ignored regenerable raw inputs, local build outputs, and map artifacts
  published outside Git.
  `git clean -fd` leaves ignored files; `rm -rf data` removes tracked assets
  too.

### Geometry, solar modeling, and frontend contracts
- Roof geometry has two generations (see [docs/developers/reviewers-guide.md](docs/developers/reviewers-guide.md)).
  The fallback: RANSAC plane fitting and straight-skeleton reconstruction
  (`src/roof_skeleton.py`) competing under partition confidence gates. Leading
  when `SOLAR_SELECTED_FACES=1`: an offline precompute (`tools/predict_faces.py`)
  scores three candidate face readings per building (SAM vit_b, a U-Net line
  detector v5 trained on the owner's markups, and normal-grown LiDAR regions)
  and writes the winner to `data/selected_faces/<id>.json`, consumed by
  `roof_partition.facets_from_selected_faces` with a one-plane gate and
  residual fill. Precedence: owner's markup > selected faces > fallback.
- Building solar potential summary data (`solar_potential.geojson`) is derived
  by aggregating layout features directly (`src/derive_solar_potential.py`),
  ensuring summary totals and individual layouts match without re-segmentation.
- Shading incorporates per-building 72-bin horizon profiles (`horizon_b64`,
  `horizon_beam_pct`) combining wide 8m DEM terrain and 1m DSM obstacles
  evaluated at eave height.
- `config.PV_ASSUMPTIONS` is the intended single source for displayed PV model
  assumptions and generated summary data.
- `site-config.js` contains deployment-specific frontend settings, while
  `economics.js` contains the pure client-side cost, savings, and payback model
  used by the map and its Node test.
- The current static-host configuration publishes repository files. The local
  refit API exists only through `src/live_server.py` and is unavailable on
  static hosting.

## Open questions

- What release versioning, retention, and provenance record should accompany
  published generated datasets?
- Which PMTiles build and hosting process should deliver detailed layouts at
  district scale?
- Should containerisation be adopted after local workflow measurements? See
  [ADR 0001](docs/decisions/0001-containerisation-strategy.md).
- How are pilot root inputs created or symlinked into `data/regions/pilot/` on a
  fresh checkout?
- Is `src/live_server.py` intentionally limited to root-level pilot inputs, or
  should it be adapted for region-aware outputs before regional development is
  supported?
