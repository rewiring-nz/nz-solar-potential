# Quickstart
_This quickstart describes howto build rooftop spatial datasets from source, and publish in a webpage._

## 1. Download source code

```sh
cd ~
git clone git@github.com:rewiring-nz/solar-estimates.git
```

## 2. Setup Environment

1. Setup your environment, per:
  * [Setup Mac Environment](data-maintainers/env-setup-mac.md)
  * [Setup Ubuntu Environment](data-maintainers/env-setup-ubuntu.md)
  * [Setup Windows Environment](data-maintainers/env-setup-win.md)

## 3. LINZ credentials

You need an API Key to download datasets from LINZ (Land Information New Zealand).

1. Create an account and API key from https://data.linz.govt.nz/my/api/create/
2. Select **API Key** > **Manual scope**
3. Enable maximum access, by changing pull-down selection of "No Access" to "Create, edit, and ..."
4. Copy the API key into a config file `~/nz-solar-potential/.env`:

```text
LINZ_API_KEY=[your-key-here]
```

# 4. Solar pipeline

Run the pipeline:

```sh
bash quickstart.sh
```

This will generate data for the default `PIPELINE_REGION` set in `nz-solar-potential/config.py`.

# 5. Review status

* Check `~/nz-solar-potential/data/quickstart_runs/<area>/<datestamp>/report.md for output status.
* Check [Troubleshooting](data-maintainers/troubleshooting.md) for tips.
* [Pipeline reference](data-maintainers/pipeline-reference.md) for run details.

# 6. Next

Try generating data for another region, as defined in the `REGIONS` in `nz-solar-potential/config.py`:

```
bash quickstart.sh town_gorge_north
```