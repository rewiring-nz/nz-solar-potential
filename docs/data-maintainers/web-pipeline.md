# Web pipeline panel

A local web page for the pipeline: choose suburbs and localities on a map,
see an estimate, type `yes`, and follow the build. A toggle at the top picks
the target:

| Target | Shows | A build runs | Costs |
| --- | --- | --- | --- |
| **Local** | Areas built in this checkout | `tools/pipeline.py <region>` on this computer, as the [quickstart](quickstart.md) does | Nothing in the cloud |
| **Developer** | Every developer's previews in `gs://rewiring-solar-previews/devel/<alias>/` | `tools/pipeline.py "<regions>" --mode cloud --cloud build` on the shared build VM, publishing to your own `devel/<alias>/` preview, as the [cloud quickstart](quickstart-cloud.md) does | Google Cloud, shown before you start |
| **Published** | The live map's data version and its areas | `tools/remote_build.sh --regions "<regions>" --publish` on the shared build VM; it publishes only if the [deploy gate](../../tools/predeploy_check.py) passes | Google Cloud, shown before you start |

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

- **Regions** depends on the target:
  - Local lists every area built or prepared in this checkout (green:
    built; amber: inputs downloaded, not built; red: the last run failed),
    with links to its map preview and last `report.md`.
  - Developer lists each alias's preview areas with a link to that preview.
  - Published shows the live data version, the versions kept in the
    bucket, and the live areas.

  Cloud listings are read from the public buckets and cached for 10
  minutes; **Refresh** reads them again. **Rebuild** adds the area to a new
  build for the current target.
- **New build**: search for a suburb or locality, or zoom in to level 10 or
  closer and click areas on the map. **Estimate** shows, per area:
  - building count
  - download size
  - time
  - disk use
  - warnings, such as no LiDAR or a large area
  - blockers, such as no survey coverage or too little disk

  For Developer and Published the estimate also shows the Google Cloud
  cost (see [Cloud builds](#cloud-builds)). To start, type `yes`. The paid
  AI referee is off unless you tick it; the estimate shows its upper cost.
- **Queue**: what is selected but not started, and each lane's running and
  waiting jobs. **Remove** drops a waiting job; **Cancel** stops a running
  one.
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
  spending cap; actual charges can be higher. Starting needs a typed `yes`,
  and the server checks that the start request carries the same cost the
  estimate showed.
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

## Security

- The panel listens on `127.0.0.1` only and answers only a localhost
  `Host` header. It never serves dotfiles such as `.env`.
- Every API call needs the per-launch token in the printed link. Anyone
  with that link can start builds on this computer, so do not share it.
- Build requests must come from the panel's own page as JSON.
- The LINZ key stays on the server. Locality requests go through it, and
  errors name only the failure type.

- Anyone with the link can also start paid builds on the shared VM; each
  still needs the typed `yes` against the shown cost.

The panel is not a hosted service; it runs on your computer with your
`gcloud` credentials.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "Read-only: the panel server is not running" | Start `tools/pipeline_web.py` and open the link it prints. |
| "Cannot listen on 127.0.0.1:8766 (Address already in use)" | If the message names an earlier pipeline panel, run the `kill <pid>` it prints ([Start it](#start-it)). If another program holds the port, start the panel with `--port N` or `--port 0`. |
| "This link's token is out of date" | The panel was restarted; open the new link. |
| "No LINZ API key found" | Set `LINZ_API_KEY` as in [local setup](local-setup.md). |
| "zoom in" when choosing areas | The view is wider than 1°; zoom in. |
| "a CLI build is running in this checkout" | Wait for it, or stop it and see [Local task queue](pipeline-reference.md#local-task-queue) before removing a stale lock. |
| An area failed | Open its report from the Jobs tab and follow [Troubleshooting](troubleshooting.md). |
| macOS "Python quit unexpectedly" after Estimate | A panel older than this fix loaded PROJ in the server, and the next git, gcloud or build process it started crashed. Stop the panel and start it again on current code. Area calculations now run in a separate process (`tools/pipeline_webui/geo_worker.py`). |
| VM status `UNKNOWN` | The message shows gcloud's error; usually `gcloud auth login` or missing access to the project ([team access](team-access.md)). |
| "cannot publish: … unpushed commit(s)" | Push or reset those commits, then estimate again. |
| A cloud job stopped at the code sync | The VM was busy with another build; wait for it, then start again. |
