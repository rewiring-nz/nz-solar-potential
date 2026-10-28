# Cloud quickstart (Google Cloud)

Two ways to build in the cloud:

- **Shared build VM** (team): `bash quickstart.sh --mode cloud`
- **Your own VM** (experimental): `bash quickstart.sh --mode cloud --profile PROFILE`

Both build a whole configured region; there is no building cap
(`frankton_arm_lake` is about 79 buildings).

## Shared build VM

```sh
bash quickstart.sh --mode cloud            # default region
bash quickstart.sh --mode cloud REGION     # another region
bash quickstart.sh --mode cloud --cloud preflight   # free check, builds nothing
```

It starts the shared VM if needed, syncs the Git ref, builds the region, runs
the deploy gate, publishes a passing build as a developer preview and opens
it. The VM builds what is pushed to the ref in
`config.CLOUD_SHARED_BUILD["ref"]`, so commit and push first.

Defaults (project, VM, zone, SSH user, region, ref, preview bucket,
retention, cost assumptions) are in `CLOUD_SHARED_BUILD` in
[config.py](../config.py).

Before using the VM it checks the checkout and config, checks for `gcloud`
(and offers to install it, asking first), checks your Google account, then
asks you to approve the cost.

Logs: `data/cloud_quickstart_runs/<run-id>.log`. Steps `[QS-01]`–`[QS-06]`
are checkout/config, `gcloud`, account, cost approval, VM/SSH, and the action.
A profile run's remote pipeline continues from `[QS-07]`. Local runs use
`[QS-01]`–`[QS-18]`.

### Cost

The estimate uses the rates in `config.py`: an 8-hour VM run, a 100 GB disk,
1 GB of preview storage kept 14 days, and 20 GB of public downloads. That
comes to about NZ$5.00 (rounded up to the next 50c).

This is a planning figure, not a cap, and not checked against real billing.
Charges can be higher if the build runs longer, the preview is downloaded
more, or you publish several runs in 14 days. Disk charges continue while a
VM is stopped. Check prices with the
[Google Cloud pricing calculator](https://cloud.google.com/products/calculator).

You must type `yes`. Enter, anything else, or a non-interactive run declines
before the VM is touched. There are no flags to override the estimate.
Preflight, status and cleanup don't ask.

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

The bucket already exists. Only the project owner creates or reconfigures it
(it costs money and makes objects public):

```sh
gcloud storage buckets create gs://rewiring-solar-previews \
  --project=solar-potential-map-512020 --location=australia-southeast1 \
  --uniform-bucket-level-access
python3 tools/publish_preview.py --configure-bucket
```

`--configure-bucket` grants public read, gives the shared VM's service
account read/write, and adds the 14-day `devel/` lifecycle rule (keeping
existing rules). Use it only on this bucket. Public Access Prevention must be
off. No CORS is needed. If the name is taken, change `preview_bucket` in
`config.CLOUD_SHARED_BUILD`.

## Your own VM (profiles)

Profiles hold resource settings, not credentials. They live in
`~/.config/nz-solar-potential/cloud-profiles.json`. Actions:
`--cloud preflight | create | bootstrap | ssh | run | collect | preview | status | delete`.

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

The profile workflow is one VM. It doesn't start a fleet, publish production
data or change bucket permissions. Failures:
[troubleshooting](data-maintainers/troubleshooting.md).
