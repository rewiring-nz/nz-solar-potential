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

Defaults for the project, VM, zone, SSH user, region, ref, and cost assumptions
are held in `config.py` under `CLOUD_SHARED_BUILD`. After cost approval, the
command creates a commit-pinned developer worktree, builds into its
run-specific `data/` directory, reports the production gate, publishes to the
public developer preview, and opens the preview URL. The production gate does
not block a developer preview; it still blocks production publishing. Common
downloaded source inputs are reused from a validated cache, while generated
outputs stay in the run worktree rather than the shared production checkout.
The VM owns each developer run after it starts, so closing the local launcher
does not cancel the build. The public run dashboard shows status and logs;
stop requests go through an authenticated GitHub Actions workflow. Starting
another run prompts how to handle active runs unless `--parallel` is selected.
The alias is resolved from the GitHub SSH authentication greeting (using an
existing SSH key; no extra CLI is needed when this checkout already
authenticates to GitHub) and falls back to the GitHub CLI if available. It
calculates the estimate from the VM, storage, retention, and expected-download
assumptions in `CLOUD_SHARED_BUILD`. See [Cloud quickstart](quickstart-cloud.md)
for workspace paths, cache behavior, one-time preview-bucket setup, optional
per-developer VM profiles, and preflight.

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