"""Fetch the wide, 8m DEM used for distant terrain horizons.

The extent covers the pilot and all configured regional DSM areas, plus a
metric buffer. The output is the root-level asset consumed by the horizon
stages: data/dem_wide_mosaic.tif.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.fetch_data import fetch_raster

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEM_WIDE_LAYER = config.LINZ_WIDE_DEM_LAYER
DEM_WIDE_BBOX = config.DEM_WIDE_BBOX


def wide_dem_bbox_wgs84():
    """Return the configured WGS84 extent for the wide DEM export."""
    return list(DEM_WIDE_BBOX)


def ensure_dem_wide(api_key, out_dir=DATA_DIR):
    """Fetch the wide DEM once, returning the existing or new mosaic path."""
    out_dir = Path(out_dir)
    mosaic_path = out_dir / "dem_wide_mosaic.tif"
    if mosaic_path.exists() and mosaic_path.stat().st_size > 0:
        print(f"  {mosaic_path} exists, skipping")
        return mosaic_path

    bbox = wide_dem_bbox_wgs84()
    print(f"Fetching 8m DEM layer {DEM_WIDE_LAYER} for bbox {bbox}...")
    return fetch_raster(bbox, api_key, DEM_WIDE_LAYER, "dem_wide", out_dir=out_dir)


def main():
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    api_key = os.environ.get("LINZ_API_KEY")
    if not api_key:
        raise SystemExit("LINZ_API_KEY not set")
    DATA_DIR.mkdir(exist_ok=True)
    ensure_dem_wide(api_key)


if __name__ == "__main__":
    main()