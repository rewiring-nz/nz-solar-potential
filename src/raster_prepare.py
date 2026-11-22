"""Experimental regional raster preparation from leased native cache assets."""

from __future__ import annotations

import os
import json
import math
import re
import subprocess
import tempfile
from contextlib import nullcontext
from pathlib import Path

from src import s3_rasters
from src.raster_acquisition_plan import _bounds
from src.raster_cache import RasterCache, _checksum, _key
from src.raster_processing import validate_processing as _validate_processing
from src.raster_provenance import file_sha256, fingerprint, verify_mosaic


def _check_native_crs(planned: object, actual: object, label: str) -> None:
    if planned is None:
        return
    import rasterio

    if not isinstance(planned, (str, int)) or isinstance(planned, bool):
        raise ValueError(f"Invalid planned native CRS for {label}")
    try:
        expected = rasterio.crs.CRS.from_user_input(planned)
        actual_crs = rasterio.crs.CRS.from_user_input(actual)
    except (rasterio.errors.CRSError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid or unverified native CRS for {label}") from exc
    if expected != actual_crs:
        raise ValueError(f"Native CRS does not match planned native CRS for {label}")


def _planned_assets(plan: dict, region: str, role: str, footprint: tuple) -> tuple[dict, list[dict]]:
    from shapely.geometry import box
    from shapely.ops import unary_union

    bounds = _bounds(footprint, "Acquisition footprint")
    if not isinstance(plan, dict) or not isinstance(plan.get("inputs"), list) \
            or not isinstance(plan.get("assets"), list):
        raise ValueError("A batch acquisition plan with inputs and assets is required")
    matches = [item for item in plan["inputs"]
               if isinstance(item, dict) and item.get("region") == region
               and item.get("role") == role]
    if len(matches) != 1:
        raise ValueError(f"Expected one planned input for {region} {role}")
    selected = matches[0]
    footprints = selected.get("footprints_wgs84")
    if not isinstance(footprints, list) or bounds not in [
        _bounds(item, f"{region} {role} footprint") for item in footprints
    ]:
        raise ValueError(f"Footprint was not planned for {region} {role}")
    source = selected.get("source")
    if source is None:
        raise ValueError(f"{region} {role} is an exports fallback, not a cached source")
    if not isinstance(source, dict):
        raise ValueError(f"Invalid source in plan for {region} {role}")
    coverage = plan.get("capture_coverage", [])
    if not isinstance(coverage, list) or any(not isinstance(item, dict) for item in coverage):
        raise ValueError("Invalid raster capture coverage")
    for item in coverage:
        if (item.get("region") == region and item.get("role") == role
                and _bounds(item.get("footprint_wgs84"), "Capture coverage footprint") == bounds
                and item.get("state") == "outside"):
            raise ValueError(f"{region} {role} footprint is outside published capture geometry")
    region_box = box(*bounds)
    assets = []
    for asset in plan["assets"]:
        if not isinstance(asset, dict) or not isinstance(asset.get("uses"), list):
            raise ValueError("Invalid asset in acquisition plan")
        if not any(isinstance(use, dict) and use.get("region") == region
                   and use.get("role") == role for use in asset["uses"]):
            continue
        if (asset.get("collection_url") != source.get("collection_url")
                or asset.get("asset_key") != source.get("asset_key")
                or (source.get("expected_collection_id") is not None
                    and asset.get("collection_id") != source["expected_collection_id"])):
            raise ValueError(f"Planned asset source does not match {region} {role}")
        if any(use.get("layer_id") != selected.get("layer_id") for use in asset["uses"]
               if isinstance(use, dict) and use.get("region") == region and use.get("role") == role):
            raise ValueError(f"Planned asset layer does not match {region} {role}")
        if box(*_bounds(asset.get("bbox_wgs84"), "STAC asset footprint")).intersects(region_box):
            assets.append(asset)
    if not assets:
        raise ValueError(f"No planned raster asset intersects {region} {role} footprint")
    if not unary_union([box(*asset["bbox_wgs84"]) for asset in assets]).covers(region_box):
        raise ValueError(f"Planned raster item bounds do not cover {region} {role} footprint")
    return selected, assets


def _capture_metadata(plan: dict, region: str, role: str, footprints: list[tuple]) -> dict:
    """Keep the plan's advisory capture metadata with the mosaic it describes."""
    wanted = set(footprints)
    result = {}
    for field in ("capture_coverage", "capture_surveys"):
        entries = plan.get(field, [])
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            raise ValueError(f"Invalid raster {field}")
        selected = [item for item in entries
                    if item.get("region") == region and item.get("role") == role
                    and _bounds(item.get("footprint_wgs84"), field) in wanted]
        if selected:
            result[field] = selected
    return result


def prepare_planned_mosaic(cache: RasterCache, plan: dict, region: str, role: str,
                           footprint: tuple, destination: Path, *,
                           res: float | None = None, resampling: str = "average") -> Path:
    """Prepare one declared footprint from exactly its planned source assets."""
    bounds = _bounds(footprint, "Acquisition footprint")
    selected, assets = _planned_assets(plan, region, role, bounds)
    return prepare_cached_mosaic(
        cache, assets, bounds, destination, res=res, resampling=resampling,
        provenance={"region": region, "role": role, "layer_id": selected.get("layer_id"),
                    "survey_name": selected.get("survey_name"),
                    "mapping_evidence": selected.get("mapping_evidence"),
                    **_capture_metadata(plan, region, role, [bounds])})


def prepare_planned_region(cache: RasterCache, plan: dict, region: str, role: str,
                           destination: Path, *, res: float | None = None,
                           resampling: str = "average") -> Path:
    """Prepare all declared footprints, retaining provenance for their union."""
    import rasterio
    from rasterio.enums import MaskFlags
    from rasterio.merge import merge
    if not isinstance(plan, dict) or not isinstance(plan.get("inputs"), list):
        raise ValueError("A batch acquisition plan with inputs is required")
    selected = [item for item in plan["inputs"]
                if isinstance(item, dict) and item.get("region") == region and item.get("role") == role]
    if len(selected) != 1 or not isinstance(selected[0].get("footprints_wgs84"), list) \
            or not selected[0]["footprints_wgs84"]:
        raise ValueError(f"Expected planned footprints for {region} {role}")
    footprints = [_bounds(item, f"{region} {role} footprint")
                  for item in selected[0]["footprints_wgs84"]]
    if len(footprints) == 1:
        return prepare_planned_mosaic(cache, plan, region, role, footprints[0], destination,
                                      res=res, resampling=resampling)
    assets = {}
    for footprint in footprints:
        _, footprint_assets = _planned_assets(plan, region, role, footprint)
        for asset in footprint_assets:
            key = _key(asset)
            if key in assets and assets[key] != asset:
                raise ValueError(f"Conflicting planned raster asset: {key}")
            assets[key] = asset
    destination = Path(destination)
    sidecar = destination.with_name(destination.name + ".source.json")
    if not destination.parent.is_dir():
        raise ValueError(f"Destination directory does not exist: {destination.parent}")
    if destination.exists() or destination.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise FileExistsError(f"Refusing to overwrite mosaic or provenance: {destination}")
    with cache.lease_many(list(assets.values())) as leased_paths:
        with tempfile.TemporaryDirectory(prefix=".raster-region-", dir=destination.parent) as tmp:
            tmp = Path(tmp)
            parts = []
            manifests = []
            for index, footprint in enumerate(footprints):
                part = tmp / f"part{index:04d}.tif"
                _, part_assets = _planned_assets(plan, region, role, footprint)
                _prepare_cached_mosaic(
                    cache, part_assets, footprint, part, res=res, resampling=resampling,
                    provenance={"region": region, "role": role,
                                "layer_id": selected[0].get("layer_id"),
                                "survey_name": selected[0].get("survey_name"),
                                "mapping_evidence": selected[0].get("mapping_evidence"),
                                **_capture_metadata(plan, region, role, [footprint])},
                    _leased_paths={_key(asset): leased_paths[_key(asset)] for asset in part_assets})
                parts.append(part)
                manifests.append(verify_mosaic(part, part.with_name(part.name + ".source.json")))
            with rasterio.open(parts[0]) as first:
                profile = first.profile.copy()
                if first.nodata is None or any(MaskFlags.per_dataset in flags for flags in first.mask_flag_enums):
                    raise ValueError("Multi-footprint merge requires nodata without per-dataset masks")
                signature = (first.crs, first.count, first.dtypes, first.nodatavals, first.colorinterp,
                             first.res)
                origin = first.transform
                if origin.b != 0 or origin.d != 0 or origin.a <= 0 or origin.e >= 0:
                    raise ValueError("Multi-footprint rasters have unsupported grid alignment")
            for part in parts[1:]:
                with rasterio.open(part) as source:
                    if (source.crs, source.count, source.dtypes, source.nodatavals,
                            source.colorinterp, source.res) != signature:
                        raise ValueError("Multi-footprint rasters have incompatible profiles")
                    if any(MaskFlags.per_dataset in flags for flags in source.mask_flag_enums):
                        raise ValueError("Multi-footprint merge cannot preserve per-dataset masks")
                    dx = (source.transform.c - origin.c) / origin.a
                    dy = (source.transform.f - origin.f) / origin.e
                    if (source.transform.b != 0 or source.transform.d != 0
                            or abs(dx - round(dx)) > 1e-6 or abs(dy - round(dy)) > 1e-6):
                        raise ValueError("Multi-footprint rasters have incompatible grid alignment")
            output = tmp / "merged.tif"
            profile.update(driver="GTiff", tiled=True, compress="deflate", BIGTIFF="IF_SAFER")
            merge(parts, dst_path=output, dst_kwds=profile)
            if output.with_suffix(output.suffix + ".msk").exists():
                raise ValueError("Merged mosaic has an external mask that cannot be published safely")
            with rasterio.open(output) as result:
                if (result.crs, result.count, result.dtypes, result.nodatavals,
                        result.colorinterp, result.res) != signature:
                    raise ValueError("Merged mosaic did not preserve the source raster profile")
                result.read(1, window=((0, 1), (0, 1)))
            sources = {}
            for manifest in manifests:
                for source in manifest["sources"]:
                    key = _key(source)
                    if key in sources and sources[key] != source:
                        raise ValueError(f"Raster source changed during regional preparation: {key}")
                    sources[key] = source
            content = {
                "schema_version": 1, "region": region, "role": role,
                "layer_id": selected[0].get("layer_id"),
                "survey_name": selected[0].get("survey_name"),
                "mapping_evidence": selected[0].get("mapping_evidence"),
                **_capture_metadata(plan, region, role, footprints),
                "footprints_wgs84": [list(footprint) for footprint in footprints],
                "processing": {"crs": "EPSG:2193", "resolution": res, "resampling": resampling,
                               "parts": [manifest["processing"] for manifest in manifests],
                               "merge": "rasterio-first"},
                "sources": [sources[key] for key in sorted(sources)],
                "output": {"name": destination.name, "sha256": file_sha256(output)},
            }
            manifest = {**content, "fingerprint": fingerprint(content)}
            staging = tmp / "provenance.json"
            with staging.open("w", encoding="utf-8") as stream:
                json.dump(manifest, stream, sort_keys=True, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.link(staging, sidecar)
            try:
                os.link(output, destination)
            except BaseException:
                sidecar.unlink()
                raise
    return destination


def verify_planned_region(plan: dict, region: str, role: str, destination: Path, *,
                          res: float | None = None, resampling: str = "average") -> dict:
    """Verify an existing prepared mosaic against a freshly selected plan.

    An unversioned asset without a published checksum cannot prove freshness.
    No cache payload is required; the manifest's hashes bind the output to
    the assets used when it was prepared.
    """
    _validate_processing(res, resampling)
    if not isinstance(plan, dict) or not isinstance(plan.get("inputs"), list):
        raise ValueError("A batch acquisition plan with inputs is required")
    matches = [item for item in plan["inputs"] if isinstance(item, dict)
               and item.get("region") == region and item.get("role") == role]
    if len(matches) != 1 or not isinstance(matches[0].get("footprints_wgs84"), list) \
            or not matches[0]["footprints_wgs84"]:
        raise ValueError(f"Expected planned footprints for {region} {role}")
    selected = matches[0]
    footprints = [_bounds(item, f"{region} {role} footprint")
                  for item in selected["footprints_wgs84"]]
    planned = {}
    for footprint in footprints:
        _, assets = _planned_assets(plan, region, role, footprint)
        for asset in assets:
            key = _key(asset)
            if key in planned and planned[key] != asset:
                raise ValueError(f"Conflicting planned raster asset: {key}")
            planned[key] = asset
    destination = Path(destination)
    manifest = verify_mosaic(destination, destination.with_name(destination.name + ".source.json"))
    if (manifest.get("region") != region or manifest.get("role") != role
            or manifest.get("layer_id") != selected.get("layer_id")
            or manifest.get("derivations")):
        raise ValueError(f"Existing mosaic does not match planned input: {destination}")
    expected_footprints = [list(footprint) for footprint in footprints]
    if len(footprints) == 1:
        actual_footprints = [manifest.get("footprint_wgs84")]
    else:
        actual_footprints = manifest.get("footprints_wgs84")
    if actual_footprints != expected_footprints:
        raise ValueError(f"Existing mosaic footprints do not match plan: {destination}")
    processing = manifest.get("processing")
    if (not isinstance(processing, dict) or processing.get("crs") != "EPSG:2193"
            or processing.get("resolution") != res or processing.get("resampling") != resampling
            or (len(footprints) > 1 and processing.get("merge") != "rasterio-first")):
        raise ValueError(f"Existing mosaic processing does not match plan: {destination}")
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise ValueError(f"Existing mosaic lacks planned sources: {destination}")
    recorded = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError(f"Invalid source in mosaic provenance: {destination}")
        key = _key(source)
        if key in recorded:
            raise ValueError(f"Duplicate source in mosaic provenance: {destination}")
        recorded[key] = source
    if set(planned) != set(recorded):
        raise ValueError(f"Existing mosaic source assets do not match plan: {destination}")
    for key, asset in planned.items():
        source = recorded[key]
        _check_native_crs(asset.get("native_crs"), source.get("native_crs_actual"),
                          f"existing mosaic source {key}")
        checksum = _checksum(asset.get("checksum"))
        revision = asset.get("revision")
        if checksum is None and (not isinstance(revision, str) or not revision.strip()):
            raise ValueError(f"Cannot verify freshness of unversioned raster asset: {key}")
        if (not isinstance(source.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"])
                or source.get("url") != asset.get("url")
                or source.get("revision") != asset.get("revision")
                or source.get("source_checksum") != asset.get("checksum")
                or (asset.get("size_bytes") is not None
                    and source.get("size_bytes") != asset["size_bytes"])
                or (checksum is not None and source.get("sha256") != checksum)):
            raise ValueError(f"Existing mosaic source version does not match plan: {key}")
    return manifest


def ensure_planned_region(cache: RasterCache, plan: dict, region: str, role: str,
                          destination: Path, *, res: float | None = None,
                          resampling: str = "average") -> Path:
    """Prepare a new mosaic or reuse an existing one only after verification."""
    destination = Path(destination)
    sidecar = destination.with_name(destination.name + ".source.json")
    if (destination.exists() or destination.is_symlink()
            or sidecar.exists() or sidecar.is_symlink()):
        verify_planned_region(plan, region, role, destination, res=res, resampling=resampling)
        return destination
    return prepare_planned_region(cache, plan, region, role, destination,
                                  res=res, resampling=resampling)


def prepare_cached_mosaic(cache: RasterCache, assets: list[dict], footprint: tuple,
                          destination: Path, *, res: float | None = None,
                          resampling: str = "average",
                          provenance: dict | None = None) -> Path:
    """Crop local tiles with the existing S3 crop grid and hold leases throughout.

    The output must be new: adopting or replacing existing regional mosaics
    requires an explicit replacement policy.
    """
    return _prepare_cached_mosaic(cache, assets, footprint, destination, res=res,
                                  resampling=resampling, provenance=provenance)


def _reproject_mosaic(vrt: Path, output: Path, bounds: tuple, res: float,
                      resampling: str) -> None:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.vrt import WarpedVRT

    x0, y0, x1, y1 = bounds
    left = math.floor(x0 / res) * res
    top = math.ceil(y1 / res) * res
    width = math.ceil((x1 - left) / res)
    height = math.ceil((top - y0) / res)
    transform = from_origin(left, top, res, res)
    with rasterio.open(vrt) as source:
        profile = source.profile.copy()
        profile.update(driver="GTiff", crs="EPSG:2193", width=width, height=height,
                       transform=transform, tiled=True, blockxsize=256, blockysize=256,
                       compress="deflate", BIGTIFF="IF_SAFER")
        with WarpedVRT(source, crs="EPSG:2193", transform=transform,
                       width=width, height=height, resampling=Resampling[resampling]) as warped:
            with rasterio.open(output, "w", **profile) as destination:
                destination.colorinterp = source.colorinterp
                for _, window in destination.block_windows(1):
                    destination.write(warped.read(window=window), window=window)


def _prepare_cached_mosaic(cache: RasterCache, assets: list[dict], footprint: tuple,
                           destination: Path, *, res: float | None = None,
                           resampling: str = "average",
                           provenance: dict | None = None,
                           _leased_paths: dict[str, Path] | None = None) -> Path:
    import rasterio
    from rasterio.enums import ColorInterp, MaskFlags
    from rasterio.warp import transform_bounds
    from shapely.geometry import box
    from shapely.ops import unary_union

    bounds = _bounds(footprint, "Acquisition footprint")
    if not isinstance(assets, list) or not assets:
        raise ValueError("At least one planned raster asset is required")
    identities = {(asset.get("collection_url"), asset.get("collection_id"), asset.get("asset_key"))
                  for asset in assets}
    if len(identities) != 1:
        raise ValueError("Raster tiles must belong to the same source and asset key")
    _validate_processing(res, resampling)
    if provenance is not None and (
        not isinstance(provenance, dict)
        or not {"region", "role", "layer_id"} <= set(provenance)
        or set(provenance) - {"region", "role", "layer_id", "survey_name", "mapping_evidence",
                              "capture_coverage", "capture_surveys"}
        or not isinstance(provenance["region"], str) or not provenance["region"]
        or provenance["role"] not in ("dsm", "imagery", "reference_imagery", "wide_dem")
        or (provenance["layer_id"] is not None and
            (type(provenance["layer_id"]) is not int or provenance["layer_id"] <= 0))
        or any(value is not None and (not isinstance(value, str) or not value.strip())
               for value in (provenance.get("survey_name"), provenance.get("mapping_evidence")))
        or any(not isinstance(provenance[field], list)
               or any(not isinstance(item, dict) for item in provenance[field])
               for field in ("capture_coverage", "capture_surveys") if field in provenance)
    ):
        raise ValueError("Provenance requires a region, raster role and valid layer ID")
    destination = Path(destination)
    sidecar = destination.with_name(destination.name + ".source.json")
    if not destination.parent.is_dir():
        raise ValueError(f"Destination directory does not exist: {destination.parent}")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Refusing to overwrite an existing mosaic: {destination}")
    if provenance is not None and (sidecar.exists() or sidecar.is_symlink()):
        raise FileExistsError(f"Refusing to overwrite existing raster provenance: {sidecar}")
    reader = s3_rasters.require_raster_support()
    lease = cache.lease_many(assets) if _leased_paths is None else nullcontext(_leased_paths)
    planned_assets = {_key(asset): asset for asset in assets}
    with lease as paths:
        reference = None
        source_crs = None
        native_crs_by_key = {}
        tile_extents = []
        missing_mask = False
        for key, path in paths.items():
            with rasterio.open(path) as source:
                if source.crs is None:
                    raise ValueError(f"Cached tile has no native CRS: {path}")
                _check_native_crs(planned_assets[key].get("native_crs"), source.crs,
                                  f"cached tile {path}")
                native_crs_by_key[key] = source.crs.to_wkt()
                if source_crs is None:
                    source_crs = source.crs
                elif source.crs != source_crs:
                    raise ValueError(f"Cached tiles have incompatible native CRSs: {path}")
                reproject = source.crs.to_epsg() != 2193
                if reproject:
                    if res is None:
                        raise ValueError("Reprojecting cached tiles requires an explicit "
                                         "EPSG:2193 output resolution")
                    nodata = source.nodatavals
                    if (any(value is None for value in nodata)
                            or any(not (value == nodata[0] or
                                        (math.isnan(value) and math.isnan(nodata[0])))
                                   for value in nodata if value is not None)
                            or ColorInterp.alpha in source.colorinterp
                            or any(MaskFlags.per_dataset in flags for flags in source.mask_flag_enums)):
                        raise ValueError("Reprojecting cached tiles requires common nodata "
                                         "without alpha or per-dataset masks")
                transform = source.transform
                if transform.b != 0 or transform.d != 0 or transform.a <= 0 or transform.e >= 0:
                    raise ValueError(f"Cached tile has an unsupported raster grid: {path}")
                profile = (transform, source.count, source.dtypes, source.colorinterp,
                           source.nodatavals)
                if reference is None:
                    reference = profile
                else:
                    origin, count, dtypes, colors, nodata = reference
                    if (source.count != count or source.dtypes != dtypes
                            or source.colorinterp != colors):
                        raise ValueError(f"Cached tile has incompatible source bands: {path}")
                    if any(not (a == b or (a is not None and b is not None
                                          and math.isnan(a) and math.isnan(b)))
                           for a, b in zip(source.nodatavals, nodata)):
                        raise ValueError(f"Cached tile has incompatible nodata values: {path}")
                    if (not math.isclose(transform.a, origin.a, rel_tol=1e-9, abs_tol=0)
                            or not math.isclose(transform.e, origin.e, rel_tol=1e-9, abs_tol=0)
                            or abs((transform.c - origin.c) / origin.a
                                   - round((transform.c - origin.c) / origin.a)) > 1e-6
                            or abs((transform.f - origin.f) / origin.e
                                   - round((transform.f - origin.f) / origin.e)) > 1e-6):
                        raise ValueError(f"Cached tile has an incompatible native grid: {path}")
                if reader == "LIBERTIFF" and any(
                    MaskFlags.per_dataset in flags and MaskFlags.alpha not in flags
                    for flags in source.mask_flag_enums
                ):
                    raise ValueError(
                        f"LIBERTIFF mosaic preparation cannot preserve this tile's mask: {path}")
                tile_extents.append(box(*source.bounds))
                missing_mask |= any(MaskFlags.all_valid in flags and color != ColorInterp.alpha
                                    for color, flags in zip(source.colorinterp, source.mask_flag_enums))
        footprint_native = box(*transform_bounds("EPSG:4326", source_crs, *bounds,
                                                  densify_pts=21))
        if not any(extent.intersection(footprint_native).area > 0 for extent in tile_extents):
            raise ValueError("Cached tile headers do not overlap the acquisition footprint")
        if source_crs.to_epsg() != 2193:
            x0, y0, x1, y1 = transform_bounds("EPSG:4326", "EPSG:2193", *bounds, densify_pts=21)
        else:
            x0, y0, x1, y1 = s3_rasters._nztm_bounds(bounds)
        if missing_mask and not unary_union(tile_extents).covers(box(x0, y0, x1, y1)):
            raise ValueError("Uncovered mosaic area cannot be represented without a mask or nodata")
        with tempfile.TemporaryDirectory(prefix=".raster-prepare-", dir=destination.parent) as tmp:
            vrt = Path(tmp) / "source.vrt"
            output = Path(tmp) / "mosaic.tif"
            s3_rasters._build_vrt([str(path) for path in paths.values()], vrt, reader)
            if source_crs.to_epsg() != 2193:
                if res is None:
                    raise ValueError("Reprojecting cached tiles requires an explicit "
                                     "EPSG:2193 output resolution")
                _reproject_mosaic(vrt, output, (x0, y0, x1, y1), res, resampling)
            elif reader == "LIBERTIFF":
                s3_rasters._rasterio_crop(vrt, output, (x0, y0, x1, y1), res, resampling)
            else:
                command = ["gdal_translate", "-q", "-projwin", str(x0), str(y1),
                           str(x1), str(y0), "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE",
                           "-co", "BIGTIFF=IF_SAFER"]
                if res is not None:
                    command.extend(["-tr", str(res), str(res), "-r", resampling])
                env = s3_rasters._reader_env(reader)
                env["GDAL_TIFF_INTERNAL_MASK"] = "YES"
                subprocess.run([*command, str(vrt), str(output)],
                               check=True, env=env)
            if output.with_suffix(output.suffix + ".msk").exists():
                raise ValueError("Prepared mosaic has an external mask that cannot be published safely")
            with rasterio.open(output) as result:
                if result.crs is None or result.crs.to_epsg() != 2193 or not result.count:
                    raise ValueError("Prepared mosaic lacks the expected CRS or raster bands")
                result.read(1, window=((0, 1), (0, 1)))
            if provenance is None:
                os.link(output, destination)
            else:
                sources = []
                by_key = {_key(asset): asset for asset in assets}
                for key, path in paths.items():
                    asset = by_key[key]
                    sources.append({
                        "collection_url": asset["collection_url"],
                        "collection_id": asset["collection_id"],
                        "item_id": asset["item_id"], "asset_key": asset["asset_key"],
                        "url": asset["url"], "revision": asset.get("revision"),
                        "source_checksum": asset.get("checksum"), "size_bytes": path.stat().st_size,
                        "sha256": file_sha256(path),
                        "datetime": asset.get("datetime"), "start_datetime": asset.get("start_datetime"),
                        "end_datetime": asset.get("end_datetime"), "native_crs": asset.get("native_crs"),
                        "native_crs_actual": native_crs_by_key[key],
                        "resolution": asset.get("resolution"), "license": asset.get("license"),
                        "providers": asset.get("providers"),
                    })
                content = {
                    "schema_version": 1, **provenance,
                    "footprint_wgs84": list(bounds),
                    "processing": {"crs": "EPSG:2193", "resolution": res,
                                   "resampling": resampling, "reader": reader},
                    "sources": sources,
                    "output": {"name": destination.name, "sha256": file_sha256(output)},
                }
                manifest = {**content, "fingerprint": fingerprint(content)}
                staging = Path(tmp) / "provenance.json"
                with staging.open("w", encoding="utf-8") as stream:
                    json.dump(manifest, stream, sort_keys=True, allow_nan=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(staging, sidecar)
                try:
                    os.link(output, destination)
                except BaseException:
                    sidecar.unlink()
                    raise
    return destination
