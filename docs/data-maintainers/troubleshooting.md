# Troubleshooting

Start with the run's `data/pipeline_runs/<run-id>/report.md`. Find the first
`FAIL`, `DEGRADED` or unexpected `SKIPPED` step, then read the lines with the
same `[QS-NN]` tag in `run.log`.

`FAIL` stops dependent steps. `DEGRADED` carries on without a source; a later
`PASS` does not undo that (a DSM-only run stays DSM-only).

## Local queue errors before step 01

If no numbered report was created, inspect the printed queue directory:
`state.json` records task status/errors, and `attempts/` contains executor
logs when a subprocess started. A queue `succeeded` result does not erase
`DEGRADED` steps in the pipeline report.

- **Queue already owned / workspace conflict:** another coordinator or
  queued build owns that directory. Coordinate with its owner; do not
  delete a live lock or run direct stages against its writable data.
- **Code/configuration changed after submission:** submit again after
  agreeing which revision/settings to run. The old task is not silently
  updated.
- **Coordinator unavailable / worker exited unexpectedly:** stop and
  inspect surviving processes and attempt logs. There is no automatic
  retry or stale-worker takeover.

For status commands, lock locations and deliberate crash recovery, use
[Evidence, conflicts and recovery](pipeline-reference.md#evidence-conflicts-and-recovery).
The local coordinator requires no cloud credentials; a later fetch failure
may still require a LINZ key.

### `Namespace` has no attribute `include_imagery`

The local queue must persist the imagery choice and pass it to the pipeline
worker. Older tasks without that option default to including imagery.
Update the checkout with the queue-option fix, restart the web panel, create
a new estimate and submit again. Reinstalling dependencies does not fix this
error. Completed outline downloads remain cached.

If a worker crashes between numbered steps, the run report may remain
`RUNNING` even though the job failed. Read the job's `stdout.log` or the
queue's attempt log for the traceback; pending steps did not run.

## Don't delete `data/`

`data/` holds tracked files you cannot fetch again: roof labels, benchmark and
verdict data, trained models. Do not delete it or a whole region directory
as a troubleshooting step. See [Cleaning up](pipeline-reference.md#cleaning-up)
before removing generated inputs or untracked work.

## Preflight (step 01)

### GDAL is installed but a web build reports missing commands

Check `command -v gdalbuildvrt gdal_translate` in the terminal used to start
the panel. If both resolve there, reinstalling requirements or Homebrew tools
does not address the failure. Web jobs must inherit the panel's environment,
including `PATH`, when adding the source layers pinned by the estimate.
Update the checkout with the environment-inheritance fix in
[the job runner](../../tools/pipeline_webui/jobs.py), stop and restart the
panel from that terminal, then create a fresh estimate and retry.
Existing panel processes do not reload Python changes.

If either command is genuinely missing, follow the system-tool setup in
[Local setup](local-setup.md); Python requirements do not install GDAL CLI
tools. No cached data needs to be deleted for this preflight failure.

### LERC raster dependency check

`Cannot decode required LERC terrain tiles`: system GDAL's TIFF reader cannot
decode LERC, even if `liblerc` is installed. The pipeline tests this on a
small offline file before fetching, for both Rasterio and system GDAL.

- macOS: `brew bundle --file=Brewfile`
- Ubuntu/WSL: install `gdal-bin`, then run the check:

```sh
.venv/bin/python -c 'from src.s3_rasters import require_raster_support; require_raster_support()'
```

The pipeline tries `GTiff`, then `LIBERTIFF`. To force one:

```sh
SOLAR_TIFF_READER=GTiff bash quickstart.sh
SOLAR_TIFF_READER=LIBERTIFF bash quickstart.sh
```

Values: `auto`, `GTiff`, `LIBERTIFF`. Tested on macOS with GDAL 3.13.3 and
Rasterio 1.5.2; untested on Ubuntu/WSL. Don't set `GDAL_SKIP=GTiff`
globally: it breaks TIFF writing.

If Rasterio fails, reinstall `requirements.txt` into the interpreter in the
report. System GDAL and Rasterio's bundled GDAL are separate; upgrading one
does not fix the other. Nothing is installed automatically.

### Matplotlib is required for the map heatmap stage

Install the requirements into the Python named in the report (usually `.venv`):

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'import matplotlib.colors; print(matplotlib.__version__)'
```

On Windows Git Bash use `.venv/Scripts/python.exe`. Always run pip as
`<python> -m pip` so it installs into the same environment.

### `LINZ_API_KEY is not set`

Export `LINZ_API_KEY` in the shell, or add `LINZ_API_KEY=...` to `.env`. See
[LINZ credentials](local-setup.md#linz-credentials).

### LINZ source metadata or coverage could not be resolved

Confirm the locality boundary and LINZ metadata service are available. The
resolver selects a DSM whose published extent contains the complete build
bbox; it does not use a partial or neighboring survey. If no DSM covers the
area, the build warns and uses a synthetic surface with guessed roof pitches.

Area-specific layers are discovered automatically and cached under
`data/sources/linz/`. Retry after a transient LINZ failure; if the selected
metadata appears incorrect, inspect the cached metadata and the LINZ layer
extent before changing the resolver.

## Fetch (step 02)

### `LINZ export creation timed out`

Raster-export creation waits up to 120 seconds for LINZ, matching the
council-outline exporter. Retry the build if LINZ does not respond in time;
completed raster mosaics are reused. Creation requests are not automatically
retried because LINZ may have created a job before its response timed out.
See [src/fetch_data.py](../../src/fetch_data.py).

### HTTP 401 from the Exports API

The key works for web services but cannot create exports. LINZ cannot add
permissions to a key: make a new one with export permission and replace it.
See [src/fetch_data.py](../../src/fetch_data.py).

A locality's building outlines come from a per-council Exports API
GeoPackage. Without export permission, or when the LINZ export fails or
times out (30 minutes), preflight prints `council GeoPackage unavailable
(...); using LINZ WFS` and carries on with WFS. `locality.json` records the
reason under `outline_source.fallback_reason`. To retry the export:
`.venv/bin/python src/linz_exports.py fetch "<Council name>" --refresh`.
See [Building outlines source](pipeline-reference.md#building-outlines-source).

### `deprecated` warning for a region name

The region is a legacy `config.REGIONS` bounding-box name. It still builds.
For new work use a LINZ locality id; see [Choosing an area](choosing-an-area.md).

### HTTP 400 or `outside-extent`

Check the layer ID and that it covers the bbox. Some imagery chunks outside
coverage are normal at the edge of a survey; a DSM wholly outside is not.
DSM/DEM layers are Grid, imagery is Raster. Don't change the bbox or layer
just to silence it.

### `Fetch returned zero building outlines`

Check the region name, bbox order and that it covers buildings in NZ.

### Point-cloud coverage incomplete

The run carries on with the 1 m DSM: coarser geometry and gates, and the heat
map may be empty. Check `lidar_tile_index_layer`, `pointcloud_bulk_url` and
`pointcloud_tile_year` all describe the same survey. Never point at another
survey's store. See [src/fetch_pointcloud_regions.py](../../src/fetch_pointcloud_regions.py).

## Layout workers

### `Error in sys.excepthook`

Older layout workers left Rasterio datasets open until interpreter teardown,
after their Python callbacks were unavailable. Current workers close their
DSM and imagery handles through a multiprocessing finalizer before teardown.
Update the checkout and retry the build; no extra Python package is needed.
See [src/build_layout_geojson.py](../../src/build_layout_geojson.py).

## Map build and preview

### Tippecanoe missing (step 01)

Install `tippecanoe`, `tile-join` and `tippecanoe-decode`, open a new
terminal and check with `command -v`. macOS: `brew bundle --file=Brewfile`.
Ubuntu/WSL: [build it](env-setup-ubuntu.md#2-tippecanoe).

### Emit or combine failed (steps 17, 18)

Read the `[QS-17]` or `[QS-18]` lines. Emit needs the region's solar and
layout GeoJSON, the heat-map raster and sidecar, and its build keys. The
required fields are in [tests/test_output_contract.py](../../tests/test_output_contract.py).

`no_estimate` features in `panel_layouts.pmtiles` are intended: the outline of
a building with no estimate. Don't remove them.

### Preview or PMTiles range probe failed (step 21)

Check `map-preview-server.log` and the `[QS-21]` lines. Confirm
`data/buildings.pmtiles` exists and is not empty and the port is free. The
server listens on `127.0.0.1` only. A good range probe returns HTTP 206, not
200.

### No roof heat layer

Step 15 reports how many buildings rendered from LiDAR. Zero means no usable
point cloud; the rest of the map still works. Check step 02's coverage.

### 404s in the console, but the map works

Expected: the address search tries `addresses/index.json` before
`addresses.json`; the static preview has no `/api/refit`; heat-map tiles
outside the area don't exist. If nothing shows at all, check the start
location and turn on **Panel Layout**.

## Asking for help

Share the first failing `[QS-NN]` error, its `run.log` lines, the non-secret
parts of `report.md` (area, bbox, survey, statuses, exit codes), your OS and
which Python you used. Never share `.env` or the key.
