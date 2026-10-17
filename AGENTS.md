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
  replaces the current map contract with the selected region. Runs locally by
  default; `--mode cloud` builds and gates the configured region on the shared
  VM, then publishes and opens a public developer preview after explicit
  approval of the calculated cost. It uses `config.CLOUD_SHARED_BUILD` for
  defaults and resolves the preview owner from the GitHub SSH identity,
  falling back to GitHub CLI when available.
  `--mode cloud --profile <name>` selects per-developer cloud execution,
  and `--cloud` selects a VM lifecycle action. See
  [docs/quickstart-cloud.md](docs/quickstart-cloud.md).
  `tools/pipeline_serve.py` serves a preview with PMTiles byte-range support on loopback.
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

## Every number is its own workings (consistency rules)

Every figure the map shows must be traceable by the reader and must agree
with every other figure that describes the same thing (Josh, 17 Oct). This
is enforced by code, not by care:

- **Show only Workings nodes.** A number on screen is `node.value` of a
  `workings.js` node, and its breakdown is that node opened up. Do not
  compute a displayed figure in view code and do not write a description
  of a sum beside it; build the sum from `Wk.input/product/sum/...` so the
  breakdown cannot drift. Non-arithmetic steps are `Wk.table` (with a
  `rowCheck`) or `Wk.given` with a `check`.
- **One generation calculation.** `generation.js` prices the panels drawn on
  the roof from the build's own inputs (tile `sb`/`ab`/`sh`, data/sun/<region>.json
  captured from `src/solar_model.py` by `src/workings_data.py`). The yellow
  box, the curve chart, the bills and the savings all read the page's `GEN`.
  The curve is scaled so its year equals the annual figure; never feed the
  bills a curve with a different total.
- **Four locks, all must pass:** (1) `emit_region` re-derives every panel's
  kWh from the shipped inputs and refuses to emit on a mismatch; (2)
  `predeploy_check` fails a release whose summaries lack that check or
  disagree; (3) CI runs `tests/test_workings.mjs`, `tests/test_generation.mjs`
  (JavaScript vs Python on real panels, tests/fixtures/workings_kinloch.json),
  `test_bill_tool.mjs` and `test_economics.mjs`; (4) the page runs
  `Wk.verify` on every breakdown it opens and marks a number that does not add
  up in red.
- **Changing the solar model or panel figures:** regenerate the fixture
  (`tools/export_workings_fixture.py kinloch`) and re-emit every region.
- **Known exception (to remove):** the payback colours of buildings OTHER
  than the open one, and the left-hand in-view economics, still use the
  simpler annual model in `economics.js` (`economicsFor`). The open
  building's panel colours are priced from its own bill-model figures
  (`panelEconOpen`), so they agree with the savings beside them.

## Operating rules (builds, data, review)

- The VM is reachable only through Identity-Aware Proxy: always pass
  `--tunnel-through-iap` to `gcloud compute ssh` / `scp` (the tools/ scripts do).
- **Confirm cloud cost before any chargeable action.** A cloud quickstart must
  show a conservative, region-appropriate cost estimate and the assumptions
  before making cloud calls that could start or use a billable VM. Clearly say
  the estimate is not a spending cap and actual charges can be higher. Require
  an explicit human `yes`; Enter, any other response, or a non-interactive
  invocation must decline without starting cloud work. If no defensible default
  estimate exists for the selected workload, require a user-supplied estimate.
  Keep no-cost checks separate so they can run without this confirmation.
  Profile-based single-VM lifecycle actions are implemented in
  `tools/pipeline.py` and invoked through `quickstart.sh`; the shared-VM
  build-and-publish workflow remains in `tools/remote_build.sh`. Setup guidance:
  [docs/quickstart-cloud.md](docs/quickstart-cloud.md) and
  [docs/data-maintainers/team-access.md](docs/data-maintainers/team-access.md).

