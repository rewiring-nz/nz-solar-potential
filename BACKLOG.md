# Backlog

The working queue. **Read this at the start of a session and after every
rebuild or deploy**, and update it as items move — it is the only place the
plan survives; anything that lives only in chat is lost when context is
compacted, which is why Josh kept having to re-state the list.

Ordered by evidence, not by appeal. Every item names what it is based on.

## Realism merge: correct but nearly inert (27 Aug)

Constrained to a 4 deg cap -- the steepest join a rigid panel can lie across --
it changes almost nothing: pilot panels 71,852 -> 71,868, facets 4,443 -> 4,437.

The large gain it showed before the cap (+1,596 panels, -33% facets) came from
merging across REAL ridges, median accepted angle 19.6 deg, which Josh caught
on the map as panels crossing roof sections.

**Conclusion: slivers cannot be merged away after the fact.** They are not
spurious subdivisions of one plane; they are genuinely different planes the
segmenter found. Getting "few large blocky faces" requires the segmenter to
produce them, which makes imagery-first boundaries the route, not an optional
extra. The merge stays in (it is correct and costs nothing) but is not the fix.

## APPLY AS SOON AS THE REBUILD CLEARS (27 Aug)

**Colour corroboration keeps the whole blob.** In detect_obstructions_combined,
a colour blob is kept entire when >=15% of it overlaps height evidence. On a
flat commercial roof the colour path flags membrane tone and shadow -- 7
Shotover St (#4734932): 242.7 m2 flagged on a 462 m2 roof -- so a 73 m2 tonal
region containing one small real vent is carved whole. That is the big pink
region Josh reported.

Fix: keep the corroborated PORTION, not the blob. Colour says where a tonal
region is; height says what is actually raised. Take the intersection with the
height evidence plus a small margin, and keep the whole blob only when it is
small enough to be a plausible single object anyway.

Validate both directions afterwards -- the equipment reference (#5370338) is
the canary, and the colour path is what finds pale plant the height path
misses.

## IN PROGRESS -- resume here

**Panel fitting is the maximum priority (Josh).**

1. **Realism merge** -- DONE and committed (`0222213`). Stops splitting roofs
   where the split costs more usable area than the yield it buys. Pilot
   rebuilding now with it; baseline for comparison is
   `scratchpad/pilot_old_layouts.geojson`.
   **Next step:** when the build finishes, run
   `python src/compare_layouts.py --old <that file> --area pilot --n 12`,
   publish it, and have Josh judge. Layouts are the judge, not counts.
2. **Imagery-first roof shapes** -- not started. Josh's proposal: derive roof
   boundaries from the 0.1 m imagery, where ridgelines are usually a clear
   tonal break, and use LiDAR only for the slope of each face and where the
   image is unreadable (bright roofs, flat commercial with no internal edges).
   Three separate failures today all wanted imagery: deck detection,
   obstruction footprints, ridge placement.
3. **"Lifetime ROI" reads like a rate** and is not -- 201% annualises to about
   3.8%/yr. Label it or show an annualised figure.

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

## Reconstruction: rejected in this form (26 Aug)

Josh reviewed ten before/after layouts and called it worse on all ten. The
cause is a design error, not tuning: `panel_fitting` erodes every facet by
`RIDGE_SETBACK_M` and panels cannot span facets, so splitting a roof costs
usable area every time (a 6 m² facet keeps 57% of itself, a 400 m² one keeps
94%).

**Rule for any next attempt: few large blocky faces.** A split must earn back
the setback area it costs. Judge on layouts, never on plane count or
off-plane residual -- both scored this version as fine.

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
- Anything derived from state goes through `refreshDerived()`. Do not hand-pick
  dependents at a mutation site -- that is how the generation curve ended up
  describing panels that had been filtered away.
- One rule, one definition. `panelVisible` / `panelBandOf` decide which panels
  count, everywhere. The band rule had been written out four times and two
  copies were stale.
- A patch script that asserts and then writes at the end is all-or-nothing: a
  failed assertion late in the script silently discards the edits before it.
  Write after each edit, or verify the file actually changed.
