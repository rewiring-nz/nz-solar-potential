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

Each run writes to `data/quickstart_runs/<region>/<UTC-run-id>/`, separate from
the normal map dataset. The report path and preview URL are printed at the
end. Start with `report.md`: it records stage statuses, timing, consequences,
and links to individual logs. `run.log` is the ordered output, `step-NN-*.log`
contains a stage's full output, and `run.json` is the machine-readable record.

The visual verification cards are at
`data/regions/<region>/quickstart_report.html`. The isolated preview bundle is
under `map-preview/`; the map reads the dataset under `map-data/data/`. The
preview is static, serves only on loopback, and requires a browser connection
to public basemap providers. Live parameter refitting requires the separate
`src/live_server.py` `/api/refit` service.

Stop the preview server after review using the PID file path shown in the run
report:

```sh
kill "$(cat data/quickstart_runs/<region>/<run-id>/map-preview-server.pid)"
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
