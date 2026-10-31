# Reference: pipeline run options

The ways to run the pipeline, and the options for each. For step-by-step
tasks, use the how-to guides:
[local quickstart](howto-local-quickstart.md),
[cloud quickstart](howto-cloud-quickstart.md),
[build a region](howto-build-a-region.md),
[build several regions](howto-scale.md).
For stages, outputs and statuses, see the
[pipeline reference](pipeline-reference.md).

The scripts are the source of truth. Run any Python entry point with
`--help` for its current options.

## Ways to run

| Way | Command | Where it runs | Regions per run | Result |
| --- | --- | --- | --- | --- |
| Local | `bash quickstart.sh [REGION]` | Your computer | 1 | Replaces the map in `data/`, then starts a loopback preview |
| Shared VM quickstart | `bash quickstart.sh --mode cloud [REGION]` | Shared build VM | 1, or `CLOUD_SHARED_BUILD["regions"]` | Public developer preview, after cost approval |
| Your own VM | `bash quickstart.sh --mode cloud --profile NAME [REGION]` | A VM you administer | 1 | Report and log copied home; preview through an SSH tunnel |
| Remote build | `tools/remote_build.sh [options]` | Shared build VM | Any | Gated build; developer preview or production release |
| GitHub workflow | Actions, then **Build and publish the map** | Shared build VM | Any | Same as remote build; `main` only |
| District build | `bash src/run_district_build.sh [options]` | On the build VM itself | Any | Per-region outputs and combined map on the VM |
| Fleet (experimental) | `tools/fleet.sh up N` | N spot VMs | Queued regions | Region outputs in the build bucket; no combined map |
| Dev loop | `bash src/run_dev_loop.sh [AREA ...]` | Your computer | Any, with inputs already fetched | Layout, gate, rerank and audits only; no map |
| Combine | `.venv/bin/python src/combine_regions.py [--regions A B ...]` | Your computer or VM | Any already emitted | Replaces the map in `data/` with those regions |

