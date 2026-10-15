"""Training tiles for finding buildings in a photo: LINZ outlines as the answers.

    python tools/footprint_dataset.py [--regions a b] [--out data/footprints] [--per-region 600]

WHY. A house built after LINZ traced its outlines has no outline, so the map
cannot model it at all, LiDAR or photo (bug form, 8 Sep 2026: a resident's
whole street in Arrowtown missing). The 2026 map photo shows those houses;
a model that finds buildings in a photo can supply the missing outlines
(tools/detect_new_buildings.py).

THE PAIRS MUST AGREE IN TIME. Queenstown's outlines were traced from the
2021 photo ("capture_source_name: Queenstown 0.1m Urban Aerial Photos
(2021)"), and every Queenstown region keeps that photo as
reference_imagery_mosaic.tif. Training on the 2026 photo instead would teach
the model that every new house is ground.

ONE TILE: TILE_PX x TILE_PX at RES_M (64 m square), RGB uint8 and a building
mask rasterised from every outline in the region (raw, not deduplicated:
a neighbour's building in the tile is still a building). Tiles are sampled on
a grid, keeping all that hold some building and a share of empty ones.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

REGIONS_DIR = ROOT / "data" / "regions"
TILE_PX = 256
RES_M = 0.25
EMPTY_SHARE = 0.15


def regions_with_reference():
    return [r for r in sorted(config.REGIONS)
            if (REGIONS_DIR / r / "reference_imagery_mosaic.tif").exists()
            and (REGIONS_DIR / r / "building_outlines.geojson").exists()]


def export_region(region, out_dir, per_region, rng):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    from rasterio.windows import from_bounds as wfb
    from shapely.geometry import box, shape
    from shapely.strtree import STRtree
    d = REGIONS_DIR / region
    geoms = [shape(f["geometry"]) for f in json.loads((d / "building_outlines.geojson").read_text())["features"]]
    geoms = [g if g.is_valid else g.buffer(0) for g in geoms if g is not None]
    tree = STRtree(geoms)
    side = TILE_PX * RES_M
    photos, masks = [], []
    with rasterio.open(d / "reference_imagery_mosaic.tif") as src:
        b = src.bounds
        xs = np.arange(b.left + side, b.right - side, side)
        ys = np.arange(b.bottom + side, b.top - side, side)
        cells = [(x, y) for x in xs for y in ys]
        rng.shuffle(cells)
        n_empty = 0
        for x, y in cells:
            if len(photos) >= per_region:
                break
            t = box(x, y, x + side, y + side)
            hits = [geoms[i] for i in tree.query(t)]
            if not hits:
                if n_empty >= EMPTY_SHARE * per_region:
                    continue
                n_empty += 1
            img = src.read([1, 2, 3], window=wfb(x, y, x + side, y + side, src.transform),
                           out_shape=(3, TILE_PX, TILE_PX), resampling=Resampling.average, boundless=True)
            if img.dtype == np.uint16:
                img = img // 257
            if (img.sum(axis=0) == 0).mean() > 0.02:
                continue
            m = rasterize([(g, 1) for g in hits], out_shape=(TILE_PX, TILE_PX),
                          transform=from_bounds(x, y, x + side, y + side, TILE_PX, TILE_PX),
                          fill=0, dtype=np.uint8) if hits else np.zeros((TILE_PX, TILE_PX), np.uint8)
            photos.append(np.clip(img, 0, 255).astype(np.uint8))
            masks.append(m)
    if photos:
        np.savez_compressed(out_dir / f"{region}.npz", photo=np.stack(photos), mask=np.stack(masks),
                            region=np.array([region] * len(photos)))
    return len(photos)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", nargs="*")
    ap.add_argument("--out", default=str(ROOT / "data" / "footprints"))
    ap.add_argument("--per-region", type=int, default=600)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    regions = a.regions or regions_with_reference()
    print(f"{len(regions)} regions with a survey-year photo: {' '.join(regions)}", flush=True)
    for r in regions:
        print(f"  {r}: {export_region(r, out, a.per_region, rng)} tiles", flush=True)


if __name__ == "__main__":
    main()
