"""
Fetch building outlines + DSM + imagery for every config.REGIONS entry into
data/regions/<name>/, reusing fetch_data.py's LINZ WFS/Exports machinery.

Resumable by design: each output file is skipped if it already exists, so the
script can be re-run after any network failure or interrupted export and only
does the remaining work. Imagery exports are the slow part (LINZ generates
the cropped export server-side before download -- minutes per region) and by
far the largest (~0.35GB/km2 at 0.1m), so DSMs for every region are fetched
first (small, quick -- the pipeline can start on a region as soon as its
imagery lands later).

Large regions are split into <= MAX_EXPORT_KM2 chunks per export job and
mosaicked back together -- one giant imagery export both risks the Exports
API's job-size ceiling and buffers multi-GB zips in memory.

Usage: python src/fetch_regions.py [region ...]   (default: all config.REGIONS)

A region is a config.REGIONS name or a LINZ locality id
<council>/<town-city>/<suburb-locality> (find one with tools/find_area.py).
"""

import json
import os
import sys
from pathlib import Path

import pyproj
import rasterio
from dotenv import load_dotenv
from rasterio.merge import merge

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.surveys import survey_for
from src.fetch_data import fetch_building_outlines, fetch_raster
from src.fetch_dem_wide import ensure_dem_wide
from src.region_build import area_bbox_wgs84

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REGIONS_DIR = DATA_DIR / "regions"
MAX_EXPORT_KM2 = 8.0  # imagery chunk ceiling -- ~2.8GB per chunk at 0.1m, comfortably inside
# the Exports API's limits and this machine's memory for the download buffer

TO_NZTM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True)


def bbox_nztm(bbox_wgs84):
    x0, y0 = TO_NZTM.transform(bbox_wgs84[0], bbox_wgs84[1])
    x1, y1 = TO_NZTM.transform(bbox_wgs84[2], bbox_wgs84[3])
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def bbox_area_km2(bbox_wgs84):
    b = bbox_nztm(bbox_wgs84)
    return abs(b[2] - b[0]) * abs(b[3] - b[1]) / 1e6


def split_bbox(bbox_wgs84, max_km2):
    """Recursively halve along the longer axis until every part <= max_km2."""
    if bbox_area_km2(bbox_wgs84) <= max_km2:
        return [bbox_wgs84]
    min_lon, min_lat, max_lon, max_lat = bbox_wgs84
    # compare metric extents, not degree extents (degrees of longitude are shorter here)
    b = bbox_nztm(bbox_wgs84)
    if (b[2] - b[0]) >= (b[3] - b[1]):
        mid = (min_lon + max_lon) / 2
        parts = [[min_lon, min_lat, mid, max_lat], [mid, min_lat, max_lon, max_lat]]
    else:
        mid = (min_lat + max_lat) / 2
        parts = [[min_lon, min_lat, max_lon, mid], [min_lon, mid, max_lon, max_lat]]
    out = []
    for p in parts:
        out.extend(split_bbox(p, max_km2))
    return out


def fetch_raster_chunked(bbox_wgs84, api_key, layer_id, name, out_dir, format_key):
    """fetch_raster, but split into MAX_EXPORT_KM2 chunks and re-mosaicked."""
    mosaic_path = out_dir / f"{name}_mosaic.tif"
    if mosaic_path.exists():
        print(f"  {mosaic_path.name} exists, skipping")
        return mosaic_path
    parts = split_bbox(bbox_wgs84, MAX_EXPORT_KM2)
    part_paths = []
    outside = 0
    for i, part in enumerate(parts):
        part_name = name if len(parts) == 1 else f"{name}_part{i}"
        part_path = out_dir / f"{part_name}_mosaic.tif"
        if not part_path.exists():
            print(f"  exporting {part_name} ({bbox_area_km2(part):.1f} km2)...")
            try:
                fetch_raster(part, api_key, layer_id, part_name,
                             out_dir=out_dir, format_key=format_key)
            except RuntimeError as exc:
                # A CHUNK OUTSIDE THE SURVEY IS NOT A FAILURE, IT IS A FACT.
                #
                # A layer's published extent is a bounding box; the data
                # inside it is a polygon with gaps and a ragged edge. Any
                # region whose own box overhangs that edge has chunks with
                # nothing in them, and LINZ answers those with
                # 400 invalid_reasons ['outside-extent']. Albert Town hit it
                # on its third chunk and aborted the whole expansion.
                #
                # Skip the empty chunk and keep the rest: buildings with no
                # DSM under them already ship as "not estimated" with a
                # reason, which is the honest outcome. Only a region where
                # EVERY chunk is outside is a real error -- that means the
                # region is in the wrong survey, which is worth stopping for.
                if "outside-extent" not in str(exc):
                    raise
                print(f"  {part_name}: outside this survey's coverage, skipped")
                outside += 1
                continue
        part_paths.append(part_path)
    if not part_paths:
        raise RuntimeError(
            f"{name}: every one of {len(parts)} chunks is outside layer "
            f"{layer_id}'s coverage -- this region is assigned to the wrong "
            f"survey, or its bbox is in the wrong place")
    if outside:
        print(f"  {name}: {outside} of {len(parts)} chunks had no data")
    if len(part_paths) == 1:
        if part_paths[0] != mosaic_path:
            part_paths[0].rename(mosaic_path)
        return mosaic_path
    _merge_footprints(part_paths, mosaic_path)
    print(f"  merged {len(part_paths)} parts -> {mosaic_path.name}")
    return mosaic_path


