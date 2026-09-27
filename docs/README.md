# NZ Solar Potential documentation

This directory documents only the `nz-solar-potential` project. The sibling
`solar-estimates` project is out of scope and has its own documentation needs.

## Start here

| Audience | Document | Purpose | Update trigger |
| --- | --- | --- | --- |
| Data maintainers | [Quickstart](quickstart.md) | Run the full regional pipeline for a configured region and open its map preview. | Pipeline command or region configuration changes. |
| Data maintainers | [Local setup](data-maintainers/local-setup.md) | Set up a supported workstation, Python environment, map-build tools, and credentials. | Dependencies, supported platforms, or credentials change. |
| Data maintainers | [Troubleshooting](data-maintainers/troubleshooting.md) | Diagnose pipeline preflight, LINZ fetch, map-build, and local preview errors using run-report step numbers. | A recurring diagnostic or repair path changes. |
| Data maintainers | [Pipeline reference](data-maintainers/pipeline-reference.md) | Explain region configuration, run artifacts, status interpretation, and clean-room limits. | Pipeline outputs or operating limits change. |
| Data maintainers | [Dataset operations](data-maintainers/dataset-operations.md) | Fetch, build, validate, merge, and publish data. | Pipeline scripts, source datasets, outputs, or release checks change. |
| Data maintainers | [How a roof estimate is made](https://rewiring-nz.github.io/nz-solar-potential/method.html) | Every decision the pipeline makes between a laser scan and the number on the map, in plain English. Public; shareable. Rebuild with `python tools/build_method_page.py method.html`. | The pipeline gains or loses a step. |
| Researchers / academic readers | [Economics](economics.md) | How cost, savings, payback, plans and batteries are calculated, and what the model leaves out. | economics.js, its assumptions, or the plan/battery model change. |
| Researchers / academic readers | [Why a building estimate can be trusted](theory/theory.md) | Explain the evidence chain, datasets, models, and limits behind per-building estimates. | Source provenance, model strategy, validation evidence, or uncertainty framing changes. |
| Software developers | [Architecture](developers/architecture.md) | Understand the code, data flow, boundaries, and development workflow. | Module boundaries, output contracts, or local workflow change. |
| Software developers | [Reviewer's guide](developers/reviewers-guide.md) | Verify the logic: rule-to-code map, per-stage check commands, glossary. | A rule's enforcement point or check command changes. |
| Project maintainers | [Scaling and iteration](scaling-and-iteration.md) | What blocks a national rollout, and why a fix takes hours. Measured. | Data volumes, build times, or the merge/tile architecture change. |
| Web map users | [Using the web map](web-map-users.md) | Find a building and interpret an estimate. | Map interaction, metrics, or assumptions change. |
| AI context maintainers | [AI context](ai-context/README.md) | Maintain concise, verified project context for AI-assisted work. | A durable decision, constraint, workflow, or unresolved issue changes. |

## Documentation rules

- Treat executable scripts, `config.py`, and committed data contracts as the
  implementation source of truth. Link to them instead of copying volatile
  details.
- Mark future cloud work, unverified claims, and experiments clearly. Current
  documented operations run locally.
- Keep platform-specific setup in the data maintainer guides. macOS is the
  primary platform; Ubuntu and Windows guidance is included where it differs.
- Keep AI context factual, concise, and reviewable in Git. Remove duplicate or
  superseded statements when adding new evidence.
- Update the relevant guide in the same change as a changed command, input,
  output, operating limit, or externally visible estimate.
