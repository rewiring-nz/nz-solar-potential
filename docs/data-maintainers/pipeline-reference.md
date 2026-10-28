# Pipeline reference

What `quickstart.sh` / `tools/pipeline.py` reads, writes and reports. The
how-to is the [quickstart](../quickstart.md); failures are in
[Troubleshooting](troubleshooting.md).

## Regions

- `config.REGIONS`: region names and WGS84 bounding boxes.
- `config.SURVEYS`: each survey's layers and coverage. A region must sit
  wholly inside one survey.
- `config.PIPELINE_REGION`: the default region. Pass a name to override it.
  The original central Queenstown pilot is `pilot`.

Add a region by editing `config.py`.

## Needs

- Python 3.11+ and the project environment ([Local setup](local-setup.md)).
- `LINZ_API_KEY` in the shell or `.env`. It is never written to reports or logs.
- `tippecanoe`, `tile-join` and `tippecanoe-decode` on `PATH`
  ([macOS](env-setup-mac.md#2-project-tools),
  [Ubuntu/WSL](env-setup-ubuntu.md#2-tippecanoe)).
- About 10 GB free for a small region.
- Optional: the vision step needs torch, segment-anything, the SAM checkpoint
  and both roof-line models in `data/models/`. Without them it is skipped.

Runs are local by default. `--mode cloud --profile NAME` runs the same region
on a VM, and `--cloud ACTION` manages that VM; see the
[cloud quickstart](../quickstart-cloud.md). Cloud mode runs the checkout
already on the VM, so update it to the commit you want first.

## Steps

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
  with the selected region. Don't run it over data you want to keep.
- `data/regions/<region>/quickstart_report.html`: per-building check cards.

The preview serves `data/` on `127.0.0.1` and needs internet access for the
basemaps. The tuning sliders need `src/live_server.py` (`/api/refit`). Stop
the preview with:

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

`data/` holds tracked files that LINZ cannot give back: roof labels,
benchmark and verdict data, trained models. Keep `.env` and `.venv` too.

- `git clean -fd` leaves ignored files alone. Safe.
- Don't use `git clean -fdX` or `rm -rf data`.
- To rebuild one region, delete only `data/regions/<region>/` and its run
  folders.
- `data/dem_wide_mosaic.tif` and `data/pointcloud/` can be fetched again, but
  that costs downloads and affects other builds.

A clean rebuild has been checked for one small Queenstown region only.
