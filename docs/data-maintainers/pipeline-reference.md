# Pipeline reference

Look up the stages, reports and outputs of a single-region local run (also
used on a cloud profile VM). Start one with the [quickstart](../quickstart.md);
diagnose failures with [Troubleshooting](troubleshooting.md).

## Regions

- `config.REGIONS`: region names and WGS84 bounding boxes.
- `config.SURVEYS`: each survey's layers and coverage. A region must sit
  wholly inside one survey.
- `config.PIPELINE_REGION`: the local/profile default region. Shared-cloud
  mode instead defaults to `config.CLOUD_SHARED_BUILD["regions"]`.
  Pass a name to override it. The original central Queenstown pilot is `pilot`.

Add a region by editing `config.py`.

## Needs

Complete [Local setup](local-setup.md) for system tools, Python and credentials.
Additional operating limits:

- About 10 GB free for a small region.
- Optional: the vision step needs torch, segment-anything, the SAM checkpoint
  and both roof-line models in `data/models/`. Without them it is skipped.

`--mode cloud --profile NAME` runs this sequence in the existing developer-VM
checkout; update that checkout first. Shared cloud mode instead uses the
district engine, not the table below. Cloud commands are in the
[cloud quickstart](../quickstart-cloud.md); exact backend differences are
in the [execution reference](../developers/tooling-execution.md#regional-workflow-differences).

## Local and profile-VM steps

These are not the shared-cloud district sequence. The local pipeline writes
stage markers but does not pass `--skip-done`: rerunning creates a new report
and reruns the commands, with any input reuse handled by individual tools.

| # | Step |
|---|---|
| 01 | Preflight: validate area and runtime |
| 02 | Fetch outlines, elevation, imagery, point cloud; ensure the wide DEM (`src/fetch_regions.py`, `src/fetch_dem_wide.py`) |
| 03 | Vision precompute (optional) |
| 04 | Build roof geometry and layout |
| 05 | Gate panel layouts |
| 06 | Rerank panel layouts |
| 07 | Derive building solar potential |
| 08 | Render building verification report |
| 09 | Patch roof confidence |
| 10 | Bake per-building horizons |
| 11 | Register imagery alignment (optional) |
| 12 | Build per-pixel solar heat map |
| 13 | Add building addresses (optional) |
| 14 | Emit regional map tiles (`src/emit_region.py`) |
| 15 | Combine map dataset (`src/combine_regions.py`) |
| 16 | Build local 3D terrain tiles |
| 17 | Validate map contract |
| 18 | Start local map preview |

Exact definitions: [tools/pipeline.py](../../tools/pipeline.py).

## Outputs

- `data/pipeline_runs/<UTC-run-id>/`: `report.md` (status, timing and effect
  of each step), `run.log` (all output, tagged `[QS-NN]`), and the preview
  server's PID and log.
- `data/out/<region>/`: the region's emitted tiles and summaries.
- `data/`: the combined map. **Step 15 replaces whatever map data was there**
  with the selected region. Other emitted regions remain under `data/out/`
  but are not included in this combine. Don't run it over map data you want
  to keep. Root `data/solar_potential.geojson` is not a current map input.
- `data/regions/<region>/quickstart_report.html`: per-building check cards.

The preview serves the map and combined data on `127.0.0.1` and needs
internet access for basemaps. It does not expose the pilot-only refit API
([execution boundaries](../developers/tooling-execution.md#deployment-and-ownership)).
Stop the preview with:

```sh
kill "$(cat data/pipeline_runs/<run-id>/map-preview-server.pid)"
```

## Statuses

- `PASS`: ran and its output checks passed.
- `DEGRADED`: carried on without a source or capability. Read the effect.
- `SKIPPED`: optional step not run (usually vision).
- `FAIL`: stopped. Later steps show `NOT RUN`.

A finished run can still be degraded. Missing LiDAR weakens the geometry and
can leave the heat map empty; missing imagery, addresses or terrain each lose
their part. Read the report before trusting a result.

## Cleaning up

**Do not delete `data/` wholesale.** It contains curated labels, benchmarks,
verdicts and trained models as well as generated files. Do not delete a
whole region directory without inspecting it either: it can contain review
results and provenance you need to keep. Preserve `.env` and `.venv`.

To reclaim local space, identify regenerable inputs for the selected region
before removing them. The wide DEM and point-cloud tiles can be shared by
neighbouring regions. The
[regional publication cleanup](../scale-architecture.md#deleting-inputs)
checks upload sizes and shared-tile ownership; it is not a blanket cleanup
command for a local pipeline run.

`git clean -fd` leaves ignored inputs but deletes untracked work, including
new labels or documentation. Preview with `git clean -nd` and inspect every
path before using it. Do not use `git clean -fdX` to reset this project.
