# Quickstart

Build the rooftop solar data for one area and view it on a local map.
For a shared-VM build and public developer preview, use the
[cloud quickstart](quickstart-cloud.md) instead.

## 1. Get the code

```sh
cd ~
git clone https://github.com/rewiring-nz/nz-solar-potential.git
cd nz-solar-potential
```

## 2. Set up once

Follow [Local setup](local-setup.md) for your system tools,
Python environment and LINZ key. Return here when the environment check
prints `ready` and credentials are configured.

## 3. Run the pipeline

**This replaces the current combined map data in `data/` with the selected
region.** Use a small region locally; district builds belong on the build VM.

```sh
bash quickstart.sh
```

This builds the region set by `PIPELINE_REGION` in `config.py`, on your
computer. To build another configured region, name it:

```sh
bash quickstart.sh town_gorge_north
```

Region names are in `REGIONS` in `config.py`.
The local queue starts one worker process without cloud access. For
multi-worker debugging and persistent task status, see
[Local task queue](pipeline-reference.md#local-task-queue).
Extra workers do not split a single region or speed up builds sharing one checkout.

## 4. Check the result

- The pipeline opens a local map preview. Open the printed URL if the browser
  did not open automatically.
- Read `data/pipeline_runs/<run-id>/report.md`.
- A completed run can still be degraded; check its coverage and warnings
  before trusting the map.
- Failures: [Troubleshooting](troubleshooting.md).
- Stages, outputs and stopping the preview:
  [Pipeline reference](pipeline-reference.md).
