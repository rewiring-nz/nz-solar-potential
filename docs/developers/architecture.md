# Architecture for software contributors

## System shape

`nz-solar-potential` is a local Python geospatial pipeline plus a static
MapLibre web-map frontend. The pipeline derives per-building solar estimates
and panel layouts from LINZ building outlines, elevation data, point clouds,
and aerial imagery. The frontend reads generated data and presents aggregate
and selected-building views.

```mermaid
flowchart TB
    LINZ[LINZ outlines, DSM, DEM, imagery] --> Fetch[fetch_data / fetch_regions]
    Fetch --> Inputs[data or data/regions/name]
    Inputs --> Segment[roof segmentation & skeleton reconstruction]
    Segment --> Detect[obstruction detection]
    Detect --> Fit[panel fitting and gates]
    Fit --> Layouts[panel_layouts.geojson]
    Layouts --> Derive[derive_solar_potential]
    Derive --> Horizon[bake_building_horizons]
    Horizon --> Outputs[solar_potential.geojson & rasters]
    Outputs --> Merge[merge and tile preparation]
    Merge --> Map[preview.html / static hosting]
```

## Key boundaries

- `config.py` owns model constants, source layer IDs, area definitions,
  exclusions, and user-visible PV assumptions.
- `src/fetch_data.py` and `src/fetch_regions.py` acquire source inputs.
- `src/region_build.py` resolves paths and assigns overlapping outlines to one
  region before builds. All region-aware tools should use its path helpers.
- `src/roof_segmentation.py`, `src/roof_partition.py`, and
  `src/roof_skeleton.py` segment roofs into planar facets using RANSAC and
  constructive straight-skeleton methods competing under confidence gates.
- `src/build_layout_geojson.py`, `src/gate_panels.py`, and
  `src/rerank_layouts.py` fit, gate, and rank physical panel layouts.
- `src/derive_solar_potential.py` aggregates layout outputs into the
  building-summary layer (`solar_potential.geojson`), guaranteeing numerical
  consistency between layout features and building totals without recomputation.
- `src/building_horizon.py` and `src/bake_building_horizons.py` compute
  per-building 72-bin horizon profiles (`horizon_b64`, `horizon_beam_pct`)
  combining wide bare-earth DEM terrain and near DSM obstacles.
- `src/merge_regions.py` produces site-level artifacts. It is the boundary
  between per-region processing and map-facing datasets.
- `src/render_building_debug.py` and `src/render_top_movers.py` generate visual
  debug cards and build-over-build diff reports for pre-release validation.
- `preview.html` is the static map. `src/live_server.py` adds a local-only
  refit endpoint and must stay behaviourally aligned with the static build.

## Design principles

- Prefer reproducible local builds from versioned code, configuration, and
  source data.
- Preserve geographic correctness: use NZTM2000 for metre-based processing and
  WGS84 only for web-map output.
- Keep regions independent during costly processing to bound memory and enable
  resumable failures.
- Treat generated datasets as contracts. Preserve field names and assumptions
  consumed by the map unless updating producer and consumer together.
- Use deterministic processing where randomness is involved so a building's
  output does not depend on unrelated build order.
- Validate visually as well as geometrically. Roof layout quality is a
  user-visible property not fully captured by containment checks alone.

## Development workflow

Set up the environment using the [data maintainer guide](../data-maintainers/local-setup.md), then make a narrow change and use `bash src/run_dev_loop.sh pilot` for the fastest behaviour check. Run the relevant audit or validation script before a broad regional rebuild. Use `bash src/run_full_build.sh` only when releasing or checking full-region effects.

There is no established automated test suite or documentation-site generator
at present. Add targeted tests when changing deterministic algorithms or data
contracts, and update the matching document when a command, output, model
assumption, or operational constraint changes.