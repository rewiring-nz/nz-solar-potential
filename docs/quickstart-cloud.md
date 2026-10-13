# Cloud quickstart (Google Cloud)

Run one configured solar region on a Google Compute Engine VM using
`bash quickstart.sh --mode cloud --profile PROFILE [REGION]`. Cloud VM
provisioning and bootstrap remain explicit setup steps in
`tools/cloud_quickstart.py`.

The quickstart builds a whole configured region; it does not cap building
count. `frankton_arm_lake` is currently listed at about 79 buildings in
`config.REGIONS`.

## Indicative costs

Indicative cost for an `e2-standard-4` VM in Sydney is **NZ$0.24 per running
hour**. Indicative cost for a 100 GB `pd-balanced` disk is **NZ$24 per month**.
An 8-hour run with the disk prorated is about **NZ$2.20**. These estimates use
October 2026 prices and an approximate USD/NZD conversion; check the
[Google Cloud pricing calculator](https://cloud.google.com/products/calculator)
for current prices. Disk charges continue while a VM is stopped.

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
   CIDR (`x.x.x.x/32`). Profiles contain resource settings, not credentials.
   The profile name is `external-poc`.
5. Check the profile and create the VM:

   ```sh
   python3 tools/cloud_quickstart.py --profile external-poc preflight
   python3 tools/cloud_quickstart.py --profile external-poc create \
     --confirm YOUR_PROJECT_ID/australia-southeast1-b/solar-quickstart
   python3 tools/cloud_quickstart.py --profile external-poc bootstrap
   ```

   Creation bills the project. The helper creates one VM and a profile-scoped
   SSH rule limited to the configured CIDR. Use least-privilege Compute Engine
   and firewall permissions; the VM has no attached service account by default.

6. Add your LINZ API key to the VM's ignored `.env` file, then run the region:

   ```sh
   python3 tools/cloud_quickstart.py --profile external-poc ssh
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
   Cloud mode currently delegates VM operations to `tools/cloud_quickstart.py`;
   that helper remains the explicit provisioning interface during this transition.

7. Open the preview through an SSH tunnel:

   ```sh
   python3 tools/cloud_quickstart.py --profile external-poc preview
   ```

   Browse to `http://127.0.0.1:8765/`; stop the tunnel with Ctrl-C. Copy run
   reports to your workstation before cleanup:

   ```sh
   python3 tools/cloud_quickstart.py --profile external-poc collect
   ```

8. Delete the VM, its auto-delete boot disk, and the helper-owned SSH rule:

   ```sh
   python3 tools/cloud_quickstart.py --profile external-poc delete \
     --confirm YOUR_PROJECT_ID/australia-southeast1-b/solar-quickstart
   ```

   The helper only deletes resources it can verify belong to this profile.

## External developer: share the environment with an internal developer

1. Get the internal developer's Google account email and public IP/CIDR.
2. In Google Cloud **IAM**, grant that account the Compute Engine and SSH
   permissions needed for the agreed role: connect to the existing VM, or
   create/manage a separate VM. Do not grant project Owner or Editor access.
3. Ensure their IP is allowed by the VM's SSH firewall rule. Add both
   developers' IPs to `ssh_source_ranges` before creating the VM. The helper
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
`preflight`.

For an existing VM, skip `create` and `bootstrap` unless authorized. Use `ssh`,
`run`, `preview`, and `collect` as approved. Coordinate an exclusive run
window: the pipeline replaces the VM's combined map data with the selected
region. The environment owner is responsible for shared-resource cleanup.

## Scope

This is a single-VM workflow. It does not start a fleet, publish map data,
change bucket IAM, or integrate cloud provisioning into `tools/pipeline.py`.
For run failures, see the [troubleshooting guide](data-maintainers/troubleshooting.md).
Keep cloud selection behind the shared quickstart/pipeline interface as the
cloud backend grows; retire the standalone helper only after its lifecycle
operations have a tested replacement.
