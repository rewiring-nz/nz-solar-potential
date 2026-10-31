# Troubleshooting

Start with the run's `data/pipeline_runs/<run-id>/report.md`. Find the first
`FAIL`, `DEGRADED` or unexpected `SKIPPED` step, then read the lines with the
same `[QS-NN]` tag in `run.log`.

`FAIL` stops dependent steps. `DEGRADED` carries on without a source; a later
`PASS` does not undo that (a DSM-only run stays DSM-only).

## Don't delete `data/`

`data/` holds tracked files you cannot fetch again: roof labels, benchmark and
verdict data, trained models. Do not delete it or a whole region directory
as a troubleshooting step. See [Cleaning up](pipeline-reference.md#cleaning-up)
before removing generated inputs or untracked work.

## Preflight (step 01)

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

### `No usable source survey for this bbox`

The region must be in `config.REGIONS`, as `[west, south, east, north]` in
WGS84, and wholly inside one `config.SURVEYS` entry with a DSM layer. Split a
region that straddles two surveys.

For a new survey: set the DSM layer; add imagery and reference-imagery layers
only if they exist. A point-cloud bulk URL also needs the survey's LiDAR
tile-index layer and tile year; use `null` if there is no point cloud. A wrong
year or store quietly drops the run to DSM only, so read step 02's coverage
note.

## Fetch (step 02)

### HTTP 401 from the Exports API

The key works for web services but cannot create exports. LINZ cannot add
permissions to a key: make a new one with export permission and replace it.
See [src/fetch_data.py](../../src/fetch_data.py).

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

## Map build and preview

### Tippecanoe missing (step 01)

Install `tippecanoe`, `tile-join` and `tippecanoe-decode`, open a new
terminal and check with `command -v`. macOS: `brew bundle --file=Brewfile`.
Ubuntu/WSL: [build it](env-setup-ubuntu.md#2-tippecanoe).

### Emit or combine failed (steps 14, 15)

Read the `[QS-14]` or `[QS-15]` lines. Emit needs the region's solar and
layout GeoJSON, the heat-map raster and sidecar, and its build keys. The
required fields are in [tests/test_output_contract.py](../../tests/test_output_contract.py).

`no_estimate` features in `panel_layouts.pmtiles` are intended: the outline of
a building with no estimate. Don't remove them.

### Preview or PMTiles range probe failed (step 18)

Check `map-preview-server.log` and the `[QS-18]` lines. Confirm
`data/buildings.pmtiles` exists and is not empty and the port is free. The
server listens on `127.0.0.1` only. A good range probe returns HTTP 206, not
200.

### No roof heat layer

Step 12 reports how many buildings rendered from LiDAR. Zero means no usable
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
