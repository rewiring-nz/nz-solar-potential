"""
Download raw LiDAR point-cloud tiles when LINZ metadata identifies both a
covering tile index and a verified public bulk store/year for the area.

The LINZ tile index maps an NZTM bbox to tile names; only a verified
survey-specific bulk store can be used to download them. laspy reads the
downloaded .laz files and the pilot's .copc.laz files identically.

Resumable by design: existing tiles are skipped, downloads go to a .part
file first and rename on completion, so a killed run never leaves a
truncated .laz behind for laspy to choke on later.

Usage: python src/fetch_pointcloud_regions.py [region ...]  (default: all)
"""

import os
import sys
from pathlib import Path

import pyproj
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.surveys import survey_for
from src.fetch_data import fetch_building_outlines

POINTCLOUD_DIR = Path(__file__).resolve().parent.parent / "data" / "pointcloud"
TO_NZTM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True)


def tilename_to_filename(tilename, year):
    if not year:
        raise ValueError("a verified survey year is required for point-cloud tile names")
    sheet, tile = tilename.split("_", 1)  # "CC11_1000_0712" -> ("CC11", "1000_0712")
    return f"CL2_{sheet}_{year}_{tile}.laz"


def area_bbox_wgs84(name):
    # Through region_build, which derives a bbox from the region's own
    # outlines when config does not list it -- a region that has data is
    # buildable, full stop.
    from src.region_build import area_bbox_wgs84 as _bbox
    return _bbox(name)


def area_footprints_wgs84(name):
    """Disjoint buffered building footprints for a selection, else its bbox."""
    import json
    from src.region_build import area_paths
    bbox = area_bbox_wgs84(name)
    locality = area_paths(name)["dir"] / "locality.json"
    try:
        footprints = json.loads(locality.read_text()).get("acquisition_footprints")
    except (OSError, ValueError, AttributeError):
        footprints = None
    return footprints or [bbox]


def tiles_for_bbox_wgs84(bbox, api_key, layer_id):
    minx, miny = TO_NZTM.transform(bbox[0], bbox[1])
    maxx, maxy = TO_NZTM.transform(bbox[2], bbox[3])
    data = fetch_building_outlines(
        [minx, miny, maxx, maxy], api_key,
        layer_id=layer_id)
    return sorted({f["properties"]["tilename"] for f in data["features"]})


def download_tile(filename, store=None, retries=4):
    dest = POINTCLOUD_DIR / filename
    copc_variant = POINTCLOUD_DIR / filename.replace(".laz", ".copc.laz")
    # A tile restored from a region pack (src/pack_region.py) carries the full
    # tile's name but only the points near THAT region's buildings. Treating
    # it as present would build a neighbouring region from a subset, silently.
    # So a packed tile is re-fetched whole, which is a superset of the pack
    # and builds the packed region identically too.
    from src.pack_region import packed_tiles, forget_packed
    if filename in packed_tiles():
        dest.unlink(missing_ok=True)
        copc_variant.unlink(missing_ok=True)
        forget_packed(filename)
    if dest.exists() or copc_variant.exists():
        return "exists"
    part = dest.with_suffix(".part")
    for attempt in range(retries):
        try:
            from src.dataset_timing import transfer
            with transfer():
                if not store:
                    raise ValueError("a verified point-cloud bulk store is required")
                resp = requests.get(f"{store}/{filename}", stream=True, timeout=120)
                if resp.status_code == 404:
                    return "missing-upstream"
                resp.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                        f.write(chunk)
            part.rename(dest)
            return f"{dest.stat().st_size / 1e6:.0f}MB"
        except (requests.exceptions.RequestException, OSError) as e:
            # transient S3 timeouts/read errors are routine over a multi-GB run;
            # back off and retry rather than killing the whole batch
            if attempt == retries - 1:
                raise
            import time
            print(f"    retry {attempt + 1} after {type(e).__name__}")
            time.sleep(10 * (attempt + 1))


def main(region_names=None):
    """region_names lets fetch_regions call this directly, so setting up a new
    region pulls its point cloud automatically instead of relying on someone
    remembering a second script."""
    load_dotenv()
    from src.project_secrets import linz_api_key
    api_key = linz_api_key()
    if not api_key:
        raise SystemExit("LINZ_API_KEY not set and not readable from Secret Manager")
    POINTCLOUD_DIR.mkdir(parents=True, exist_ok=True)
    # "pilot" is a first-class area with its own bbox -- its exclusive CBD
    # tiles were silently never fetched by the regions-only default, which
    # left holes over the town centre (Turner St, 23 Aug).
    region_names = region_names or sys.argv[1:] or (["pilot"] + list(config.REGIONS))

    # filename -> (region, verified bulk store). The store and year are
    # properties of the selected LINZ survey, not project-wide defaults.
    all_tiles = {}
    for name in region_names:
        bbox = area_bbox_wgs84(name)
        sv = survey_for(bbox, name)
        store = sv.get("pointcloud_bulk_url")
        if not store:
            print(f"{name}: survey {sv.get('name')} publishes no point cloud "
                  f"-- this region will build from its 1 m DSM")
            from src.region_build import area_paths
            area_paths(name)["dir"].joinpath("pointcloud_tiles.txt").unlink(missing_ok=True)
            continue
        year = sv.get("pointcloud_tile_year")
        layer_id = sv.get("lidar_tile_index_layer")
        if not year or not layer_id:
            raise RuntimeError(
                f"{name}: LINZ metadata lacks a verified point-cloud year or tile index")
        tiles = sorted({tile for footprint in area_footprints_wgs84(name)
                        for tile in tiles_for_bbox_wgs84(footprint, api_key, layer_id)})
        print(f"{name}: {len(tiles)} tiles from {store.rsplit('/', 1)[-1]} ({year})")
        names = [tilename_to_filename(t, year) for t in tiles]
        for fn in names:
            all_tiles.setdefault(fn, (name, store))
        # RECORD WHICH TILES THIS REGION USES. Tiles are shared at region
        # borders, and publish_region deletes a tile only when no other region
        # still on the disk lists it -- which it can only know from this file.
        try:
            from src.region_build import area_paths
            d = area_paths(name)["dir"]
            d.mkdir(parents=True, exist_ok=True)
            (d / "pointcloud_tiles.txt").write_text("\n".join(sorted(names)) + "\n")
        except Exception as exc:
            print(f"  (could not record {name}'s tile list: {exc})")

    print(f"\n{len(all_tiles)} unique tiles across {len(region_names)} regions")
    missing_upstream = []
    for i, filename in enumerate(sorted(all_tiles)):
        _region, store = all_tiles[filename]
        result = download_tile(filename, store)
        if result == "missing-upstream":
            missing_upstream.append(filename)
        print(f"  [{i + 1}/{len(all_tiles)}] {filename}: {result}")

    if missing_upstream:
        print(f"\nWARNING: {len(missing_upstream)} tiles not in the bulk store "
              f"(coverage gap to investigate): {missing_upstream[:10]}")
    return missing_upstream


if __name__ == "__main__":
    main()
