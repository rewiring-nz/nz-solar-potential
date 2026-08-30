# NZ Solar Potential documentation

This directory documents only the `nz-solar-potential` project. The sibling
`solar-estimates` project is out of scope and has its own documentation needs.

## Start here

| Audience | Document | Purpose | Update trigger |
| --- | --- | --- | --- |
| Web map users | [Using the web map](web-map-users.md) | Find a building and interpret an estimate. | Map interaction, metrics, or assumptions change. |
| Data maintainers | [Local setup](data-maintainers/local-setup.md) | Set up a supported workstation and obtain required credentials. | Dependencies, supported platforms, or credentials change. |
| Data maintainers | [Dataset operations](data-maintainers/dataset-operations.md) | Fetch, build, validate, merge, and publish data. | Pipeline scripts, source datasets, outputs, or release checks change. |
| Software contributors | [Architecture](developers/architecture.md) | Understand the code, data flow, boundaries, and development workflow. | Module boundaries, output contracts, or local workflow change. |
| AI context maintainers | [AI context](ai-context/README.md) | Maintain concise, verified project context for AI-assisted work. | A durable decision, constraint, workflow, or unresolved issue changes. |
| Project maintainers | [ADR 0001](decisions/0001-containerisation-strategy.md) | Evaluate containerisation without adopting it prematurely. | Docker or cloud execution is proposed, trialled, adopted, or rejected. |

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

## Navigation

This project currently has no documentation-site generator. Markdown files
and relative links are the supported navigation mechanism. Introduce a
documentation generator only when the document set needs generated navigation,
search, versioning, or publication beyond repository browsing.