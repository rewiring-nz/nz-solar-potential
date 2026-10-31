# Cloud quickstart (Google Cloud)

Two ways to build in the cloud:

- **Shared build VM** (team): `bash quickstart.sh --mode cloud`
- **Your own VM** (experimental): `bash quickstart.sh --mode cloud --profile PROFILE`

Both build a whole configured region; there is no building cap. Use the
shared VM if you have [team access](team-access.md).
The profile workflow is for a separate VM you administer.

## Shared build VM

Read the [cost and external-API warning](#cost) before building. Preflight
alone does not start a build or confirm preview-bucket readiness.

```sh
bash quickstart.sh --mode cloud            # default region
bash quickstart.sh --mode cloud REGION     # another region
bash quickstart.sh --mode cloud --cloud preflight   # free check, builds nothing
```

Before running:

- Commit and push to `CLOUD_SHARED_BUILD["ref"]` in [config.py](../../config.py).
  The launcher does not automatically use your local branch or commit.
- Choose a prepared region: inputs and selected faces must already be on the
  VM. For a new area, follow [dataset preparation](dataset-operations.md#2-fetch)
  first; this quickstart does not fetch it.
- Confirm with the administrator that
  [preview hosting](team-access.md#preview-hosting-administration)
  is configured. Preflight does not check bucket readiness.
- Unset `SOLAR_CLOUD_PROFILE` if it is selecting a developer VM.

The launcher checks cloud access, asks for cost approval, starts the VM if
needed, builds, gates and publishes a public developer preview. The default
region is `CLOUD_SHARED_BUILD["regions"]`; a positional name overrides it.
Keep the launcher connected until publication: the detached build alone
does not make the whole workflow survive a disconnect.

Logs are in `data/cloud_quickstart_runs/<run-id>.log` (`[QS-01]` to
`[QS-06]`). Shared mode supports `build` and `preflight` only.

### Cost

Read the estimate printed before approval. Its VM, disk, preview-storage
and download assumptions come from `CLOUD_SHARED_BUILD` in
[config.py](../../config.py), not live billing. It is not a spending cap.
Charges can be higher if the build runs longer, the preview is downloaded
more, or you publish several runs in 14 days. Disk charges continue while a
VM is stopped. Check prices with the
[Google Cloud pricing calculator](https://cloud.google.com/products/calculator).

The shared district build includes demolition screening that can
call a paid external vision API with survey/current image crops. The
VM/disk/preview estimate above does **not** include that API cost. Obtain
separate authorization for that cost and image transfer before running with
the referee enabled; see [demolition screening](dataset-operations.md#demolition-screening)
for the stage controls and cache behaviour. This is a current gap in the
full-workflow estimate, not an additional spending guarantee.

You must type `yes`. Enter, anything else, or a non-interactive run declines
before the VM is touched. There are no flags to override the estimate.
Preflight and profile status/delete actions do not ask for this approval.

### Developer preview

A passing build is published to:

```text
https://storage.googleapis.com/rewiring-solar-previews/devel/GITHUB_ALIAS/preview.html
```

Your alias comes from `ssh -T git@github.com`, or `gh api user --jq .login`
if SSH is not set up. Each run uploads to a new
`devel/<alias>/runs/<run-id>/data/` folder and moves your alias's pointer.
Production data and `site-config.js` are not touched.

The bucket is public. Never upload keys, raw inputs or logs. Everything under
`devel/` is deleted after 14 days, including your preview page if you don't
refresh it.

The launcher checks the returned preview URL, then requests browser opening.
It does not check whether that request succeeded or whether the page loaded;
open the printed URL manually if needed. End-to-end page availability is
the intended quickstart outcome, not a guarantee of the current success
status.

## Your own VM (profiles)

Profiles hold resource settings, not credentials. They live in
`~/.config/nz-solar-potential/cloud-profiles.json`. Actions:
`--cloud preflight | create | bootstrap | ssh | run | collect | preview | status | delete`.
`SOLAR_CLOUD_PROFILE` also selects a profile; unset it to use shared mode.
Profile `status` reports the VM, not individual active pipeline runs.
Unlike shared mode, this runs the local pipeline sequence; see the
[pipeline reference](pipeline-reference.md) for steps and
statuses. It does not publish to the public preview bucket or production.

### Set up an environment

1. Create a Google Cloud project with billing and a budget alert. Enable
   `compute.googleapis.com`. Check quota for 4 vCPUs and a 100 GB disk.
2. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
   and run `gcloud auth login`.
3. Copy the example profile:

   ```sh
   git clone https://github.com/rewiring-nz/nz-solar-potential.git
   cd nz-solar-potential
   mkdir -p ~/.config/nz-solar-potential
   cp tools/cloud-profiles.example.json ~/.config/nz-solar-potential/cloud-profiles.json
   chmod 600 ~/.config/nz-solar-potential/cloud-profiles.json
   ```

4. Edit it: your project ID and your public IP as an SSH CIDR (`x.x.x.x/32`).
   Check the machine, disk and cost estimate. The profile is `external-poc`.
5. Check, create and set up the VM (creating it starts billing):

   ```sh
   bash quickstart.sh --mode cloud --profile external-poc --cloud preflight
   bash quickstart.sh --mode cloud --profile external-poc --cloud create \
     --confirm YOUR_PROJECT_ID/australia-southeast1-b/solar-quickstart
   bash quickstart.sh --mode cloud --profile external-poc --cloud bootstrap
   ```

   This makes one VM (no service account) and one SSH firewall rule limited
   to your CIDR.

6. Put your LINZ key on the VM, never in the profile or repo:

   ```sh
   bash quickstart.sh --mode cloud --profile external-poc --cloud ssh
   cd ~/nz-solar-potential
   umask 077
   nano .env          # LINZ_API_KEY=your-key
   ```

7. Run a region from your computer:

   ```sh
   bash quickstart.sh --mode cloud --profile external-poc frankton_arm_lake
   ```

   It runs the checkout already on the VM; local changes are not uploaded.
   SSH in and update it to the commit you want first. The report and log
   are copied to `data/pipeline_runs/<run-id>/`.

8. View the preview through an SSH tunnel at `http://127.0.0.1:8765/`
   (Ctrl-C to stop), and copy reports home before cleaning up:

   ```sh
   bash quickstart.sh --mode cloud --profile external-poc --cloud preview
   bash quickstart.sh --mode cloud --profile external-poc --cloud collect
   ```

9. Delete the VM, its boot disk and the SSH rule:

   ```sh
   bash quickstart.sh --mode cloud --profile external-poc --cloud delete \
     --confirm YOUR_PROJECT_ID/australia-southeast1-b/solar-quickstart
   ```

   It deletes only resources it can show belong to this profile.

### Share an environment

Owner:

1. Get the other person's Google account and public IP.
2. In IAM, grant only the Compute Engine and SSH permissions they need. Never
   Owner or Editor.
3. Add their IP to `ssh_source_ranges` before creating the VM. The pipeline
   won't change an existing firewall rule.
4. Send the project ID, region, zone, VM name and profile securely. Never
   send the LINZ key or your `gcloud` credentials.
5. Agree who pays, when the VM is used, who collects reports, and who deletes
   it.

Guest: get the profile and confirm your access, quota, billing contact and
whether you may create or delete things. Install `gcloud`, save the profile,
run `--cloud preflight`. On an existing VM skip `create` and `bootstrap`
unless allowed. Book your run: each run replaces the VM's combined map data.

## Scope

For failures, use [Troubleshooting](troubleshooting.md).
For how the two cloud modes delegate to different engines, use the
[execution reference](../developers/tooling-execution.md).
