# Documentation

Docs for `nz-solar-potential`, the NZ rooftop solar map.

| For | Document | What it covers |
| --- | --- | --- |
| Map users | [Using the web map](web-map-users.md) | Find a building and read its estimate. |
| Everyone | [How a roof estimate is made](https://rewiring-nz.github.io/nz-solar-potential/method.html) | Every step from laser scan to the number on the map, in plain English. Source: [`method.html`](../method.html), edited by hand. |
| Researchers | [Economics](economics.md) | How bills, savings, payback and batteries are worked out. |
| Researchers | [Theory](theory/theory.md) | The evidence, data and models behind each estimate, and their limits. |
| Data maintainers | [Quickstart](quickstart.md) | Run the pipeline on one region and open the map. |
| Data maintainers | [Local setup](data-maintainers/local-setup.md) | Workstation, Python, map tools and credentials. |
| Data maintainers | [Pipeline reference](data-maintainers/pipeline-reference.md) | Region config, run outputs and limits. |
| Data maintainers | [Dataset operations](data-maintainers/dataset-operations.md) | Fetch, build, check and publish data. |
| Data maintainers | [Team access](data-maintainers/team-access.md) | Cloud access and the shared build VM. |
| Data maintainers | [Troubleshooting](data-maintainers/troubleshooting.md) | Fix pipeline, fetch, map-build and preview errors. |
| Developers | [Cloud quickstart](quickstart-cloud.md) | Experimental single-VM cloud build: profiles, cost and clean-up. |
| Developers | [Architecture](developers/architecture.md) | Code layout, data flow and boundaries. |
| Developers | [Reviewer's guide](developers/reviewers-guide.md) | Which code enforces each rule, and how to check it. |
| Maintainers | [Scaling and iteration](scaling-and-iteration.md) | What stands between this and a national map, with measurements. |
| Maintainers | [Scale architecture](scale-architecture.md) | How builds and serving scale past one district. |
| Maintainers | [ADR 0001](decisions/0001-containerisation-strategy.md) | Containerisation decision. |
| AI agents | [AGENTS.md](../AGENTS.md) | Facts and rules for AI-assisted work. |

Archived point-in-time notes (may be out of date):
[handover 24 Sep](handover-2026-09-24.md),
[bug notes 22 Aug](bugdoc-2026-08-22.md),
[national rehearsal plan](national-rehearsal.md).

## Rules

- Scripts, `config.py` and the data contracts are the source of truth. Link
  to them rather than copying values that change.
- Update the matching doc in the same change as a command, input, output,
  limit or published figure.
- Mark experiments and unverified cloud steps as such.
- Keep platform setup in the data-maintainer guides. macOS first; Ubuntu and
  Windows where they differ.
