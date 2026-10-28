# Team access

You need two accounts: **GitHub** (code, tasks, reviews) and **Google** (the
cloud project). A Google account does not need Gmail: register any email at
accounts.google.com ("Use my current email address instead").

Access is granted to the Google Group `solarpotential@googlegroups.com`, not
to people. To give someone access, add them to the group. Billing and project
ownership stay with the project owner.

## What lives where

All cloud resources are in the Google Cloud project **Solar Potential Map**
(`solar-potential-map-512020`). Nothing should still point at the old
personal project (`downloads-417521`).

| Thing | Where | Who can change it |
|---|---|---|
| Code, map page, tasks | GitHub `rewiring-nz/nz-solar-potential`; board github.com/orgs/rewiring-nz/projects/5 | repository collaborators |
| Published map data | `gs://solar-potential-map-tiles/v<N>/` (public read) | the Google Group |
| Developer previews | `gs://rewiring-solar-previews` (public read; `devel/` deleted after 14 days) | the Google Group |
| Build inputs and models | `gs://solar-potential-map-data` (private) | the Google Group |
| Build machine | VM `claude-doing-things`, australia-southeast1-b | the Google Group |
| API keys | Google Secret Manager (`linz-api-key`) | read: the Google Group and the VM |

## Roles the group holds

| Role | On | For |
|---|---|---|
| Storage Object Admin | the project's buckets | publishing, previews, reading build data |
| Secret Manager Secret Accessor | the project's secrets | reading `linz-api-key` |
| Compute Instance Admin (v1) + Service Account User | the project | starting, stopping and using the VM |
| OS Login + IAP-secured Tunnel User | the project | logging in to the VM as yourself |

## First-time setup

1. Install `gcloud`: on macOS see [the macOS guide](env-setup-mac.md#3-cloud-only-gcloud);
   elsewhere use [Google's instructions](https://cloud.google.com/sdk/docs/install).
   Then `gcloud auth login`.
2. Check access (free, builds nothing):

```bash
bash quickstart.sh --mode cloud --profile PROFILE --cloud preflight
```

The pipeline reads `LINZ_API_KEY` from the environment or `.env`, else from
Secret Manager as you ([src/project_secrets.py](../../src/project_secrets.py)).

## Building and publishing

The VM builds what is pushed to GitHub, so push first.

- **From GitHub:** Actions tab > "Build and publish the map" > Run workflow.
  Name the regions (blank = every stale one), tick "Fetch" for a new region,
  pick a mode (normal, yield-only, force), untick "Publish" to build and gate
  only. It starts the VM, builds `main`, publishes only if the gate passes,
  and stops the VM. Needs write access to the repository.
- **From a laptop:** `tools/remote_build.sh --regions "a b"` builds and gates;
  add `--publish` to publish if the gate passes.

Rules: the operating rules in [AGENTS.md](../../AGENTS.md). Single-VM
profiles: [Cloud quickstart](../quickstart-cloud.md).

## Claude Code cloud sessions

A claude.ai/code environment needs `data.linz.govt.nz` under **Network access
> Custom > Allowed domains**, and `LINZ_API_KEY` under **Environment
variables** (from Secret Manager, `linz-api-key`). Anyone using that
environment can read its variables, so keep it personal.

## Tasks

Open work is GitHub Issues on the
[project board](https://github.com/orgs/rewiring-nz/projects/5), labelled
`geometry`, `model`, `scale`, `no-lidar`, `infra`, `roof-case` (one roof
drawn wrong) or `needs-josh` (waiting on the owner). Assign yourself and link
the pull request with `Fixes #N`. Every pull request runs the data-free tests
(`.github/workflows/tests.yml`).
