# Documentation

Docs for `nz-solar-potential`, the NZ rooftop solar map.
Choose your role below. Start with a how-to when doing a task; use a
reference when you need the exact stages, files or implementation.

## Map users

| Document | What it covers |
| --- | --- |
| [Using the web map](web-map-users.md) | Find a building and read its estimate. |

## Data maintainers

| Document | What it covers |
| --- | --- |
| [Quickstart](quickstart.md) | Run the pipeline on one region and open the map. |
| [Cloud quickstart](quickstart-cloud.md) | Shared-VM developer previews and experimental developer-VM profiles, costs and clean-up. |
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
| [Economics](economics.md) | Inspect the bill model, financial assumptions and battery scenarios. |
| [Theory](theory/theory.md) | Assess evidence, modelling choices, accuracy limits and missing validation. |

## Maintainers

| Document | What it covers |
| --- | --- |
| [Scaling and iteration](scaling-and-iteration.md) | Plan national capacity from historical measurements and identify what still needs measuring. |
| [Scale architecture](scale-architecture.md) | Understand regional assembly, input retention and fleet coordination. |
| [ADR 0001](decisions/0001-containerisation-strategy.md) | Containerisation decision. |

## AI agents

| Document | What it covers |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | Facts and rules for AI-assisted work. |
| [handover 24 Sep](handover-2026-09-24.md), [bug notes 22 Aug](bugdoc-2026-08-22.md), [national rehearsal plan](national-rehearsal.md) | Archived point-in-time notes (may be out of date) |
