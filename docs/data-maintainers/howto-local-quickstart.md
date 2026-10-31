# How to run the local quickstart

Build the default area on your computer and open it on a local map.

The default area is `queenstown-lakes-district/queenstown/sunshine-bay`
(about 250 buildings), set by `CLOUD_SHARED_BUILD["local_region"]` in
[config.py](../../config.py).

## Before you start

- macOS, Ubuntu, or Windows with WSL.
- About 10 GB of free disk.
- A [LINZ Data Service](https://data.linz.govt.nz/) API key.

## Steps

1. Get the code:

   ```sh
   git clone https://github.com/rewiring-nz/nz-solar-potential.git
   cd nz-solar-potential
   ```

2. Install the tools, Python environment and LINZ key with
   [Local setup](local-setup.md). Continue when its environment check prints
   `ready`.

3. Run the pipeline:

   ```sh
   bash quickstart.sh
   ```

   Answer the paid-referee question. Enter declines it, which costs nothing.

   This run replaces the combined map data in `data/` with this area.

4. Open the map. The browser opens automatically; otherwise open the
   `Map preview:` URL printed at the end.

5. Read the report printed as `Pipeline report:`
   (`data/pipeline_runs/<run-id>/report.md`). Every step should be `PASS` or
   `SKIPPED`. A `DEGRADED` step means part of the result is missing. Read its
   effect before you trust the map.

6. Stop the preview server when finished:

   ```sh
   kill "$(cat data/pipeline_runs/<run-id>/map-preview-server.pid)"
   ```

If a step fails, find its number in [Troubleshooting](troubleshooting.md).

## Next steps

- [Build another region](howto-build-a-region.md).
- [Run the quickstart in the cloud](howto-cloud-quickstart.md) for a
  shareable preview.
- [Build several regions](howto-scale.md).
- Look up run options in the [pipeline run options](reference-pipeline.md)
  reference.
