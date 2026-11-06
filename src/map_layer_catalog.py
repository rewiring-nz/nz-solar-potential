"""Layer catalogues for local source caches and the combined map dataset."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any


_COLOURS = {
    "lidar": "#7c3aed",
    "raster": "#0ea5e9",
    "outlines": "#64748b",
    "coverage": "#16a34a",
    "buildings": "#f59e0b",
    "layouts": "#dc2626",
    "cells": "#2563eb",
}
_RASTER_NAMES = {
    "dsm_mosaic.tif",
    "imagery_mosaic.tif",
    "reference_imagery_mosaic.tif",
    "dsm_pack.tif",
    "imagery_pack.tif",
}
_SAFE_FILE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")


def _relative(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()


def _valid_bounds(bounds: Any) -> list[float] | None:
    try:
        values = [float(value) for value in bounds]
    except (TypeError, ValueError):
        return None
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        return None
    west, south, east, north = values
    if west >= east or south >= north:
        return None
    return values


def _wgs84_bounds(bounds: Any, crs: Any) -> list[float] | None:
    values = _valid_bounds(bounds)
    if values is None:
        return None
    import pyproj

    try:
        transformer = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        converted = transformer.transform_bounds(*values, densify_pts=21)
    except (pyproj.exceptions.ProjError, ValueError):
        return None
    result = _valid_bounds(converted)
    if result is None or result[0] < -180 or result[2] > 180 or result[1] < -90 or result[3] > 90:
        return None
    return result


def _polygon(bounds: list[float]) -> dict:
    west, south, east, north = bounds
    return {
        "type": "Polygon",
        "coordinates": [[
            [west, south], [east, south], [east, north],
            [west, north], [west, south],
        ]],
    }


def _feature(
    feature_id: str, label: str, bounds: list[float], regions: list[str], **properties: Any,
) -> dict:
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": _polygon(bounds),
        "properties": {
            "name": label,
            "regions": regions,
            "bounds": bounds,
            **properties,
        },
    }


def _layer(
    layer_id: str,
    label: str,
    group: str,
    representation: str,
    features: list[dict],
    colour: str,
    description: str,
    *,
    url: str | None = None,
) -> dict | None:
    if not features:
        return None
    for feature in features:
        # Popup queries show properties generically, so every feature names its layer.
        feature["properties"].setdefault("layer_id", layer_id)
    bounds = [
        min(feature["properties"]["bounds"][0] for feature in features),
        min(feature["properties"]["bounds"][1] for feature in features),
        max(feature["properties"]["bounds"][2] for feature in features),
        max(feature["properties"]["bounds"][3] for feature in features),
    ]
    regions = sorted({
        region
        for feature in features
        for region in feature["properties"].get("regions", [])
    })
    layer = {
        "id": layer_id,
        "label": label,
        "group": group,
        "representation": representation,
        "colour": colour,
        "description": description,
        "regions": regions,
        "bounds": bounds,
        "defaultVisible": False,
    }
    if url is None:
        layer["data"] = {"type": "FeatureCollection", "features": features}
    else:
        layer["url"] = url
    return layer


def _warn(warnings: list[str], path: Path, base: Path, message: str) -> None:
    try:
        name = _relative(path, base)
    except ValueError:
        name = path.name
    warnings.append(f"{name}: {message}")


def _region_ids(data: Path) -> list[str]:
    regions = data / "regions"
    found = set()
    if regions.is_dir():
        for name in ("locality.json", "pointcloud_tiles.txt"):
            for path in regions.rglob(name):
                if path.is_file():
                    found.add(path.parent.relative_to(regions).as_posix())
        for path in regions.rglob("summary.json"):
            found.add(path.parent.relative_to(regions).as_posix())
    output = data / "out"
    if output.is_dir():
        for path in output.rglob("summary.json"):
            found.add(path.parent.relative_to(output).as_posix())
    return sorted(found)


def _pointcloud_regions(data: Path, warnings: list[str]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    root = data / "regions"
    if not root.is_dir():
        return result
    for path in root.rglob("pointcloud_tiles.txt"):
        region = path.parent.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            _warn(warnings, path, data, f"could not read tile list ({type(exc).__name__})")
            continue
        for line in lines:
            name = line.strip()
            if not name:
                continue
            if Path(name).name != name or not name.endswith(".laz"):
                _warn(warnings, path, data, f"ignored invalid tile name {name!r}")
                continue
            result.setdefault(name, set()).add(region)
            if name.endswith(".copc.laz"):
                result.setdefault(name.removesuffix(".copc.laz") + ".laz", set()).add(region)
    return result


def _packed_names(data: Path, warnings: list[str]) -> set[str] | None:
    registry = data / "pointcloud" / ".packed.json"
    if not registry.exists():
        return set()
    try:
        value = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _warn(warnings, registry, data, f"invalid packed-subset registry ({type(exc).__name__})")
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        _warn(warnings, registry, data, "invalid packed-subset registry (expected a list of tile names)")
        return None
    return {
        name.removesuffix(".copc.laz") + ".laz"
        if name.endswith(".copc.laz") else name
        for name in value
    }


def _las_bounds(path: Path, warnings: list[str], data: Path) -> list[float] | None:
    import laspy

    try:
        with laspy.open(path) as reader:
            header = reader.header
            bounds = _wgs84_bounds(
                [header.mins[0], header.mins[1], header.maxs[0], header.maxs[1]],
                header.parse_crs() or "EPSG:2193",
            )
            if bounds is None:
                _warn(warnings, path, data, "point-cloud header has invalid bounds or an unsupported CRS")
            return bounds
    except (OSError, ValueError, laspy.errors.LaspyException) as exc:
        _warn(warnings, path, data, f"could not read point-cloud header ({type(exc).__name__})")
        return None


def _pointcloud_layer(data: Path, warnings: list[str]) -> dict | None:
    directory = data / "pointcloud"
    if not directory.is_dir():
        return None
    packed = _packed_names(data, warnings)
    usage = _pointcloud_regions(data, warnings)
    features = []

    # A packed tile restored to the shared cache retains its original name.
    # Its registry entry prevents its subset extent from being presented as a
    # complete survey tile; region pack files below retain their own extent.
    by_tile: dict[str, list[Path]] = {}
    for path in sorted(directory.glob("*.laz")):
        if path.name.startswith(".") or path.name.startswith("._"):
            continue
        tile = path.name.removesuffix(".copc.laz") + ".laz" \
            if path.name.endswith(".copc.laz") else path.name
        by_tile.setdefault(tile, []).append(path)
    if packed is not None:
        for tile, variants in sorted(by_tile.items()):
            if tile in packed:
                continue
            # COPC and plain LAZ names can identify the same source tile. Prefer
            # the COPC spelling if both are cached, so a shared input is one item.
            path = next((p for p in variants if p.name.endswith(".copc.laz")), variants[0])
            bounds = _las_bounds(path, warnings, data)
            if bounds is None:
                continue
            regions = sorted(usage.get(path.name, set()) | usage.get(tile, set()))
            features.append(_feature(
                f"lidar:{tile}", tile, bounds, regions,
                source="pointcloud", status="cached", subset=False,
                format="COPC LAZ" if path.name.endswith(".copc.laz") else "LAZ",
                cached_path=_relative(path, data),
            ))

    regions_root = data / "regions"
    if regions_root.is_dir():
        for pack in regions_root.rglob("pack/pack.json"):
            region = pack.parent.parent.relative_to(regions_root).as_posix()
            try:
                manifest = json.loads(pack.read_text(encoding="utf-8"))
                tile_names = manifest.get("tiles", [])
                if not isinstance(tile_names, list) or not all(isinstance(name, str) for name in tile_names):
                    raise ValueError("tiles must be a list of names")
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, ValueError) as exc:
                _warn(warnings, pack, data, f"invalid point-cloud pack manifest ({type(exc).__name__})")
                continue
            points = pack.parent / "points"
            for tile in sorted(set(tile_names)):
                if Path(tile).name != tile or not tile.endswith(".laz"):
                    _warn(warnings, pack, data, f"ignored invalid packed tile name {tile!r}")
                    continue
                path = points / tile
                if not path.is_file():
                    continue
                bounds = _las_bounds(path, warnings, data)
                if bounds is None:
                    continue
                features.append(_feature(
                    f"lidar-subset:{region}:{tile}", tile, bounds, [region],
                    source="pointcloud", status="cached_subset", subset=True,
                    format="packed LAZ subset",
                    cached_path=_relative(path, data),
                ))

    return _layer(
        "source-lidar", "LiDAR point-cloud tiles", "source", "footprint",
        features, _COLOURS["lidar"],
        "Footprints from cached LAZ/COPC headers. Packed files show only the locally retained point subset.",
    )


def _raster_layer(data: Path, warnings: list[str]) -> dict | None:
    import rasterio

    candidates: dict[Path, tuple[Path, set[str]]] = {}

    def add_candidate(path: Path, region: str | None = None) -> None:
        resolved = path.resolve()
        if resolved not in candidates:
            candidates[resolved] = (path, set())
        if region:
            candidates[resolved][1].add(region)

    for path in data.glob("dem_wide_mosaic*.tif"):
        add_candidate(path)
    dem_tiles = data / "dem8m_tiles"
    if dem_tiles.is_dir():
        for path in dem_tiles.iterdir():
            if path.suffix.lower() in {".tif", ".tiff"}:
                add_candidate(path)
    regions = data / "regions"
    if regions.is_dir():
        for path in regions.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".tif", ".tiff"}:
                continue
            if path.name in _RASTER_NAMES:
                region = path.parent.parent.relative_to(regions).as_posix() if path.parent.name == "pack" \
                    else path.parent.relative_to(regions).as_posix()
                add_candidate(path, region)
    dsm_features, imagery_features, terrain_features = [], [], []
    region_bounds: dict[str, list[float]] = {}
    ordered = sorted(candidates.values(), key=lambda item: (not bool(item[1]), str(item[0])))
    for path, path_regions in ordered:
        try:
            with rasterio.open(path) as dataset:
                if dataset.crs is None:
                    raise ValueError("raster has no CRS")
                bounds = _wgs84_bounds(dataset.bounds, dataset.crs)
                if bounds is None:
                    raise ValueError("raster header bounds cannot be transformed to EPSG:4326")
        except (OSError, ValueError, rasterio.errors.RasterioError) as exc:
            _warn(warnings, path, data, f"could not read raster header ({type(exc).__name__})")
            continue
        if path_regions and "dsm" in path.name.lower():
            for region in path_regions:
                region_bounds[region] = bounds
        if path_regions:
            region_list = sorted(path_regions)
        else:
            region_list = sorted(
                region for region, area_bounds in region_bounds.items()
                if bounds[0] <= area_bounds[2] and bounds[2] >= area_bounds[0]
                and bounds[1] <= area_bounds[3] and bounds[3] >= area_bounds[1]
            )
        if "dsm" in path.name.lower():
            target = dsm_features
            source_type = "dsm"
        elif "imagery" in path.name.lower():
            target = imagery_features
            source_type = "imagery"
        else:
            target = terrain_features
            source_type = "terrain_dem"
        is_pack = path.parent.name == "pack"
        target.append(_feature(
            f"{source_type}:{_relative(path, data)}", path.name, bounds,
            region_list, source=source_type, status="cached_subset" if is_pack else "cached",
            extent_kind="raster_header_extent",
            subset=is_pack, cached_path=_relative(path, data),
        ))

    result = []
    for layer in (
        _layer("source-dsm", "DSM rasters", "source", "footprint", dsm_features,
               _COLOURS["raster"], "Raster header extents, not valid-data coverage."),
        _layer("source-imagery", "Aerial imagery rasters", "source", "footprint", imagery_features,
               _COLOURS["raster"], "Raster header extents, not valid-data coverage."),
        _layer("source-terrain-dem", "Terrain DEM rasters", "source", "footprint", terrain_features,
               _COLOURS["raster"], "Raster header extents, not valid-data coverage."),
    ):
        if layer:
            result.append(layer)
    return result


def _authority_regions(data: Path, authority: str) -> list[str]:
    authority_key = re.sub(r"[^a-z0-9]+", "-", authority.lower()).strip("-")
    result = [
        region for region in _region_ids(data)
        if region.split("/", 1)[0].lower() == authority_key
    ]
    regions = data / "regions"
    if not regions.is_dir():
        return result
    for locality in regions.rglob("locality.json"):
        region = locality.parent.relative_to(regions).as_posix()
        try:
            doc = json.loads(locality.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        linz = doc.get("linz") if isinstance(doc, dict) else None
        if not isinstance(linz, dict):
            continue
        value = linz.get("territorial_authority") or linz.get("territorialAuthority") or ""
        key = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
        if key == authority_key:
            result.append(region)
    return sorted(set(result))


def _council_outline_layer(data: Path, warnings: list[str]) -> dict | None:
    root = data / "sources" / "linz"
    if not root.is_dir():
        return None
    features = []
    for manifest_path in sorted(root.rglob("manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            gpkg = manifest_path.parent / "building_outlines.gpkg"
            if not gpkg.is_file():
                continue
            with sqlite3.connect(gpkg) as connection:
                rows = connection.execute(
                    "SELECT srs_id, min_x, min_y, max_x, max_y "
                    "FROM gpkg_contents WHERE data_type = 'features' "
                    "AND min_x IS NOT NULL AND min_y IS NOT NULL "
                    "AND max_x IS NOT NULL AND max_y IS NOT NULL"
                ).fetchall()
            for srs_id, west, south, east, north in rows:
                bounds = _wgs84_bounds([west, south, east, north], f"EPSG:{srs_id}")
                if bounds is None:
                    raise ValueError("GPKG extent cannot be transformed to EPSG:4326")
                authority = str(manifest.get("territorial_authority") or "LINZ council")
                regions = _authority_regions(data, authority)
                features.append(_feature(
                    f"council-outlines:{_relative(gpkg, data)}",
                    authority, bounds, regions, source="building_outlines", status="cached",
                    extent_kind="gpkg_metadata_extent",
                    feature_count=manifest.get("features"),
                    linz_layer=manifest.get("layer"),
                    linz_layer_version=manifest.get("layer_version"),
                    downloaded_utc=manifest.get("downloaded_utc"),
                    cached_path=_relative(gpkg, data),
                ))
        except (OSError, UnicodeError, json.JSONDecodeError, sqlite3.Error,
                AttributeError, TypeError, ValueError) as exc:
            _warn(warnings, manifest_path, data, f"could not read cached outline metadata ({type(exc).__name__})")
    return _layer(
        "source-council-outlines", "Cached council building outlines", "source", "footprint",
        features, _COLOURS["outlines"],
        "Cached GPKG extents from GeoPackage metadata; extent is not feature-level coverage.",
    )


def _cell_bounds(path: Path, warnings: list[str], base: Path) -> list[float] | None:
    try:
        cells = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cells, dict):
            raise ValueError("expected a cell-object map")
        from src.build_building_tiles import tile_bounds

        extents = []
        for key in cells:
            parts = key.split("/")
            if len(parts) != 4:
                raise ValueError(f"invalid cell key {key!r}")
            zoom, x, y = (int(value) for value in parts[:3])
            if zoom < 0 or zoom > 30 or not 0 <= x < 2**zoom or not 0 <= y < 2**zoom:
                raise ValueError(f"invalid cell coordinates {key!r}")
            extents.append(tile_bounds(x, y, zoom))
        if not extents:
            return None
        return [
            min(item[0] for item in extents), min(item[1] for item in extents),
            max(item[2] for item in extents), max(item[3] for item in extents),
        ]
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        _warn(warnings, path, base, f"could not read derived cell coverage ({type(exc).__name__})")
        return None


def _combined_analysis_layers(
    regions: list[str],
    out_root: Path,
    available_files: Path,
    warnings: list[str],
    *,
    url_prefix: str,
) -> tuple[list[dict], dict[str, list[float]]]:
    region_bounds = {}
    for region in regions:
        path = Path(out_root) / region / "cells.json"
        bounds = _cell_bounds(path, warnings, Path(out_root).parent)
        if bounds is not None:
            region_bounds[region] = bounds
    if not region_bounds:
        if regions:
            warnings.append("No selected region has non-empty emitted cell coverage; combined analysis layers omitted.")
        return [], region_bounds

    extent = [
        min(bounds[0] for bounds in region_bounds.values()),
        min(bounds[1] for bounds in region_bounds.values()),
        max(bounds[2] for bounds in region_bounds.values()),
        max(bounds[3] for bounds in region_bounds.values()),
    ]
    region_list = sorted(region_bounds)
    root = Path(available_files)
    from src.output_contract import BUILDING_LAYER, LAYOUT_LAYER

    layers = []
    # Listed top to bottom, the order they are drawn in (the catalogue's
    # layers run from the highest order down; tests/test_map_layer_catalog.py).
    vector_descriptors = (
        ("analysis-panels", "Solar panels", "panel_layouts.pmtiles", LAYOUT_LAYER,
         "panel", "#dc2626", 30, "Combined panel polygons from the emitted layout vector tiles."),
        ("analysis-obstructions", "Roof obstructions", "panel_layouts.pmtiles", LAYOUT_LAYER,
         "obstruction", "#7c3aed", 24, "Combined obstruction polygons from the emitted layout vector tiles."),
        ("analysis-outlines", "Building outlines", "buildings.pmtiles", BUILDING_LAYER,
         "building", "#64748b", 20, "Combined building outlines from the emitted building vector tiles."),
        ("analysis-facets", "Roof facets", "panel_layouts.pmtiles", LAYOUT_LAYER,
         "facet", "#f97316", 15, "Combined roof-facet polygons; hue shows facing direction and saturation shows pitch, not solar yield."),
    )
    for layer_id, label, filename, source_layer, kind, colour, order, description in vector_descriptors:
        path = root / filename
        url = f"{url_prefix}{filename}"
        if not path.is_file() or path.stat().st_size == 0:
            continue
        if not _safe_static_url(url):
            warnings.append(f"{filename}: unsafe combined vector-tile URL omitted")
            continue
        layers.append({
            "id": layer_id,
            "label": label,
            "group": "analysis",
            "representation": "vector-tiles",
            "url": url,
            "sourceLayer": source_layer,
            "kind": kind,
            "colour": colour,
            "description": description,
            "regions": region_list,
            "bounds": extent,
            "defaultVisible": False,
            "order": order,
        })

    heatmap_dir = root / "heatmap_tiles"
    meta_path = heatmap_dir / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            minzoom, maxzoom, tile_size = meta["minzoom"], meta["maxzoom"], meta["tileSize"]
            if (
                isinstance(minzoom, bool) or not isinstance(minzoom, int) or minzoom < 0
                or isinstance(maxzoom, bool) or not isinstance(maxzoom, int) or maxzoom < minzoom
                or isinstance(tile_size, bool) or not isinstance(tile_size, int) or tile_size <= 0
            ):
                raise ValueError("invalid tile zoom or size metadata")
            if not any(heatmap_dir.rglob("*.png")):
                warnings.append("heatmap_tiles/meta.json: no raster tiles are present; heatmap layer omitted")
            else:
                tiles = f"{url_prefix}heatmap_tiles/{{z}}/{{x}}/{{y}}.png"
                if not _safe_static_url(tiles.replace("{z}", "z").replace("{x}", "x").replace("{y}", "y")):
                    warnings.append("heatmap_tiles: unsafe raster-tile URL omitted")
                else:
                    layers.append({
                        "id": "analysis-heatmap",
                        "label": "Solar potential heatmap",
                        "group": "analysis",
                        "representation": "raster-tiles",
                        "tiles": tiles,
                        "tileSize": tile_size,
                        "minzoom": minzoom,
                        "maxzoom": maxzoom,
                        "colour": "#f59e0b",
                        "description": "Combined solar-potential raster tiles from the emitted regions.",
                        "regions": region_list,
                        "bounds": extent,
                        "defaultVisible": False,
                        "order": 10,
                    })
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            _warn(warnings, meta_path, root, f"could not read heatmap tile metadata ({type(exc).__name__})")

    return layers, region_bounds


def _local_combined_regions(data: Path, warnings: list[str]) -> list[str]:
    summary_path = data / "build_summary.json"
    if not summary_path.is_file():
        return []
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        regions = summary["regions"]
        if not isinstance(regions, list) or not all(isinstance(region, str) for region in regions):
            raise ValueError("regions must be a list of IDs")
        for region in regions:
            parts = region.split("/")
            if (
                not region or region.startswith("/") or "\\" in region
                or any(part in {"", ".", ".."} for part in parts)
            ):
                raise ValueError(f"unsafe region ID {region!r}")
        return regions
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        _warn(warnings, summary_path, data, f"could not read combined region selection ({type(exc).__name__})")
        return []


def build_local_catalog(data_dir: Path) -> dict:
    """Describe only cached local sources and region outputs on disk."""
    data = Path(data_dir)
    warnings: list[str] = []
    layers = []
    for layer in (
        _pointcloud_layer(data, warnings),
        *_raster_layer(data, warnings),
        _council_outline_layer(data, warnings),
    ):
        if layer:
            layers.append(layer)

    combined_regions = _local_combined_regions(data, warnings)
    analysis, _ = _combined_analysis_layers(
        combined_regions, data / "out", data, warnings, url_prefix="data/",
    )
    layers.extend(analysis)

    out_root = data / "out"
    if out_root.is_dir():
        for cells_path in sorted(out_root.rglob("cells.json")):
            region = cells_path.parent.relative_to(out_root).as_posix()
            bounds = _cell_bounds(cells_path, warnings, data)
            if bounds is None:
                continue
            vector_path = data / "regions" / region / "solar_potential.geojson"
            if not vector_path.is_file():
                warnings.append(f"{_relative(cells_path, data)}: derived coverage exists but regional solar-potential vector is absent")
                continue
            try:
                url = _relative(vector_path, data.parent)
            except ValueError:
                _warn(warnings, vector_path, data, "vector is outside the static server root")
                continue
            if not _SAFE_FILE.fullmatch(url):
                _warn(warnings, vector_path, data, "unsafe vector URL omitted")
                continue
            feature = _feature(f"solar-potential:{region}", region, bounds, [region],
                               source="solar_potential", status="built",
                               cached_path=_relative(vector_path, data))
            layer = _layer(
                f"derived-solar-potential:{region}",
                f"Solar potential: {region}", "derived", "vector",
                [feature], _COLOURS["buildings"],
                "Per-building solar-potential GeoJSON in EPSG:4326.",
                url=url,
            )
            if layer:
                layers.append(layer)

    return {
        "version": 1,
        "layers": layers,
        "warnings": warnings,
        "availability": {"source": "local", "local_cache": True},
    }


def unavailable_catalog(source: str) -> dict:
    return {
        "version": 1,
        "layers": [],
        "warnings": [f"Local source-cache footprints are unavailable for the {source} dataset."],
        "availability": {"source": source, "local_cache": False},
    }


def _safe_static_url(url: str) -> bool:
    return bool(_SAFE_FILE.fullmatch(url)) and not any(part in {".", ".."} for part in url.split("/"))


def build_static_catalog(regions: list[str], out_root: Path, available_files: Path) -> dict:
    """Build the published catalogue solely from the selected emitted set."""
    warnings = [
        "This combined dataset contains emitted map artifacts only; local source-cache footprints are unavailable."
    ]
    features = []
    analysis_layers, region_bounds = _combined_analysis_layers(
        list(regions), Path(out_root), Path(available_files), warnings, url_prefix="data/",
    )
    for region, bounds in region_bounds.items():
        features.append(_feature(f"coverage:{region}", region, bounds, [region],
                                 source="emitted_cells", status="emitted"))

    layers = list(analysis_layers)
    footprint = _layer(
        "derived-building-coverage", "Derived building coverage", "derived", "footprint",
        features, _COLOURS["coverage"],
        "Bounding footprints of non-empty cell coverage emitted for the selected regions.",
    )
    if footprint:
        layers.append(footprint)

    return {
        "version": 1,
        "layers": layers,
        "warnings": warnings,
        "availability": {"source": "static", "local_cache": False},
    }
