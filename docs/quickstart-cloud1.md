# Cloud quickstart (Google Cloud)

This is an **experimental, single-VM proof of concept**, not a production
deployment or a fleet build. It runs the existing regional pipeline on a
dedicated Compute Engine VM. Cloud provisioning is a separate command-line
tool; it is not integrated into `tools/pipeline.py`.

The quickstart does not enforce a building-count limit. `tools/pipeline.py`
builds the entire configured region and combines that region into the VM's
local `data/` map contract. Use a region with current survey coverage and a
small configured bbox. `frankton_arm_lake` is currently listed as about 79
buildings in `config.REGIONS`; this is an approximate target, not a guaranteed
count or a measured resource minimum. Confirm region and source coverage
before running.

## What runs where

| Work | Location |
| --- | --- |
| Install Google Cloud CLI, authenticate, edit a local profile, and invoke the cloud helper | Developer workstation |
| Fetch LINZ outlines/elevation/imagery and public point-cloud data; process the region; build the map and preview | The selected VM |
| Open the VM-local preview | Developer workstation, through an SSH port-forward |
| Copy pipeline reports and logs before cleanup | Developer workstation, under `data/cloud_runs/` |

The helper only creates, connects to, runs on, collects logs from, and deletes
the single VM selected by one named profile, plus that profile's narrowly
scoped SSH firewall rule. It does not start a fleet, publish map data, alter
bucket IAM, or delete map buckets. The quickstart profile does not need a
bucket or attached VM service account. Fleet, queue, and publishing tools are
separate workflows.

## Resource estimate and cost controls

The example uses **4 vCPUs, 16 GB RAM, and a 100 GB balanced boot disk**
(`e2-standard-4`, `pd-balanced`) in Sydney. This is an initial educated guess
for a small region, not measured capacity. Pipeline reports and resource use
from a real run must be reviewed before treating it as a baseline.

Indicative on-demand cost, using Google Cloud's published Sydney prices
available in October 2026 and an approximate USD/NZD conversion of 1.79:

| Resource | Indicative price |
| --- | ---: |
| `e2-standard-4` VM | about **NZ$0.24 per running hour** |
| 100 GB `pd-balanced` boot disk | about **NZ$24 per month** while provisioned |
| Example 8-hour run, VM plus disk prorated for 8 hours | about **NZ$2.20** |

These are planning estimates, not a quote. They exclude possible taxes, an
external IPv4 charge, outbound data transfer, downloaded data/image storage,
and other project resources. Stopping the VM stops most compute charges but
does **not** remove its disk or stop disk charges. Delete a quickstart-owned VM
when finished if the data on it is no longer needed. Deleting the VM also
deletes its auto-delete boot disk; copy reports first. Billing budgets and
alerts help monitor cost but are not hard spending caps.

