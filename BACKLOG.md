# Backlog

The working queue. **Read this at the start of a session and after every
rebuild or deploy**, and update it as items move — it is the only place the
plan survives; anything that lives only in chat is lost when context is
compacted, which is why Josh kept having to re-state the list.

Ordered by evidence, not by appeal. Every item names what it is based on.

## Next up

| # | Item | Why | Blocked by |
|---|------|-----|------------|
| 1 | **Strong-path cap rework** | The size cap rejects large real plant *before* the above-plane test that identifies it. 76 panels sit on real structure across the validation set. Patch is written: `scratchpad/apply_cap_rework.py` | Must not be applied while a rebuild is running — each area is a fresh Python process, so a mid-run edit gives some areas old code and some new |
| 2 | **Business layouts more conservative** | Josh: flat commercial roofs have space to spare, so bigger setbacks around obstructions are cheap there | Touches `panel_fitting.py`; wait for rebuild |
| 3 | **Marginal-rate variant of panel economics** | Current highlight uses the building's blended rate. Strictly, a marginal panel *exports*, so a stricter test would use the export rate alone | Nothing — frontend only |
| 4 | **440 W / 550 W dual panel sizing** | Not started | — |

## Known-real, not yet measurable

- **Decks/balconies counted as roof.** Confirmed by Josh on 1/49 Belfast Terrace
  (facets over 99% of a 228 m² outline, about half of it open deck). Inflates
  capacity and puts panels on balconies. **Three detection attempts failed** —
  see `src/audit_decks.py` for what and why. Needs imagery, not LiDAR.
- **2 Kent St class: extraction misses small faces.** Both the shipped model and
  the reconstruction find 4 faces where Josh counts 13–14. Greedy RANSAC at a
  0.15 m tolerance absorbs small raised faces into the large planes near them.
  Fix would be normal-based region growing.
- **Under-detection.** Panels still on real structure at 1 Earl St, 17 Marine
  Pde, 35 Shotover St. Item 1 is the first attempt at this.

## Bigger bets

- **Imagery-guided boundaries.** Imagery is 0.1 m, LiDAR ~0.42 m — a 4×
  advantage on edges that is currently unused. Three separate problems have now
  pointed here: deck detection, obstruction footprints, and ridge placement.
- **Optimise layout quality directly, not plane counts.** Today showed that
  optimising a proxy leads astray. `audit_layouts.py` already measures the hard
  violations; that should be the objective.

## Blocked on Josh

- **Permanent LINZ developer key** (`basemaps@linz.govt.nz`). Gates imagery for
  four areas — **1,901 buildings** that cannot be built at all.

## Done today (26 Aug)

- Self-consumption modelled as a daytime load, not a share of output
- Heat-map resolution swaps cross-fade instead of blinking
- Parallel area driver (`run_layouts_regate_par.sh`) — 8× on the rebuild
- Tariff assumptions surfaced on the building panel; ROI added
- Heat-map economics follow the coverage choice
- Obstruction blobs closed rather than only dilated (+3.2% panels on the
  validation set) — currently rebuilding
- 20 roofs labelled by Josh; scorer in `src/score_labels.py`
- Per-panel economics highlight with three ROI bands and filtering

## Standing rules

- Never edit pipeline files while a rebuild is running.
- A sweep must charge a crash as a failure, not skip it — a skipped case
  contributes zero error and makes the settings that crash most look best.
- Validate obstruction changes in BOTH directions (`validate_obstructions.py`);
  the equipment reference is the canary.
- Read the warnings already in a file before using it.
