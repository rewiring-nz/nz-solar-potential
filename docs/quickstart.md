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
- A free LINZ API key (data.linz.govt.nz → account → API keys, enable the
  **REST API** scope)
- Environment set up per
  [data-maintainers/local-setup.md](data-maintainers/local-setup.md)
- Optional, for the full vision chain: `pip install torch segment-anything`
  (the quickstart downloads Meta's public SAM checkpoint, 358 MB; the
  project's own roof-line detectors ship in `data/models/`)

## Run it

```bash
cp my_area.example.json my_area.json   # edit name + bbox
export LINZ_API_KEY=...
bash quickstart.sh my_test_area
```

Keep the bbox small the first time (~0.005° × 0.005°, a few dozen
buildings): the run finishes in minutes and the report stays readable.
The defaults in the example file cover the Queenstown Lakes district; for
any other part of NZ, set your district's DSM/DEM/imagery layer ids (the
example file says where to find them). Building outlines are national.

Output: `data/regions/<name>/quickstart_report.html` — one card per
building with the aerial photo, the roof facets the pipeline built
(white), detected obstructions (red), and placed panels (blue), plus the
derived numbers and which geometry path produced each roof.

## What actually ran (and where to read it)

The stages, in order — each is the production stage, not a stand-in:

| stage | code | what it claims |
|---|---|---|
| Fetch | `src/fetch_regions.py`, `src/fetch_pointcloud_regions.py` | LINZ outlines/DSM/DEM/imagery + LiDAR tiles for your bbox |
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
   it is the one performed on every change.
2. **Panels against physics.** Panels are 1.134 × 1.961 m
   (`config.PANEL_WIDTH_M/HEIGHT_M`, a real Trina module). Measure a roof
   you know: does the count fit the area, minus the 0.3 m edge setback
   and obstruction clearances?
3. **Numbers against arithmetic.** For any building:
   `panel_count × 0.5 kW = kwp`; annual kWh ÷ panel count should sit
   within NZ's plausible per-panel yield (roughly 550–800 kWh/panel/yr
   depending on tilt/aspect/shading). The derivation is
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

- If your survey has no public LiDAR point cloud on OpenTopography, the
  run continues DSM-only: facet planes fit on the 1 m DSM raster instead
  of raw returns, and the lumpy/sparse panel gates weaken. The report is
  still meaningful; the production map for such an area would need the
  point cloud.
- Without torch/SAM, the vision precompute is skipped and every roof uses
  the LiDAR partition — the same fallback production uses where the
  vision chain declines. Roof shapes on complex houses are noticeably
  better with the vision chain on.
- Hand-drawn markup (`data/roof_labels.json`) only exists for Queenstown
  benchmark roofs; your area's roofs will all be machine-read.
