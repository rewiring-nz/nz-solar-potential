# Quickstart: run the methodology on your own patch of NZ

This project estimates rooftop solar potential for every building in a
district. This quickstart lets you run the production roof/layout and yield
stages on a small area you choose — your own street, if it is in a covered
survey — and inspect the result. It uses the same stage implementations,
thresholds, and gates as the regional build. It is a **verification build, not
a complete map deployment**: it does not emit/combine PMTiles or make the new
area appear in the normal web map.

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
  (the quickstart downloads Meta's public SAM checkpoint, 358 MB; both
  project roof-line model checkpoints must be present in `data/models/`)

## Run it

```bash
cp my_area.example.json my_area.json   # edit name + bbox
export LINZ_API_KEY=...
bash quickstart.sh my_test_area
```

The area name passed on the command line must exactly match `name` in
`my_area.json`. The script validates this, the WGS84 bbox, the Python
executable, and the presence (not the value) of `LINZ_API_KEY` before
starting network work. Credentials are never copied into the report or logs.

Keep the bbox small the first time (~0.005° × 0.005°, a few dozen
buildings): the run finishes in minutes and the reports stay readable.
The defaults in the example file cover the Queenstown Lakes district; for
any other part of NZ, set your district's DSM/DEM/imagery layer ids (the
example file says where to find them). Building outlines are national.

### Primary outputs

- **Pipeline run report (`report.md`):**
  `data/quickstart_runs/<name>/<run-id>/report.md`
  The primary audit record. Open this first to verify that all steps passed,
  check durations, review any degraded inputs (such as missing point-cloud
  tiles), and access links to detailed per-step logs (`step-NN-*.log`).
- **Visual verification cards (`quickstart_report.html`):**
  `data/regions/<name>/quickstart_report.html`
  The visual report. Open in a browser to inspect aerial photos with overlaid
  roof facets (white), obstructions (red), and placed panels (blue).

## Run artifacts and debugging

Each invocation gets a unique, ignored run directory:
`data/quickstart_runs/<name>/<UTC-run-id>/`. It is separate from generated
region data and is not published with the map. The terminal prints the exact
paths when the run stops or finishes.

- `report.md` is the incrementally updated run record. Its step numbers and
  descriptions match the `[QS-NN]` prefixes in the terminal and logs. Each row
  records inputs, expected outputs, status, exit code, duration, and the
  failure consequence or degradation.
- `run.log` is the complete ordered log with those same step prefixes.
- `step-NN-*.log` contains the full stdout/stderr for that step. Start with
  the first failed step in `report.md`, then inspect its matching log.
- `run.json` is the machine-readable record, including run metadata, timestamps,
  durations, exit codes, and statuses.

`PASS` means the command succeeded and expected output checks passed.
`DEGRADED` means the run continued with an optional source or method missing;
read the consequence in the report before interpreting the results. `SKIPPED`
is used for optional vision precomputation when prerequisites are absent.
`FAIL` stops dependent work. Later steps are marked `NOT RUN`, not silently
treated as successful. The report is written after every status change, so a
failed or interrupted run still leaves a useful partial record.

The separate visual output remains at
`data/regions/<name>/quickstart_report.html` — one card per building with the
aerial photo, roof facets (white), detected obstructions (red), placed panels
(blue), derived numbers, and the geometry source.

## What actually ran (and where to read it)

The numbered executable steps, in order, are:

| step | description (exact log/report label) | code | what it claims |
|---|---|---|---|
| 01 | Preflight: validate area and runtime | `tools/quickstart_run.py` | validate the area/environment; make the run record |
| 02 | Fetch outlines, elevation, imagery, and point cloud | `src/fetch_regions.py` | LINZ outlines, DSM/DEM/imagery, and point-cloud fetch (this fetcher invokes point-cloud acquisition itself) |
| 03 | Vision precompute (optional) | `tools/predict_faces.py` | candidate roof-face readings; skip/failure is recorded and geometry uses available readings and normal fallbacks |
| 04 | Build roof geometry and layout | `src/run_stage.py build_layout_geojson` | roof facets; precedence markup > selected vision faces > normal partition path |
| 05 | Gate panel layouts | `src/run_stage.py gate_panels` | applies panel surface gates |
| 06 | Rerank panel layouts | `src/run_stage.py rerank_layouts` | ranks/fills gated panel layouts |
| 07 | Derive building solar potential | `src/run_stage.py derive_solar_potential` | derives building totals from panel layouts |
| 08 | Render building verification report | `tools/quickstart_report.py` | renders per-building visual verification cards |

The methodology's rulebook, with the enforcement point and check command
for every rule, is
[developers/reviewers-guide.md](developers/reviewers-guide.md).

## How to verify it

1. **Pipeline health in `report.md`.** Open
   `data/quickstart_runs/<name>/<run-id>/report.md` (the run terminal prints the
   exact path on completion). Confirm all steps completed (`PASS` or explainable
   `DEGRADED`/`SKIPPED` status for optional vision/point-cloud steps). If any step
   degraded or failed, click the linked `step-NN-*.log` to review raw stdout/stderr.
2. **Facets against the photograph (`quickstart_report.html`).** Open
   `data/regions/<name>/quickstart_report.html` in a browser and compare white
   boundaries to what you can see: ridges where ridges are, one facet per
   roof plane, boundaries straight. This is the single strongest check —
   it is the one performed on every change.
3. **Panels against physics.** Panels are 1.134 × 1.961 m
   (`config.PANEL_WIDTH_M/HEIGHT_M`, a real Trina module). Measure a roof
   you know: does the count fit the area, minus the 0.3 m edge setback
   and obstruction clearances?
4. **Numbers against arithmetic.** For any building:
   `panel_count × 0.5 kW = kwp`; annual kWh ÷ panel count should sit
   within NZ's plausible per-panel yield (roughly 550–800 kWh/panel/yr
   depending on tilt/aspect/shading). The derivation is
   `src/derive_solar_potential.py` — totals are sums over the panels you
   can see, by construction.
5. **One building, fully.** `python src/refit_one.py <building_id>`
   rebuilds one roof with full diagnostics; compare its facet list to the
   report card in `quickstart_report.html`.
6. **The assumptions.** Every displayed PV assumption lives in
   `config.PV_ASSUMPTIONS` with its justification inline. Losses,
   panel spec, and the irradiance model are all there to be argued with.
7. **Your own roof.** If you live in a covered area, run your street and
   check the building you know best. That is the test the project exists
   to pass.

## Known limits of a quickstart run

- If your survey has no public LiDAR point cloud on OpenTopography, the
  fetch report records absent/missing tiles and the build may rely on the
  1 m DSM instead of raw returns. Evidence and surface gates may be weaker;
  interpret this as degraded rather than equivalent data.
- Without torch/SAM or either required roof-line checkpoint, vision
  precomputation is explicitly marked `SKIPPED`. Geometry still follows its
  normal selected-reading and fallback precedence; it is not accurate to say
  every roof necessarily uses only one fallback method.
- The quickstart does not run horizon/heatmap stages, regional tile emission,
  or combination. Its successful output is **not map-ready**; the local web
  map continues to read the existing map-facing files in `data/`. Do not
  combine an isolated quickstart area into the normal map data directory.
