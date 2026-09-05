# Project facts

Last verified: 2026-08-31

## Verified facts

- The project is a local Python geospatial pipeline with a static MapLibre map
  interface. It is scoped to Queenstown pilot and configured expansion areas.
- The processing CRS is EPSG:2193 (NZTM2000); web-map output is EPSG:4326.
- LINZ WFS access in the current fetch code uses WFS 1.0.0 with `typeName` and
  `maxFeatures`. The repository records that the WFS 2.0.0 parameter form
  returned empty results during prior validation.
- Full regional builds isolate each area in its own Python process because
  decoded point-cloud tiles are retained in process memory.
- Missing aerial imagery does not block builds: the regional fetcher logs a
  warning and the build runs without imagery-based obstruction detection.
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