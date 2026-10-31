# Web pipeline panel

A local web page for the pipeline: choose suburbs and localities on a map,
see an estimate, type `yes`, and follow the build. It runs the same
`tools/pipeline.py` as the [quickstart](quickstart.md), one region after
another, on this computer. It does not start cloud machines or cost cloud
money.

## Start it

Do the [local setup](local-setup.md) first; the panel needs the same Python
environment and LINZ API key.

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

`Ctrl+C` stops the panel. A build that is still running is cancelled first.

## Use it

- **Regions** lists every area built or prepared in this checkout:
  - green: built
  - amber: inputs downloaded, not built
  - red: the last run failed

  Each area links to its map preview and last `report.md`. **Rebuild** adds
  the area to a new build.
- **New build**: search for a suburb or locality, or zoom in to level 10 or
  closer and click areas on the map. **Estimate** shows, per area:
  - building count
  - download size
  - time
  - disk use
  - warnings, such as no LiDAR or a large area
  - blockers, such as no survey coverage or too little disk

  To start, type `yes`. The paid AI referee is off unless you tick it; the
  estimate shows its upper cost.
- **Jobs**: progress, an estimated time left, per-area status with links to
  each run's report, and the live log. **Cancel** stops the area being
  built; finished areas are kept.

Estimates are planning assumptions, not limits; actual time and download can
be higher. The time per building comes from the median of this checkout's
last ten finished runs, or the defaults in
[`estimate_defaults.json`](../../tools/pipeline_webui/estimate_defaults.json),
which record where each figure was measured. An estimate expires after 30
minutes; a build can only start from the estimate that was shown.

## What a build does

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

Each area's run directory is `data/pipeline_runs/<run-id>/`, the same as a
command-line run. It holds `report.md`, `run.log` and `status.json` (see
[Pipeline reference](pipeline-reference.md)). The panel keeps its own
records under `data/pipeline_web/`, which is ignored by Git:

- `jobs/<id>/job.json` and `stdout.log`
- `estimates/`
- `cache/` (LINZ locality polygons for 7 days, and building counts)

## Security

- The panel listens on `127.0.0.1` only and answers only a localhost
  `Host` header. It never serves dotfiles such as `.env`.
- Every API call needs the per-launch token in the printed link. Anyone
  with that link can start builds on this computer, so do not share it.
- Build requests must come from the panel's own page as JSON.
- The LINZ key stays on the server. Locality requests go through it, and
  errors name only the failure type.

The panel is not a hosted service. Running builds on the shared VM from the
browser is planned separately in
[issue #12](https://github.com/rewiring-nz/nz-solar-potential/issues/12);
until then, use the [cloud quickstart](quickstart-cloud.md).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "Read-only: the panel server is not running" | Start `tools/pipeline_web.py` and open the link it prints. |
| "This link's token is out of date" | The panel was restarted; open the new link. |
| "No LINZ API key found" | Set `LINZ_API_KEY` as in [local setup](local-setup.md). |
| "zoom in" when choosing areas | The view is wider than 1°; zoom in. |
| "a CLI build is running in this checkout" | Wait for it, or stop it and see [Local task queue](pipeline-reference.md#local-task-queue) before removing a stale lock. |
| An area failed | Open its report from the Jobs tab and follow [Troubleshooting](troubleshooting.md). |
