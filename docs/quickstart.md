# Quickstart

Build the rooftop solar data for one area and view it on a local map.

## 1. Get the code

```sh
cd ~
git clone https://github.com/rewiring-nz/nz-solar-potential.git
cd nz-solar-potential
```

## 2. Set up your computer

Follow the guide for your system:

- [macOS](data-maintainers/env-setup-mac.md) (tested)
- [Ubuntu](data-maintainers/env-setup-ubuntu.md)
- [Windows (WSL2)](data-maintainers/env-setup-win.md)

Then [create the Python environment](data-maintainers/local-setup.md#create-the-project-python-environment).

The pipeline checks GDAL and LERC raster decoding before it downloads
anything. If that check fails, see
[LERC dependency check](data-maintainers/troubleshooting.md#lerc-raster-dependency-check).

## 3. Add a LINZ API key

1. Create a key at https://data.linz.govt.nz/my/api/create/.
2. Choose **API Key** > **Manual scope**.
3. Set every service from "No Access" to "Create, edit, and ...".
4. Put it in `.env` in the project folder:

```text
LINZ_API_KEY=your-key
```

## 4. Run the pipeline

```sh
bash quickstart.sh
```

This builds the region set by `PIPELINE_REGION` in `config.py`, on your
computer. To build another configured region, name it:

```sh
bash quickstart.sh town_gorge_north
```

Region names are in `REGIONS` in `config.py`.

To build on the team's shared cloud VM instead, see the
[cloud quickstart](quickstart-cloud.md):

```sh
bash quickstart.sh --mode cloud
```

## 5. Check the result

- Read `data/pipeline_runs/<run-id>/report.md`.
- The run replaces the combined map data in `data/` with the selected region.
- Failures: [Troubleshooting](data-maintainers/troubleshooting.md).
- Run details: [Pipeline reference](data-maintainers/pipeline-reference.md).
