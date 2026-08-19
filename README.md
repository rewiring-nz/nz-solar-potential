# NZ Rooftop Solar Potential Map — pilot

Goal: for a pilot region, estimate how much solar energy each rooftop
receives and how many 1×2m panels could physically fit on it, then render
that as a per-building heatmap. If the pilot pipeline holds up, the same
code scales to the rest of the country.

Nationwide LiDAR is multi-terabyte and processing ~2M+ buildings is a real
batch-compute job — not something to attempt first. This pilot proves the
method on one small area, locally, before any of that.

## Data sources (verified August 2026)

| Source | What | License | Access |
|---|---|---|---|
| [LINZ LiDAR](https://www.linz.govt.nz/products-services/data/types-linz-data/elevation-data/lidar-data-coverage) | Point clouds (LAZ) + DSM/DEM GeoTIFFs, 20cm–1m res depending on region | CC-BY-4.0, free | [data.linz.govt.nz](https://data.linz.govt.nz/), needs free account + API key |
| [LINZ NZ Building Outlines](https://data.linz.govt.nz/layer/101290-nz-building-outlines/) | Building footprint polygons | CC-BY-4.0, free | Same LDS API key; **check coverage for the chosen pilot area first** — some regions (e.g. Bay of Plenty, Gisborne) weren't listed as covered as of this writing |
| [NASA POWER](https://power.larc.nasa.gov/docs/tutorials/service-data-request/api/) | Global solar irradiance, cloud-adjusted | Open, no key needed | REST API, but coarse (~1°×1° grid, ~100km) — use only as a broad calibration/sanity-check input, not the primary geometry-level model |
| [NIWA SolarView](https://niwa.co.nz/renewable-energy/solarview) | Per-address calculator using nearest climate station | **Results are non-commercial-use only per NIWA's EULA** | No bulk API — don't build on top of its output directly for a public tool; treat its methodology as a reference, not a data source |

**Irradiance approach:** rather than depending on NIWA's restricted
calculator, compute irradiance from first principles with
[pvlib](https://pvlib-python.readthedocs.io/) (sun position + clear-sky
model per roof facet's slope/aspect/lat), then bias-correct with NASA
POWER's actual-cloud-adjusted monthly averages for the pilot region. This
keeps the pipeline fully reproducible and licence-clean, and is the same
approach used in the published LiDAR-solar-potential literature (GRASS
`r.sun` / PVGIS-style models).

## Pipeline

1. **Fetch** — pull building outlines (WFS, filtered to pilot bbox) and
   LiDAR DSM + point cloud (LDS API) for the pilot area.
2. **Segment** — for each building footprint, extract the LiDAR patch and
   split the roof into planar facets (RANSAC plane-fitting on the point
   cloud, or slope/aspect clustering on the DSM raster). This is what
   separates "flat carport roof" from "two dormers and a chimney" and
   throws out anything too steep (>~45°) or too small.
3. **Fit panels** — per facet, work in the facet's local 2D coordinate
   frame (rotated to the roof's ridge line) and rectangle-pack 1×2m panels
   with edge setbacks, maximizing count. This is genuinely custom code —
   no off-the-shelf library does roof-specific panel packing.
4. **Model irradiance** — per facet, compute annual kWh/m² from slope +
   aspect + latitude (pvlib), corrected for self-shading between facets
   and shading from neighbouring buildings/terrain (using the DSM as a
   horizon obstruction surface).
5. **Aggregate & render** — combine panel count × per-panel yield → kWp,
   daily kWh, and annual kWh per building, output as GeoJSON, render on a
   **satellite basemap** (MapLibre GL — consistent with
   [`../community-groups-map`](../community-groups-map), which already
   handles the free-tile-source + Webflow-embed pattern this would
   eventually reuse) with:
   - a colour-coded heatmap/choropleth over every rooftop for the zoomed-out view
   - click-to-inspect: clicking a rooftop opens a popup with **kW peak,
     kWh/day, and kWh/year**, plus the PV assumptions used to get there
     (panel efficiency, inverter efficiency, system losses — see
     `config.PV_ASSUMPTIONS`, the single source of truth both the model and
     the UI read from, so the displayed numbers can never drift from what
     was actually calculated)

## What's needed before step 1 can run

1. **A free LINZ Data Service account + API key** — sign up at
   [data.linz.govt.nz](https://data.linz.govt.nz/), then generate a web
   services API key from your account settings. This is a self-service,
   no-cost signup; I can't do it on your behalf, but once you have the
   key, drop it in `.env` (see `.env.example`) and the fetch scripts can
   use it directly.
2. **A pilot area** — a small bbox (a suburb or two, not a whole city) with
   confirmed LiDAR + building-outline coverage. Cross-check the
   [LiDAR coverage viewer](https://www.linz.govt.nz/products-services/data/types-linz-data/elevation-data/lidar-data-coverage)
   before committing.

## Status

- **Pilot area confirmed**: central Queenstown (`config.PILOT_BBOX`) — 1270
  building outlines verified present, DSM layer confirmed available
  (`layer 105855`). Real data, not assumed coverage.
- **Local environment**: Python 3.12 venv, full geospatial stack (geopandas,
  rasterio, laspy, pvlib) installed and importing cleanly.
- **LINZ WFS access works**, but needed a non-obvious fix: WFS 2.0.0 with
  `typeNames`/`count` params silently returned 0 features for *every* bbox
  tried (including known-covered Wellington), even though the request
  succeeded (HTTP 200, valid empty FeatureCollection — no error to debug
  from). Falling back to **WFS 1.0.0** with `typeName` (singular) and
  `maxFeatures` fixed it immediately. Also: the default response CRS is
  **EPSG:2193 (NZTM2000)**, not WGS84 — `srsName=EPSG:4326` in the request
  didn't change the output CRS with `outputFormat=json`, so bbox values
  must be supplied in NZTM2000 metres (see `config.PILOT_BBOX_NZTM2000`,
  already converted). If building this out further, keep using WFS 1.0.0
  for GeoJSON queries against this service.
- **DSM fetch works**: key scope fixed, `fetch_data.py` now runs the whole
  pipeline end to end — creates a Koordinates export job cropped to the
  pilot bbox, polls it, downloads and unzips it (19 survey tiles), and
  mosaics them into `data/dsm_mosaic.tif`. Verified against real values:
  308–704m elevation range across the bbox, consistent with Queenstown's
  town-basin-to-hillside terrain, and a hillshade render clearly shows
  individual rooftops and streets.
- **Full pipeline built and working end-to-end**, real data throughout:
  - `roof_segmentation.py` — RANSAC multi-plane fit on the DSM pixel grid
    per building footprint, with a merge pass for duplicate near-identical
    planes (common on large flat roofs). 1270 buildings -> 3619 facets,
    zero crashes, ~55s.
  - `panel_fitting.py` — unrolls each facet into true on-surface (u, v)
    coordinates (correcting for slope foreshortening) and grid-packs
    1x2m panels with setback, trying both orientations and a few row/
    column offsets. 1270 buildings -> 32,858 panels, ~64s.
  - `solar_model.py` — pvlib clear-sky model bias-corrected against NASA
    POWER's actual monthly irradiance for Queenstown, cached as a
    (slope, aspect) lookup table (built in ~2.4s). Physics sanity-checked:
    30°-tilt north-facing facets get 1653 kWh/m²/yr vs. 740 for
    south-facing -- correctly the best/worst orientations for the
    Southern Hemisphere, not the Northern Hemisphere answer a naive port
    would give.
  - `build_heatmap.py` — runs all of the above over every building, writes
    `data/solar_potential.geojson` (kWp, kWh/day avg, kWh/year, panel/
    facet counts per building, plus the assumptions block used).
  - **Pilot totals**: 1270 buildings, 1164 with viable roof space, 14,458
    kWp, ~15.3 GWh/year combined for the pilot bbox.
- **`preview.html`** — working satellite-view map (MapLibre GL + Esri
  World Imagery, same free-tile approach as `../community-groups-map`)
  with the heatmap and click-to-inspect popup from the brief: click a
  rooftop, see panel count, kW peak, kWh/day, kWh/year, and the exact
  assumptions used, sourced live from the GeoJSON so the numbers shown
  can never drift from what was calculated. Run `python3 -m http.server`
  from the repo root and open `/solar-map/preview.html`.
- Known limitation, not yet addressed: small/complex roofs (garages, hip
  roofs under ~150m²) sometimes segment into thin, jagged facets at 1m
  DSM resolution that can't fit a single setback-shrunk panel -- the
  precision ceiling of grid-based (vs. point-cloud-based) segmentation,
  already flagged as the upgrade path above.
- **Facets are clipped to the true building outline** (`roof_segmentation.py`)
  -- the raw DSM-pixel-vectorized facet edge is blocky (staircases along
  the 1m grid) and can overhang the real roofline by up to ~0.7m
  diagonally; intersecting with the imagery-derived (0.1m) building
  outline snaps edges back to the accurate line. Small effect on the
  numbers (32,858 -> 32,595 panels across the pilot) but a real accuracy
  fix, not cosmetic.
- **Obstruction avoidance from aerial imagery** (`obstruction_detection.py`,
  `data/imagery_mosaic.tif` -- LINZ layer 114745, same 2021 survey as the
  DSM and building outlines, so nothing is temporally misaligned). This
  covers a real LiDAR blind spot: anything flush with the roof plane
  (skylights, existing panel arrays, low vents) produces no elevation
  signal for RANSAC to catch, so on a DSM-only pipeline those get treated
  as plain roof and panels would get placed right on top of them. Colour
  is often the only signal available for that case. Method: flag pixels
  within a facet whose colour deviates >2.75 std devs from that facet's
  own median colour, clean up noise, keep blobs in a plausible object
  size range (0.09m2 - 40% of facet area), subtract from the usable area
  before panel packing. Visually verified against real roofs -- flagged
  regions lined up closely with actual visible vents/HVAC units on a
  metal industrial roof, and with ridge/dormer clutter on a complex
  heritage-style roof. This is a heuristic, not a trained classifier, so
  it will mis-flag some things (hard shadow edges, moss patches) and miss
  others (a skylight that closely matches the surrounding roofing colour)
  -- the pipeline treats a flag as "exclude from panel placement," the
  conservative direction to be wrong in. Cut the pilot total from 14,342
  to 13,611 kWp (~5%) -- a more defensible number than pretending every
  roof is a blank rectangle.
- **Packing algorithm fixed after a validation pass found it under-filling
  good roof faces.** Method: picked 5 real, structurally diverse buildings
  from the pilot, rendered each with facets coloured by irradiance and
  panels overlaid, and checked the result against real-world install
  conventions (max out the big clean faces, align rows, don't bother with
  slivers) -- an automated scan for actual installed panels in our own
  2021 imagery came back empty (no ground-truth arrays to trace;
  Queenstown's residential solar uptake was minimal at capture time), so
  conventions were the available check. The first example (building
  5371001) immediately showed the bug: its highest-irradiance facet (1614
  kWh/m2/yr, 27m2) fitted only 2 panels while a lower-value facet fitted
  12. Root cause: `_pack_orientation` only tried a handful of fixed
  row/column start offsets, so any facet whose true (often irregular,
  DSM-pixel- and imagery-clip-derived) shape didn't line up with one of
  those few grid positions got badly under-packed regardless of how much
  usable area it actually had. Fix: each row-band now does a fine-grained
  left-to-right scan for the next valid panel position instead of only
  checking fixed multiples of the panel width -- still row-aligned
  (matches how panels are actually racked) but no longer blind to each
  facet's specific shape. Result on the diagnostic building: 22 -> 27
  panels. Across the full pilot: 32,595 -> 35,235 panels, 13,611 -> 15,503
  kWp (+14%). All 5 validation buildings re-checked visually after the
  fix -- dense, sensible coverage on every large clean facet, correct
  avoidance of obstructions and undersized slivers.
- **Panels now constrained parallel to the real roof edge, not raw plane
  aspect.** Even after the packing fix above, layouts still looked less
  clean than a professional install -- panel rows were aligned to the
  RANSAC-fit plane's aspect direction, which can be off by a few degrees
  from the facet's actual eave/ridge line (segmentation noise). Real
  installers always rack parallel to the visible roof edge. Fix
  (`_edge_aligned_axes` in `panel_fitting.py`): find the facet polygon's
  minimum-rotated-rectangle, use whichever of its two perpendicular edge
  directions best matches the aspect-derived contour direction as the
  packing u-axis, instead of deriving that axis from aspect alone. Slope
  (for the foreshortening correction) still comes from the physically-fit
  plane -- only the in-plane rotation changed. Falls back to pure-aspect
  axes if the polygon is degenerate. Verified visually across all 5
  validation buildings -- panel rows now visibly track the roofline in
  every case. Net effect on numbers was small (35,235 -> 35,754 panels,
  +1.5%) since this is primarily a visual/alignment correction, not a
  capacity one.
- **Facet boundaries smoothed -- this was the big one.** Even after the
  two fixes above, layouts still looked less clean than professional, and
  a closer look (comparing facet outlines against the visibly straight
  roofline in the imagery) showed why: every facet boundary that wasn't
  the building's own outer edge (i.e. every internal ridge/valley/hip line
  between two roof planes) was still traced directly off the 1m DSM pixel
  grid -- a real staircase, not a rendering artifact. It was cutting
  concave notches into facets right where the true roof is straight,
  which (a) shrank the packable area for no real reason and (b) fed
  mixed/blended boundary pixels into the obstruction detector, which then
  misread that staircase seam as a colour anomaly and flagged a chunk of
  good roof as an obstruction. Fix, in `roof_segmentation.py`: after
  clipping a facet to the building outline, apply a morphological closing
  (buffer out then back in, mitred joins to keep real corners sharp) to
  bridge the single-pixel notches, then simplify to collapse the
  remaining staircase into a handful of near-straight segments; re-clip
  to the building outline afterward since the closing step can push the
  smoothed edge slightly back outside it. `obstruction_detection.py` also
  now erodes its sampling region inward by the panel setback before
  analysing colour, so it isn't looking at boundary pixels at all --
  belt-and-suspenders with the smoothing fix. Impact was large: full
  pilot went from 35,754 -> 42,996 panels (+20%), 15,732 -> 18,628 kWp
  (+18%). All 5 validation buildings re-verified -- boundaries visibly
  straight now, matching the roofline in the imagery, not just "more
  panels."
- **Click-to-inspect layout on the map** (`preview.html` + `src/build_layout_geojson.py`
  -> `data/panel_layouts.geojson`): clicking a rooftop now draws that
  building's actual facets and panels on the map itself, not just the
  aggregate stats popup -- filtered client-side by `building_id` so the
  ~50k facet/panel/obstruction features only render for whichever
  building is selected.
- **Facet colour scheme fixed -- was actively misleading.** The per-facet
  "sunshine received" colour and the "avoided obstruction" colour were
  both shades of red, functionally indistinguishable once blended at
  partial opacity -- a large, legitimately high-irradiance north-facing
  roof and a small genuinely-flagged obstruction looked the same colour.
  On one large building this made it look like most of the roof had been
  wrongly flagged as an obstruction and excluded, when in fact obstructed
  area was 1-2% of every facet (confirmed by direct measurement) and the
  red was almost entirely the irradiance heatmap. Fixed two ways: (1) the
  facet colour is now a proper diverging scale -- blue (south-facing, low
  yield) -> yellow (mid) -> red (north-facing, high yield), matching how
  a NZ installer would actually read a roof at a glance, instead of a
  sequential yellow-to-red ramp that collided with the obstruction
  colour; (2) obstructions now render purple, nowhere near the heatmap's
  palette. A second legend was added (bottom-right) explaining the scale.
- **RANSAC determinism bug found while investigating the above.**
  `roof_segmentation.py` used one shared, module-level `np.random`
  instance for every building's plane-fitting, so a building's result
  depended on how many *other* buildings had already consumed random
  draws earlier in the same run -- same input, different output,
  depending on unrelated processing history. Concretely bit us: a
  standalone repro of one building (173 panels expected) gave 229 panels
  in isolation, because it hadn't inherited the RNG state that 1269 prior
  buildings had already advanced. Fixed by seeding a fresh
  `np.random.default_rng(building_id)` per building instead of sharing
  one global instance -- verified identical output whether a building is
  processed alone or after 50 others. Both `solar_potential.geojson` and
  `panel_layouts.geojson` rebuilt on the fixed version (18,569 kWp,
  42,203 panels -- both within ~1% of pre-fix numbers, as expected: this
  changes *which* valid RANSAC solution gets found, not the general
  correctness of the pipeline).
- **Local satellite tile pyramid** (`src/build_tiles.py` -> `tiles/`):
  Esri's free World Imagery has a hard zoom cutoff in this area ("Map
  data not yet available" partway through a normal zoom-in gesture). We
  already own 0.1m LINZ imagery for the whole pilot bbox -- finer than
  Esri ever had here -- so it's tiled locally into a standard Web
  Mercator XYZ pyramid (z14-19, 1846 tiles, 226MB) and layered on top of
  Esri in `preview.html`; Esri still shows everywhere outside the pilot
  bbox and at zooms we didn't generate. First attempt at the tile-index
  math had the y-axis backwards (tile y increases southward, opposite to
  how northing increases) and silently produced an empty index range at
  every zoom -- fixed and verified against a hand-computed tile bounds
  check before the full run.
- **Per-pixel generation-potential heatmap** (`src/build_heatmap_raster.py`
  -> `data/heatmap_raster.png`): shows raw orientation-driven generation
  potential at DSM resolution, before any panel layout -- a north-facing
  patch red/orange, south-facing blue, same diverging scale as
  everywhere else. A raw first-difference gradient per pixel came out as
  pure salt-and-pepper noise (1m DSM quantization noise dominates a
  per-pixel derivative -- the same reason facet segmentation fits planes
  over many pixels via RANSAC rather than trusting any single pixel);
  Gaussian-smoothing the DSM first (sigma=3.5px) before computing
  slope/aspect recovered a clean, readable signal. One image over the
  whole pilot bbox, masked to building footprints, added to the map as a
  single georeferenced overlay.
- **Heat Map / Panel Layout toggle buttons**, on the building popup.
  Heat Map is a single global layer (toggling it affects the whole
  visible map, not just whichever building's popup you toggled it from,
  since it's one precomputed raster rather than per-building data); Panel
  Layout controls the existing per-building facets/panels/obstructions.
  With both off, the selected building's own choropleth fill is also
  excluded (via a `!=` filter on `buildings-fill`) so you see the bare
  rooftop -- otherwise the overview colour would sit on top of whatever
  you're trying to inspect underneath.
- **`reference_examples/`** -- a place to drop real bare-roof /
  panels-installed photo pairs so the packing rules can be checked
  against actual installer behaviour, not just geometry reasoning. Not
  an automated training loop (`panel_fitting.py` is hand-written logic,
  not a trained model) -- see `reference_examples/README.md` for what
  actually happens with submitted examples. `src/view_reference_examples.py`
  lays any submitted pairs out into one montage for review.
- **Heatmap rebuilt to render actual facets, not a blurred DSM
  gradient.** The per-pixel version above looked wrong up close: smoothing
  strong enough to be readable also blurred straight across real ridge/hip
  lines, showing a gradient where a roof actually has two flat planes
  meeting at a sharp angle. Fixed by rasterizing the *real* RANSAC facet
  segmentation directly (same one `panel_fitting.py` uses) instead of an
  independent DSM gradient -- uniform colour within a facet, a hard cut
  exactly at its boundary, and no colour at all on pixels that didn't
  resolve into a facet (a thin honest gap at a valley) rather than a
  smoothed-over guess.
- **`MAX_PLANES_PER_BUILDING = 6` was silently truncating large buildings
  -- this was the real cause of a "why is there obvious spare roof with
  no panels" report.** Checked directly on the reported building: it
  needs ~20 distinct planes (a large multi-wing complex), RANSAC found
  them fine when allowed to, but the old cap stopped at 6 -- claiming
  only 57.5% of the roof's points before giving up, silently dropping an
  entire clean flat section (no facet, no obstruction check, no panels,
  no error). Raised to 40 (real buildings in the pilot topped out around
  20; RANSAC still exits early on its own once no significant plane
  remains, so this doesn't slow down the many small/simple buildings at
  all -- full-batch went 73s -> 92s). That one building: 6 -> 17 facets,
  221 -> 287 panels, 97 -> 126 kWp.
- **Panels changed from opaque navy to semi-transparent black** so the
  roof underneath (imagery or heatmap) stays visible through them, and
  the facet fill was removed entirely in favour of a dotted outline --
  it used the same colour scale as the heatmap, so a low-yield
  south-facing facet was nearly the same dark blue as the panels sitting
  on top of it. **Heat Map and Panel Layout are now one mutually
  exclusive mode** (`none` / `heatmap` / `layout`), not two independent
  toggles -- showing both by default was exactly the "why is there a
  blue area where panels aren't placed" confusion, since that blue was
  the heatmap, still on underneath.
- **Cache-busting on the data fetches** (`?v=` query param, bumped
  per rebuild) -- without it, a browser happily keeps serving a stale
  cached `solar_potential.geojson`/`panel_layouts.geojson` after a
  reload even though the file on disk changed, which looked exactly
  like a fix hadn't taken effect when it actually had.
- **Live parameter-tuning sliders** (`src/live_server.py`, replaces the
  plain `python3 -m http.server` -- see setup below). Every building's
  popup now has a "Tune panel-fitting parameters" section: edge setback,
  roof flatness tolerance (the RANSAC plane-fit threshold), and
  obstruction sensitivity, each a slider. Moving one calls
  `/api/refit?building_id=...&setback=...&ransac_threshold=...&z_threshold=...`,
  which re-runs segmentation + obstruction detection + panel fitting for
  *just that one building* with the overridden values (tens of
  milliseconds -- all the heavy inputs are loaded once at server startup,
  not per request) and renders the result in a separate `live-layout`
  source that never touches the shared bulk dataset used for every other
  building. Debounced 250ms so dragging doesn't fire a request per pixel.
  "Reset to defaults" clears the live layer and restores the normal
  static (pre-computed) view. Point of this: land on better default
  constants by feel, on real buildings, instead of guessing blind and
  waiting ~90s for a full pilot rebuild to see the effect.
- **Buildings-fill low-colour collision fixed.** Its 0-kWp end was
  `#2c3e8c`, a blue-indigo close enough to the obstruction colour
  (`#a855f7`, purple) that a row of small, low-kWp buildings read as "this
  roof is covered in obstructions" when what was actually showing was
  neighbouring (unselected) buildings' own low-value choropleth colour.
  Swapped for an unambiguous pure blue (`#1565c0`).
- **Tried and reverted: plane-intersection boundary refinement.** The
  theory was sound -- where two roof planes actually meet, their
  intersection *is* the true straight ridge/valley line, computable
  directly from the plane equations RANSAC already finds, so reassigning
  boundary pixels to whichever nearby plane fits best should replace the
  RANSAC-sampling staircase with the real line. Looked promising on two
  clean-facet buildings, but full-pilot testing showed a net *regression*
  (42,978 -> 40,288 panels, -6.3%) -- reverted rather than shipped.
  Investigating it surfaced two things worth keeping: (1) a real latent
  bug in `merge_similar_facets` (a `unary_union` result was never checked
  for coming back as a `MultiPolygon`, which crashed the demo renderer
  and could silently corrupt merged facets elsewhere) -- fixed and kept;
  (2) direct visual proof of the actual ceiling on this approach: one
  building turned out to be a sawtooth/north-light industrial roof with
  ridges only 1-2m apart, genuinely below what a 1m DSM can resolve into
  clean planes. No boundary post-processing manufactures facets that
  were never resolvable at this resolution -- that specific class of
  "complex roof" needs the higher-density point cloud (still declined,
  still on offer), not a smarter fit on the same 1m grid.
- **Dashboard for Heat Map / Panel Layout**, top-left, always visible --
  not just inside a building's popup. Same `setMode()` the popup buttons
  already used, kept in sync in both directions.
- **Heat Map "ceiling" estimate**: independent of the actual fitted panel
  layout entirely -- (resolved facet area) x (customisable coverage %,
  20-95% slider, default 80) x (panel power density from the same
  assumptions block the popup shows). Recomputed client-side on every
  slider move (no server round-trip) from `facet_area_m2` and
  `avg_poa_kwh_m2`, two new fields `build_heatmap.py` now writes per
  building. Small `kWp` text labels render per building in Heat Map mode
  (zoom >= 16, filtered to >0.5 kWp to avoid clutter) via a MapLibre
  symbol layer reading the recomputed value straight off the source data.
- **Heatmap gaps filled with a visibly-lower-confidence fallback.** One
  real building only resolved 31% of its footprint into facets (a large
  vaulted/curved section RANSAC couldn't fit a plane to) -- the other 69%
  rendered as a blank hole right through the middle of the roof, which
  reads as a bug even though it was working as designed (no fake colour
  where there's no real facet). Now two-tier: resolved facets render at
  full alpha (255) as before; any other roof pixel gets the original
  per-pixel Gaussian-smoothed DSM gradient (the method actually *rejected*
  earlier for blurring across real ridge lines -- demoted from "the
  method" to "better than a blank gap") at reduced alpha (140/255), so it
  visibly reads as lower-confidence without needing a separate hatch
  texture. Pilot-wide: 61% of roof pixels are high-confidence (resolved
  facet), 39% fallback. Verified on the reported building -- the vaulted
  centre section is now visibly filled, paler than the sharp facet colours
  around it, not a hole.
- **Fixed the real cause of "large obviously-usable roof sections have zero
  panels."** Two distinct bugs, found by direct measurement after several
  wrong theories (stale cache, plane cap, obstruction over-detection) were
  ruled out by testing them directly and finding no effect:
  1. `label_raster_to_polygon` (roof_segmentation.py) vectorized a RANSAC
     plane's inlier-pixel mask and kept only `max(polygons, key=area)` --
     the single largest connected component -- silently discarding every
     other disconnected chunk. RANSAC has no spatial-contiguity
     constraint, so a single plane fit routinely claims pixels from more
     than one physically separate part of a complex roof (both ends of a
     hip, a chunk beyond a dormer, a separate wing at the same
     pitch/orientation); all of that correctly-classified area was being
     thrown away, never becoming a facet or a panel, and could never be
     reclaimed by a later RANSAC pass since the points were already
     marked claimed. Measured on one building: 97% of the footprint's
     points were correctly claimed by some plane, but this truncation
     alone cut that to 1792m² (46%) of polygon area *before* the
     building-outline clip even ran. Fixed by rewriting it as
     `label_raster_to_polygons`, returning one facet candidate per
     connected component instead of only the largest -- each goes through
     the same clip/smooth/`MIN_FACET_AREA_M2` filter as before, so
     genuine single-pixel noise still gets dropped, but real secondary
     chunks no longer do. Verified full pilot-wide rebuild: 42,978 -> 49,305
     panels (+14.7%), heatmap high-confidence coverage 61% -> 71%.
  2. Separately, and probably the bigger contributor to the *wide,
     multi-building* screenshots specifically: Panel Layout mode's layers
     were always filtered to `building_id == selectedBuildingId`, which
     defaulted to `-1` (nothing) until a building was clicked, and only
     ever showed *one* building's facets/panels/obstructions at a time --
     every other building, however well segmented, rendered nothing until
     it was individually clicked. A debug render that appeared to show a
     whole roof missing panels turned out, on inspection, to be a
     *separate building* (a different `building_id` sharing the same
     bounding-box crop) that was simply never in scope for that one
     single-building debug call -- rendered on its own, it had full, dense
     panel coverage. Fixed in `preview.html`: the layout layers now carry
     a permanent `kind`-only base filter (`LAYOUT_KIND_FILTER`) so every
     building's layout renders simultaneously regardless of selection;
     clicking a building still drives the popup and (via `refitLive`)
     excludes just that one building's static layout while its live-tuned
     preview is shown, but no longer hides anyone else's.

## Local setup

```bash
cd solar-map
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

To run the map (from the repo root, one level up from `solar-map/`), use
`live_server.py` rather than a plain static server -- it serves
`preview.html` and `data/` exactly the same way, plus the `/api/refit`
endpoint the tuning sliders need:

```bash
source solar-map/.venv/bin/activate
python solar-map/src/live_server.py 8000
```

Then open `http://localhost:8000/solar-map/preview.html`. A plain
`python3 -m http.server` still works for everything except the sliders
(they'll show "Refit failed -- is live_server.py running?").
