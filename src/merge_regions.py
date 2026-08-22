"""
Merge per-region build outputs into the site-level data files.

Because dedupe_outlines() already assigned every building to exactly one
region before the builds ran, this is a plain concatenation -- no overlap
resolution here.

Outputs:
- data/solar_potential.geojson    (merged; assumptions from the first region)
- data/panel_layouts.geojson      (merged -- NOTE: at full Queenstown scale
  this lands in the hundreds of MB, fine as pipeline output on disk but NOT
  servable to browsers as one fetch; the deploy path needs the planned
  PMTiles conversion before this goes live)
- data/heatmaps/<region>.png + .json, plus data/heatmaps/manifest.json
  listing every region raster for the frontend to load as one source each.

Usage: python src/merge_regions.py [region ...]   (default: all regions)
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.region_build import DATA_DIR, all_areas, area_paths

HEATMAPS_DIR = DATA_DIR / "heatmaps"


def merge_geojson(regions, key, out_path):
    merged = None
    for name in regions:
        path = area_paths(name)[key]
        if not path.exists():
            print(f"  WARNING: {name} has no {path.name}, skipping")
            continue
        data = json.loads(path.read_text())
        if merged is None:
            merged = data
        else:
            merged["features"].extend(data["features"])
    if merged is None:
        raise SystemExit(f"no region produced {key}")
    out_path.write_text(json.dumps(merged))
    print(f"{out_path.name}: {len(merged['features'])} features, "
          f"{out_path.stat().st_size / 1e6:.1f}MB")


def collect_heatmaps(regions):
    HEATMAPS_DIR.mkdir(exist_ok=True)
    manifest = []
    for name in regions:
        paths = area_paths(name)
        if not paths["heatmap_png"].exists():
            print(f"  WARNING: {name} has no heatmap raster, skipping")
            continue
        shutil.copy2(paths["heatmap_png"], HEATMAPS_DIR / f"{name}.png")
        meta = json.loads(paths["heatmap_json"].read_text())
        manifest.append({"name": name, "png": f"data/heatmaps/{name}.png",
                          "coordinates": meta["coordinates"]})
    (HEATMAPS_DIR / "manifest.json").write_text(json.dumps(manifest))
    print(f"heatmaps/manifest.json: {len(manifest)} region rasters")


def main():
    regions = sys.argv[1:] or all_areas()
    merge_geojson(regions, "solar_potential", DATA_DIR / "solar_potential.geojson")
    merge_geojson(regions, "panel_layouts", DATA_DIR / "panel_layouts.geojson")
    collect_heatmaps(regions)


if __name__ == "__main__":
    main()
