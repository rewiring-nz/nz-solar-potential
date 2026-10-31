# How to build several regions

Build more than one region and view them together on one map.

`bash quickstart.sh` builds one region per run, and each run's map shows
only that region. Choose a method:

| Regions | Method |
| --- | --- |
| A few small ones, for checking | [On your computer](#on-your-computer) |
| A town or district | [On the shared VM](#on-the-shared-vm) |
| Many districts | [On a worker fleet](#on-a-worker-fleet) (experimental) |

## Before you start

- Get each region id with `tools/find_area.py`; see
  [How to build a region](howto-build-a-region.md).
- Choose regions covered by the same survey if you want them on one map
  (`find_area.py --details`).

## On your computer

1. Build each region:

   ```sh
   bash quickstart.sh queenstown-lakes-district/arrowtown/arrowtown --no-open-browser
   bash quickstart.sh queenstown-lakes-district/queenstown/sunshine-bay --no-open-browser
   ```

   Check each run's `report.md`. Stop each preview server that the pipeline
   starts. The [local quickstart](howto-local-quickstart.md#steps) shows how.

2. Combine them into one map:

   ```sh
   .venv/bin/python src/combine_regions.py --skip-markup-lines --regions \
     queenstown-lakes-district/arrowtown/arrowtown \
     queenstown-lakes-district/queenstown/sunshine-bay
   ```

   This replaces the map data in `data/` with these regions. If you leave out
   `--regions`, every region under `data/out/` is included.

3. Serve and open it:

   ```sh
   .venv/bin/python tools/pipeline_serve.py --root . --port 8000
   ```

   Open `http://localhost:8000/preview.html`. Press Ctrl+C to stop.

## On the shared VM

This uses billable VM time. Unlike the cloud quickstart, `remote_build.sh`
shows no VM cost estimate. Estimate the cost with the
[Google Cloud pricing calculator](https://cloud.google.com/products/calculator).
The estimate is not a spending cap. Agree it with whoever pays before you
run the build.

1. Push your code to `main`, or pass `--ref <branch>`.

2. Build, gate and publish a developer preview:

   ```sh
   tools/remote_build.sh --fetch --preview-user <github-alias> --regions \
     "queenstown-lakes-district/arrowtown/arrowtown queenstown-lakes-district/queenstown/arrow-junction"
   ```

   Answer the paid-referee question. Enter declines it. The VM starts if it
   is stopped, and stops at the end if this run started it.

3. If the run is cut off, run the same command again. It continues from where
   it stopped.

4. Open
   `https://storage.googleapis.com/rewiring-solar-previews/devel/<github-alias>/preview.html`
   and read any `GATE WARNING` list.

The combined map includes every region that has been emitted on the VM, not
only the regions in `--regions`. A production release uses `--publish` and
the [deploy gate](dataset-operations.md#7-publish), not `--preview-user`.
You can also start this workflow from GitHub: go to **Actions**, then
**Build and publish the map**, and enter space-separated regions.

## On a worker fleet

Experimental: multi-worker throughput and recovery are not yet proven. Each
worker creates a billable spot VM. Read
[Many machines](../maintainers/scale-architecture.md#many-machines) and
agree the cost before you run:

```sh
.venv/bin/python tools/enqueue_regions.py --config --dry-run   # what would be queued
.venv/bin/python tools/enqueue_regions.py --config             # queue config.REGIONS
tools/fleet.sh up 2                                            # start 2 workers
.venv/bin/python tools/status.py                               # progress
tools/fleet.sh down                                            # delete every worker
```

Workers publish each region's outputs. They do not combine the regions or
release a map.

## Next steps

- Review and release a district with
  [Dataset operations](dataset-operations.md).
- Compare engines and their options in the
  [pipeline run options](reference-pipeline.md) reference.
- Plan national capacity with
  [Scaling and iteration](../maintainers/scaling-and-iteration.md).
