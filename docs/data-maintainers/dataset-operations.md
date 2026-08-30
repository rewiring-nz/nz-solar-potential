# Dataset operations

Run commands from the `nz-solar-potential` directory with the virtual
environment active. `config.py` is the authoritative definition of pilot and
regional bounding boxes, data-layer identifiers, exclusions, and PV
assumptions.

## Prerequisites

Ensure you have setup your environment, per the [Local Setup](local-setup.md).

## Start the Python environment

Activate your python environment if not done so already. The python environment must be activated at the start of each new Terminal session. The `.venv` folder remains on the computer between sessions.
```sh
cd ~/nz-solar-potential
source .venv/bin/activate
```

If `.venv/bin/activate` is not found, return to [Create the project Python
environment](local-setup.md#create-the-project-python-environment) before
continuing. If activation succeeds, `(.venv)` appears at the start of the
Terminal prompt.

## Set coverage area in config

1. Confirm LINZ coverage for the intended area and update from the defaults set in `config.py`.

## Acquire source data

1. Fetch the original pilot inputs:

```sh
.venv/bin/python src/fetch_data.py
```

The pilot download is saved in the project's `data/` directory. It includes
`building_outlines.geojson`, `dsm_mosaic.tif`, and `imagery_mosaic.tif`, plus
download and extraction files created while the rasters are processed.

2. Fetch one named expansion region, or omit the name to fetch every configured
   region:

```sh
.venv/bin/python src/fetch_regions.py frankton_flats
```

This command saves the named region under
`data/regions/frankton_flats/`. Each region directory contains its building
outlines and mosaicked DSM and imagery inputs. For example, replace
`frankton_flats` with the region name passed to the command.

Regional fetching is resumable: existing outputs are skipped. Imagery exports
are split into chunks no larger than $8\,\mathrm{km^2}$ because imagery is the
largest source and provider export jobs have practical size limits. Some rural
areas have no urban aerial imagery; the fetcher records a warning and the
pipeline continues with LiDAR-only inputs.

## Prepare and build

After fetching all regions that will be combined, assign each overlapping
building outline to one owning region:

```sh
.venv/bin/python src/region_build.py
```

For a fast layout iteration on the pilot or named areas:

```sh
bash src/run_dev_loop.sh pilot
```

For a full regional build, then merged site-level outputs:

```sh
bash src/run_full_build.sh frankton_flats
```

The full build runs each area in a separate Python process to keep decoded
LiDAR tile memory bounded. Logs are written to `data/build_logs/`. A failed
area prevents merging so incomplete output is not mistaken for a full release.

## Serve the map locally

Run either command from the `nz-solar-potential` project directory.

For a static preview of generated data, run:

```sh
python3 -m http.server 8000
```

Open `http://localhost:8000/preview.html` in a browser.

For the local parameter-refit API and tuning sliders, run:

```sh
.venv/bin/python src/live_server.py
```

This server loads substantial source data at startup and is intended for local
development only. It serves the project from its parent directory, so open
`http://localhost:8000/nz-solar-potential/preview.html`. Static hosting does
not provide its `/api/refit` endpoint.

## Validate before publishing

1. Read each requested region log for failed stages and warnings.
2. Run `src/audit_layout_quality.py` for rebuilt areas; investigate low fill,
   fragmented arrays, multiple panel angles, and excessive obstruction shares.
3. Use `src/validate_obstructions.py` when changing obstruction behaviour.
4. Use the local map preview to inspect a representative mix of rooftops,
   including known difficult buildings and areas without imagery.
5. Verify merged feature counts and output sizes. `data/solar_potential.geojson`
   is the site-level building summary; `data/panel_layouts.geojson` is detailed
   layout data and can reach hundreds of megabytes.

## Publish current artifacts

Static hosting publishes the repository root according to `netlify.toml`.
The map can use committed static data, but the local `/api/refit` capability is
not available in static hosting. Do not deploy a monolithic detailed layout
file directly to browsers at full district scale; the merge code identifies
PMTiles conversion as the intended delivery path.

## Recovery and safety

- Pipeline writes that replace JSON use atomic replacement, so an interruption
  should retain either the prior artifact or a complete replacement.
- Re-run resumable fetches after a network failure; do not delete complete
  source files merely to retry.
- Preserve source metadata, config changes, command logs, and validation
  evidence with a dataset release so estimates can be traced to their inputs.