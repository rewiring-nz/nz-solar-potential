"""
Where roof-line candidates come from -- imagery today, a vision model later.

Track D of the vision pathway: the seam a trained model plugs into, built and
testable BEFORE the model exists.

The partition does not need to know who proposed a line. It needs (angle,
offset) in the footprint's own frame, and it then decides for itself whether the
LiDAR agrees -- `_line_is_real` in roof_partition only cuts where the surface
actually turns or the two sides sit at different heights. That gate is the whole
fusion story and it stays exactly where it is:

    THE MODEL PROPOSES, THE LIDAR DISPOSES.

which is also why swapping the proposer is safe. A model that hallucinates a
ridge produces a candidate that fails the height test and is discarded, exactly
as a stain or a tonal band in the imagery is discarded today.

Two kinds of candidate, and the distinction matters:

  ORDINARY   offered to the partition and kept only if cutting improves the fit.
             The right test when both sensors can see the feature.
  STRONG     acted on WITHOUT the LiDAR agreeing, because some roofs are
             invisible to it -- 7 Anderson Heights has two hip creases that are
             unmistakable in imagery and almost absent from a near-flat point
             cloud, so every LiDAR-scored candidate there is rejected and the
             faces run straight over both hips.

Model lines currently enter through the SAME gates as imagery lines, and are NOT
automatically promoted to strong. That is deliberate: whether a model deserves
more trust than a Hough fragment is a question for the evaluation harness
(tools/score_geometry.py), not an assumption to bake in before any model has
been measured.

WITH NO MODEL FILE PRESENT THIS CHANGES NOTHING. It delegates straight to
roof_lines, so the current behaviour is bit-for-bit what it was.

Model predictions are read from:
    data/vision_lines/<building_id>.json
      {"lines": [[x1,y1,x2,y2], ...],      # NZTM metres
       "scores": [0.91, ...],              # optional, 0-1
       "model": "wireframe-v1"}            # optional, for provenance
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
VISION_DIR = DATA_DIR / "vision_lines"

# A predicted line below this confidence is not offered at all. Deliberately
# permissive: the LiDAR gate downstream is the real filter, and throwing away
# candidates here would hide model recall problems the scorer needs to see.
MIN_SCORE = 0.25


def _to_angle_offset(x1, y1, x2, y2, footprint):
    """Segment endpoints -> the (angle_deg, offset) form roof_partition._cut takes.

    DELEGATES to roof_lines._angle_offset rather than reimplementing it. The
    first version of this function did reimplement it and got two things wrong,
    and because nothing exercised the path with a real model on disk, both sat
    undetected from August until 2 September:

      * it returned RADIANS. _cut takes degrees and calls np.radians on them, so
        a line at 45 degrees was cut at 0.785 degrees.
      * it measured offset from the ORIGIN. _cut measures from the polygon
        centroid, and in NZTM the origin is about 1.2 million metres away.

    Every proposed line was therefore tested somewhere meaningless, and the
    LiDAR gate rejected 100% of them -- 68 of 68 on one roof -- which read
    exactly like a model with nothing to say.

    One implementation, in the module that owns the convention."""
    from shapely.geometry import LineString
    from src.roof_lines import _angle_offset
    c = footprint.centroid
    ang, off = _angle_offset(LineString([(x1, y1), (x2, y2)]), c.x, c.y)
    return ang, off, math.hypot(x2 - x1, y2 - y1)


def model_lines(building_id, footprint=None):
    """Predicted lines for one building, or None if the model has not run.

    Returns a list of (angle_deg, offset, length, score). The offset is relative
    to `footprint`'s centroid, so the footprint is required -- passing None
    yields the raw segments instead, for callers that want geometry."""
    if building_id is None:
        return None
    p = VISION_DIR / f"{building_id}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except Exception:
        return None
    segs = d.get("lines") or []
    scores = d.get("scores") or [1.0] * len(segs)
    out = []
    for s, sc in zip(segs, scores):
        if len(s) != 4 or sc < MIN_SCORE:
            continue
        if footprint is None:
            out.append((None, None, math.hypot(s[2] - s[0], s[3] - s[1]),
                        float(sc), list(s)))
            continue
        ang, off, ln = _to_angle_offset(*s, footprint)
        out.append((ang, off, ln, float(sc)))
    return out


def has_model(building_id):
    return building_id is not None and (VISION_DIR / f"{building_id}.json").exists()


def strong_lines(imagery_ds, footprint, building_id=None):
    """Lines to act on even where the LiDAR cannot confirm them.

    Falls straight through to roof_lines.strong_roof_lines when no model
    prediction exists, so today's behaviour is unchanged."""
    from src.roof_lines import strong_roof_lines
    lines = model_lines(building_id, footprint)
    if lines is None:
        return strong_roof_lines(imagery_ds, footprint)
    # A model line is only promoted to "strong" on the same evidence an imagery
    # line needs -- length relative to the building. Confidence alone is not
    # enough: a model can be confident and wrong, and the whole point of the
    # strong path is that nothing downstream will check it.
    from src.roof_lines import STRONG_LINE_MIN_M, STRONG_LINE_AREA_COEF, MAX_STRONG_LINES
    bar = max(STRONG_LINE_MIN_M,
              STRONG_LINE_AREA_COEF * math.sqrt(max(footprint.area, 1.0)))
    strong = [(a, o) for a, o, ln, sc in sorted(lines, key=lambda t: -t[2])
              if ln >= bar]
    if not strong:
        # A model that proposes nothing long enough must not DELETE the imagery
        # strong lines. Returning [] here would have silently removed them on
        # every building carrying a prediction file.
        return strong_roof_lines(imagery_ds, footprint)
    return strong[:MAX_STRONG_LINES]


def candidate_lines(imagery_ds, footprint, building_id=None):
    """Every line worth OFFERING to the partition, which keeps only the ones
    that improve the fit. Model lines are merged with imagery lines rather than
    replacing them -- until the scorer says otherwise, more candidates offered
    to a gate that rejects bad ones is strictly better than fewer."""
    from src.roof_lines import roof_line_candidates
    base = list(roof_line_candidates(imagery_ds, footprint))
    lines = model_lines(building_id, footprint)
    if lines is None:
        return base
    return base + [(a, o) for a, o, ln, sc in lines]


def provenance(building_id):
    """Which proposer was used, for the scorecard and the build log."""
    if not has_model(building_id):
        return "imagery"
    try:
        d = json.loads((VISION_DIR / f"{building_id}.json").read_text())
        return f"model:{d.get('model', 'unknown')}"
    except Exception:
        return "model:unreadable"