Always check the live [Google Cloud pricing calculator](https://cloud.google.com/products/calculator),
[Compute Engine pricing](https://cloud.google.com/compute/pricing), and
[disk/image pricing](https://cloud.google.com/compute/disks-image-pricing)
for the chosen region, disk, network, billing account, and current NZD rates.
Do not assume the Sydney workload is covered by the US-only Compute Engine
Always Free offer. Set a billing budget/alert before creating resources.

## External developer (primary path)

Clone the repository and run the following workstation commands from its root.
The cloud helper itself uses only Python's standard library; the project's
geospatial dependencies are installed on the VM by `bootstrap`.

```sh
git clone https://github.com/rewiring-nz/nz-solar-potential.git
cd nz-solar-potential
```

### 1. Establish the Google Cloud environment

1. Sign in to or create a Google account and create a **dedicated Google Cloud
   project** for this experiment. Record its project ID.
2. Attach a billing account. Set a budget and alert; confirm who is responsible
   for charges. Billing must be enabled even if you expect credits.
3. Enable `compute.googleapis.com` in the project. The helper checks access to
   the selected zone and machine type but does not enable APIs or change quota.
4. Check that the region has VM and disk quota available for 4 vCPUs and
   100 GB of balanced disk. Availability and quota depend on the project.
5. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
   on your workstation, then authenticate:

   ```sh
   gcloud auth login
   gcloud auth list
   ```

6. Allow SSH only from your workstation's public IPv4 address (typically
   `x.x.x.x/32`) on the profile-specific network tag created by the helper. Do not leave a
   broad `0.0.0.0/0` SSH rule applicable to this VM. The helper creates a
   profile-scoped TCP/22 firewall rule from the configured CIDRs; it does not
   remove or override other permissive firewall rules. Review the effective
   VPC firewall rules before proceeding.
7. Use least-privilege IAM. VM creation/deletion requires Compute Engine
   instance permissions; creating the tagged SSH rule requires firewall-rule
   permissions. SSH access requires the project's configured SSH/OS Login
   permissions. The example attaches no service account. If you set one,
   grant only required roles and grant the operator permission to attach it.

### 2. Configure a local profile

Copy the safe example outside the repository:

```sh
mkdir -p ~/.config/nz-solar-potential
cp tools/cloud-profiles.example.json \
  ~/.config/nz-solar-potential/cloud-profiles.json
chmod 600 ~/.config/nz-solar-potential/cloud-profiles.json
```

Edit the file and replace the example project ID and reserved example CIDR
(`203.0.113.10/32`) with your project ID and your actual public source IP.
Keep the region/zone paired, and change the VM name if another VM already uses
it. Profiles contain resource identifiers and sizing only, never credentials.
The default local file path can be changed with
`SOLAR_CLOUD_PROFILES_FILE`; choose a profile with `--profile` or
`SOLAR_CLOUD_PROFILE`.

Review the resolved profile:

```sh
python3 tools/cloud_quickstart.py --profile external-poc preflight
```

Preflight verifies the local `gcloud` installation, active login, selected
zone and machine type, and profile values. It does not create resources,
enable APIs, or test pipeline dependencies on the VM.

### 3. Create and bootstrap the VM

The confirmation argument must exactly match the displayed project/zone/VM
target:

```sh
python3 tools/cloud_quickstart.py --profile external-poc \
  create --confirm YOUR_PROJECT_ID/australia-southeast1-b/solar-quickstart
python3 tools/cloud_quickstart.py --profile external-poc bootstrap
```

`create` refuses to replace or adopt an existing VM. It creates one VM and
one profile-specific SSH firewall rule. VM creation is billable. `bootstrap`
installs Git, Python, Tippecanoe and the project's Python requirements, then
clones or fast-forwards this repository on the VM. This is a best-effort
bootstrap path and has not yet been verified by a second developer in a clean
cloud project.

### 4. Add LINZ credentials on the VM and run

Obtain a LINZ Data Service API key as described in
[local setup](data-maintainers/local-setup.md#linz-credentials). Do not put it
in a profile, command-line argument, VM metadata, startup script, repository,
issue, or chat. Enter the VM and write it directly to the ignored `.env` file:

```sh
python3 tools/cloud_quickstart.py --profile external-poc ssh
cd ~/nz-solar-potential
umask 077
nano .env
```

Put only this line in `.env`, then save and exit:

```text
LINZ_API_KEY=your-key
```

Run one configured, survey-covered small region. The current config lists
`frankton_arm_lake` at about 79 buildings:

```sh
python3 tools/cloud_quickstart.py --profile external-poc \
  run frankton_arm_lake
```

The helper checks that `.env` is non-empty, then the pipeline performs its own
credential, dependency, region, and survey preflight. It runs the complete
configured region—not a 100-building cap. Inspect the numbered run report for
degraded inputs as well as hard failures.

### 5. Preview and preserve the run report

Keep the SSH tunnel open in one workstation terminal:

```sh
python3 tools/cloud_quickstart.py --profile external-poc preview
```

Open `http://127.0.0.1:8765/` in a browser on that workstation. Preview access
is through the SSH tunnel; the helper does not open a public HTTP port. Stop
the tunnel with Ctrl-C.

Before deleting the VM, copy reports and logs to the workstation:

```sh
python3 tools/cloud_quickstart.py --profile external-poc collect
```

The files are copied under `data/cloud_runs/<profile>/`. The helper does not
download the VM's full dataset or the rendered map.

### 6. Clean up

Review the target and copied reports, then explicitly confirm the exact VM:

```sh
python3 tools/cloud_quickstart.py --profile external-poc \
  delete --confirm YOUR_PROJECT_ID/australia-southeast1-b/solar-quickstart
```

Deletion is refused unless the VM has both ownership labels set by `create`.
It deletes that VM, its auto-delete boot disk, and the helper-owned SSH rule.
It does not remove unrelated disks, images, buckets, or other resources.
Check the Google Cloud Console after cleanup for anything created manually
and confirm billing has stopped for the intended resources.

## Internal developer (existing environment)

Ask the internal environment owner for:

- the project, region, zone, VM name and profile JSON (shared securely; do
  not commit real profiles);
- whether the VM is dedicated to you or shared, and whether you may create a
  separate VM or only connect to a pre-created one;
- IAM/OS Login access, the authorized SSH source network, enabled APIs and
  quota status;
- the billing/quota contact, cost-monitoring expectations, LINZ-key handling,
  and who owns cleanup.

Install `gcloud`, authenticate with the granted account, save the supplied
non-secret profile in the local profile file, and run `preflight`. For a
pre-created VM, skip `create` and `bootstrap` unless the owner explicitly
authorizes those changes; use `ssh`, `run`, `preview`, and `collect` only as
approved. If authorized to create a dedicated VM in a shared project, agree
on a unique profile/VM name so the per-profile SSH rule does not collide with
another developer's rule. The helper refuses to delete VMs that it did not
label as quickstart-managed. The environment owner is responsible for shared
VM cleanup and shared firewall rules.

The pipeline replaces the VM's combined map output with the selected region.
Do not run concurrently with another build on the same VM or against shared
working data. Use a dedicated VM or coordinate an exclusive run window.
Retained disks and shared resources continue to incur charges; the owner
should state the cleanup policy before access is granted.

## Existing cloud tooling and boundaries

The `SOLAR_CLOUD_PROFILE` setting can also provide project/zone/VM values to
the existing sync/deploy/fleet shell tools, and bucket/prefix values to the
queue and tiles-bucket value to publishing. Those tools remain separate from
this quickstart. Fleet operation requires a profile with a bucket; publishing
requires `tiles_bucket`. Existing push/publication confirmation behavior
remains in place. Do not select the one-VM POC profile for fleet or publishing
workflows.

Cloud provisioning and profile selection are not integrated with
`tools/pipeline.py`. A later, separate task may decide whether pipeline-level
cloud orchestration is appropriate.

## Status and limitations

- The profile loader and command construction can be tested locally without
  cloud resources. No live VM provisioning or end-to-end cloud run is part of
  repository tests.
- The VM sizing and price calculation are estimates. Record actual wall time,
  peak memory, disk use, source-transfer volume, and billing after a verified
  run before tuning the next profile.
- A successful pipeline run is not necessarily a full-evidence run. Inspect
  `report.md` for missing imagery, point-cloud tiles, vision models, and other
  degraded/optional stages.
- Troubleshoot pipeline stage failures using
  [the troubleshooting guide](data-maintainers/troubleshooting.md). For VM
  permissions, quota, or billing problems, use the project or Google Cloud
  administrator; the cloud helper does not attempt IAM or quota changes.
