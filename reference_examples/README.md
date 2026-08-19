# Reference examples

A place to drop real-world roof photos so the panel-fitting rules in
`src/panel_fitting.py` can be checked against how installers actually do
it, instead of only against physics/geometry reasoning.

**Important to be upfront about**: this isn't a training pipeline that
automatically improves itself from these images. `panel_fitting.py` is a
hand-written geometric algorithm (setback distance, row alignment, offset
search), not a trained model -- there's no automated learning loop to
feed. What actually happens is: you drop examples here, and in a
conversation I look at them next to what the algorithm currently
produces, and where they disagree I go change the specific rule causing
it (e.g. "real installs leave more clearance around vent pipes than our
0.3m setback" -> change `PANEL_EDGE_SETBACK_M` in `config.py`, or
"installers never split a row across a hip line like this" -> change the
packing logic). The examples are the calibration input; I'm the one doing
the calibrating, on request, not a background process.

## How to add an example

One subfolder per roof:

```
reference_examples/
  <short-name>/
    bare.jpg          -- the roof with no panels (aerial or ground photo)
    installed.jpg      -- the same roof with panels actually installed
    notes.md            -- optional: anything worth knowing (see below)
```

`<short-name>` can be anything identifying, e.g. `queenstown-gable-01` or
just an address. Both images should ideally be the same roof from
roughly the same angle -- an aerial `bare.jpg` next to a ground-level
`installed.jpg` still helps, but a matched pair makes the comparison much
more direct.

### `notes.md` (optional but valuable)

Free text, but useful things to include if you know them:
- Roof pitch/material if visibly unusual
- Panel size, if different from the 1x2m assumed in `config.py`
- Anything about the layout that looks like a deliberate installer
  choice rather than just "fit as many as possible" (e.g. leaving a gap
  for a future skylight, avoiding a weak section of roof, keeping symmetry
  across a gable even though it costs a panel or two)

## What I'll actually do with these

When you point me at this folder (or a specific example in it), I will:
1. Look at the real layout -- setback from edges, row spacing/alignment,
   how it handles obstructions, hips, valleys, and orientation
   trade-offs.
2. If the roof is one of the pilot buildings, run it through the current
   pipeline and compare directly.
3. Where the algorithm's choice looks wrong against the real example,
   trace it to a specific rule/constant and propose a change -- not a
   vague "make it more realistic," an actual diff.

No fixed cadence -- just say "check the new examples in
reference_examples/" when you've added some.