def acquisition_footprints(name, bbox):
    """Multiple buffered footprints for a grid selection, else its bbox."""
    record_path = REGIONS_DIR / name / "locality.json"
    try:
        footprints = json.loads(record_path.read_text()).get("acquisition_footprints")
    except (OSError, ValueError, AttributeError):
        footprints = None
    valid = []
    for footprint in footprints or []:
        try:
            values = [float(v) for v in footprint]
        except (TypeError, ValueError):
            continue
        if (len(values) == 4 and -180 <= values[0] < values[2] <= 180
                and -90 <= values[1] < values[3] <= 90):
            valid.append(values)
    return valid or [bbox]


def raster_parts_exist(out_dir, name):
    out_dir = Path(out_dir)
    return (any((out_dir / f"{name}_footprints").glob("*_mosaic.tif"))
            or any(out_dir.glob(f"{name}_part*_mosaic.tif")))


def raster_acquisition_outcome(out_dir, name):
    if (Path(out_dir) / f"{name}_mosaic.tif").exists():
        return "reused_from_cache"
    return "partially_resumed" if raster_parts_exist(out_dir, name) else "downloaded"


def _merge_footprints(paths, destination):
    from src.dataset_timing import phase

    paths = [Path(path) for path in paths if Path(path).is_file()]
    if not paths:
        raise RuntimeError(f"no footprint rasters to combine for {destination.name}")
    with phase("local_prepare"):
        if len(paths) == 1:
            paths[0].replace(destination)
            return destination
        sources = [rasterio.open(path) for path in paths]
        temporary = destination.with_name(destination.stem + ".part.tif")
        try:
            mosaic, transform = merge(sources)
            profile = sources[0].profile
            profile.update(height=mosaic.shape[1], width=mosaic.shape[2], transform=transform,
                           BIGTIFF="IF_SAFER")
            with rasterio.open(temporary, "w", **profile) as dst:
                dst.write(mosaic)
            temporary.replace(destination)
        finally:
            for source in sources:
                source.close()
            temporary.unlink(missing_ok=True)
    return destination


def fetch_raster_footprints(footprints, api_key, layer_id, name, out_dir, format_key):
    """Fetch disjoint selection footprints, retaining resumable raster parts."""
    destination = out_dir / f"{name}_mosaic.tif"
    if destination.exists():
        print(f"  {destination.name} exists, skipping")
        return destination
    if len(footprints) == 1:
        return fetch_raster_chunked(footprints[0], api_key, layer_id, name, out_dir, format_key)
    parts_dir = out_dir / f"{name}_footprints"
    parts_dir.mkdir(parents=True, exist_ok=True)
    parts = [fetch_raster_chunked(footprint, api_key, layer_id, f"part{i:04d}",
                                  parts_dir, format_key)
             for i, footprint in enumerate(footprints)]
    return _merge_footprints(parts, destination)


def fetch_s3_footprints(footprints, source, out_path, res=None):
    """Cut several disjoint footprints from the same open LINZ raster source."""
    out_path = Path(out_path)
    if out_path.exists():
        print(f"  {out_path.name} exists, skipping")
        return out_path
    parts_dir = out_path.parent / f"{out_path.stem}_footprints"
    parts_dir.mkdir(parents=True, exist_ok=True)
    from src import s3_rasters
    parts = [s3_rasters.fetch(source, footprint, parts_dir / f"part{i:04d}.tif", res=res)
             for i, footprint in enumerate(footprints)]
    return _merge_footprints(parts, out_path)


