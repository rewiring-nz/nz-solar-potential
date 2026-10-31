# Team access

For joining or administering the shared project. To run a build after access
is granted, use the [cloud quickstart](../quickstart-cloud.md) for a developer
preview or [dataset operations](dataset-operations.md#7-publish) for a release.

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
2. Unset `SOLAR_CLOUD_PROFILE` if set, then check shared access (free, builds
   nothing):

```bash
bash quickstart.sh --mode cloud --cloud preflight
```

The pipeline reads `LINZ_API_KEY` from the environment or `.env`, else from
Secret Manager as you ([src/project_secrets.py](../../src/project_secrets.py)).

Use [profile setup](../quickstart-cloud.md#your-own-vm-profiles) only when
you intend to administer a separate VM.

## Preview hosting administration

The shared quickstart requires a dedicated public-preview bucket. It does
not create the bucket or check its readiness during preflight. Only an
authorized administrator should create or reconfigure it; the group object
roles above do not grant bucket creation or IAM-policy administration.
Configuration is not proof that the resource exists.

Confirm the costs and public-access change before running these commands.
Check the project, bucket and location against `CLOUD_SHARED_BUILD` in
[config.py](../../config.py); the commands below use the shared defaults:

```sh
gcloud storage buckets create gs://rewiring-solar-previews \
  --project=solar-potential-map-512020 --location=australia-southeast1 \
  --uniform-bucket-level-access
python3 tools/publish_preview.py --configure-bucket
```

`--configure-bucket` grants public read, gives the shared VM's service
account object read/write access, and adds the configured `devel/` expiry
rule while preserving existing lifecycle rules. Public Access Prevention
must allow public reads. No CORS is needed for this preview layout.
Use the command only on the dedicated preview bucket; never put secrets,
raw inputs or private logs there. If its name changes, update
`CLOUD_SHARED_BUILD["preview_bucket"]` before setup.

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
