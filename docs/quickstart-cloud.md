# Cloud quickstart (Google Cloud)

Run the configured default region on the team's shared Google Compute Engine
build VM with:

```sh
bash quickstart.sh --mode cloud
```

This starts the shared VM if needed, syncs the selected Git ref, builds the
configured region, runs the production deploy gate for visibility, publishes
the build to the developer-preview bucket, and opens the stable preview URL.
The production gate does not block a developer preview; it remains mandatory
for production publishing. The preview publisher still requires the build's
core artifacts and frontend files. The alias comes first from the GitHub
greeting returned by the existing SSH authentication
(`ssh -T git@github.com`); no extra CLI is needed when this checkout's SSH key is
already linked to GitHub. If SSH authentication is not available, the
quickstart can use the GitHub CLI if installed. Default project, VM, zone, SSH
user, region, Git ref, preview bucket, retention, and cost assumptions are stored in
[`config.py`](../config.py) under `CLOUD_SHARED_BUILD`. Pass a region name to
override the configured region. Use `--cloud preflight` for the no-build,
no-cost check; preflight does not require GitHub CLI authentication.

The separate per-developer VM lifecycle remains available with
`bash quickstart.sh --mode cloud --profile PROFILE [REGION]`. Its provisioning,
bootstrap, report collection, preview tunnelling, and cleanup are selected
with `--cloud`.
Cloud commands first verify the Git checkout and selected configuration, check
or offer to install `gcloud`, verify the active Google account and configured
cloud target, then ask for cost approval before checking SSH connectivity or
using the VM.
On macOS, a missing CLI can be installed through Homebrew; on Ubuntu, Debian,
and Ubuntu under WSL, the quickstart can configure Google's apt repository and
install `google-cloud-cli`. Each installation requires an explicit `yes` and
may request the system administrator password. The quickstart does not install
Homebrew or modify unsupported Linux distributions; use Google's
[installation instructions](https://docs.cloud.google.com/sdk/docs/install-sdk)
there.

The numbered preflight and shared-build log is saved under
`data/cloud_quickstart_runs/<run-id>.log`. Its `[QS-01]` through `[QS-06]`
labels cover checkout/config, `gcloud`, account/target, cost approval, VM/SSH
readiness, and the selected action. For a profile-based cloud region run, the remote
pipeline continues numbering at `[QS-07]`; its `report.md` and `run.log` use
the same numbers. Local runs retain their existing `[QS-01]` through
`[QS-18]` sequence.

The quickstart builds a whole configured region; it does not cap building
count. `frankton_arm_lake` is currently listed at about 79 buildings in
`config.REGIONS`.

## Indicative costs

For the shared build, the quickstart calculates an estimate from the configured
VM-hourly rate and runtime, prorated disk rate, expected preview storage and
retention, and expected public downloads. The current planning assumptions are
an 8-hour VM run, 100 GB disk, 1 GB preview storage retained for 14 days, and
20 GB of public downloads. With the current indicative rates in `config.py`,
the calculated total is NZ$5.00, rounded up to the next NZ$0.50. These
usage and unit-price assumptions are indicative, are not verified against
project billing, and are not spending caps. Actual charges can be higher,
particularly if a build takes longer or the public page receives more
downloads or you publish several runs during the 14-day retention period. Disk
charges continue while a VM is stopped. Check current prices with the
[Google Cloud pricing calculator](https://cloud.google.com/products/calculator).

Before starting the shared VM or running a profile-based action that uses a
billable VM, after the local installation and authentication checks, the
quickstart displays the calculated estimate and assumptions and requires the
user to type `yes`; Enter, any other response, or non-interactive execution
declines before VM readiness checks or other VM use. Shared cost assumptions
are in `config.py`; estimates for optional per-developer VM profiles are set in
their profile. Estimate override command-line flags are not supported.
Preflight, status, and cleanup do not require the cost prompt.

`config.py` is the repository's source for shared, non-secret pipeline and
cloud-build defaults. The root `.env` is a local ignored file for secrets such
as `LINZ_API_KEY`; start from `.env.example` and do not commit it. The optional
per-developer VM profile is stored outside the checkout by default at
`~/.config/nz-solar-potential/cloud-profiles.json`.

### Developer preview

The default shared-VM command requests a public developer preview using
`tools/remote_build.sh --dev-publish GITHUB_ALIAS`; the
`quickstart.sh --mode cloud` flow supplies that option automatically. This
preview-only option does not invoke production publication. The production
gate runs but is warning-only for the developer preview; it still blocks
production publishing. Before upload, the selected region is checked with
`tools/validate_quickstart_map.py`, and required preview artifacts must be
present and upload successfully. No extra preview or alias flags are needed
in the normal quickstart. The alias is resolved
from the GitHub SSH authentication greeting, or from `gh api user --jq .login`
when SSH authentication is unavailable and GitHub CLI is installed. The
quickstart opens the stable URL:

```sh
bash quickstart.sh --mode cloud
```

The shared VM syncs the Git ref configured in
`config.CLOUD_SHARED_BUILD["ref"]`; commit and push the preview implementation
to that ref before running the command.

After the data and page are uploaded successfully, the quickstart prints and
opens a stable URL:

```text
https://storage.googleapis.com/rewiring-solar-previews/devel/GITHUB_ALIAS/preview.html
```

Each successful run uploads served data to a new immutable
`devel/<alias>/runs/<run-id>/data/` prefix, then updates that alias's
`site-config.js` pointer. The preview uses its own copy of `preview.html` and
its companion JavaScript files; production `site-config.js` and the
versioned production bucket are not changed. The bucket is public: anyone with
the URL can view the served map data. Do not upload keys, raw inputs, build
logs, or other private material.

Before the first preview, create the dedicated bucket named in
`config.CLOUD_SHARED_BUILD["preview_bucket"]` in the configured cloud project.
It must use uniform bucket-level access and allow public access; do not reuse
the private source-data bucket or production tiles bucket. From a checkout
with the required Google Cloud permissions, create it in the configured region
(creation and public storage may incur charges):

```sh
gcloud storage buckets create gs://rewiring-solar-previews \
  --project=downloads-417521 --location=australia-southeast1 \
  --uniform-bucket-level-access
```

The bucket name is globally unique; if that name is unavailable, change
`preview_bucket` in `config.CLOUD_SHARED_BUILD` and use the same name here.
Then configure its sharing, upload access, and expiry policy:

```sh
python3 tools/publish_preview.py --configure-bucket
```

This grants public object-read access, grants the shared VM's service account
object read/write access so it can update the stable preview pointer, and adds
a lifecycle rule that deletes objects under
`devel/` after 14 days. The setup command preserves existing lifecycle rules;
use it only on the dedicated preview bucket because the bucket-wide public
read policy makes all its objects public. Public Access Prevention must not
be enforced for this bucket. The page and its data share one HTTPS origin, so
cross-origin CORS configuration is not required.

Old immutable run data expires automatically. The stable preview page also
expires after 14 days without a successful refresh. Bucket storage and public
downloads can incur additional charges; both are included in the calculated
planning estimate described above. Interactive `yes` confirmation is still
required, and the estimate is not a spending cap.

## External developer (create an environment)

1. Create a Google Cloud project, attach billing, set a budget alert, enable
   `compute.googleapis.com`, and check quota for 4 vCPUs and a 100 GB disk.
2. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
   and authenticate:

   ```sh
   gcloud auth login
   ```

3. Clone this repository and copy the example profile:

   ```sh
   git clone https://github.com/rewiring-nz/nz-solar-potential.git
   cd nz-solar-potential
   mkdir -p ~/.config/nz-solar-potential
   cp tools/cloud-profiles.example.json ~/.config/nz-solar-potential/cloud-profiles.json
   chmod 600 ~/.config/nz-solar-potential/cloud-profiles.json
   ```

4. Edit `cloud-profiles.json`: set your project ID and public IP as an SSH
   CIDR (`x.x.x.x/32`). Review the machine, disk, cost estimate, and estimate
   assumptions against your intended workload. Profiles contain resource
   settings, not credentials. The profile name is `external-poc`.
5. Check the profile and create the VM:

   ```sh
   bash quickstart.sh --mode cloud --profile external-poc --cloud preflight
   bash quickstart.sh --mode cloud --profile external-poc --cloud create \
     --confirm YOUR_PROJECT_ID/australia-southeast1-b/solar-quickstart
   bash quickstart.sh --mode cloud --profile external-poc --cloud bootstrap
   ```

   Creation bills the project. The pipeline creates one VM and a profile-scoped
   SSH rule limited to the configured CIDR. Use least-privilege Compute Engine
   and firewall permissions; the VM has no attached service account by default.

6. Add your LINZ API key to the VM's ignored `.env` file, then run the region:

   ```sh
   bash quickstart.sh --mode cloud --profile external-poc --cloud ssh
   cd ~/nz-solar-potential
   umask 077
   nano .env
   ```

   Add `LINZ_API_KEY=your-key`, save, and exit. Do not put the key in the
   profile or repository. From your workstation:

   ```sh
   bash quickstart.sh --mode cloud --profile external-poc frankton_arm_lake
   ```

   The numbered report and combined run log are copied to local
   `data/pipeline_runs/<run-id>/`, matching local-run output.
   Cloud mode runs the checkout already on the VM; it does not upload local
   changes. Before a run, connect with `ssh`, update the VM checkout to the
   intended commit, and confirm the working tree is clean. Uncommitted
   workstation changes are not included.
   Cloud actions execute in `tools/pipeline.py`; `quickstart.sh` is the
   supported command entry point.

7. Open the preview through an SSH tunnel:

   ```sh
   bash quickstart.sh --mode cloud --profile external-poc --cloud preview
   ```

   Browse to `http://127.0.0.1:8765/`; stop the tunnel with Ctrl-C. Copy run
   reports to your workstation before cleanup:

   ```sh
   bash quickstart.sh --mode cloud --profile external-poc --cloud collect
   ```

8. Delete the VM, its auto-delete boot disk, and the profile-owned SSH rule:

   ```sh
   bash quickstart.sh --mode cloud --profile external-poc --cloud delete \
     --confirm YOUR_PROJECT_ID/australia-southeast1-b/solar-quickstart
   ```

   Cleanup only deletes resources it can verify belong to this profile.

## External developer: share the environment with an internal developer

1. Get the internal developer's Google account email and public IP/CIDR.
2. In Google Cloud **IAM**, grant that account the Compute Engine and SSH
   permissions needed for the agreed role: connect to the existing VM, or
   create/manage a separate VM. Do not grant project Owner or Editor access.
3. Ensure their IP is allowed by the VM's SSH firewall rule. Add both
   developers' IPs to `ssh_source_ranges` before creating the VM. The pipeline
   will not silently change an existing firewall rule; if the VM already
   exists, agree on an authorized shared network with the project administrator.
4. Send them the project ID, region, zone, VM name, and non-secret profile
   securely. Do not send the LINZ key or share your `gcloud` credentials.
5. Agree who pays, when the VM may be used, who collects reports, and who
   deletes the VM and firewall rule when finished.

## Internal developer (use a shared environment)

Request the profile and project/VM details from the environment owner, along
with confirmation of your IAM/SSH access, enabled APIs, available quota,
billing contact, and whether you may create or delete resources. Install and
authenticate with `gcloud`, save the non-secret profile locally, then run
`bash quickstart.sh --mode cloud --profile PROFILE --cloud preflight`.

For an existing VM, skip `create` and `bootstrap` unless authorized. Use
`--cloud ssh`, the default run action, `--cloud preview`, and
`--cloud collect` as approved. Coordinate an exclusive run
window: the pipeline replaces the VM's combined map data with the selected
region. The environment owner is responsible for shared-resource cleanup.

## Scope

This is a single-VM workflow. It does not start a fleet, publish map data, or
change bucket IAM. All single-VM quickstart actions are driven by
`quickstart.sh` and `tools/pipeline.py`.
For run failures, see the [troubleshooting guide](data-maintainers/troubleshooting.md).
