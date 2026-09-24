# Research and one-off scripts

Moved here on 24 Sep 2026 from `tools/`. Each was written to answer one
question, a measurement, an A/B comparison or a one-off rebuild check, and
nothing in the build, the deploy, the docs or the other tools refers to it any
more. They're kept because several record how a number in `src/` was
measured, and they still run. They aren't maintained: a script here can fall
behind the code it measures.

`tools/` holds what the build, the deploy and the review workflow use.

| Script | What it answered |
|---|---|
| `ab_skeleton.py` | straight-skeleton roofs against the partition, A/B |
| `analyse_missed_obstructions.py`, `analyse_oversegmentation.py` | where obstruction detection and segmentation go wrong on the marked roofs |
| `check_facet_plane_quality.py`, `check_label_registration.py` | plane-fit quality per facet; how well the drawn markup sits on the LiDAR |
| `export_rid_regions.py`, `export_face_training.py`, `train_face_model.py` | training data and a face model that the selected-faces chain replaced |
| `faces_preview.py`, `lines_preview.py`, `render_cases.py`, `render_disagreement.py` | image sheets for reviewing face and line readings |
| `repair_facet_area.py` | one-off repair of facet_area_m2 on patched buildings (fixed at source) |
| `test_placement_stability.py`, `measure_roof_extent_gain.py` | does placement move when the roof outline grows (the eave finding) |
| `verify_rebuild.py`, `verify_imagery_rebuild.sh`, `finish_imagery_rebuild.sh` | the 3 Sep imagery rebuild |

Run them from the repo root (`.venv/bin/python tools/research/<name>.py`).
`tests/test_xref.py` still checks that their imports resolve.