- Tasks: GitHub Issues on the project board
  (https://github.com/orgs/rewiring-nz/projects/5). Link pull requests with
  `Fixes #N`; record finished work in BACKLOG.md as well.
- Mark AI-written GitHub content. Issues, comments and pull requests an AI
  agent writes carry the `ai-generated` label and open with a line saying
  so ("Written by Claude (AI)"); commits carry a `Co-Authored-By:` trailer.
  A person removes the label once they have checked it.

- **District builds run on the build VM**, `claude-doing-things`
  (australia-southeast1-b, project downloads-417521), never on a laptop: the
  VM fetches LiDAR and imagery from LINZ. It is a spot VM and stops itself 16
  hours after starting; a cut-off build resumes with
  `src/run_district_build.sh --force --regions "<the rest>"`.
- **Never change code on the VM while it is building.** Each stage is a fresh
  process, so a mid-build change mixes old and new code across regions
  undetectably. The VM's `~/solar-map` is a git checkout of this repo:
  push, then `tools/sync_vm.sh` (or `--ref <branch>`) pulls it there, and
  refuses when the VM is busy.
- **Publishing goes through the deploy gate.** `tools/deploy_from_vm.sh` pulls
  the VM's served set and runs `tools/predeploy_check.py` against the live
  build; `--push` publishes a new version folder to
  `gs://rewiring-solar-tiles/v<N>/`, keeps the newest three, and commits only
  the `site-config.js` switch. `--push-reviewed "<why>"` publishes past a gate
  whose list has been rendered and judged. Rollback: point `site-config.js`
  `dataBase`/`dataVersion` at an older version folder.
- **Developer previews are separate from production.** The shared cloud
  quickstart publishes every passing gated build by default to the dedicated
  public bucket in `config.CLOUD_SHARED_BUILD["preview_bucket"]`, under
  immutable `devel/<alias>/runs/` paths. It resolves the alias from the
  authenticated GitHub SSH identity (or the GitHub CLI fallback), calculates a
  planning estimate from the configured `preview_cost_model`, and updates only
  that alias's preview pointer. The bucket lifecycle expires all `devel/` objects after
  `preview_retention_days`. Never use the bucket for private inputs or
  production releases.
- **Keys** come from the environment or `.env`, else Google Secret Manager
  (`src/project_secrets.py`; secret `linz-api-key`). Never commit or print a
  key.
- **Hand-drawn markup governs** the roofs it covers (`data/roof_labels.json`).
  Geometry changes are judged by renders against the photo and measurement
  against the markup, not by panel counts; no per-building special cases.
- **Where no LiDAR exists**, a survey marked `"elevation": "synthetic"` builds
  on a modelled surface (`src/synthesize_dsm.py`) and every building carries
  `pitch_guessed`.

Team access and setup: [docs/data-maintainers/team-access.md](docs/data-maintainers/team-access.md).

## Documentation rules

- Treat executable scripts, `config.py`, and committed data contracts as the
  implementation source of truth. Link to them instead of copying volatile
  details.
- Mark future cloud work, unverified claims, and experiments clearly. Quick
  checks run locally; district builds run on the build VM and published data is
  served from the Google Cloud tiles bucket (see Operating rules). The
  profile-based single-VM workflow is experimental and documented separately
  in [docs/quickstart-cloud.md](docs/quickstart-cloud.md).
- Keep platform-specific setup in the data maintainer guides under
  [docs/data-maintainers/](docs/data-maintainers/). macOS is the primary
  platform; Ubuntu and Windows guidance is included where it differs.
- Keep agent context factual, concise, and reviewable in Git. Remove duplicate
  or superseded statements when adding new evidence.
- Update the matching doc or guide in [docs/](docs/) in the same change as a
  changed command, input, output, operating limit, or externally visible estimate.

## Verified project facts

Last verified: 2026-10-11

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
- The experimental GCP quickstart in `tools/pipeline.py` provisions one
  dedicated VM and a profile-scoped SSH firewall rule; profiles are local and
  non-secret. See
  [docs/quickstart-cloud.md](docs/quickstart-cloud.md).
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
- `config.py` is the source of shared non-secret pipeline and cloud-build
  defaults, including `config.CLOUD_SHARED_BUILD` and
  `config.PV_ASSUMPTIONS`. The root `.env` is local and ignored, for secrets
  such as `LINZ_API_KEY`; optional per-developer cloud profiles remain separate
  under `~/.config/nz-solar-potential/cloud-profiles.json`.
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