`quickstart.sh` passes all arguments to [tools/pipeline.py](../../tools/pipeline.py).
The [execution reference](../developers/tooling-execution.md#capability-matrix)
lists what each way syncs, fetches and publishes.

## Regions

A region is a LINZ locality id, `<council>/<town>/<suburb>`, found with
`tools/find_area.py`; see [Choosing an area](choosing-an-area.md). Older
`config.REGIONS` names such as `pilot` still build, with a deprecation
warning.

| Setting in [config.py](../../config.py) | Used by |
| --- | --- |
| `CLOUD_SHARED_BUILD["local_region"]` | Local and own-VM runs with no region given |
| `CLOUD_SHARED_BUILD["regions"]` | Shared VM quickstart with no region given; can hold several, space-separated |
| `CLOUD_SHARED_BUILD["ref"]` | Branch the shared VM quickstart builds |
| `PIPELINE_REGION` | Deprecated fallback when `local_region` is absent |

## `quickstart.sh` and `tools/pipeline.py`

| Option | Mode | Effect |
| --- | --- | --- |
| `REGION` | All | Region to build (positional, one only) |
| `--mode local\|cloud` | All | Where to run; default `local` |
| `--no-open-browser` | Local | Start the preview without opening a browser tab |
| `--port N` | Local | Preferred preview port; default 8765, or the next free port |
| `--workers N` | Local | Local queue worker processes; default 1. Runs in one checkout still run one at a time |
| `--queue-dir PATH` | Local | Persistent queue directory; default a new `data/pipeline_queues/<id>/` |
| `--python PATH` | Local | Python used for pipeline subprocesses (`quickstart.sh` sets `.venv`) |
| `--paid-referee` / `--no-paid-referee` | All | Answer the paid demolition referee question in advance |
| `--yes`, `-y` | Cloud | Accept the printed cost estimate without a prompt. Does not approve the referee |
| `--interactive` | Cloud | Stop and ask at a failed review gate; cannot be combined with `--yes` |
| `--profile NAME` | Cloud | Use your own VM profile instead of the shared VM |
| `--profiles-file PATH` | Cloud | Profile file; default `~/.config/nz-solar-potential/cloud-profiles.json` |
| `--cloud ACTION` | Cloud | Shared VM: `preflight` or `build` (default). Profile: `preflight`, `create`, `bootstrap`, `ssh`, `run` (default), `collect`, `preview`, `status` or `delete` |
| `--confirm PROJECT/ZONE/VM` | Cloud | Exact target, required by profile `create` and `delete` |

The local queue is described in
[Local task queue](pipeline-reference.md#local-task-queue). Profile set-up is
described in [Your own VM](quickstart-cloud.md#your-own-vm-profiles).

## `tools/remote_build.sh`

With no options, it runs an incremental build of every region, then gates it.
It does not publish and shows no VM cost estimate.

| Option | Effect |
| --- | --- |
| `--regions "A B"` | Build only these regions. The combined map still includes every emitted region |
| `--fetch` | Fetch the regions' inputs first; needs `--regions` |
| `--force` | Rebuild every building |
| `--yield-only` | Recompute energy from stored geometry, after a solar-model change |
| `--preview-user ALIAS` | Publish a developer preview under `devel/ALIAS/` |
| `--publish` | Publish a production release if the gate passes; `main` only |
| `--reviewed "why"` | Publish to production although the gate failed |
| `--interactive` | Stop and ask at a failed gate |
| `--ref BRANCH` | Branch to sync to the VM; default `main` |
| `--keep-running` | Leave the VM running at the end |
| `--paid-referee` / `--no-paid-referee` | Answer the paid demolition referee question in advance |

`--preview-user` and `--publish` cannot be combined. The GitHub workflow
offers the same choices as inputs: regions, fetch, mode, publish, reviewed
and paid referee.

## `src/run_district_build.sh`

Runs on the build VM. With no options, it rebuilds stale buildings in every
region and resumes interrupted work.

| Option | Effect |
| --- | --- |
| `--regions "A B"` | Build only these regions |
| `--force` | Rebuild everything |
| `--yield-only` | Recompute energy only; no LiDAR |

## Environment variables

| Variable | Effect |
| --- | --- |
| `LINZ_API_KEY` | LINZ key, from the environment or `.env`; see [Local setup](local-setup.md) |
| `SOLAR_CLOUD_PROFILE` | Selects a cloud profile; unset it to use the shared VM |
| `SOLAR_CLOUD_PROFILES_FILE` | Profile file path |
| `SOLAR_DEMOLISHED_REFEREE=1` | Approve the paid referee for fleet or direct engine calls |
| `SOLAR_DEMOLISHED_MAX_USD` | Referee spend at which it stops, per region; default USD $5 |
| `SOLAR_DEMOLISHED=0` | Skip demolition screening |
| `SOLAR_OUTLINE_FIT=0` | Skip fitting outlines to the roof |
| `SOLAR_REPEAT_ROOFS=0` | Skip agreeing identical roofs |
| `SOLAR_SELECTED_FACES=0` | District build: use only the fallback roof geometry. The local pipeline always sets it to `1` |
| `SOLAR_OUTLINE_SOURCE=wfs` | Fetch building outlines from LINZ WFS instead of the council GeoPackage |
| `CLOUD_PROJECT`, `CLOUD_VM`, `CLOUD_ZONE`, `VM_USER` | Override the shared VM target used by `remote_build.sh` |

## Costs and approvals

| Run | Asks before cost | How to answer in advance |
| --- | --- | --- |
| Local | Paid referee only | `--paid-referee` / `--no-paid-referee` |
| Shared VM quickstart | VM cost estimate, then paid referee | `--yes` for the estimate; referee flags |
| Own VM profile | Profile's `cost_estimate_nzd` for `create`, `bootstrap`, `ssh`, `run`, `collect`, `preview`; paid referee for `run`; `--confirm` target for `create` and `delete` | `--yes` for the estimate; referee flags |
| `remote_build.sh`, GitHub workflow | Paid referee only | Referee flags or workflow input |
| Fleet | Paid referee, once | `SOLAR_DEMOLISHED_REFEREE=1` |

No estimate is a spending cap. See [Cost](quickstart-cloud.md#cost) and
[demolition screening](dataset-operations.md#demolition-screening).

## See also

- [Pipeline reference](pipeline-reference.md): stages, outputs, statuses,
  clean-up.
- [Dataset operations](dataset-operations.md): review and release.
- [Troubleshooting](troubleshooting.md).
