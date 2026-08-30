# Local setup for data maintainers

Data acquisition and dataset builds currently run on local workstations. Cloud
execution is not a requirement or a documented production workflow yet.

## Prerequisites

- Git and Python 3.12 or a compatible Python version accepted by the project.
- Sufficient local storage for source rasters, point clouds, intermediate
  region data, and generated outputs. High-resolution imagery can be multiple
  gigabytes per region.
- A LINZ Data Service account and API key with both OGC web-services and
  `Search and Download` REST access enabled.

Create the credentials file without committing the secret:

```sh
cp .env.example .env
```

Set `LINZ_API_KEY` in `.env`. The template is the authoritative list of
required environment variables.

## Python environment

From the `nz-solar-potential` directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Quickly confirm the core geospatial stack imports:

```sh
python -c "import geopandas, rasterio, shapely, laspy, pvlib; print('ready')"
```

### macOS

macOS is the primary development platform. Install Python through a maintained
package manager or python.org, then use the commands above. Apple Silicon and
Intel systems may require current binary wheels for geospatial dependencies;
use a supported Python version before attempting source builds.

### Ubuntu

Install Python, virtual-environment support, and compiler/system libraries
needed if wheels are unavailable. Prefer current Python-package wheels first;
if installation compiles packages, install the GDAL, GEOS, PROJ, and LAS/LAZ
development dependencies appropriate to the Ubuntu release.

### Windows

Use WSL2 with Ubuntu for the same POSIX shell workflow used by the scripts.
Keep the repository and large `data/` directory inside the WSL Linux
filesystem rather than a mounted Windows path, which can significantly slow
many-file geospatial operations. Native Windows is unverified and should be
treated as an experiment until documented with reproducible results.

## Local serving

For the static map preview, serve the repository parent so URLs retain the
project prefix:

```sh
cd ..
python3 -m http.server 8000
```

Open `http://localhost:8000/nz-solar-potential/preview.html`.

For the local parameter-refit API, run this from the project directory:

```sh
.venv/bin/python src/live_server.py
```

This server loads substantial source data at startup and is intended for local
development only. Static hosting does not provide its `/api/refit` endpoint.