# Local setup

Set up a computer to fetch data and build regions locally. District builds
run on the build VM (see [Dataset operations](dataset-operations.md)).

## 1. Install system tools

- [macOS](env-setup-mac.md): the tested setup.
- [Windows](env-setup-win.md): uses WSL2. Not yet tested.
- [Ubuntu](env-setup-ubuntu.md): not yet tested.

You need GDAL (`gdalbuildvrt`, `gdal_translate`) with working LERC decoding,
and Tippecanoe. The pipeline checks both before it fetches. Python packages do
not add codecs to system GDAL; see
[LERC dependency check](troubleshooting.md#lerc-raster-dependency-check).

Leave plenty of disk space: imagery alone can be several GB per region.

## Create the project Python environment

From the project folder:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -c "import geopandas, rasterio, shapely, laspy, pvlib; print('ready')"
```

The last line should print `ready`. Run `. .venv/bin/activate` in each new
terminal; the prompt then starts with `(.venv)`.

## LINZ credentials

The pipeline downloads building outlines, elevation and imagery from the
LINZ Data Service, which needs an API key.

1. Sign in or create an account at [LINZ Data Service](https://data.linz.govt.nz/).
2. Go to [API Keys](https://data.linz.govt.nz/my/api/).
3. Choose **API Key** > **Manual scope**.
4. Set every service from "No Access" to "Create, edit, and ...".
5. Create `.env` from the example and add the key:

```sh
cp .env.example .env
```

```text
LINZ_API_KEY=your-key
```

`.env` sits beside `config.py` and Git ignores it. Never commit the key or
paste it into issues or chat.

VS Code may say terminal environment injection is disabled. Ignore it: the
fetch scripts read `.env` themselves.
