# Quickstart: run the methodology on your own patch of NZ

This project estimates rooftop solar potential for every building in a
district. This quickstart lets you run the **identical methodology** on a
small area you choose — your own street, if it's in a covered survey — and
verify each step yourself. It is not a demo build: your area becomes a
first-class region and flows through the same code, thresholds, and gates
that produced the live Queenstown map. If the quickstart is wrong
somewhere, the map is wrong the same way; that is what makes checking it
meaningful.

## What you need

- macOS, Linux, or Windows, ~10 GB free disk, Python 3.11+
  - **Windows:** the simplest route is WSL (Windows Subsystem for Linux),
    where everything below is just the Linux instructions. Native Windows
    works too, from Git Bash — with one caveat: the per-building time
    budget relies on a POSIX alarm signal Windows does not have, so it is
    switched off there (the run says so). A pathological roof will take a
    long time instead of being dropped and named. On a small quickstart
    area that is unlikely to matter.
- A free LINZ API key, created and saved to `.env` as described in
  [data-maintainers/local-setup.md](data-maintainers/local-setup.md#linz-credentials)
  (the fetch uses both the WFS and the Exports API, so the key needs more
  than the default web-services scope)
- Environment set up per
  [data-maintainers/local-setup.md](data-maintainers/local-setup.md)
- Optional, for the full vision chain, inside the project's `.venv`:

  ```bash
  pip install torch torchvision segment-anything
  ```

  All three are needed (`segment-anything` imports `torchvision`). On Linux
  and WSL, plain `pip install torch` pulls the CUDA build, ~5 GB; with no
  NVIDIA GPU, the CPU build is a fraction of that:
  `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`.
  The quickstart then downloads Meta's public SAM checkpoint (358 MB); the
  project's own roof-line detectors ship in `data/models/`.

## Run it

```bash
cp my_area.example.json my_area.json   # edit name + bbox
export LINZ_API_KEY=...
bash quickstart.sh my_test_area
```

Keep the bbox small the first time (~0.005° × 0.005°, a few dozen
buildings): the build takes a minute or two and the report stays readable.
The first run's downloads take longer than the build — mostly the wide 8 m
terrain model below.

**Where in NZ.** Inside the surveys this repo already knows (Queenstown
2021, Wanaka 2022, Kingston 2025 — `config.SURVEYS`) leave the layer fields
`null` and the covering survey is used. Anywhere else, set your survey's
layer ids — at least `dsm_layer` — in `my_area.json`; the example file says
where to find them, and the script stops with a clear message if they are
missing. Building outlines are national.

**What gets downloaded,** roughly, for a 0.005° area: building outlines
and the 1 m DSM (a few MB), 0.1 m aerial imagery (~100 MB), the few 1 km LiDAR
tiles touching your bbox, and the national 8 m DEM clipped to
your area plus 30 km (~3,700 km², ~250 MB) — distant mountains shade roofs,
and the horizon model walks 20 km out to find them. The DEM is your area's
own, in `data/regions/<name>/`; it is not the district's.

Output: `data/regions/<name>/quickstart_report.html` — one card per
building with the aerial photo, the roof facets the pipeline built
(white), detected obstructions (red), and placed panels (blue), plus the
derived numbers and which geometry path produced each roof.

## What actually ran (and where to read it)

The stages, in order — each is the production stage, not a stand-in:

| stage | code | what it claims |
|---|---|---|
| Fetch | `src/fetch_regions.py` (→ `src/fetch_pointcloud_regions.py`, `src/fetch_dem_wide.py`) | LINZ outlines/DSM/imagery, the wide 8 m DEM, and LiDAR tiles for your bbox |
| Vision precompute (optional) | `tools/predict_faces.py` | three candidate face readings per roof (SAM, line detector, LiDAR region-growing); an evidence scorer picks one |
| Roof geometry | `src/build_layout_geojson.py` → `src/roof_segmentation.py`/`src/roof_partition.py` | facets as a planar partition; precedence markup > vision > LiDAR partition |
| Obstructions | `src/obstruction_detection.py` | colour + height evidence, reconciled |
| Panels | `src/panel_fitting.py` | real panel dimensions, edge/ridge setbacks, sunniest-first fill order |
| Gates | `src/gate_panels.py` | drops panels on lumpy/sparse surfaces |
| Aggregation | `src/derive_solar_potential.py` | building totals derived FROM the panels, never recomputed |

The methodology's rulebook, with the enforcement point and check command
for every rule, is
[developers/reviewers-guide.md](developers/reviewers-guide.md).

## How to verify it

1. **Facets against the photograph.** Open the report and compare white
   boundaries to what you can see: ridges where ridges are, one facet per
   roof plane, boundaries straight. This is the single strongest check —
   it is the one the project's owner performs on every change.
2. **Panels against physics.** Panels are 1.134 × 1.961 m
   (`config.PANEL_WIDTH_M/HEIGHT_M`, a real Trina module). Measure a roof
   you know: does the count fit the area, minus the edge setback
   (`config.PANEL_EDGE_SETBACK_M`, 0.1 m at the time of writing), the
   ridge setback (`config.RIDGE_SETBACK_M`) and obstruction clearances?
3. **Numbers against arithmetic.** For any building:
   `panel_count × 0.5 kW = kwp`; annual kWh ÷ panel count should sit
   within NZ's plausible per-panel yield — roughly 550–800 kWh/panel/yr on
   north-, east- and west-facing roofs. South-facing and heavily shaded
   faces legitimately come in lower (500 is not a bug on a south slope in
   Queenstown); check the facet's aspect in step 4 before suspecting one. The derivation is
   `src/derive_solar_potential.py` — totals are sums over the panels you
   can see, by construction.
4. **One building, fully.** `python src/refit_one.py <building_id>`
   rebuilds one roof with full diagnostics; compare its facet list to the
   report card.
5. **The assumptions.** Every displayed PV assumption lives in
   `config.PV_ASSUMPTIONS` with its justification inline. Losses,
   panel spec, and the irradiance model are all there to be argued with.
6. **Your own roof.** If you live in a covered area, run your street and
   check the building you know best. That is the test the project exists
   to pass.

## Known limits of a quickstart run

- If your survey has no public LiDAR point cloud on OpenTopography (or,
  outside the known surveys, you leave `pointcloud_bulk_url` null), the run
  continues DSM-only: facet
  planes fit on the 1 m DSM raster instead of raw returns, and the
  lumpy/sparse panel gates weaken. The build says so in a WARNING line. The
  report is still meaningful; the production map for such an area would
  need the point cloud.
- Outside a known survey, LiDAR tile file names are assumed to follow the
  Otago convention (`CL2_<sheet>_<year>_<tile>.laz`). If your survey's
  store names them differently every tile 404s and the run is DSM-only —
  the fetch lists the missing names.
- The cloud correction to clear-sky irradiance comes from the nearest of
  ~28 NIWA measured-radiation stations (`src/solar_model.py`,
  `NIWA_MEASURED_GHI_STATIONS`), so a town far from its station inherits
  that station's cloud climate. Only well away from every station does it
  fall back to NASA POWER's ~100 km grid, which needs network access at
  build time.
- Hand-drawn markup (`data/roof_labels.json`) only exists for Queenstown
  benchmark roofs; your area's roofs will all be machine-read.
- Your area is marked as a quickstart area on disk
  (`data/regions/<name>/QUICKSTART_AREA`) and is left out of district-wide
  builds and merges, so trying the quickstart on a maintainer's machine
  cannot leak a test street into the live map. Delete the directory to
  remove it.
