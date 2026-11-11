# Build data

The local **Build data** workspace keeps area selection, existing data status,
estimates and job progress beside one map. Expand **New build**, **Queue** and
**Jobs** to follow a build without switching pages. A target toggle at the top
picks where the work applies:

| Target | Shows | A build runs | Costs |
| --- | --- | --- | --- |
| **Local** | Prepared and built areas in this checkout, plus its cached datasets | `tools/pipeline.py <region>` on this computer, as the [quickstart](quickstart.md) does | Nothing in the cloud |
| **Developer** | Built areas in developer previews on the shared VM | `tools/pipeline.py "<regions>" --mode cloud --cloud build` on the shared build VM, publishing to your own `devel/<alias>/` preview, as the [cloud quickstart](quickstart-cloud.md) does | Google Cloud, shown before you start |
| **Published** | Built areas in the live map data version | `tools/remote_build.sh --regions "<regions>" --publish` on the shared build VM; it publishes only if the [deploy gate](../../tools/predeploy_check.py) passes | Google Cloud, shown before you start |

The panel never publishes past a failed gate: `--reviewed` is left to the
command line, after someone has rendered and judged the gate's list.

## Start it

Do the [local setup](local-setup.md) first; the panel needs the same Python
environment and LINZ API key. The Developer and Published targets also need
`gcloud` signed in with access to the shared project
([team access](team-access.md)) and the GitHub CLI for your preview alias.

```bash
.venv/bin/python tools/pipeline_web.py
```

It prints a link such as
`http://127.0.0.1:8766/pipeline.html#token=…` and opens it. Options:

| Option | Meaning |
| --- | --- |
| `--port N` | Listen on another loopback port (default 8766; the map preview uses 8765). |
| `--python PATH` | Interpreter for pipeline runs (default: the one running the panel). |
| `--no-open-browser` | Print the link without opening it. |

`Ctrl+C` stops the panel. Running builds are cancelled first and queued
ones are dropped. Cancelling a cloud build stops the local command; the VM
is stopped only if that build started it.

Closing the browser tab does not stop the panel. If its terminal is still
open, return to it and press `Ctrl+C`. If the terminal is gone, starting a new
panel on the same port fails and names the old one:

```text
Cannot listen on 127.0.0.1:8766 (Address already in use).
An earlier pipeline panel is still running (process 12345).
Stop it with Ctrl+C in its terminal, or from here with:
  kill 12345
```

Run the `kill` command it prints. The panel treats `kill` (SIGTERM) and
`Ctrl+C` (SIGINT) the same way: it cancels running builds and drops queued
ones before exiting, including when it was started in the background. If the
port belongs to another program, the message shows that program's command
instead; check it yourself with:

```sh
lsof -nP -iTCP:8766 -sTCP:LISTEN
ps -p 12345 -o pid=,command=
```

For a panel started with `--port N`, use `N` instead of `8766`. Check that the
listener is gone by repeating the `lsof` command. Do not stop a process just
because it owns the port if its command is unrelated.

## Use it

