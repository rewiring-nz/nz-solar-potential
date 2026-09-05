# Project facts

Last verified: 2026-09-05

## Verified facts

- The project is a local Python geospatial pipeline with a static MapLibre map
  interface. It is scoped to Queenstown pilot and configured expansion areas.
- The processing CRS is EPSG:2193 (NZTM2000); web-map output is EPSG:4326.
- LINZ WFS access in the current fetch code uses WFS 1.0.0 with `typeName` and
  `maxFeatures`. The repository records that the WFS 2.0.0 parameter form
  returned empty results during prior validation.
- Full regional builds isolate each area in its own Python process because
  decoded point-cloud tiles are retained in process memory.
- `src/run_district_build.sh` is the current resumable district orchestrator.
  It runs per-area layout, gating, reranking, derivation, confidence, horizon,
  and raster stages, then merges and runs district-wide post-processing and
  PMTiles generation. `src/run_full_build.sh` is an older simpler path.
- `src/region_build.py` treats `pilot` as a region-aware build area whose
  inputs and outputs resolve under `data/regions/pilot/`, while pilot source
  acquisition starts under `data/`. The repository comments state that pilot
  inputs are symlinked into the region tree; the setup mechanism should be
  verified on a fresh checkout before documenting it as a user command.
- Per-area generated outputs are under `data/regions/<area>/`; merged map-facing
  outputs are under `data/`, including GeoJSON, heatmap artifacts, and
  `panel_layouts.pmtiles`.
- Missing aerial imagery does not block builds: the regional fetcher logs a
  warning and the build runs without imagery-based obstruction detection.
- `data/dem_wide_mosaic.tif` is a required root-level input for several current
  stages, including building horizons and terrain masks. No repository script
  builds it; a maintained copy must be supplied separately.
- Roof segmentation uses RANSAC plane fitting and straight-skeleton constructive
  reconstruction (`src/roof_skeleton.py`) competing under partition confidence
  gates, with top-surface point filtering and plane refits.
- Building solar potential summary data (`solar_potential.geojson`) is derived
  by aggregating layout features directly (`src/derive_solar_potential.py`),
  ensuring summary totals and individual layouts match without re-segmentation.
- Shading incorporates per-building 72-bin horizon profiles (`horizon_b64`,
  `horizon_beam_pct`) combining wide 8m DEM terrain and 1m DSM obstacles
  evaluated at eave height.
- `config.PV_ASSUMPTIONS` is the intended single source for displayed PV model
  assumptions and generated summary data.
- The current static-host configuration publishes repository files. The local
  refit API exists only through `src/live_server.py` and is unavailable on
  static hosting.

## Open questions

- What release versioning, retention, and provenance record should accompany
  published generated datasets?
- Which PMTiles build and hosting process should deliver detailed layouts at
  district scale?
- Should containerisation be adopted after local workflow measurements? See
  [ADR 0001](../decisions/0001-containerisation-strategy.md).
- How are pilot root inputs created or symlinked into `data/regions/pilot/` on a
  fresh checkout?
- Is `src/live_server.py` intentionally limited to root-level pilot inputs, or
  should it be adapted for region-aware outputs before regional development is
  supported?