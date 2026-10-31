# How to build a region

Build an area other than the quickstart default, either on your computer
or on the shared build VM.

## Before you start

- Complete the [local quickstart](howto-local-quickstart.md) or the
  [cloud quickstart](howto-cloud-quickstart.md) once.
- Have your LINZ key configured. `tools/find_area.py` needs it even for
  cloud builds.

## 1. Find the region id

```sh
.venv/bin/python tools/find_area.py arrowtown
```

Copy the region id for the area you want. It has the form
`<council>/<town>/<suburb>`, for example
`queenstown-lakes-district/arrowtown/arrowtown`.

## 2. Check it can be built

```sh
.venv/bin/python tools/find_area.py arrowtown --details
```

Check these values:

- **Survey**: one must be named. If none covers the area, the area cannot be
  built.
- **Buildings and imagery estimate**: build a few hundred buildings locally.
  Build larger areas on the shared VM.

[Choosing an area](choosing-an-area.md) covers duplicate names, areas with no
town and areas that cross council boundaries.

## 3a. Build it locally

```sh
bash quickstart.sh queenstown-lakes-district/arrowtown/arrowtown
```

The first build in a council downloads its LINZ building outlines once,
which can take a few minutes. The combined map in `data/` then shows only
this region. Other built regions remain under `data/out/`.

## 3b. Or build it on the shared VM

```sh
bash quickstart.sh --mode cloud queenstown-lakes-district/arrowtown/arrowtown
```

Approve the cost and paid-referee questions as in the
[cloud quickstart](howto-cloud-quickstart.md). The VM fetches the region's
inputs before it builds them. Inputs that are already on the VM are reused.

## 4. Check the result

- Local: read `data/pipeline_runs/<run-id>/report.md` and the per-building
  cards in `data/regions/<region-id>/quickstart_report.html`.
- Cloud: read `data/cloud_quickstart_runs/<run-id>.log` and any
  `GATE WARNING` list, then open the preview URL.
- For areas without LiDAR, every building is marked `pitch_guessed`. Treat
  their pitches as estimates.

## Next steps

- [Build several regions](howto-scale.md) and view them on one map.
- Diagnose a failed or `DEGRADED` step with
  [Troubleshooting](troubleshooting.md).
- Look up stages, outputs and statuses in the
  [pipeline reference](pipeline-reference.md).
- Look up run options in the [pipeline run options](reference-pipeline.md)
  reference.
