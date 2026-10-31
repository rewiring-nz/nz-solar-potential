# Agent instructions for nz-solar-potential

Verified facts and rules for AI-assisted work on this repo. Code wins where
it disagrees with this file. Update this file in the same change that alters
a fact, rule or workflow.

## Keeping this file

- Keep here: verified decisions, limits, data facts, contracts, workflows,
  open questions. Not secrets, personal data, logs, chat, or walkthroughs
  that live in [docs/](docs).
- State the fact and where it lives. Replace old statements; don't append.
- No attribution ("X said", chat quotes, dates of conversations).

## Architecture

A Python geospatial pipeline (LINZ outlines, LiDAR, imagery → per-building
solar estimates and panel layouts) and a static MapLibre map. Module
boundaries: [docs/developers/architecture.md](docs/developers/architecture.md).
Entry points, delegation, machine boundaries and run ownership:
[docs/developers/tooling-execution.md](docs/developers/tooling-execution.md).
`tools/pipeline.py` is not yet a common front door for all operations:
local pipeline, district and worker builds maintain separate stage
sequences. Document current behaviour separately from the consolidation target.
Doc map: [docs/index.md](docs/index.md).
Write for the persona named there. Keep each procedure or detailed
explanation in one primary guide and link from summaries; repeat safety
warnings where a reader is about to act.
Keep Markdown guides in persona subdirectories matching the index, with
point-in-time notes under `docs/ai-agents/archive/`. When code is removed,
remove its obsolete documentation and diagram paths in the same change;
consolidate redundant explanations rather than preserving them as history.

## Build and test

Setup: [docs/data-maintainers/local-setup.md](docs/data-maintainers/local-setup.md).

- `bash src/run_dev_loop.sh pilot`: quick pilot-region check.
- `bash src/run_district_build.sh`: district build. Incremental by default;
  `--force` rebuilds everything; `--yield-only` when only the solar model
  changed.
- `tools/pipeline.py [region]` (also run by `quickstart.sh`): builds one
  region, then emits and combines the map data under `data/`, replacing the
  current map data. Logs: `data/pipeline_runs/<run-id>/run.log` and
  `report.md`. Local by default. `--mode cloud` builds and gates on the
  shared VM, then publishes a public developer preview after the user
  approves the cost; defaults in `config.CLOUD_SHARED_BUILD`, preview owner
  from the GitHub SSH identity (GitHub CLI fallback).
  `--mode cloud --profile <name>` uses a per-developer VM; `--cloud` runs a
  VM lifecycle action. See [docs/data-maintainers/quickstart-cloud.md](docs/data-maintainers/quickstart-cloud.md).
- `tools/pipeline_serve.py`: local preview server with PMTiles byte ranges.
- Local pipeline execution uses `src/local_queue.py` and
  `tools/pipeline_queue.py`: loopback coordinator, JSON state and one worker
  process by default (`--workers` changes the pool size). Tasks in a checkout
  serialize; concurrent builds need human-managed independent writable
  workspaces. No GCS/cloud credentials are required for queue operation.
  GCS fleet tasks are not yet migrated. Recovery and locks:
  [pipeline reference](docs/data-maintainers/pipeline-reference.md#local-task-queue).
- `bash tests/run_all.sh`: all local checks (Python, economics, deprecated
  APIs, repo sync, diagram, synthetic region in `tests/synthetic`). Add
  targeted tests under `tests/` when changing algorithms or data contracts.
- Change `src/output_contract.py` deliberately whenever what the map reads
  changes.

## Conventions

- Comments and docs state the rule, the measurement and the reason.
- Processing CRS is EPSG:2193 (NZTM2000); web output is EPSG:4326. Don't mix
  them.
- Generated datasets are contracts. Each region emits its own artefacts and
  tiles; published data lives outside Git. The root
  `data/solar_potential.geojson` is a local build file, not tracked and not read by the map.
  Change producer and consumer together.
- `config.PV_ASSUMPTIONS` is the one source for PV assumptions shown in the
  UI. Don't hardcode them elsewhere.
- Scripts, `config.py` and data contracts are the source of truth: link to
  them rather than copying values. Update the matching doc in `docs/` (and
  `method.html`, which is maintained by hand) in the same change as a
  command, output, limit or constant. Mark experiments and unverified cloud
  steps. Platform setup lives in [docs/data-maintainers/](docs/data-maintainers).

## Every number shows its workings

Every figure on the map must be traceable and must agree with every other
figure for the same thing. Code enforces this:

- **Show only Workings nodes.** A number on screen is `node.value` of a
  `workings.js` node; its breakdown is that node opened. Don't compute a
  displayed figure in view code or describe a sum beside it. Build sums
  from `Wk.input/product/sum/...`. Non-arithmetic steps are `Wk.table` (with
  a `rowCheck`) or `Wk.given` with a `check`.
- **One generation calculation.** `generation.js` prices the panels drawn on
  the roof from the build's own inputs (tile `sb`/`ab`/`sh`, and
  `data/sun/<region>.json` captured from `src/solar_model.py` by
  `src/workings_data.py`). The yellow box, curve chart, bills and savings all
  read the page's `GEN`. The curve is scaled so its year equals the annual
  figure.
