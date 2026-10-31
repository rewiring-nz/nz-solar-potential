# NZ Rooftop Solar Potential Map

A map of rooftop solar for every building, built from LINZ building outlines,
LiDAR and aerial photos. For each roof it shows how much sun each face gets,
how many panels fit, what they would generate, and what that is worth.

- Live map: https://rewiring-nz.github.io/nz-solar-potential/
- How it works, in plain English:
  [method.html](https://rewiring-nz.github.io/nz-solar-potential/method.html)
- Try it on your own area: [docs/data-maintainers/quickstart.md](docs/data-maintainers/quickstart.md)

Coverage (data version 64): 32,838 buildings in 35 regions across
Queenstown Lakes and Cromwell: Queenstown, Frankton, Arrowtown, Kingston,
Wanaka, Albert Town, Hawea, Cromwell, Bannockburn, Glenorchy and Kinloch.
1,424,084 panels, 832.8 GWh a year. Auckland (Mt Eden) has been built but is
not on the map.

## Data sources

| Source | Used for | Licence |
|---|---|---|
| [LINZ LiDAR](https://www.linz.govt.nz/products-services/data/types-linz-data/elevation-data/lidar-data-coverage) (point clouds, 1 m DSM and DEM) | Roof shape, pitch, obstructions, nearby shading | CC BY 4.0 |
| [LINZ NZ Building Outlines](https://data.linz.govt.nz/layer/101290-nz-building-outlines/) | Building footprints | CC BY 4.0 |
| [LINZ aerial imagery](https://data.linz.govt.nz/) | Roof lines, obstructions, roofs newer than the LiDAR | CC BY 4.0 |
| [LINZ NZ 8 m DEM](https://data.linz.govt.nz/layer/51768-nz-8m-digital-elevation-model-2012/) | Hills and mountains that block the sun | CC BY 4.0 |
| [LINZ NZ Addresses](https://data.linz.govt.nz/layer/123113-nz-addresses/) | Street addresses and search | CC BY 4.0 |
| [LINZ Basemaps](https://basemaps.linz.govt.nz/) | Map photo from zoom 14 in | CC BY 4.0 |
| [Esri World Imagery](https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9) | Map photo below zoom 14 | Esri terms of use |
| [NIWA SolarView](https://niwa.co.nz/renewable-energy/solarview) | Measured sunlight (GHI) that calibrates the Wakatipu basin | NIWA |
| NIWA measured-radiation stations | Calibrates sunlight outside the basin; Queenstown Aero daily radiation corrects ERA5's darkest days | NIWA |
| [ERA5](https://cds.climate.copernicus.eu/) (Copernicus, via [Open-Meteo](https://open-meteo.com/)) | How sunlight differs between places; daily sunlight 1940–2025 for low-solar runs | CC BY 4.0 |
| [QLDC District Plan zoning](https://www.qldc.govt.nz/) | Whether a building is a home or a business | Queenstown Lakes District Council |

Sunlight: [pvlib](https://pvlib-python.readthedocs.io/) gives clear-sky sun
for each roof face's slope and direction. That is calibrated to NIWA SolarView
measured GHI in the Wakatipu basin, or else to the nearest NIWA
measured-radiation station, then scaled by ERA5's difference between that
point and the region ([`src/solar_model.py`](src/solar_model.py)). NASA POWER
is in the code as a fallback but no built region uses it.

There is no licence file for this repository yet.

## How it works

1. **Fetch** outlines, LiDAR, the surface model and aerial photos for each
   region (`src/fetch_regions.py`, `src/fetch_pointcloud_regions.py`).
2. **Read the roof.** Each footprint is cut into flat faces. Hand-drawn markup
   (`mark_roofs.html`, `data/roof_labels.json`) wins where it exists. Next
   comes a precomputed reading (repeated-roof consensus when available, else
   `data/selected_faces/`), then the LiDAR fit (`src/roof_partition.py`,
   `src/roof_segmentation.py`). Obstructions
   come from the photo and the LiDAR.
3. **Place panels** on one grid per building, clear of edges, ridges and
   obstructions, in racked arrays on the sunny side (`src/panel_fitting.py`).
4. **Yield.** Sun on each panel from slope, direction, latitude and the
   building's own horizon (`src/solar_model.py`, `src/building_horizon.py`).
5. **Money.** Bills, savings and payback are worked out in the browser by
   `bill_tool.js` (see [docs/researchers/economics.md](docs/researchers/economics.md)).
6. **Publish.** Each region emits its own tiles (`src/emit_region.py`);
   `src/combine_regions.py` joins them. `tools/deploy_from_vm.sh` checks the
   result against the live map (`tools/predeploy_check.py`) and publishes a new
   version folder to a Google Cloud Storage bucket. The site is on GitHub
   Pages; `site-config.js` points it at the current data version.

District builds run on a cloud VM, region by region
(`src/run_district_build.sh`). They are incremental: only buildings whose
reading, markup or geometry code changed are rebuilt. A solar-model-only
change uses `--yield-only`.

## Checks

```bash
bash tests/run_all.sh           # everything, incl. a build of a synthetic region (~1 min)
bash tests/run_all.sh --fast    # skips the synthetic build
python tools/predeploy_check.py # what a new build changes against the live one
python tools/bench.py           # geometry against the hand-drawn markup
python tools/cases.py check     # every flagged roof
```

## Local setup

Start with the [local quickstart](docs/data-maintainers/quickstart.md) to build one small
region and open its map, or the [cloud quickstart](docs/data-maintainers/quickstart-cloud.md)
to build on the shared VM and publish a developer preview.

Tools, Python and credentials:
[local setup](docs/data-maintainers/local-setup.md).
To preview an already-built dataset without rebuilding, follow
[Serve locally](docs/data-maintainers/dataset-operations.md#6-serve-locally).

## More

- [docs/index.md](docs/index.md): map of the documentation.
- [BACKLOG.md](BACKLOG.md): open work.
- [AGENTS.md](AGENTS.md): rules for AI-assisted work.
