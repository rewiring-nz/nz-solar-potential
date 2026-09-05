# Agent instructions for nz-solar-potential

Read [docs/ai-context/README.md](docs/ai-context/README.md) and
[docs/ai-context/project-facts.md](docs/ai-context/project-facts.md) first —
they hold verified, version-controlled facts about this project's data,
architecture, and open questions. Treat them as current unless the code
contradicts them, and update them in the same change that alters a fact.

## Architecture

Local Python geospatial pipeline (LINZ outlines, LiDAR, imagery →
per-building solar estimates and panel layouts) plus a static MapLibre map
frontend. See [docs/developers/architecture.md](docs/developers/architecture.md)
for module boundaries and the [docs README](docs/README.md) for the full
documentation map (data maintainers, web-map users, ADRs).

## Build and test

Set up per [docs/data-maintainers/local-setup.md](docs/data-maintainers/local-setup.md).
Use `bash src/run_dev_loop.sh pilot` for a fast pilot-region check and
`bash src/run_district_build.sh` for resumable district releases. Treat
`bash src/run_full_build.sh` as a legacy or targeted workflow. There is no
established automated test suite; add targeted tests under `tests/` when
changing deterministic algorithms or data contracts.

## Conventions

- Processing CRS is EPSG:2193 (NZTM2000); web-map output is EPSG:4326. Don't
  mix them.
- Generated datasets (`data/*.geojson`, rasters) are contracts consumed by the
  frontend — preserve field names/assumptions unless updating producer and
  consumer together.
- `config.PV_ASSUMPTIONS` is the single source of truth for PV model
  assumptions shown in the UI; don't hardcode assumption values elsewhere.
- Update the matching doc in `docs/` in the same change as a command, output,
  or operating-limit change.