- **One pricing model.** `bill_tool.js` prices every roof: the open one's
  card and every roof's panel colours (`btPanelEcon`). `economicsFor` in
  `economics.js` is not used for map figures.
- **Four locks:** (1) `emit_region` re-derives every panel's kWh from the
  shipped inputs and refuses to emit on a mismatch; (2) `predeploy_check`
  fails a release whose summaries lack that check or disagree; (3) CI runs
  `tests/test_workings.mjs`, `test_generation.mjs` (JavaScript vs Python on
  real panels, `tests/fixtures/workings_kinloch.json`), `test_economics.mjs`,
  `test_bill_tool.mjs` and `test_low_solar_battery.mjs`; (4) the page runs
  `Wk.verify` on every breakdown it opens and marks a mismatch in red.
- **Changing the solar model or panel figures:** regenerate the fixture
  (`tools/export_workings_fixture.py kinloch`) and re-emit every region.

## Operating rules

- **Confirm cloud cost before any chargeable action.** Show a conservative,
  region-appropriate estimate and its assumptions before any call that could
  start or use a billable VM. Say it is not a spending cap. Require an
  explicit `yes`; Enter, anything else, or a non-interactive run declines.
  The one exception is the explicit `--yes` flag on `tools/pipeline.py`: it
  still prints and logs the estimate, then records that `--yes` (not a
  person at the prompt) approved it. No environment variable approves cost,
  and `--yes` never approves the paid referee, a gcloud install or a
  `--confirm` target.
  With no defensible default estimate, require one from the user. Keep
  no-cost checks separate. Single-VM profile actions live in
  `tools/pipeline.py` (via `quickstart.sh`); the shared-VM build and publish
  is `tools/remote_build.sh`. See [docs/data-maintainers/quickstart-cloud.md](docs/data-maintainers/quickstart-cloud.md)
  and [docs/data-maintainers/team-access.md](docs/data-maintainers/team-access.md).
- **Tasks** are GitHub Issues on the project board
  (https://github.com/orgs/rewiring-nz/projects/5). Link PRs with `Fixes #N`.
  Open work is also in BACKLOG.md.
- **Mark AI-written GitHub content.** Issues, comments and PRs written by an
  AI agent carry the `ai-generated` label and open with "Written by Claude
  (AI)"; commits carry a `Co-Authored-By:` trailer. A person removes the
  label after checking.
- **District builds run on the build VM**, `claude-doing-things`
  (australia-southeast1-b, project solar-potential-map-512020), never on a
  laptop. The VM fetches LiDAR and imagery from LINZ. Reach it only through
  Identity-Aware Proxy: pass `--tunnel-through-iap` to `gcloud compute ssh` /
  `scp` (the tools/ scripts do). It is a spot VM that stops itself 40 hours
  after starting (max-run-duration 144000 s); resume a cut-off build with
  `src/run_district_build.sh --force --regions "<the rest>"`.
- **Never change code on the VM while it is building.** Each stage is a new
  process, so a mid-build change mixes code across regions without trace.
  The VM's `~/solar-map` is a git checkout: push, then `tools/sync_vm.sh` (or
  `--ref <branch>`) pulls it; it refuses while the VM is busy.
