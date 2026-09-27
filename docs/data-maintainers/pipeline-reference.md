# Pipeline reference

The [pipeline how-to](../quickstart.md) runs one configured region. This
reference covers configuration, run artifacts, stage statuses, and cleanup
limits. Use [Troubleshooting](troubleshooting.md) for failures.

## Region configuration

Regions and their WGS84 bounding boxes are defined in `config.REGIONS`;
survey-specific source layers and coverage are defined in `config.SURVEYS`.
`config.PIPELINE_REGION` is the default region. Pass another configured
region name to `quickstart.sh` or `tools/pipeline.py` to override it. The
selected region must be fully contained by a configured survey. The original
central Queenstown pilot is available as `pilot`. To add a
region, edit the configuration in `config.py`; the retired per-machine JSON
area files are not loaded.

The pipeline requires Python 3.11+, a configured project environment, a LINZ
API key, and `tippecanoe`, `tile-join`, and `tippecanoe-decode` on `PATH`. See
[Local setup](local-setup.md) and the platform-specific tool setup for
[macOS](env-setup-mac.md#install-project-tools),
[Ubuntu](env-setup-ubuntu.md#install-tippecanoe-map-tools), and
[WSL](env-setup-win.md#install-tippecanoe-map-tools). Allow about 10 GB free
for a small region; downloads and processing time vary.

Set `LINZ_API_KEY` in the launching shell or ignored `.env`. Its value is not
recorded in reports or logs. Vision precomputation is optional and requires
torch, segment-anything, the SAM checkpoint, and both roof-line models under
`data/models/`. Missing prerequisites cause that optional stage to be skipped.

## Run artifacts

Each run writes diagnostics to `data/pipeline_runs/<UTC-run-id>/`. `run.log` is
the combined chronological log with each step's stdout/stderr tagged by
`[QS-NN]`; `report.md` records stage statuses, timing, and consequences. When
the preview server starts, its PID and server log are also kept in that run
directory. The report path and preview URL are printed at the end.

The pipeline emits regional intermediates under `data/out/<region>/`, then
combines the selected region directly into `data/`. **This replaces the
currently served combined map dataset**; it does not preserve other regions in
the combined map. Do not run it against shared/site data when that replacement
is not intended. The preview serves the resulting `data/` map on loopback.

The visual verification cards are at
`data/regions/<region>/quickstart_report.html`. The preview serves directly from
`data/` via loopback, and requires a browser connection to public basemap
providers. Live parameter refitting requires the separate `src/live_server.py`
`/api/refit` service.

Stop the preview server after review using the PID file path shown in the run
report:

```sh
kill "$(cat data/pipeline_runs/<run-id>/map-preview-server.pid)"
```

## Stage statuses and interpretation

- `PASS`: the command succeeded and expected output checks passed.
- `DEGRADED`: the run continued with a missing source or reduced capability;
  read the consequence in the report.
- `SKIPPED`: an optional stage was not run, commonly vision precomputation.
- `FAIL`: dependent work stopped; later stages are marked `NOT RUN`.

A successful pipeline does not imply every optional input was available.
Missing LiDAR can weaken geometry evidence or leave the heatmap empty;
unavailable vision, imagery alignment, addresses, or terrain reduce their
respective capabilities. Inspect the report before interpreting a result.

Step 02 runs `src/fetch_regions.py` and `src/fetch_dem_wide.py`; the latter
ensures the shared wide DEM exists and covers the configured region extent.

The numbered stage implementation and exact labels are in
[tools/pipeline.py](../../tools/pipeline.py). The output contract is validated
before the preview starts. For methodology rules and per-stage verification,
see the [Reviewer's guide](../developers/reviewers-guide.md).

## Clean-room rebuild limits

`data/` contains tracked curated roof labels, benchmark/truth/verdict assets,
trained roof-line models, and map-facing site outputs, alongside ignored raw
inputs and generated intermediates. These tracked assets are not all
retrievable from LINZ. Preserve `.env` or the launching shell's key and `.venv`.

`git clean -fd` removes untracked, non-ignored files but leaves ignored caches.
Do not use `git clean -fdX` or `rm -rf data` as general cleanup. For a
region-only rebuild, remove only that ignored region's directory and its run
artifacts if needed. The shared `data/dem_wide_mosaic.tif` and
`data/pointcloud/` caches can be reacquired, but removal costs additional
downloads and affects other builds. A clean-room rebuild was verified for one
small Queenstown region only; this does not establish availability for every
survey or make tracked inputs regenerable.