- **New build**: search for a suburb or locality, or zoom in to level 10 or
  closer and click areas on the map. **Existing data for this area** reports
  the selected target's known area state and links to available previews or
  reports. A built summary, prepared inputs and a failed last run are distinct
  states; no area record does not mean that no shared cached dataset covers
  the area. Local cached dataset extents are shown separately in **Layers &
  legend**; an extent does not guarantee complete valid data. **Refresh**
  rereads area status and, for cloud targets, VM status. **Estimate** shows,
  per area:
  - building count
  - download size
  - time
  - disk use
  - warnings, such as no LiDAR or a large area
  - blockers, such as unavailable LINZ metadata or too little disk

  Aerial imagery is included by default and can be unchecked. That skips its
  download, alignment, image-based obstruction checks, and paid AI referee;
  outlines, elevation and LiDAR remain unchanged. The estimate recalculates
  when the imagery or paid-referee choice changes, and the resulting choices
  are pinned to the job. The paid AI referee is off by default and is only
  available when imagery is included.

  Covering LINZ elevation and imagery layers are discovered from public layer
  metadata, cached under ignored `data/sources/linz/`, and pinned from estimate
  through execution. Areas with no covering LiDAR DSM use the warned synthetic
  surface path; they are not blocked by a static survey list.

  Selecting a locality also enables **Choose grid cells**. The parent polygon
  is the LINZ suburb/locality boundary; the council name groups localities and
  is not the boundary being built. The panel divides the boundary's bounding
  box into 2×2 through 10×10 cells (default 4×4) and clips each cell to the
  locality polygon. It uses the LINZ key and boundary already needed for area
  search; no additional API key or dataset is required. Select
  individual cells or use **Select all** / **Deselect all**. An empty
  selection cannot be estimated. Changing the parent or grid size invalidates
  the previous estimate.

  The estimate reports the selected building count, the sum of disjoint
  buffered acquisition footprints, download estimate, runtime, disk estimate
  and warnings. These are planning estimates, not limits. A partial-area build
  runs locally only; it cannot start a Developer or Published build. It runs
  the normal pipeline as one work area, not one pipeline per selected cell.

  Buildings are selected as whole LINZ building-outline features. For each
  outline, the pipeline finds its representative point in EPSG:2193 and assigns
  it to the lexically lowest selected-area ID whose polygon covers that point.
  Only buildings owned by a selected grid cell are included; boundaries never
  clip buildings and ties cannot duplicate them. This is a stable rule for an
  identical parent, grid size and selection.

  A selection has a deterministic ID below its parent:
  `data/regions/<parent>/grid-<16-hex-id>/`. Its `selection.json`
  records the parent, selected IDs, boundary dataset/version and geometry,
  ownership rule, building IDs and acquisition footprints. Pipeline artefacts
  are written below that selection directory, and emitted artefacts below the
  matching `data/out/` directory; the parent locality's files are not changed.
  The run combines only the selection into the local map data, so that map
  output represents the selected work area, not complete parent coverage.
  Selections are excluded from normal complete-area listings and are never
  published as complete coverage.

  For Developer and Published the estimate also shows the Google Cloud
  cost (see [Cloud builds](#cloud-builds)). The Start button is enabled after
  checking the cost-acknowledgment box. Local builds start directly from the
  button. The paid AI referee is off unless you tick it; the estimate shows
  its upper cost.
- **Queue**: each lane's running and waiting jobs, titled by locality rather
  than job ID. Running cards show
  the current stage and area, approximate progress, elapsed time, estimated
  remaining time and the age of the last log/status activity. Waiting jobs
  show their lane position, wait reason and estimated runtime, not a moving
  bar or a promised start time. **Remove** drops a waiting job; **Cancel**
  stops a running one. Completed jobs are shown only in **Jobs** so each job
  appears in one stage at a time.
- **Jobs**: completed job history, newest first. Select a job to see progress,
  per-area status and report links, and to open its live log. **Cancel** stops
  a running build; finished areas are kept.

Queue cards refresh every five seconds and use the same progress calculation
as job details. Completed and deliberately skipped stages advance progress,
weighted by historical stage durations when available; interrupted, failed
and unrun stages do not count as complete. Percentages can remain still
during a long stage and are approximate, not elapsed-time animations.
Only a successful job reaches 100%, after final combining or publishing.
Preparation and cloud builds without measured stage telemetry show an
indeterminate indicator instead of an invented percentage. Cloud stage
details are not currently streamed from the VM.

An exhausted runtime estimate reads **Taking longer than estimated**, not
zero seconds remaining. **Last activity** measures log/status writes, not a
worker heartbeat; a quiet stage is not necessarily stuck. If updates fail,
the last known cards remain visible with a disconnected warning; progress
bars are hidden until updates reconnect so stale work does not appear live.

Estimates are planning assumptions, not limits; actual time and download can
be higher. The time per building comes from the median of this checkout's
last ten finished runs, or the defaults in
[`estimate_defaults.json`](../../tools/pipeline_webui/estimate_defaults.json),
which record where each figure was measured. An estimate expires after 30
minutes; a build can only start from the estimate that was shown.

## What a build does

### Map layers

**Layers & legend**, below the navigation controls, reuses the shared component
and layer styling used by the canonical map. It groups analysis, derived and
source datasets above the basemap. Panels
paint above solar-analysis building outlines, which paint above the heat map.
Basemap selection switches between the existing Esri/LINZ aerial combination
and OpenStreetMap without resetting overlays.

For **Local**, the dataset catalogue checks actual files in the checkout,
including shared cached sources and available intermediate outputs. A cached
extent is drawn as a faint fill with a dashed boundary; it is not a claim
that every pixel contains usable data. Missing requested LiDAR tiles are not
shown as cached. Layer data is loaded on demand when enabled.

Prepared/built region and locality-selection boundaries are separate **Build
areas** overlays. Their status colours are not evidence that all sources were
downloaded. **Refresh**, area-status refreshes and completed jobs refresh
the catalogue; existing dataset visibility is retained where the dataset
still exists.

**Developer** and **Published** never inherit this computer's cached-source
overlays. Until remote source catalogues are supplied, the legend explicitly
reports that those dataset layers are unavailable; their existing area listings
and preview links remain usable.

Jobs run in two lanes, each one job at a time: the **local** lane on this
computer and the **cloud** lane on the shared VM. A job started while its
lane is busy waits in the queue. The lanes are independent, so a local
build and a cloud build can run together.

Local builds:

- Areas build one at a time, each as
  `tools/pipeline.py <region> --no-open-browser --no-preview-server`. The
  paid referee flag follows your choice.
- A failed area does not stop the rest.
- Each pipeline run's combine step leaves only its own region in the map
  dataset under `data/`. So when a job builds more than one area, the panel
  then runs `src/combine_regions.py` over every region with
  `data/out/<region>/summary.json`. That puts all built areas back on one
  map; the terrain tiles stay the last region's.
- Only one build runs per checkout. The panel refuses to start while a
  command-line build holds `data/pipeline-workspace.lock`.
- If the panel is stopped mid-build, that job shows as `interrupted` next
  time.

## Cloud builds

- **Cost.** The estimate shows an upper Google Cloud cost in NZD, its
  basis and the VM. VM hours are the estimated build time × 1.5, between 8
  and 16 (the spot VM stops itself 16 hours after starting). Unit prices
  come from `config.CLOUD_SHARED_BUILD`. The figure is an estimate, not a
  spending cap; actual charges can be higher. Starting needs the unchecked
  cost-acknowledgment box to be selected, and the server checks that the start
  request carries the same cost the estimate showed.
- **Code.** The VM builds `main` as pushed to GitHub, not uncommitted or
  unpushed changes in this checkout. If the VM is already building, its
  code sync refuses and the job stops before building anything.
- **Publish blockers.** `remote_build.sh --publish` commits and pushes
  `site-config.js` on `main` from this checkout. A Published estimate is
  blocked, and the start is checked again, unless this checkout is on
  `main`, `site-config.js` has no local changes, and there are no unpushed
  commits.
- **Results.** A developer job links its preview. A published job shows the
  gate result and, when it passed, the published data version.

Each area's run directory is `data/pipeline_runs/<run-id>/`, the same as a
command-line run. It holds `report.md`, `run.log` and `status.json` (see
[Pipeline reference](pipeline-reference.md)). The panel keeps its own
records under `data/pipeline_web/`, which is ignored by Git:

- `jobs/<id>/job.json` and `stdout.log`
- `estimates/`
- `cache/` (LINZ locality polygons for 7 days, and building counts)

The run directory also holds `acquisition_timings.json`. Each dataset record
has its source/layer, UTC start, outcome, numeric elapsed seconds, and elapsed
`hh:mm:ss`; transfer seconds and `hh:mm:ss` are separate where measurable.
Each record is one dataset acquisition attempt, so a failed attempt and a
later retry remain separately visible.
Total acquisition time includes export queue/polling, transfer and local
preparation; transfer time measures byte-transfer work only, not waiting or
mosaicking. Where export polling or local preparation takes at least a minute,
the log also gives a compact `export_wait` or `local_prepare` phase. Multipart
exports and LiDAR tiles are summarized per dataset. The shared 8 m terrain DEM
keeps disjoint selection footprints, each with the normal 30 km terrain
buffer, rather than downloading the enclosing rectangle.
Unavailable and failed acquisitions are recorded too; failure records contain
the exception type, not a potentially sensitive URL or error message. Grid
outline preparation happens during Estimate, before the pipeline run, and its
timing is stored in
`data/regions/<parent>/grid-<16-hex-id>/acquisition_timings.json`.

## Security

- The panel listens on `127.0.0.1` only and answers only a localhost
  `Host` header. It never serves dotfiles such as `.env`.
- Every API call needs the per-launch token in the printed link. Anyone
  with that link can start builds on this computer, so do not share it.
- Build requests must come from the panel's own page as JSON.
- The LINZ key stays on the server. Locality requests go through it, and
  errors name only the failure type.
- Anyone with the link can also start paid builds on the shared VM; each
  still needs the cost-acknowledgment checkbox against the shown estimate.

The panel is not a hosted service; it runs on your computer with your
`gcloud` credentials.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "Read-only: the panel server is not running" | Start `tools/pipeline_web.py` and open the link it prints. |
| "Cannot listen on 127.0.0.1:8766 (Address already in use)" | If the message names an earlier pipeline panel, run the `kill <pid>` it prints ([Start it](#start-it)). If another program holds the port, start the panel with `--port N` or `--port 0`. |
| "This link's token is out of date" | The panel was restarted; open the new link. |
| "No LINZ API key found" | Set `LINZ_API_KEY` as in [local setup](local-setup.md). |
| "Could not load grid" | Check the LINZ locality and API key, then retry. |
| "No LINZ building outlines were found in the selected grid cells" | Some grid cells contain no mapped buildings; select cells that cover buildings. |
| "zoom in" when choosing areas | The view is wider than 1°; zoom in. |
| "a CLI build is running in this checkout" | Wait for it, or stop it and see [Local task queue](pipeline-reference.md#local-task-queue) before removing a stale lock. |
| An area failed | Open its report from the Jobs section and follow [Troubleshooting](troubleshooting.md). |
| macOS "Python quit unexpectedly" after Estimate | The panel process loaded PROJ before starting a child process; on macOS, PROJ's SQLite fork handler can crash that child. Stop an older panel and restart it on current code to clear the loaded library state. Area calculations and local layer-catalogue generation run in a separate process (`tools/pipeline_webui/geo_worker.py`). |
| VM status `UNKNOWN` | The message shows gcloud's error; usually `gcloud auth login` or missing access to the project ([team access](team-access.md)). |
| "cannot publish: … unpushed commit(s)" | Push or reset those commits, then estimate again. |
| A cloud job stopped at the code sync | The VM was busy with another build; wait for it, then start again. |
