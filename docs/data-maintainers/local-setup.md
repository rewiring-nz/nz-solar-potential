# Local setup for data maintainers

Data acquisition and dataset builds currently run on local workstations. Cloud
execution is not a requirement or a documented production workflow yet.

Choose the guide for the computer being used:

- [macOS setup](env-setup-mac.md) is the primary and tested setup path.
- [Windows setup](env-setup-win.md) uses WSL2 and is not yet tested for this
  project.
- [Ubuntu setup](env-setup-ubuntu.md) is not yet tested for this project.

All setups need sufficient local storage for source rasters, point clouds,
intermediate region data, and generated outputs. High-resolution imagery can
be multiple gigabytes per region.

## Create the project Python environment

After completing the guide for your computer, run these commands from the
`nz-solar-potential` project directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -c "import geopandas, rasterio, shapely, laspy, pvlib; print('ready')"
```

The first command creates an isolated `.venv` folder for this project, so the
setup does not rely on packages installed elsewhere on the computer. If `.venv`
already exists, the command updates it in place. The `(.venv)` prefix means the
project environment is active. Run `. .venv/bin/activate` whenever you open a
new terminal to work on the project.

The final command should print:

```text
ready
```

## LINZ credentials

Data acquisition requires a LINZ Data Service (LDS) account and API key. LDS
provides the building outlines, elevation data, and imagery used by the
pipeline.

1. Go to [LINZ Data Service](https://data.linz.govt.nz/) and select **Log in**.
2. Create an account, or sign in to an existing account.
3. Open the account menu, select **API keys**, and create an API key.
4. Enable its OGC web-services access and its **Search and Download** REST
  access. The project uses web services for building outlines and the export
  API for elevation data and imagery.
5. From the project directory, create the local credentials file:

```sh
cp .env.example .env
```

6. Copy the API key into `.env`:

```text
LINZ_API_KEY=your-key-here
```

Do not commit `.env` or share the key in issues, chat, or documentation. The
template `.env.example` is the authoritative list of required environment
variables. For LDS account and key help, see the official [LINZ Data Service
guide](https://www.linz.govt.nz/guidance/data-service/linz-data-service-guide).

**VS Code note:** You may see a message that terminal environment injection is
disabled. No action is required for the documented data-maintenance commands:
the project's fetch scripts load `LINZ_API_KEY` from `.env` themselves. Leave
`python.terminal.useEnvFile` disabled unless there is a separate reason to make
`.env` variables available to every VS Code terminal command.