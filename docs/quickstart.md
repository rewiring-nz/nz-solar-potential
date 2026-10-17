# Quickstart
_This quickstart describes howto build rooftop spatial datasets from source, and publish in a webpage._

## 1. Download source code

```sh
cd ~
git clone https://github.com/rewiring-nz/nz-solar-potential.git
cd nz-solar-potential
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

This runs locally by default and generates data for `PIPELINE_REGION` in
`config.py`. Pass a configured region name to select another area.

To build and gate the default region on the team's shared cloud build VM:

```sh
bash quickstart.sh --mode cloud
```

Defaults for the project, VM, zone, SSH user, region, ref, and cost estimate
are held in `config.py` under `CLOUD_SHARED_BUILD`. The command asks for
explicit cost approval before starting or using the VM, and it builds and gates
without publishing. See [Cloud quickstart](quickstart-cloud.md) for optional
per-developer VM profiles, preflight, and other options.

# 5. Review status

* Check `~/nz-solar-potential/data/pipeline_runs/<datestamp>/report.md` for output status.
* The pipeline combines the selected region directly into `data/`, replacing the current combined map dataset.
* Check [Troubleshooting](data-maintainers/troubleshooting.md) for tips.
* [Pipeline reference](data-maintainers/pipeline-reference.md) for run details.

# 6. Next

Try generating data for another region, as defined in the `REGIONS` in `nz-solar-potential/config.py`:

```sh
bash quickstart.sh town_gorge_north
```