# Documentation

Docs for `nz-solar-potential`, the NZ rooftop solar map.
Choose your role below. Start with a how-to when doing a task; use a
reference when you need the exact stages, files or implementation.

## Map users

| Document | What it covers |
| --- | --- |
| [Using the web map](map-users/web-map-users.md) | Find a building and read its estimate. |

## Data maintainers

Start here. Each how-to is a short set of steps ending in "Next steps".

| How-to | What it covers |
| --- | --- |
| [Run the local quickstart](data-maintainers/howto-local-quickstart.md) | Build the default area on your computer and open the map. |
| [Run the cloud quickstart](data-maintainers/howto-cloud-quickstart.md) | Build the default area on the shared VM and open a developer preview. |
| [Build a region](data-maintainers/howto-build-a-region.md) | Find, check and build another area, locally or on the shared VM. |
| [Build several regions](data-maintainers/howto-scale.md) | Build multiple regions and view them on one map: locally, on the shared VM or on a fleet. |
| [Pipeline run options](data-maintainers/reference-pipeline.md) | Reference: every way to run the pipeline, its options, environment variables and cost prompts. |

Background and detail:

| Document | What it covers |
| --- | --- |
| [Quickstart](data-maintainers/quickstart.md) | Run the pipeline on one region and open the map. |
| [Choosing an area](data-maintainers/choosing-an-area.md) | Find a LINZ suburb or locality and its region id. |
| [Areas](data-maintainers/areas.md) | Browse the national candidate-area catalogue by council, with exact region ids. |
| [Cloud quickstart](data-maintainers/quickstart-cloud.md) | Shared-VM developer previews and experimental developer-VM profiles, costs and clean-up. |
| [Local setup](data-maintainers/local-setup.md) | Install tools, create the Python environment and configure LINZ credentials. |
| [Pipeline reference](data-maintainers/pipeline-reference.md) | Interpret local/profile pipeline stages, outputs and statuses; stop previews and clean up safely. |
| [Dataset operations](data-maintainers/dataset-operations.md) | Prepare, build, review, publish and roll back a district dataset. |
| [Team access](data-maintainers/team-access.md) | Get shared-project permissions; administer preview hosting. |
| [Troubleshooting](data-maintainers/troubleshooting.md) | Fix pipeline, fetch, map-build and preview errors. |

## Developers

| Document | What it covers |
| --- | --- |
| [Architecture](developers/architecture.md) | Understand data flow and module responsibilities. |
| [Tooling and execution](developers/tooling-execution.md) | Trace commands across machines; compare build engines and the proposed common interface. |
| [Reviewer's guide](developers/reviewers-guide.md) | Check geometry rules, inspect changes and choose validation tools. |

## Researchers

| Document | What it covers |
| --- | --- |
| [How a roof estimate is made](https://rewiring-nz.github.io/nz-solar-potential/method.html) | Start here for a plain-English walkthrough. Source: [`method.html`](../method.html), edited by hand. |
| [Economics](researchers/economics.md) | Inspect the bill model, financial assumptions and battery scenarios. |
| [Theory](researchers/theory.md) | Assess evidence, modelling choices, accuracy limits and missing validation. |

## Maintainers

| Document | What it covers |
| --- | --- |
| [Scaling and iteration](maintainers/scaling-and-iteration.md) | Plan national capacity from historical measurements and identify what still needs measuring. |
| [Scale architecture](maintainers/scale-architecture.md) | Understand regional assembly, input retention and fleet coordination. |
| [ADR 0001](maintainers/decisions/0001-containerisation-strategy.md) | Containerisation decision. |

## AI agents

| Document | What it covers |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | Facts and rules for AI-assisted work. |
| [handover 24 Sep](ai-agents/archive/handover-2026-09-24.md), [bug notes 22 Aug](ai-agents/archive/bugdoc-2026-08-22.md), [national rehearsal plan](ai-agents/archive/national-rehearsal.md) | Archived point-in-time notes (may be out of date) |
