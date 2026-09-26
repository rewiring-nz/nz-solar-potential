# Troubleshooting

Use this guide when a quickstart or local data build stops unexpectedly. Start
with that run's `report.md` at
`data/quickstart_runs/<area>/<run-id>/report.md`. Find the first `FAIL`,
`DEGRADED`, or unexpected `SKIPPED` step, then open its linked
`step-NN-*.log`. The same step number and label appear as `[QS-NN]` in the
terminal and `run.log`.

`FAIL` means the quickstart stopped before dependent steps. `DEGRADED` means
it continued, but the report records a missing source or reduced capability.
A later `PASS` does not erase that degraded input—for example, successful roof
geometry does not turn a DSM-only run into a full point-cloud run.

## Quickstart preflight errors

### Matplotlib is required for the map heatmap stage

**Report symptom** (Step 01):

> Matplotlib is required for the map heatmap stage but is missing from this Python environment. Install the project requirements and retry.

The current preflight may also print a more specific message naming the
selected interpreter and the exact command to repair it; use that Python path
if it differs from the examples below. Older run reports use the quoted text.

The quickstart uses the Python executable recorded in the report (normally the
project's `.venv`). Installing Matplotlib into a different Python environment
will not fix this error. From the project root, install the declared project
requirements with the environment's interpreter:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'import matplotlib.colors; print(matplotlib.__version__)'
```

The import check should print a version. On Windows Git Bash, use
`.venv/Scripts/python.exe` instead of `.venv/bin/python` in both commands. Then
rerun the quickstart. Matplotlib is needed by `src/build_heatmap_raster.py`,
which creates the map's heatmap layer; it is included in `requirements.txt` and
pinned in `requirements.lock.txt`.

If installation succeeds but the import still fails, verify that `python`
and `pip` refer to the same environment by always invoking pip as
`<environment-python> -m pip`. If the error remains, keep the import check's
full output and the Python path from `run.json`; do not randomly upgrade
scientific packages in the shared environment.

### `LINZ_API_KEY is not set`

**Report symptom** (Step 01):

> LINZ_API_KEY is not set in the environment or .env (value was not recorded).

The runner checks that a key is present but never records its value. Either
export `LINZ_API_KEY` in the shell that launches the quickstart, or put it in
the project's ignored `.env` file as `LINZ_API_KEY=...`. The fetch scripts load
`.env` themselves. Do not add the key to `my_area.json`, reports, logs, source
control, or chat. See [Local setup — LINZ credentials](local-setup.md#linz-credentials).

### `No usable source survey for this bbox`

**Report symptom** (Step 01): the bbox is not covered by a configured survey,
or the selected survey has no DSM layer.

The whole bbox must be covered by one survey entry. Check `bbox` is
`[west, south, east, north]` in WGS84, then check the DSM layer and survey
coverage in `my_area.json` or `config.SURVEYS`. If the bbox straddles survey
boundaries, choose a smaller bbox inside one survey or split it into separate
runs. Building outlines are national; the raster and point-cloud layers are
not.

For a new survey, configure the correct DSM layer. Add the imagery and
reference-imagery layer IDs only when they apply. A point-cloud bulk URL also
needs that survey's LiDAR tile-index layer and tile year; use `null` for the
bulk URL when raw point cloud is not published. A wrong year/store can return
missing tiles and silently reduce processing to the DSM, so inspect Step 02's
coverage comment rather than relying only on its command exit code.

## LINZ API key and fetch failures

### LINZ Exports API returns HTTP 401

**Log text:**

> 401 from the LINZ Exports API: this LINZ_API_KEY may use the web services but not create exports.

The key can be valid for web-service queries while lacking permission to
create/download raster exports. LINZ does not let you add permissions to an
existing key: create a **new** key with export/download permission enabled and
replace the value in `.env` or the launching shell. Never paste the key into a
report or issue. The fetch code's exact guidance is in
[src/fetch_data.py](../../src/fetch_data.py#L85-L94); credential setup is in
[Local setup](local-setup.md#linz-credentials).

### Exports API returns HTTP 400 or `outside-extent`

**Log text:** commonly `Exports API 400 for layer ...`, `outside-extent`, or
`every one of ... chunks is outside layer ...`.

Check the survey's layer ID and bbox coverage. A LINZ layer may have a broad
published bounding rectangle while actual data has gaps; `outside-extent` on
some imagery chunks can be an expected coverage edge, but a missing DSM or all
DSM chunks outside coverage prevents a meaningful build. Confirm the layer is
the intended product and that its expected format is Grid for DSM/DEM or Raster
for imagery. Use the layer-specific explanation in the Step 02 log; do not
change the bbox or layer ID merely to silence the error.

### Zero building outlines

**Report symptom** (Step 02): `Fetch returned zero building outlines`.

Check that the requested area name matches `my_area.json`, that the bbox is in
WGS84 order `[west, south, east, north]`, and that it covers land/buildings in
New Zealand. The runner stops here because there are no buildings to estimate.

### Missing or incomplete point-cloud tiles

**Report status/comment** (Step 02): `DEGRADED`, `point-cloud coverage
incomplete`, or a warning that tiles are missing from the bulk store.

The run can continue using the 1 m DSM, but roof geometry and panel gates have
coarser evidence; the heatmap stage depends on usable point-cloud returns and
may render no buildings. Check that `lidar_tile_index_layer`,
`pointcloud_bulk_url`, and `pointcloud_tile_year` all describe the same survey.
If the survey does not publish a bulk point cloud, use `null` for the URL and
interpret the run as DSM-based; do not point at a different survey's store to
make the tile count look complete. See the fetch details in
[src/fetch_pointcloud_regions.py](../../src/fetch_pointcloud_regions.py#L120-L155).

## Map-build and preview errors

### Tippecanoe command missing

**Report symptom** (Step 01):

> Map-ready output requires these commands on PATH: tippecanoe, tile-join, tippecanoe-decode.

Install the listed map-build tools (`tippecanoe`, `tile-join`,
`tippecanoe-decode`) for your platform, then open a new terminal and verify
they resolve with `command -v tippecanoe`, `command -v tile-join`, and
`command -v tippecanoe-decode` (or the platform equivalent). The quickstart
checks these before starting large downloads. On macOS, install them with
`brew bundle --file=Brewfile`; on Ubuntu or WSL, follow
[Ubuntu environment setup](env-setup-ubuntu.md#install-tippecanoe-map-tools).

### Regional map emission or combine failed

**Report symptom** (Step 14 or 15): `FAIL`, a nonzero subprocess exit code, or
missing PMTiles/support files in the step comment.

Open the matching step log. Step 14 is the per-region output from
`src/emit_region.py`; check that solar/layout GeoJSON, the heatmap raster and
sidecar, and required build keys exist. Step 15 combines only this run's
regional output into `map-data/data/`, not the site's normal `data/` folder.
Do not manually combine a test area into the normal map dataset. The
[output-contract test](../../tests/test_output_contract.py) describes the
required regional PMTiles fields and support data.

#### `IndentationError` in `src/combine_regions.py`

If Step 15's log reports `IndentationError: unexpected indent` at the
`--skip-markup-lines` argument, the combine command did not begin; this is a
Python source indentation problem, not a data or Tippecanoe failure. Update the
checkout to the corrected code, then verify it with:

```sh
.venv/bin/python -m py_compile src/combine_regions.py
.venv/bin/python src/combine_regions.py --help
```

The help output should include `--skip-markup-lines`. Rerun the quickstart to
produce a new run report; the old run remains a failure record. The CLI smoke
test in [tests/test_quickstart_map.py](../../tests/test_quickstart_map.py)
guards this parser path.

### Local preview or PMTiles range probe failed

**Report symptom** (Step 18): `Could not start local map server`,
`Could not verify local preview`, or `PMTiles byte-range probe returned ...`.

Check `map-preview-server.log` and the Step 18 log. Confirm the preview page
and `map-data/data/buildings.pmtiles` exist and are non-empty, and that the
reported port is available. The server binds only to `127.0.0.1`; use the URL
printed in the report on the same computer. A successful PMTiles check must
return HTTP 206 for the requested byte range, not HTTP 200 for the whole file.
The map also needs a browser connection to its public basemap tile providers.

The runner records the server PID in `map-preview-server.pid`. Stop it after
review with:

```sh
kill "$(cat data/quickstart_runs/<area>/<run-id>/map-preview-server.pid)"
```

### Map opens but has no roof heat layer

Check Step 12 in `report.md`. Its comment records how many buildings rendered
from LiDAR and the output size. Zero rendered buildings means there was no
usable point-cloud roof evidence; the base building/layout map can still work,
but the raster heat layer may be empty. Check Step 02 for the tile coverage
and confirm the survey publishes the point cloud configured for this bbox.

## Reporting a new failure

When asking for help, share the first failing step's exact `[QS-NN]` error and
its `step-NN-*.log`, plus the relevant non-secret parts of `run.json` (area,
bbox, selected survey, statuses, and exit codes). Do **not** share `.env`, the
LINZ key, or any shell transcript that contains it. Include your operating
system and whether `.venv` or `.venv/Scripts` is the Python environment used.