- **Publish through the deploy gate.** `tools/deploy_from_vm.sh` pulls the
  VM's served set and runs `tools/predeploy_check.py` against the live
  build. `--push` publishes a new version folder to
  `gs://solar-potential-map-tiles/v<N>/`, keeps the newest three, never
  overwrites or prunes the served version, and commits only the
  `site-config.js` switch. `--push-reviewed "<why>"` publishes past a gate
  whose list has been rendered and judged. Roll back by pointing
  `site-config.js` `dataBase`/`dataVersion` at an older folder.
- **Review gates warn on previews and block only production.** A failed
  `tools/predeploy_check.py` gate in `tools/remote_build.sh` prints
  `GATE WARNING` and continues for build-only runs and developer previews,
  so unattended runs are not held up by human review; `--interactive` stops
  and asks instead. Production `--publish` still needs a passing gate or
  `--reviewed "<why>"`.
- **Developer previews are separate from production.** The shared cloud
  quickstart publishes each gated build to the public preview
  bucket in `config.CLOUD_SHARED_BUILD["preview_bucket"]` (with a warning
  when the gate flagged items), under immutable
  `devel/<alias>/runs/` paths, and updates only that alias's pointer. The
  bucket expires `devel/` objects after `preview_retention_days`. Never put
  private inputs or production releases there.
- **Judge a layout change before building.** Run the build's own code on a
  sample with the change off and on:
  `tools/layout_ab.py <region> --n 200 --sample-file s.json --out on.json`
  (each change has a `SOLAR_*` switch), score both with
  `tools/layout_health.py`, and render them side by side on the photo. After
  a build, also run `tools/face_area_check.py` against the previous layouts
  (a building should not lose roof-face area) and the deploy gate, and render
  every roof either flags.
- **Stop VM jobs by session; check for orphans.** Launch long jobs with
  `setsid nohup` and stop them with `pkill -s <session>`. Killing a Python
  parent by PID leaves its multiprocessing workers (~1.8 GB each) running.
  Before a build,
  `ps -eo pid=,ppid=,args= | awk '$2==1 && /multiprocessing\.spawn/'` must
  print nothing. Never `pkill -f` a pattern that is also in your own ssh
  command.
- **Keys** come from the environment or `.env`, else Google Secret Manager
  (`src/project_secrets.py`; secret `linz-api-key`). Never commit or print a
  key.
- **Hand-drawn markup governs** the roofs it covers (`data/roof_labels.json`).
  Judge geometry by renders against the photo and by measurement against the
  markup, not by panel counts. No per-building special cases.
- **Where there is no LiDAR**, a survey marked `"elevation": "synthetic"`
  builds on a modelled surface (`src/synthesize_dsm.py`) and every building
  carries `pitch_guessed`.
- **Keep status current** at every milestone (build launched, failed or
  finished, gate result, publish, infrastructure change, bug found): update
  BACKLOG.md, the owner's status page, and any doc the change makes wrong.

## Verified facts

### Pipeline
- Each region builds in its own Python process, because decoded point-cloud
  tiles stay in memory.