def main():
    load_dotenv()
    from src.project_secrets import linz_api_key
    api_key = linz_api_key()
    if not api_key:
        raise SystemExit("LINZ_API_KEY not set")

    wanted = sys.argv[1:] or list(config.REGIONS)
    print("[wide terrain] ensuring 8m DEM...")
    ensure_dem_wide(api_key, areas=wanted)
    from src.dataset_timing import measure

    # A LINZ locality region is resolved and its outlines fetched by name
    # (src/localities.py), which also records the bbox the steps below use.
    from src import localities, region_ids
    for name in wanted:
        if region_ids.is_locality_id(name):
            try:
                localities.prepare(name, api_key)
            except LookupError as e:
                raise SystemExit(str(e))
    # Known = has a bbox from ANY source area_bbox_wgs84 accepts: task.json
    # (a queued national region, in no config), locality.json, config.REGIONS,
    # or outlines on disk. Checking config.REGIONS alone refused every queued
    # region, so build_region.sh could not fetch one -- the whole point of the queue.
    for name in wanted:
        try:
            area_bbox_wgs84(name)
        except KeyError:
            raise SystemExit(f"unknown region {name!r}: no task.json, no config entry, "
                             f"no outlines on disk -- known in config: {list(config.REGIONS)}")

    # Pass 1: outlines + DSM for every region (small and fast).
    for name in wanted:
        bbox = area_bbox_wgs84(name)   # task.json, config, or the outlines on disk
        footprints = acquisition_footprints(name, bbox)
        out_dir = REGIONS_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)

        outlines_path = out_dir / "building_outlines.geojson"
        with measure("building outlines", f"LINZ layer {config.LINZ_BUILDING_OUTLINES_LAYER}",
                     name, "reused_from_cache" if outlines_path.exists() else "downloaded"):
            if not outlines_path.exists():
                print(f"[{name}] fetching building outlines...")
                data = fetch_building_outlines(bbox_nztm(bbox), api_key)
                outlines_path.write_text(json.dumps(data))
                print(f"  {len(data['features'])} outlines")
            else:
                print(f"[{name}] outlines exist, skipping")

        # Which survey covers THIS region, not "the" DSM layer. See
        # src/surveys.py: a national build is a patchwork of captures and a
        # constant here is how Wellington got pointed at Otago's data.
        try:
            sv = survey_for(bbox, name)
        except LookupError as e:
            # A region whose box pokes past every survey's box (arrowtown_east)
            # must not stop the fetch for the rest; the build decides whether
            # it has what it needs.
            with measure("DSM/elevation", "LINZ survey coverage", name, "unavailable"):
                pass
            print(f"  WARNING: {e}")
            continue
        if sv.get("elevation") == "synthetic":
            # No LiDAR has been flown here: the surface is built from the
            # outlines and the wide DEM, with the roof pitch guessed
            # (src/synthesize_dsm.py). Rebuilt every fetch -- it takes
            # seconds and must follow the outlines.
            with measure("DSM/elevation", f"{sv.get('name')} survey", name, "unavailable"):
                pass
            from src.synthesize_dsm import synthesize
            print(f"[{name}] no LiDAR (survey {sv.get('name')}) -- synthetic surface")
            synthesize(name)
            continue
        print(f"[{name}] DSM (survey {sv.get('name', 'default')})...")
        dsm_path = out_dir / "dsm_mosaic.tif"
        dsm_outcome = raster_acquisition_outcome(out_dir, "dsm")
        dsm_source = ("LINZ S3 elevation collection" if sv.get("dsm_s3")
                      else f"LINZ layer {sv.get('dsm_layer')}")
        with measure("DSM/elevation", dsm_source, name, dsm_outcome):
            if sv.get("dsm_s3"):
                fetch_s3_footprints(footprints, sv["dsm_s3"], dsm_path)
            else:
                fetch_raster_footprints(footprints, api_key, sv["dsm_layer"], "dsm", out_dir, "grid")

    # Pass 2: imagery (the long pole), region by region.
    for name in wanted:
        if os.environ.get("SOLAR_INCLUDE_IMAGERY", "1") == "0":
            print(f"[{name}] aerial imagery excluded by request; skipping imagery downloads")
            continue
        bbox = area_bbox_wgs84(name)   # task.json, config, or the outlines on disk
        footprints = acquisition_footprints(name, bbox)
        out_dir = REGIONS_DIR / name
        print(f"[{name}] imagery ({bbox_area_km2(bbox):.1f} km2)...")
        imagery_path = out_dir / "imagery_mosaic.tif"
        imagery_outcome = raster_acquisition_outcome(out_dir, "imagery")
        imagery_source = "LINZ S3 imagery collection"
        with measure("survey imagery", imagery_source, name, imagery_outcome) as timing:
            try:
                sv0 = survey_for(bbox, name)
                if sv0.get("imagery_s3"):
                    # The 0.1 m image is the colour-stage input.
                    fetch_s3_footprints(footprints, sv0["imagery_s3"],
                                        imagery_path, res=0.1)
                else:
                    imagery_source = f"LINZ layer {sv0.get('imagery_layer')}"
                    timing.source = imagery_source
                    fetch_raster_footprints(footprints, api_key, sv0["imagery_layer"],
                                            "imagery", out_dir, "raster")
            except Exception as e:
                # LINZ's 0.1m aerial layer is URBAN-only: rural regions 400 here.
                # Never let that abort the run -- DSM+outlines are what the build
                # actually requires, and builds degrade gracefully without imagery.
                timing.set_outcome("unavailable" if
                                   any(t in str(e).lower() for t in
                                       ("outside-extent", "no tile", "not configured",
                                        "no survey", "no imagery")) else "failed")
                print(f"  WARNING: imagery unavailable for {name} ({type(e).__name__}) -- LiDAR-only build")
        # The photo from the LiDAR's own year, when it is a different layer:
        # register_imagery measures each building's lean against it.
        try:
            sv = survey_for(bbox, name)
        except LookupError:
            continue
        ref = sv.get("reference_imagery_layer")
        if sv.get("reference_imagery_s3") and sv["reference_imagery_s3"] != sv.get("imagery_s3"):
            ref_path = out_dir / "reference_imagery_mosaic.tif"
            with measure("reference imagery", "LINZ S3 imagery collection", name,
                         raster_acquisition_outcome(out_dir, "reference_imagery")) as ref_timing:
                try:
                    fetch_s3_footprints(footprints, sv["reference_imagery_s3"], ref_path, res=0.1)
                except Exception as e:
                    ref_timing.set_outcome("unavailable" if
                                           any(t in str(e).lower() for t in
                                               ("outside-extent", "no tile", "not configured",
                                                "no survey", "no imagery"))
                                           else "failed")
                    print(f"  WARNING: reference imagery unavailable for {name} "
                          f"({type(e).__name__}) -- drawings not shifted")
        elif sv.get("imagery_s3"):
            pass   # the photo is the LiDAR year's own: nothing to register against
        elif ref and ref != sv["imagery_layer"]:
            ref_path = out_dir / "reference_imagery_mosaic.tif"
            with measure("reference imagery", f"LINZ layer {ref}", name,
                         raster_acquisition_outcome(out_dir, "reference_imagery")) as ref_timing:
                try:
                    fetch_raster_footprints(footprints, api_key, ref,
                                            "reference_imagery", out_dir, "raster")
                except Exception as e:
                    ref_timing.set_outcome("unavailable" if
                                           any(t in str(e).lower() for t in
                                               ("outside-extent", "no tile", "not configured",
                                                "no survey", "no imagery"))
                                           else "failed")
                    print(f"  WARNING: reference imagery unavailable for {name} "
                          f"({type(e).__name__}) -- drawings not shifted")

    # Pass 3: the raw LiDAR point cloud. This is the pipeline's PRIMARY input --
    # segmentation, obstruction height evidence, panel gating and shading all
    # read it, and the Wellington survey carries 16 pts/m2 against the 1 m DSM's
    # single sample. Without it every building falls back to the DSM SILENTLY.
    # It used to be a second script you had to remember to run; it is part
    # of the automatic process for every region.
    print(f"\n[point cloud] fetching LiDAR tiles for {len(wanted)} region(s)...")
    pointcloud_dir = DATA_DIR / "pointcloud"
    expected, present = [], 0
    for name in wanted:
        listing = REGIONS_DIR / name / "pointcloud_tiles.txt"
        if listing.exists():
            tiles = [line.strip() for line in listing.read_text().splitlines() if line.strip()]
            expected.extend(tiles)
            present += sum((pointcloud_dir / tile).exists()
                           or (pointcloud_dir / tile.replace(".laz", ".copc.laz")).exists()
                           for tile in tiles)
    if expected and present == len(expected):
        lidar_outcome = "reused_from_cache"
    elif present:
        lidar_outcome = "partially_resumed"
    else:
        lidar_outcome = "downloaded"
    with measure("LiDAR point cloud", "LINZ tile index and OpenTopography bulk store",
                 ",".join(wanted), lidar_outcome) as timing:
        try:
            from src.fetch_pointcloud_regions import main as fetch_pointcloud
            missing = fetch_pointcloud(wanted)
            if missing:
                timing.set_outcome("unavailable")
                print(f"  WARNING: {len(missing)} tiles missing from the bulk store -- "
                      f"those parts of the region will build from the 1 m DSM only.")
            if not any((REGIONS_DIR / name / "pointcloud_tiles.txt").is_file()
                       and (REGIONS_DIR / name / "pointcloud_tiles.txt").read_text().strip()
                       for name in wanted):
                timing.set_outcome("unavailable")
        except Exception as e:
            timing.set_outcome("failed")
            print(f"  WARNING: point-cloud fetch FAILED ({type(e).__name__}: {e}).\n"
                  f"  These regions would build from the 1 m DSM only, which is far "
                  f"coarser. Re-run: python src/fetch_pointcloud_regions.py "
                  + " ".join(wanted))

    print("\nAll requested regions fetched.")


if __name__ == "__main__":
    main()
