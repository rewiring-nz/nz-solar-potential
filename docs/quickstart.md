# Quickstart: run the methodology on your own patch of NZ

This project estimates rooftop solar potential for every building in a
district. This quickstart lets you run the production roof/layout and yield
stages on a small area you choose — your own street, if it is in a covered
survey — and inspect the result. It uses the same stage implementations,
thresholds, and gates as the regional build. It also emits and validates the
same map data contract into a run-isolated directory, then starts a local
MapLibre preview pointed only at that data. It does not publish or replace
the normal map dataset.

## What you need

- macOS, Linux, or Windows, ~10 GB free disk, Python 3.11+
  - **Windows:** the simplest route is WSL (Windows Subsystem for Linux),
    where everything below is just the Linux instructions. Native Windows
    works too, from Git Bash — with one caveat: the per-building time
    budget relies on a POSIX alarm signal Windows does not have, so it is
    switched off there (the run says so). A pathological roof will take a
    long time instead of being dropped and named. On a small quickstart
    area that is unlikely to matter.
- A free LINZ API key (data.linz.govt.nz → account → API keys, enable the
  **REST API** scope)
- Environment set up per
  [data-maintainers/local-setup.md](data-maintainers/local-setup.md)
- Map build tools `tippecanoe`, `tile-join`, and `tippecanoe-decode` available
  on `PATH` (install per the [macOS](data-maintainers/env-setup-mac.md#install-project-tools),
  [Ubuntu](data-maintainers/env-setup-ubuntu.md#install-tippecanoe-map-tools),
  or [WSL](data-maintainers/env-setup-win.md#install-tippecanoe-map-tools)
  setup guide). The quickstart checks these before fetching data.
- Optional, for the full vision chain: `pip install torch segment-anything`
  (the quickstart downloads Meta's public SAM checkpoint, 358 MB; both
  project roof-line model checkpoints must be present in `data/models/`)

## Run it

```bash
cp my_area.example.json my_area.json   # edit name + bbox
export LINZ_API_KEY=...
bash quickstart.sh my_test_area
```

The area name passed on the command line must exactly match `name` in
`my_area.json`. The script validates this, the WGS84 bbox, the Python
executable, and the presence (not the value) of `LINZ_API_KEY` before
starting network work. Credentials are never copied into the report or logs.
The bbox must be contained by a configured survey, or the JSON must specify
the new survey's DSM layer and any available imagery and point-cloud settings.
For a point-cloud bulk URL, also provide its tile-index layer and tile year;
use `null` when raw point-cloud data is not published.

Keep the bbox small the first time (~0.005° × 0.005°, a few dozen
buildings): the run and reports stay manageable. The time varies with source
downloads, LiDAR coverage, geometry, and map tiling.
The defaults in the example file cover the Queenstown Lakes district; for
any other part of NZ, set your district's DSM/DEM/imagery layer ids (the
example file says where to find them). Building outlines are national.

### Primary outputs

- **Pipeline run report (`report.md`):**
  `data/quickstart_runs/<name>/<run-id>/report.md`
  The primary audit record. Open this first to verify that all steps passed,
  check durations, review any degraded inputs (such as missing point-cloud
  tiles), and access links to detailed per-step logs (`step-NN-*.log`).
- **Visual verification cards (`quickstart_report.html`):**
  `data/regions/<name>/quickstart_report.html`
  The visual report. Open in a browser to inspect aerial photos with overlaid
  roof facets (white), obstructions (red), and placed panels (blue).
- **Local interactive map:** the quickstart starts a loopback-only server and
  opens the new-area preview in a browser when possible. The exact URL is
  printed at the end and recorded in `report.md`. The preview uses this run's
  data under `data/quickstart_runs/<name>/<run-id>/map-data/data/`; it does not
  read or overwrite the normal map files under `data/`.

Use `bash quickstart.sh my_test_area --no-open-browser` to suppress automatic
browser opening; the server still starts and the report contains its URL. The
local server's PID and log are recorded in the run directory. Stop it after
review with `kill "$(cat data/quickstart_runs/<name>/<run-id>/map-preview-server.pid)"`.

## Run artifacts and debugging

For common fixes, including Python dependency failures, LINZ key permissions,
missing map-build tools, and preview-server errors, see
[data-maintainers/troubleshooting.md](data-maintainers/troubleshooting.md).

Each invocation gets a unique, ignored run directory:
`data/quickstart_runs/<name>/<UTC-run-id>/`. Logs and map outputs are separate
from generated region data and are not published with the regular map. The
terminal prints the exact report and preview paths when the run stops or
finishes. Build inputs and intermediate region results still live under
`data/regions/<name>/`.

- `report.md` is the incrementally updated run record. Its step numbers and
  descriptions match the `[QS-NN]` prefixes in the terminal and logs. Each row
  records inputs, expected outputs, status, exit code, duration, and the
  failure consequence or degradation.
- `run.log` is the complete ordered log with those same step prefixes.
- `step-NN-*.log` contains the full stdout/stderr for that step. Start with
  the first failed step in `report.md`, then inspect its matching log.
- `run.json` is the machine-readable record, including run metadata, timestamps,
  durations, exit codes, and statuses.
- `region-out/<name>/` contains the PMTiles and support files emitted for this
  run; `map-data/data/` contains the combined dataset the local map reads.
- `map-preview/preview.html` and its local script assets are the run-specific
  entry page. `map-preview-server.log` records the local static server output.

### Rebuilding after removing local data

The `data/` directory is a mixture of inputs, generated files, and committed
site assets; it is not safe to delete as a whole. `git clean -fd` removes only
untracked, non-ignored files, so it leaves ignored raw-data caches in place.
The ignored region inputs and intermediate products can be fetched or rebuilt
for a configured survey, but this is not true of all data in the directory.

Keep the following before a clean-room rebuild:

- `.env` (or the shell's `LINZ_API_KEY`) and `.venv`;
- tracked hand-curated labels, truth/verdict and benchmark files, label queues,
  and trained roof-line models under `data/`. These are project inputs and
  evaluation assets, not LINZ downloads;
- tracked map-facing outputs if you want the published map to keep working
  while rebuilding. Full deletion removes them too; restore them from Git if
  that was intentional.

For a quickstart-only clean rebuild, remove only the ignored, area-specific
inputs and outputs you intend to regenerate (for example,
`data/regions/<area>/` and that area's `data/quickstart_runs/<area>/`). The
fetcher can reacquire the configured source data; `data/dem_wide_mosaic.tif`
and `data/pointcloud/` are also regenerable but shared across areas, so do not
remove them unless the additional download and impact are intended. Then run
the quickstart normally. Do not use `git clean -fdX` or `rm -rf data` as a
general cleanup command. The clean-room test verified a rebuild for one small
Queenstown area, not every survey, source service, or future environment.

`PASS` means the command succeeded and expected output checks passed.
`DEGRADED` means the run continued with an optional source or method missing;
read the consequence in the report before interpreting the results. `SKIPPED`
is used for optional vision precomputation when prerequisites are absent.
`FAIL` stops dependent work. Later steps are marked `NOT RUN`, not silently
treated as successful. The report is written after every status change, so a
failed or interrupted run still leaves a useful partial record.

`PASS` for the final map step means the emitted contract was checked and the
local preview page returned HTTP 200; a PMTiles byte-range probe also returned
HTTP 206, which confirms the local server can deliver vector tiles. An
optional vision, address, image-alignment, or terrain limitation is recorded
as `SKIPPED` or `DEGRADED`, not hidden. This preview is static: the map's live
parameter-refit controls still require the separate `/api/refit` service.

The separate visual output remains at
`data/regions/<name>/quickstart_report.html` — one card per building with the
aerial photo, roof facets (white), detected obstructions (red), placed panels
(blue), derived numbers, and the geometry source.

## What actually ran (and where to read it)

The numbered executable steps, in order, are:

| step | description (exact log/report label) | code | what it claims |
|---|---|---|---|
| 01 | Preflight: validate area and runtime | `tools/quickstart_run.py` | validate the area/environment; make the run record |
| 02 | Fetch outlines, elevation, imagery, and point cloud | `src/fetch_regions.py` | LINZ outlines, DSM/DEM/imagery, and point-cloud fetch (this fetcher invokes point-cloud acquisition itself) |
| 03 | Vision precompute (optional) | `tools/predict_faces.py` | candidate roof-face readings; skip/failure is recorded and geometry uses available readings and normal fallbacks |
| 04 | Build roof geometry and layout | `src/run_stage.py build_layout_geojson` | roof facets; precedence markup > selected vision faces > normal partition path |
| 05 | Gate panel layouts | `src/run_stage.py gate_panels` | applies panel surface gates |
| 06 | Rerank panel layouts | `src/run_stage.py rerank_layouts` | ranks/fills gated panel layouts |
| 07 | Derive building solar potential | `src/run_stage.py derive_solar_potential` | derives building totals from panel layouts |
| 08 | Render building verification report | `tools/quickstart_report.py` | renders per-building visual verification cards |
| 09 | Patch roof confidence | `src/run_stage.py patch_roof_confidence` | carries layout confidence onto building records |
| 10 | Bake per-building horizons | `src/run_stage.py bake_building_horizons` | adds per-building near/far horizon data for map detail |
| 11 | Register imagery alignment (optional) | `src/run_stage.py register_imagery` | measures roof-to-photo shifts when a reference image is available |
| 12 | Build per-pixel solar heatmap | `src/run_stage.py build_heatmap_raster` | creates the raster layer used by the map's default heatmap view |
| 13 | Add building addresses (optional) | `src/run_stage.py add_addresses` | adds LINZ address labels; IDs remain usable if this optional fetch fails |
| 14 | Emit regional map tiles | `src/emit_region.py` | writes this run's building/layout PMTiles and map support files |
| 15 | Combine isolated map dataset | `src/combine_regions.py` | creates the map-facing contract from this run's one region without replacing `data/` outputs |
| 16 | Build local 3D terrain tiles | `tools/build_terrain_tiles.py` | adds the optional 3D DSM surface to the isolated dataset |
| 17 | Validate map contract and prepare preview | `tools/validate_quickstart_map.py` | checks output contract and writes a run-specific preview config/page |
| 18 | Start local map preview | `tools/quickstart_serve.py` | serves the preview on loopback and verifies PMTiles byte-range responses |

The methodology's rulebook, with the enforcement point and check command
for every rule, is
[developers/reviewers-guide.md](developers/reviewers-guide.md).

## How to verify it

1. **Pipeline health in `report.md`.** Open
   `data/quickstart_runs/<name>/<run-id>/report.md` (the run terminal prints the
   exact path on completion). Confirm all steps completed (`PASS` or explainable
   `DEGRADED`/`SKIPPED` status for optional vision/point-cloud steps). If any step
   degraded or failed, click the linked `step-NN-*.log` to review raw stdout/stderr.
2. **Facets against the photograph (`quickstart_report.html`).** Open
   `data/regions/<name>/quickstart_report.html` in a browser and compare white
   boundaries to what you can see: ridges where ridges are, one facet per
   roof plane, boundaries straight. This is the single strongest check —
   it is the one performed on every change.
3. **Panels against physics.** Panels are 1.134 × 1.961 m
   (`config.PANEL_WIDTH_M/HEIGHT_M`, a real Trina module). Measure a roof
   you know: does the count fit the area, minus the 0.3 m edge setback
   and obstruction clearances?
4. **Numbers against arithmetic.** For any building:
   `panel_count × 0.5 kW = kwp`; annual kWh ÷ panel count should sit
   within NZ's plausible per-panel yield (roughly 550–800 kWh/panel/yr
   depending on tilt/aspect/shading). The derivation is
   `src/derive_solar_potential.py` — totals are sums over the panels you
   can see, by construction.
5. **One building, fully.** `python src/refit_one.py <building_id>`
   rebuilds one roof with full diagnostics; compare its facet list to the
   report card in `quickstart_report.html`.
6. **The assumptions.** Every displayed PV assumption lives in
   `config.PV_ASSUMPTIONS` with its justification inline. Losses,
   panel spec, and the irradiance model are all there to be argued with.
7. **Your own roof.** If you live in a covered area, run your street and
   check the building you know best. That is the test the project exists
   to pass.

## Known limits of a quickstart run

- If your survey has no public LiDAR point cloud on OpenTopography, the
  fetch report records absent/missing tiles and the build may rely on the
  1 m DSM instead of raw returns. Evidence and surface gates may be weaker;
  interpret this as degraded rather than equivalent data.
- Without torch/SAM or either required roof-line checkpoint, vision
  precomputation is explicitly marked `SKIPPED`. Geometry still follows its
  normal selected-reading and fallback precedence; it is not accurate to say
  every roof necessarily uses only one fallback method.
- Imagery alignment, address labels, and 3D terrain are optional enhancements;
  if unavailable, the report explains which map capability is reduced. The
  2D building/layout map and its map data contract are still generated and
  validated.
- The local preview uses public basemap tiles and therefore needs a browser
  connection to those services. Its generated solar/map data is local and
  isolated; the quickstart does not publish it or replace the normal site.
- The preview server binds only to `127.0.0.1`. It is a static map preview and
  does not provide live parameter refitting (`/api/refit`). The report records
  the server PID and log; stop the server after review.
