# Team access

**Status, October 2026: being set up.** Secret Manager holds `linz-api-key`;
the Google Group and its roles are not created yet.

Everything a contributor needs is reached with two accounts: a **GitHub**
account (code, tasks, reviews) and a **Google** account (the cloud project).
A Google account does not need Gmail: any email can be registered as one at
accounts.google.com ("Use my current email address instead").

## What lives where

| Thing | Where | Who can change it |
|---|---|---|
| Code, the map page, tasks | GitHub `rewiring-nz/nz-solar-potential` (Issues, Projects) | repository collaborators |
| Published map data | `gs://rewiring-solar-tiles/v<N>/` (public read) | the team's Google Group |
| Build inputs and models | `gs://rewiring-solar-data` (private) | the team's Google Group |
| Build machine | VM `claude-doing-things`, australia-southeast1-b | the team's Google Group |
| API keys | Google Secret Manager (`linz-api-key`) | read: the team's Google Group and the VM |

Access is granted to one **Google Group**, not to individuals, so adding or
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

```bash
gcloud auth login
gcloud config set project downloads-417521
```

Keys need no setup: scripts read `LINZ_API_KEY` from the environment or a
local `.env` when present, and otherwise from Secret Manager as you
(`src/project_secrets.py`). To log in to the VM:

```bash
gcloud compute ssh claude-doing-things --zone australia-southeast1-b --tunnel-through-iap
```

## Tasks

Open work is tracked as GitHub Issues, labelled by area: `geometry`, `model`,
`scale`, `no-lidar`, `infra`, `roof-case` (one roof that renders wrong) and
`needs-josh` (waiting on an owner decision or permission). Pick an issue,
assign yourself, and link the pull request with `Fixes #N`. Every pull request
runs the data-free tests (`.github/workflows/tests.yml`).

Building and publishing: see the Operating rules in [AGENTS.md](../../AGENTS.md).
