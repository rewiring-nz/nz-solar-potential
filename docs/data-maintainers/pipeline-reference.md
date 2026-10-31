# Pipeline reference

Look up the stages, reports and outputs of a single-region local run (also
used on a cloud profile VM). Start one with the [quickstart](quickstart.md);
diagnose failures with [Troubleshooting](troubleshooting.md).

## Regions

A region is one of:

- A LINZ suburb or locality, named `<council>/<town>/<suburb>` from the LINZ
  NZ Suburbs and Localities layer, e.g.
  `queenstown-lakes-district/arrowtown/arrowtown`. Find one with
  `tools/find_area.py`; see [Choosing an area](choosing-an-area.md). No
  `config.py` edit is needed. Its outlines and bounding box are written by
  `src/localities.py` to `data/regions/<id>/` (`building_outlines.geojson`,
  `locality.json`) at preflight.
- A legacy bounding-box region named in `config.REGIONS`, such as `pilot`
  (the original central Queenstown pilot) or `town_gorge_north`.

Related settings:

- `config.SURVEYS`: each survey's layers and coverage. A region must sit
  wholly inside one survey.
- `config.PIPELINE_REGION`: the local/profile default region. Shared-cloud
  mode instead defaults to `config.CLOUD_SHARED_BUILD["regions"]`.
  Pass a region id to override it.

Region ids are relative paths: a locality's outputs are under
`data/regions/<council>/<town>/<suburb>/` and `data/out/<council>/<town>/<suburb>/`.
Flat files named after a region (build markers, logs) replace `/` with `__`.

## Needs

Complete [Local setup](local-setup.md) for system tools, Python and credentials.
Additional operating limits:

- About 10 GB free for a small region.
- Optional: the vision step needs torch, segment-anything, the SAM checkpoint
  and both roof-line models in `data/models/`. Without them it is skipped.

