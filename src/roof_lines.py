"""
Candidate roof cut-lines detected in the 0.1 m imagery.

The partition places its cuts by sweeping offsets at 25 cm and keeping whichever
fits the LiDAR best. That is the wrong sensor for the job. The Queenstown point
cloud is 2021 at about 7.8 pts/m2 -- roughly 0.42 m between points, and fewer
returns on a roof -- so it cannot localise a ridge better than its own spacing.
The imagery is 0.1 m captured February-March 2026: a ridge is a sharp intensity
edge running the length of a roof, four times finer than the LiDAR spacing and
five years newer.

So let the imagery PROPOSE lines and the LiDAR DISPOSE of them. Every line found
here is only a candidate; the partition scores it exactly like a swept cut and
keeps it only if it explains the points better. A spurious line -- a gutter, a
shadow, a parked car, a path -- simply loses on score and costs nothing. That
asymmetry is what makes it safe to be generous here: the cost of a false line is
one scoring pass, and the cost of a missed line is a cut placed by guesswork.

Lines are returned as (angle_deg, offset) in the convention roof_partition._cut
already uses -- angle of the line's direction, and signed perpendicular distance
from the polygon's centroid -- so a detected ridge and a swept candidate are
interchangeable to the caller.
"""

import sys
import warnings
from pathlib import Path

import cv2
import numpy as np
import rasterio
from rasterio.features import rasterize
from shapely.geometry import LineString, Point

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CANNY_LOW = 40
CANNY_HIGH = 120
HOUGH_THRESHOLD = 25
HOUGH_MIN_LINE_PX = 15        # 1.5 m at 0.1 m/px -- shorter than this is texture
HOUGH_MAX_GAP_PX = 8
PAD_M = 2.0

MIN_LENGTH_M = 2.0            # a real ridge or hip runs at least this far
BOUNDARY_EXCLUSION_M = 0.8    # a segment hugging the footprint edge is the eave or a
# gutter, and the footprint already provides that edge exactly -- re-cutting on a
# blurry copy of it can only be worse than the surveyed line

# Two candidates this close in angle AND in perpendicular offset are the same
# physical line seen twice (Hough returns fragments), not two roof features.
CLUSTER_ANGLE_DEG = 6.0
CLUSTER_OFFSET_M = 0.7

MAX_CANDIDATES = 24           # scoring is O(candidates); a roof with more real lines
# than this is past what the partition's face budget would use anyway


def _angle_offset(seg, cx, cy):
    """LineString -> (angle_deg in [0,180), signed perpendicular offset from
    (cx, cy)), matching roof_partition._cut's convention."""
    (x1, y1), (x2, y2) = seg.coords[0], seg.coords[-1]
    ang = np.degrees(np.arctan2(y2 - y1, x2 - x1)) % 180.0
    theta = np.radians(ang)
    n = np.array([-np.sin(theta), np.cos(theta)])
    off = float((np.array([x1, y1]) - np.array([cx, cy])) @ n)
    return float(ang), off


def _dedupe(cands):
    """Collapse Hough fragments of one physical line, longest first."""
    kept = []
    for ang, off, length in sorted(cands, key=lambda c: -c[2]):
        dup = False
        for kang, koff, _ in kept:
            da = min(abs(ang - kang), 180.0 - abs(ang - kang))
            if da < CLUSTER_ANGLE_DEG and abs(off - koff) < CLUSTER_OFFSET_M:
                dup = True
                break
        if not dup:
            kept.append((ang, off, length))
        if len(kept) >= MAX_CANDIDATES:
            break
    return [(a, o) for a, o, _ in kept]


def roof_line_candidates(imagery_ds, footprint):
    """(angle_deg, offset) candidates for cutting this footprint. [] if the
    imagery is missing or nothing survives -- the caller falls back to sweeping."""
    if imagery_ds is None or footprint.is_empty:
        return []
    minx, miny, maxx, maxy = footprint.bounds
    try:
        window = rasterio.windows.from_bounds(minx - PAD_M, miny - PAD_M,
                                              maxx + PAD_M, maxy + PAD_M,
                                              imagery_ds.transform)
        arr = imagery_ds.read([1, 2, 3], window=window)
    except Exception:
        return []
    if arr.size == 0 or arr.shape[1] < 8 or arr.shape[2] < 8:
        return []

    rgb = np.moveaxis(arr, 0, -1).astype(np.uint8)
    wt = imagery_ds.window_transform(window)

    # Confine edges to the roof itself. Without this the strongest lines on the
    # page are kerbs, paths and neighbouring rooflines.
    mask = rasterize([(footprint, 1)], out_shape=rgb.shape[:2], transform=wt).astype(np.uint8)
    mask = cv2.erode(mask, np.ones((3, 3), np.uint8))

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, CANNY_LOW, CANNY_HIGH)
    edges[mask == 0] = 0

    lines = cv2.HoughLinesP(edges, rho=1, theta=np.pi / 180,
                            threshold=HOUGH_THRESHOLD,
                            minLineLength=HOUGH_MIN_LINE_PX,
                            maxLineGap=HOUGH_MAX_GAP_PX)
    if lines is None:
        return []

    cx, cy = footprint.centroid.x, footprint.centroid.y
    boundary = footprint.exterior
    out = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        wx1, wy1 = wt * (float(x1), float(y1))
        wx2, wy2 = wt * (float(x2), float(y2))
        seg = LineString([(wx1, wy1), (wx2, wy2)])
        if seg.length < MIN_LENGTH_M:
            continue
        if (boundary.distance(Point(wx1, wy1)) < BOUNDARY_EXCLUSION_M
                and boundary.distance(Point(wx2, wy2)) < BOUNDARY_EXCLUSION_M):
            continue          # tracing the eave; the surveyed outline is better
        ang, off = _angle_offset(seg, cx, cy)
        out.append((ang, off, seg.length))
    return _dedupe(out)
