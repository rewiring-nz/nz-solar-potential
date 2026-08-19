"""
Flag roof-surface anomalies (skylights, existing solar panels, vents,
AC units, moss/staining patches) from the 0.1m aerial imagery, so
panel_fitting can avoid placing panels on top of them.

This exists because LiDAR height data has a real blind spot: anything
close to flush with the roof plane (skylights, existing panels) produces
no elevation signal for RANSAC to catch as an outlier. Colour is often
the *only* signal available for those cases -- a skylight or an existing
panel array looks nothing like surrounding roofing material even when
its height is identical.

Method is a heuristic, not a trained classifier: flag pixels within a
facet whose colour deviates more than a few standard deviations from
that facet's own median colour, morphologically clean up single-pixel
noise, then keep only blobs in a plausible object-size range. This will
mis-flag some things (a hard shadow edge, a patch of moss, ridge
flashing) and miss others (a skylight that happens to closely match the
surrounding roofing colour) -- documented limitation, not hidden. The
pipeline treats a flag as "exclude from panel placement," which is the
conservative direction to be wrong in: worst case it costs a bit of
otherwise-usable roof area, not a panel mounted over a skylight.
"""

import sys
from pathlib import Path

import numpy as np
from rasterio.features import shapes as rasterio_shapes
from rasterio.mask import mask as rasterio_mask
from scipy import ndimage
from shapely.geometry import shape as shapely_shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

MIN_BLOB_AREA_M2 = 0.09  # ~30x30cm -- smaller than this is more likely JPEG/compression noise
MAX_BLOB_AREA_FRACTION = 0.4  # beyond this it's more likely a lighting gradient than a discrete object
Z_THRESHOLD = 2.75  # colour-distance-from-median std devs to count as anomalous
MIN_VALID_PIXELS = 20  # facets smaller than this in the imagery can't be usefully analysed
BOUNDARY_ERODE_M = config.PANEL_EDGE_SETBACK_M  # keep the analysed region inside where panels could go anyway;
# also keeps mixed/blended pixels right on the facet's own edge (real ridge/valley flashing, a sliver of a
# neighbouring facet's different-coloured roofing) from reading as a false anomaly right along that boundary


def _vectorize_blobs(mask_2d, transform):
    return [
        shapely_shape(geom)
        for geom, val in rasterio_shapes(mask_2d.astype(np.uint8), mask=mask_2d, transform=transform)
        if val == 1
    ]


def detect_obstructions(imagery_ds, facet_geom, z_threshold=None, boundary_erode_m=None):
    """Returns a list of shapely Polygons (in imagery_ds's CRS -- same as
    facet_geom) flagged as likely roof obstructions/anomalies within this
    facet. Empty list if nothing flagged or the facet is too small/lacks
    imagery coverage to analyse. z_threshold/boundary_erode_m override the
    module defaults -- exposed for the live parameter-tuning server."""
    z_threshold = Z_THRESHOLD if z_threshold is None else z_threshold
    boundary_erode_m = BOUNDARY_ERODE_M if boundary_erode_m is None else boundary_erode_m
    sample_geom = facet_geom.buffer(-boundary_erode_m)
    if sample_geom.is_empty:
        return []
    if sample_geom.geom_type == "MultiPolygon":
        sample_geom = max(sample_geom.geoms, key=lambda p: p.area)
    elif sample_geom.geom_type != "Polygon":
        return []

    try:
        arr, transform = rasterio_mask(imagery_ds, [sample_geom], crop=True, filled=True, nodata=0)
    except ValueError:
        return []

    if arr.shape[0] < 3:
        return []
    rgb = arr[:3].astype(float)
    alpha = arr[3] if arr.shape[0] > 3 else None
    valid = (alpha > 0) if alpha is not None else np.ones(rgb.shape[1:], dtype=bool)
    if valid.sum() < MIN_VALID_PIXELS:
        return []

    median_colour = np.median(rgb[:, valid], axis=1)
    dist = np.sqrt(((rgb - median_colour[:, None, None]) ** 2).sum(axis=0))

    mean_dist, std_dist = dist[valid].mean(), dist[valid].std()
    if std_dist < 1e-6:  # perfectly uniform colour -- nothing to flag
        return []

    flagged = (dist > mean_dist + z_threshold * std_dist) & valid
    flagged = ndimage.binary_opening(flagged, structure=np.ones((3, 3)))

    labeled, n_blobs = ndimage.label(flagged)
    if n_blobs == 0:
        return []

    pixel_area_m2 = abs(transform.a * transform.e)
    facet_area_m2 = valid.sum() * pixel_area_m2

    obstructions = []
    for blob_id in range(1, n_blobs + 1):
        blob_mask = labeled == blob_id
        blob_area_m2 = blob_mask.sum() * pixel_area_m2
        if blob_area_m2 < MIN_BLOB_AREA_M2 or blob_area_m2 > MAX_BLOB_AREA_FRACTION * facet_area_m2:
            continue
        obstructions.extend(_vectorize_blobs(blob_mask, transform))

    return obstructions
