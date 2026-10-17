# Team access

**Status, 14 October 2026: in place.** Secret Manager holds `linz-api-key`,
and the team's Google Group `solarpotential@googlegroups.com` holds the roles
below. To give someone access, add them to the group.

Everything a contributor needs is reached with two accounts: a **GitHub**
account (code, tasks, reviews) and a **Google** account (the cloud project).
A Google account does not need Gmail: any email can be registered as one at
accounts.google.com ("Use my current email address instead").

## What lives where

| Thing | Where | Who can change it |
|---|---|---|
| Code, the map page, tasks | GitHub `rewiring-nz/nz-solar-potential`; board: github.com/orgs/rewiring-nz/projects/5 | repository collaborators |
| Published map data | `gs://rewiring-solar-tiles/v<N>/` (public read) | the team's Google Group |
| Build inputs and models | `gs://rewiring-solar-data` (private) | the team's Google Group |
| Build machine | VM `claude-doing-things`, australia-southeast1-b | the team's Google Group |
| API keys | Google Secret Manager (`linz-api-key`) | read: the team's Google Group and the VM |

Access is granted to one **Google Group** (`solarpotential@googlegroups.com`),
not to individuals, so adding or
removing a person is one change in the group. Billing and project ownership
stay with the project owner.

## Roles the group holds

| Role | On | For |
|---|---|---|
| Storage Object Admin | `rewiring-solar-tiles`, `rewiring-solar-data` | publishing and reading build data |
| Secret Manager Secret Accessor | the project's secrets | reading `linz-api-key` |
| Compute Instance Admin (v1) + Service Account User | the project | starting, stopping and using the VM |
| OS Login + IAP-secured Tunnel User | the project | logging in to the VM as yourself |

## First-time setup for a contributor

On macOS, install `gcloud` with Homebrew using the
[cloud option in the macOS setup guide](env-setup-mac.md#option-for-cloud-instances).
The cloud quickstart can offer to install it if Homebrew is available; it asks
before installing. On other platforms, install the
[Google Cloud CLI](https://cloud.google.com/sdk/docs/install) using Google's
instructions. Then run `gcloud auth login`. The project is supplied explicitly
by the cloud quickstart; changing the global `gcloud` project setting is not
required.

```bash
./quickstart-cloud.sh --check
```

The check verifies the local CLI, active Google authentication, VM access, and
IAP/SSH access when the VM is already running. It does not start the VM. Fix
the reported prerequisite and run it again. Logs are saved under
`data/cloud_quickstart_runs/`.

With no arguments, `quickstart-cloud.sh` repeats the checks and builds and
gates the `frankton_arm_lake` region on the shared VM. Before any cloud checks
or build, it asks for explicit confirmation; pressing Enter or entering
anything except `yes` declines and starts no cloud action. The displayed
NZD $3.00 is a provisional planning estimate for the default region (about
79 buildings), informed by indicative VM pricing and a roughly 2.5-hour
upper-region build time. It is not verified against project billing, is not a
90th-percentile estimate or spending cap, and actual charges can be higher;
disk, storage, or data-transfer charges may also apply. Revisit the estimate
when a completed run and its billing data are available. If the VM is stopped,
the build workflow starts it and stops it afterward.

Use `--regions "region_name"` to select another configured region, `--fetch`
when source data must first be acquired, or `--keep-running` to leave a VM
started by the workflow running. A non-default or multi-region run requires a
user-supplied estimate with `--estimate-nzd AMOUNT` (or
`CLOUD_COST_ESTIMATE_NZD`); that estimate is also only a planning figure, not
a cap.

The pipeline reads `LINZ_API_KEY` from the environment or a local `.env` when
present, and otherwise from Secret Manager as you
([`src/project_secrets.py`](../../src/project_secrets.py)).

## Claude Code cloud sessions

A cloud environment on claude.ai/code needs `data.linz.govt.nz` under
**Network access → Custom → Allowed domains**, and `LINZ_API_KEY` under
**Environment variables** (copy it from Secret Manager, `linz-api-key`).
Environment variables are readable by anyone using that environment, so keep
it a personal environment, not a shared one.

## Tasks

Open work is tracked as GitHub Issues on the
[project board](https://github.com/orgs/rewiring-nz/projects/5), labelled by area: `geometry`, `model`,
`scale`, `no-lidar`, `infra`, `roof-case` (one roof that renders wrong) and
`needs-josh` (waiting on an owner decision or permission). Pick an issue,
assign yourself, and link the pull request with `Fixes #N`. Every pull request
runs the data-free tests (`.github/workflows/tests.yml`).

## Building and publishing

- **From GitHub (no setup):** Actions tab -> "Build and publish the map" ->
  Run workflow. Name the regions (blank = every stale one), tick "Fetch" for a
  new region, untick "Publish" to build and gate only. It starts the VM,
  builds the pushed `main`, publishes only if the gate passes, and stops the
  VM. Anyone with write access to the repository can run it.
- **From a laptop:** `tools/remote_build.sh --regions "a b" [--publish]` does
  the same with your own Google sign-in.

Either way the VM builds what is pushed to GitHub, so push first. Rules: the
Operating rules in [AGENTS.md](../../AGENTS.md).
