"""
Per-roof-facet generation-potential heatmap across the whole pilot area,
before any panel layout is applied -- a north-facing roof plane red/orange,
south-facing blue, per the same diverging scale used everywhere else.

Two-tier confidence, both rendered:
1. High confidence: rasterizes the *actual* RANSAC facet segmentation
   (the same one panel_fitting works from) -- uniform colour within a
   facet, a hard cut exactly at its boundary (a real angle change).
2. Fallback (lower alpha, visibly less solid): for roof pixels that
   never resolved into any facet -- genuinely common on complex roofs
   (curved/vaulted sections, ridges finer than the 1m DSM can resolve;
   one real building in the pilot only resolved 31% of its footprint
   into facets) -- a heavily Gaussian-smoothed per-pixel DSM gradient
   fills the gap instead of leaving it blank. This alone was tried as
   the *primary* method earlier and rejected (it blurs straight across
   real ridge lines), which is exactly why it's demoted to "better than
   nothing" fallback rather than the main signal.

Usage: python src/build_heatmap_raster.py
"""

import json
import sys
import time
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from matplotlib.colors import LinearSegmentedColormap, Normalize
from PIL import Image
from rasterio.features import rasterize
from rasterio.warp import transform as warp_transform
from scipy.ndimage import gaussian_filter

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.roof_segmentation import segment_building
from src.solar_model import SolarModel

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
VMIN, VMAX = 700, 1650  # kWh/m2/yr -- same fixed scale as preview.html's legend and demo_figure.py

# Same 6-stop palette as the buildings-fill choropleth in preview.html (and
# its matching .legend-bar/.legend-facet-bar CSS gradients -- all four must
# stay in sync if this changes) -- was matplotlib's stock RdYlBu_r here,
# a *diverging* colormap (implies a meaningful zero/center point) applied
# to what's actually sequential low-to-high data, with a washed-out pale
# midpoint. This is a custom sequential ramp instead, reusing colours
# already chosen (and already fixed once for a real collision with the
# obstruction-purple swatch) rather than inventing a second, inconsistent
# "heat" scale for the same app.
HEAT_COLORS = ["#1565c0", "#3aa8c1", "#6fd07c", "#f4d35e", "#f2994a", "#e63946"]
HEAT_CMAP = LinearSegmentedColormap.from_list("solar_heat", HEAT_COLORS, N=256)
FALLBACK_ALPHA = 140  # out of 255 -- visibly less solid than the confident 255, not a hatch/texture but reads clearly at a glance
SLOPE_BIN_DEG, ASPECT_BIN_DEG, MAX_SLOPE_DEG = 5, 10, 45


def build_fallback_poa(dsm_ds, model, building_mask):
    """Same method as the very first version of this file: Gaussian-smooth
    the DSM (sigma=3.5px -- enough to suppress 1m-grid quantization noise,
    per the same finding as before), compute per-pixel slope/aspect, look
    up POA. Kept only as the low-confidence fill for facet gaps now."""
    dsm = dsm_ds.read(1)
    nodata = dsm_ds.nodata
    valid = dsm != nodata

    lookup = np.full((MAX_SLOPE_DEG // SLOPE_BIN_DEG + 1, 360 // ASPECT_BIN_DEG), np.nan)
    for (slope_bin, aspect_bin), poa in model.lookup.items():
        lookup[slope_bin // SLOPE_BIN_DEG, aspect_bin // ASPECT_BIN_DEG] = poa

    fill_value = np.where(valid, dsm, np.nanmean(dsm[valid]))
    smoothed = gaussian_filter(fill_value, sigma=3.5)
    gy, gx = np.gradient(np.where(valid, smoothed, np.nan), dsm_ds.res[0])
    slope_deg = np.degrees(np.arctan(np.hypot(gx, gy)))
    aspect_deg = np.degrees(np.arctan2(-gx, gy)) % 360

    slope_bin_idx = np.clip(np.round(slope_deg / SLOPE_BIN_DEG).astype(int), 0, MAX_SLOPE_DEG // SLOPE_BIN_DEG)
    aspect_bin_idx = np.round(aspect_deg / ASPECT_BIN_DEG).astype(int) % (360 // ASPECT_BIN_DEG)
    poa = lookup[slope_bin_idx, aspect_bin_idx]
    poa[~(valid & building_mask)] = np.nan
    return poa


def main():
    with rasterio.open(DATA_DIR / "dsm_mosaic.tif") as dsm_ds:
        transform = dsm_ds.transform
        crs = dsm_ds.crs
        shape = dsm_ds.shape

        print("Building solar yield lookup table (pvlib + NASA POWER)...")
        model = SolarModel()

        gdf = gpd.read_file(DATA_DIR / "building_outlines.geojson")
        print(f"Segmenting {len(gdf)} buildings into facets...")
        shapes = []
        t0 = time.time()
        for i, row in enumerate(gdf.itertuples()):
            facets = segment_building(dsm_ds, row.geometry, row.building_id)
            for f in facets:
                poa = model.annual_poa_kwh_per_m2(f["slope_deg"], f["aspect_deg"])
                shapes.append((f["geometry"], poa))
            if i % 300 == 0:
                print(f"  {i}/{len(gdf)} elapsed={time.time() - t0:.0f}s")

        print("Rasterizing facets (high confidence)...")
        facet_poa = rasterize(shapes, out_shape=shape, transform=transform, fill=np.nan, dtype=np.float32)

        print("Building smoothed fallback for unresolved gaps...")
        building_mask = rasterize(
            [(geom, 1) for geom in gdf.geometry], out_shape=shape, transform=transform, fill=0, dtype=np.uint8
        ).astype(bool)
        fallback_poa = build_fallback_poa(dsm_ds, model, building_mask)

    has_facet = ~np.isnan(facet_poa)
    has_fallback = ~np.isnan(fallback_poa) & ~has_facet
    print(f"{has_facet.sum()} pixels from resolved facets, {has_fallback.sum()} filled from the fallback "
          f"({has_facet.sum() / (has_facet.sum() + has_fallback.sum()) * 100:.0f}% high-confidence)")

    combined_poa = np.where(has_facet, facet_poa, fallback_poa)

    norm = Normalize(vmin=VMIN, vmax=VMAX)
    rgba = (HEAT_CMAP(norm(np.nan_to_num(combined_poa, nan=VMIN))) * 255).astype(np.uint8)
    rgba[..., 3] = 0
    rgba[has_facet, 3] = 255
    rgba[has_fallback, 3] = FALLBACK_ALPHA

    out_png = DATA_DIR / "heatmap_raster.png"
    Image.fromarray(rgba, mode="RGBA").save(out_png)

    left, bottom, right, top = transform.c, transform.f + shape[0] * transform.e, transform.c + shape[1] * transform.a, transform.f
    xs, ys = [left, right, right, left], [top, top, bottom, bottom]
    lons, lats = warp_transform(crs, "EPSG:4326", xs, ys)
    coordinates = list(zip(lons, lats))

    meta_path = DATA_DIR / "heatmap_raster.json"
    meta_path.write_text(json.dumps({"coordinates": coordinates}))

    print(f"\nSaved {out_png} ({out_png.stat().st_size / 1e6:.1f}MB) and {meta_path}")


if __name__ == "__main__":
    main()