`--mode cloud --profile NAME` runs this sequence in the existing developer-VM
checkout; update that checkout first. Shared cloud mode instead uses the
district engine, not the table below. Cloud commands are in the
[cloud quickstart](quickstart-cloud.md); exact backend differences are
in the [execution reference](../developers/tooling-execution.md#stage-plans-across-engines).

## Local task queue

Local runs now submit a task to a loopback coordinator and start one worker
process by default. No Google account, GCS bucket, `gcloud` or SQLite is
needed for queue operation. Fetching source data still needs the LINZ
credentials and dependencies described in [Local setup](local-setup.md).
Process cleanup is tested locally on macOS; CI is configured to run these
tests on Linux. Use WSL for Windows, not native Windows processes.

```sh
bash quickstart.sh pilot --no-open-browser
bash quickstart.sh pilot --workers 2 --no-open-browser
```

Each invocation still builds **one region**, with the same numbered stages
below. Extra workers do not split that region. Tasks in the same checkout
serialize because they share caches, stage markers and the combined map.
Use separate, human-managed checkouts and data directories for concurrent
builds; do not symlink their writable data trees together. The coordinator
does not create worktrees, allocate CPU/RAM or manage separate environments.

The printed queue directory defaults to `data/pipeline_queues/<id>/`.
`--queue-dir PATH` chooses a persistent directory instead. It must not be
owned by another running coordinator; it is not an option for attaching to
one. A directory can retain prior tasks, including failures; prefer a new
directory for a new run.

### Manually submit and run multiple workers

For debugging, start one coordinator in a terminal:

```sh
.venv/bin/python -m tools.pipeline_queue serve --queue-dir data/my-local-queue
```

In another terminal, submit tasks **before** starting workers:

```sh
.venv/bin/python -m tools.pipeline_queue submit --queue-dir data/my-local-queue \
  pilot --no-open-browser
.venv/bin/python -m tools.pipeline_queue submit --queue-dir data/my-local-queue \
  town_gorge_north --no-open-browser
```

Run each worker command in its own terminal, using distinct names:

```sh
.venv/bin/python -m tools.pipeline_queue worker --queue-dir data/my-local-queue --worker one
.venv/bin/python -m tools.pipeline_queue worker --queue-dir data/my-local-queue --worker two
```

These two tasks serialize in the same checkout. To exercise concurrent
builds, submit a task with `--workspace /absolute/path/to/another/checkout`
and `--python /absolute/path/to/its/.venv/bin/python`. That checkout needs
the queue implementation, its dependencies and its own prepared environment.
Workers exit when the submitted batch is terminal; start workers again
after submitting more tasks.

Inspect status while the coordinator runs:

```sh
.venv/bin/python -m tools.pipeline_queue status --queue-dir data/my-local-queue
```

### Evidence, conflicts and recovery

- `state.json` persists task specifications, worker/attempt ownership and
  terminal outcomes. `attempts/<task>-<attempt>.log` retains subprocess output.
  Existing `data/pipeline_runs/` reports and preview URLs are unchanged.
- Numbered pipeline output streams to the worker terminal. Queue success
  means the pipeline returned zero; read its report for `DEGRADED` steps.
- Tasks record the Git commit and hashes of configuration, pipeline and
  queue source files. A change to those files before execution rejects the
  task. This is not a full dependency/model/environment snapshot; developers
  must still agree on matching inputs and versions.
- The coordinator binds only to loopback. `.connection.json` holds a local
  access token and is owner-readable; never share it or publish queue files.
  This is a trusted local developer tool, not a multi-user/public service.
- One active task per checkout is allowed within a queue. Across queues,
  `data/pipeline-workspace.lock/` detects a conflicting queued build and
  fails it visibly. Older/direct stage scripts do not honor that lock:
  humans must coordinate those operations.
- Ctrl+C stops the foreground coordinator/worker. The pipeline-managed pool
  stops its workers on interruption. Attempt ownership rejects stale
  acknowledgements; there is no automatic lease takeover, retry or failover.
  Failed tasks stay failed and require deliberate resubmission with a new id.
- Clean coordinator shutdown marks pending/running tasks interrupted.
  After a crash, pending tasks remain pending on restart; running tasks
  become interrupted. A hard-killed worker can leave a task running until
  coordinator shutdown/restart.
- After a crash, inspect `state.json`, attempt logs and lock `owner.json`
  files. Stop any surviving workers/child processes before manually removing
  the specific stale `coordinator.lock/` or `pipeline-workspace.lock/`
  directory. Restarting a coordinator marks previously running tasks
  interrupted, never successful. Do not remove locks on a live run.

The existing GCS fleet queue is **not migrated** by this first local slice.
It still has different task, retry and regional-execution semantics; see
the [consolidation boundary](../developers/tooling-execution.md#local-queue-consolidation-boundary).

## Local and profile-VM steps

Steps 04–17 are the shared stage plan ([src/build_plan.py](../../src/build_plan.py)),
the same per-region steps the district and fleet engines run; steps 12 and
18–21 are local-only. The local pipeline writes stage markers but does not
pass `--skip-done`: rerunning creates a new report and reruns the commands,
with any input reuse handled by individual tools.

| # | Step |
|---|---|
| 01 | Preflight: validate area and runtime |
| 02 | Fetch outlines, elevation, imagery, point cloud; ensure the wide DEM (`src/fetch_regions.py`, `src/fetch_dem_wide.py`) |
| 03 | Vision precompute (optional) |
| 04 | Fit outlines to the roof (optional; `SOLAR_OUTLINE_FIT=0` skips) |
| 05 | Agree identical roofs (optional; `SOLAR_REPEAT_ROOFS=0` skips) |
| 06 | Register imagery alignment (optional) |
| 07 | Build roof geometry and layout |
| 08 | Gate panel layouts |
| 09 | Screen demolished buildings (paid referee only if approved, below) |
| 10 | Rerank panel layouts |
| 11 | Derive building solar potential |
| 12 | Render building verification report |
| 13 | Patch roof confidence |
| 14 | Bake per-building horizons |
| 15 | Build per-pixel solar heat map |
| 16 | Add building addresses (optional) |
| 17 | Emit regional map tiles (`src/emit_region.py`) |
| 18 | Combine map dataset (`src/combine_regions.py`) |
| 19 | Build local 3D terrain tiles |
| 20 | Validate map contract |
| 21 | Start local map preview |

**Paid step question.** Before queueing (local) or inside the cost
confirmation (cloud), the pipeline asks whether to use the paid demolition
referee, with an estimate. Only `yes` approves; Enter, a non-interactive
run or `--yes` declines. `--paid-referee` / `--no-paid-referee` answer it in advance.
See [demolition screening](dataset-operations.md#demolition-screening).

**Unattended and interactive runs.** `--yes` (`-y`) accepts the cloud cost
estimate without a prompt; the estimate is still printed and logged. Review
gates never block a run by default: a failed deploy gate on the shared cloud
preview prints `GATE WARNING` and continues. `--interactive` stops and asks
at a failed gate instead (it cannot be combined with `--yes`). The local
pipeline has no review gate, so `--interactive` changes nothing there. See
[cloud quickstart: deploy gate](quickstart-cloud.md#deploy-gate).

Exact definitions: [tools/pipeline.py](../../tools/pipeline.py).

## Outputs

- `data/pipeline_runs/<UTC-run-id>/`: `report.md` (status, timing and effect
  of each step), `run.log` (all output, tagged `[QS-NN]`), and the preview
  server's PID and log.
- `data/out/<region>/`: the region's emitted tiles and summaries.
- `data/`: the combined map. **Step 18 replaces whatever map data was there**
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
[regional publication cleanup](../maintainers/scale-architecture.md#deleting-inputs)
checks upload sizes and shared-tile ownership; it is not a blanket cleanup
command for a local pipeline run.

`git clean -fd` leaves ignored inputs but deletes untracked work, including
new labels or documentation. Preview with `git clean -nd` and inspect every
path before using it. Do not use `git clean -fdX` to reset this project.