- `src/build_plan.py` is the single per-region step plan (prepare:
  `outline_fit`, `repeat_roofs`; stages through `run_stage.py`; finish:
  `add_addresses`, `emit_region`). The local executor in `tools/pipeline.py`,
  `src/run_district_build.sh` and `src/build_region.sh` all read it, and
  `tests/test_build_plan.py` fails on drift. Input preparation, stage
  selection and combine scope still differ per engine
  ([tooling execution](docs/developers/tooling-execution.md#stage-plans-across-engines)).
- `src/run_district_build.sh` plans each region from per-building build keys
  (clean / patch / full), runs layout, gating, reranking, derivation,
  confidence, horizon and raster stages, then `emit_region.py` per region and
  `combine_regions.py` once. `src/merge_regions.py` is retired.
- `src/region_build.py` treats `pilot` as a region under
  `data/regions/pilot/`; pilot source inputs are fetched under `data/` and
  symlinked into the region tree.
- Per-region outputs: `data/regions/<area>/`. Emitted outputs:
  `data/out/<area>/`. Combined map data: `data/`.
- `src/run_stage.py` wraps each stage: preflight checks, completion markers
  under `data/build_state/`, and skips a stage only when its inputs are older
  than its marker.
- `config.CLOUD_SHARED_BUILD["local_region"]` (a locality id) is the default
  region for `tools/pipeline.py`, and `["regions"]` the shared-cloud default.
  Both sit inside the `CLOUD_SHARED_BUILD` block, which
  `build_keys._hashed_bytes` strips, so moving a default changes no build
  key. Any other `config.py` edit, comments included, changes every stage
  hash and marks every published region stale. `config.PIPELINE_REGION` is
  the deprecated fallback; `config.REGIONS` names still build, with a
  warning. The CLI takes any region id.
- A region id is its path under `data/regions/` and `data/out/`
  (`src/region_ids.py`): a LINZ NZ Suburbs and Localities area
  `<council>/<town>/<suburb>` (layer `config.LINZ_SUBURBS_LOCALITIES_LAYER`,
  slugs of the LINZ ASCII names, `no-town` when LINZ gives none,
  `-<linz-id>` appended on a name collision), or a legacy `config.REGIONS`
  bbox name. `src/localities.py prepare` writes a locality's outlines,
  selected by the outlines' `suburb_locality`/`town_city`/
  `territorial_authority` attributes, and `locality.json` (bbox = outlines
  + 150 m). Dedupe gives a locality every building LINZ assigns to it.
  Flat per-region files use `region_ids.file_slug` (`/` -> `__`).
  `tools/find_area.py` and [choosing-an-area.md](docs/data-maintainers/choosing-an-area.md)
  are the maintainer entry points.
- `config.SERVED_EXCLUDE` keeps the Mt Eden regions and `akl_epsom_01` off
  the map; their builds stay on disk.
- Triage a failed pipeline run from its `report.md`, then the numbered step
  log. Guide: [docs/data-maintainers/troubleshooting.md](docs/data-maintainers/troubleshooting.md).

### Data, CRS and storage
- LINZ building outlines (layer 101290) are the source of truth for which
  buildings a locality holds. `src/linz_exports.py` downloads them once per
  territorial authority through the LINZ Exports API (GeoPackage, EPSG:2193,
  cropped by LINZ layer 3036) into
  `data/sources/linz/layer-101290/<council>/` with a `manifest.json`
  (layer version, export id); the cache is reused until the layer version
  changes. Exports are spatial only; the attribute selection runs locally.
  Selections from the GeoPackage and from WFS matched exactly (IDs,
  properties, geometry) on two Queenstown localities. WFS is the fallback
  (`SOLAR_OUTLINE_SOURCE=wfs`, or any export failure, recorded in
  `locality.json` `outline_source`).
- LINZ WFS fetches use WFS 1.0.0 with `typeName` and `maxFeatures`; 2.0.0
  returned empty results.
- Missing aerial imagery does not stop a build: the fetcher warns and the
  build skips photo-based obstruction detection.
- `src/s3_rasters.py` probes LERC decoding with system GDAL and Rasterio;
  preflight runs the probe before fetching and falls back from `GTiff` to
  `LIBERTIFF`. `SOLAR_TIFF_READER` overrides it. Native GDAL tools are in
  `Brewfile`.
- `data/dem_wide_mosaic.tif` (LINZ 8 m DEM, layer 51768, over
  `config.DEM_WIDE_BBOX` with a 30 km buffer; fetched by
  `src/fetch_dem_wide.py`) is required for building horizons and terrain
  masks. LINZ calls it cartographic, not precision terrain.
- `data/` is not disposable: it holds tracked labels, benchmark and verdict
  files and trained models beside ignored inputs and build outputs.
  `git clean -fd` keeps ignored files; `rm -rf data` deletes tracked ones.
- Published data is a version folder in the tiles bucket; the site on
  GitHub Pages reads it through `site-config.js` (`dataBase`,
  `dataVersion`).

### Geometry, sunlight and front end
- Roof precedence: hand-drawn markup > selected faces > LiDAR fallback.
  Selected faces (`SOLAR_SELECTED_FACES=1`, the default): `tools/predict_faces.py`
  scores three readings per building (SAM vit_b, a U-Net line detector, and
  normal-grown LiDAR regions) and writes the winner to
  `data/selected_faces/<id>.json`. A reading scored under 0.60 competes with
  the LiDAR path. Fallback: RANSAC plane partition and straight skeleton
  (`src/roof_skeleton.py`). Ridge snap and the plant-deck rule are off by
  default (`SOLAR_RIDGE_SNAP`, `SOLAR_PLANT_DECKS`), as are a plane per
  section on faces that curve in plan (`SOLAR_CURVED_SPLIT`) and refitting the
  tilt of an empty face its returns reject (`SOLAR_EMPTY_FACE_REFIT`). A
  drawn line end within 1 m of another drawn line is joined to it
  (`SOLAR_DANGLE_JOIN_M`, 0 turns it off). The
  3D view takes obstruction box tops from the DSM where `SITE.obstTopFromDsm`
  is set (on in this deployment) or with `?SOLAR_OBST_TOP_DSM=1`. It reads
  every height at the building's LiDAR position, taking off the photo lean
  (`img_dx`/`img_dy`) the geometry was moved by; `?SOLAR_LEAN_HEIGHTS=0` or
  `SITE.leanHeights = false` turns that off.
- Two prepare steps in `src/build_plan.py` (every engine), both on by default
  (`=0` turns either off). `SOLAR_OUTLINE_FIT`: `src/outline_fit.py` slides an outline that is
  clearly offset from the roof the LiDAR sees onto it (writes
  `data/regions/<r>/outline_fit.json`; layout, derive, horizons and heat map
  read it through `fitted_outlines`). `SOLAR_REPEAT_ROOFS`:
  `src/repeat_roofs.py` finds sets of identical roofs by their LiDAR surfaces
  and gives every member the reading that explains the whole set best
  (`data/repeat_faces/<id>.json`, read before `data/selected_faces`; an agreed
  reading skips the per-building LiDAR competition). Both feed the build keys.
- Buildings demolished or replaced since the survey are withheld by the
  `demolished` stage (`src/demolished.py`, after `gate_panels`): a pixel
  screen on every outline (survey photo vs map photo), then a Claude vision
  referee on the screened few hundred; "gone" or "replaced" at confidence
  0.7+ replaces the layout with reason `demolished`. Answers are cached per
  building in `data/regions/<r>/demolished.json` (key: outline and lean);
  the key is the project secret `Claude-API`, and runs stop at
  `SOLAR_DEMOLISHED_MAX_USD` (default $5 a region). The referee is paid and
  runs only when `SOLAR_DEMOLISHED_REFEREE=1`; every entry point asks a
  yes/no question at the start (default no; non-interactive runs and
  `--yes` decline unless `--paid-referee` or the env is set), via `build_plan.confirm_paid`.
  `SOLAR_DEMOLISHED=0` turns the stage off. Drawn
  roofs are never withheld. `config.DEMOLISHED_BUILDING_IDS` still drops
  field-reported outlines from the build entirely.
- `src/derive_solar_potential.py` builds the building summary by adding up
  its layout, so totals and layouts match.
- Shading uses per-building 72-direction horizons (`horizon_b64`,
  `horizon_beam_pct`) from the 8 m DEM and the 1 m DSM, seen from eave height.
- Sunlight is calibrated to NIWA SolarView GHI in the Wakatipu basin, else the
  nearest NIWA measured-radiation station, scaled by ERA5 spatial ratios
  (`src/solar_model.py` header).
- `config.py` holds shared non-secret defaults, including
  `CLOUD_SHARED_BUILD` and `PV_ASSUMPTIONS`. Secrets such as `LINZ_API_KEY`
  go in the ignored root `.env`. Cloud profiles live in
  `~/.config/nz-solar-potential/cloud-profiles.json`.
- `site-config.js` holds per-deployment front-end settings. `bill_tool.js`
  is the pricing model; `economics.js` holds cost tiers, prices and the
  battery simulation it uses.
- The refit API exists only in `src/live_server.py`; static hosting lacks it.

Last checked against the code: 2026-10-28.

The national candidate-area snapshot is maintained in
[Areas](docs/data-maintainers/areas.md), regenerated by `tools/list_areas.py`
from LINZ locality attributes using the pipeline's region-id helpers.
It excludes lakes and inland bays, matching `tools/find_area.py` defaults;
it does not certify building presence or survey coverage.

## Open questions

- What provenance record should go with each published data version?
- Should containerisation follow local workflow measurements? See
  [ADR 0001](docs/maintainers/decisions/0001-containerisation-strategy.md).
- How are pilot root inputs created or symlinked into `data/regions/pilot/`
  on a fresh checkout?
- Should `src/live_server.py` support region outputs, or stay limited to the
  root pilot inputs?
