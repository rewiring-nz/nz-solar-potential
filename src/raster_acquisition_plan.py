"""Read-only planning for native STAC raster assets.

This module does not download raster payloads or infer that item bounding boxes
are valid-data coverage. The operational cache and build admission are separate.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Mapping
from urllib.parse import unquote, urljoin, urlparse

from shapely.errors import GEOSException
from shapely.geometry import box, shape
from shapely.ops import unary_union

NATIONAL_DSM_COLLECTION = (
    "https://nz-elevation.s3-ap-southeast-2.amazonaws.com/"
    "new-zealand/new-zealand/dsm_1m/2193/collection.json"
)


@dataclass(frozen=True)
class RasterSource:
    """A selected collection and its explicitly chosen analytical asset key."""

    collection_url: str
    asset_key: str
    expected_collection_id: str | None = None
    index_layer: int | None = None


@dataclass(frozen=True)
class RasterRequest:
    """One selected regional raster input, or its explicitly reported fallback."""

    region: str
    role: str
    footprints: tuple[tuple[float, float, float, float], ...]
    source: RasterSource | None
    layer_id: int | None
    fallback_reason: str | None = None
    survey_name: str | None = None
    mapping_evidence: str | None = None
    allow_partial: bool = False


def _read_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError(f"STAC document is not an object: {url}")
    return value


def resolve_asset_sizes(assets: list[dict]) -> None:
    """Resolve missing transfer sizes using HTTP headers, without fetching payloads."""
    for asset in assets:
        if asset.get("size_bytes") is not None:
            continue
        url = asset.get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ValueError(f"Cannot determine transfer size for raster tile {asset.get('item_id')}")
        request = urllib.request.Request(url, method="HEAD", headers={"Accept-Encoding": "identity"})
        with urllib.request.urlopen(request, timeout=60) as response:
            length = response.headers.get("Content-Length")
        if not isinstance(length, str) or not length.isascii() or not length.isdecimal() or int(length) <= 0:
            raise ValueError(f"No valid Content-Length for raster tile {asset.get('item_id')}")
        asset["size_bytes"] = int(length)
        asset["size_source"] = "http-head"


def _bounds(value: object, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise ValueError(f"{label} must be a four-coordinate WGS84 bounding box")
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) for v in value):
        raise ValueError(f"{label} contains invalid coordinates")
    west, south, east, north = value
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError(f"{label} is not a valid WGS84 bounding box")
    return west, south, east, north


def _read_items(urls: list[str], get_json: Callable[[str], dict]) -> list[dict]:
    """Fetch public item metadata concurrently while preserving link order."""
    if get_json is not _read_json or len(urls) < 2:
        return [get_json(url) for url in urls]
    with ThreadPoolExecutor(max_workers=8) as executor:
        return list(executor.map(get_json, urls))


def discover_assets(
    source: RasterSource, *, get_json: Callable[[str], dict] = _read_json,
    footprints: list | None = None,
) -> list[dict]:
    """Read collection and item metadata; never fetch a raster payload."""
    collection_url = source.collection_url
    if not collection_url or not source.asset_key:
        raise ValueError("A collection URL and explicit raster asset key are required")
    collection = get_json(collection_url)
    collection_id = collection.get("id")
    if not isinstance(collection_id, str) or not collection_id:
        raise ValueError(f"STAC collection has no id: {collection_url}")
    if source.expected_collection_id is not None and collection_id != source.expected_collection_id:
        raise ValueError(f"STAC collection id does not match configured mapping: {collection_url}")
    links = collection.get("links")
    if not isinstance(links, list):
        raise ValueError(f"STAC collection has no links list: {collection_url}")
    item_urls = sorted({
        urljoin(collection_url, link["href"])
        for link in links
        if isinstance(link, dict) and link.get("rel") == "item"
        and isinstance(link.get("href"), str) and link["href"]
    })
    if not item_urls:
        raise ValueError(f"STAC collection has no item links: {collection_url}")
    if source.index_layer is not None and footprints is not None:
        from src.imagery_tiles import intersecting_item_urls
        item_urls = intersecting_item_urls(source.index_layer, item_urls, footprints)
    result = []
    seen = set()
    for item_url, item in zip(item_urls, _read_items(item_urls, get_json)):
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id or item_id in seen:
            raise ValueError(f"Missing or duplicate STAC item id in {item_url}")
        if source.index_layer is not None and item_id != PurePosixPath(unquote(urlparse(item_url).path)).stem:
            raise ValueError("Indexed imagery STAC item identity does not match its tile link")
        seen.add(item_id)
        assets = item.get("assets")
        asset = assets.get(source.asset_key) if isinstance(assets, dict) else None
        if not isinstance(asset, dict) or not isinstance(asset.get("href"), str) or not asset["href"]:
            raise ValueError(f"STAC item {item_id} lacks asset {source.asset_key!r}")
        media_type = asset.get("type")
        if not isinstance(media_type, str) or not (
            media_type.startswith("image/tiff") or media_type.startswith("application/geotiff")
        ):
            raise ValueError(f"STAC asset {source.asset_key!r} in {item_id} is not a GeoTIFF")
        if item.get("collection") not in (None, collection_id):
            raise ValueError(f"STAC item {item_id} belongs to a different collection")
        size = asset.get("file:size")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size <= 0):
            raise ValueError(f"STAC asset {item_id} has an invalid file:size")
        properties = item.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        result.append({
            "collection_url": collection_url,
            "collection_id": collection_id,
            "item_id": item_id,
            "asset_key": source.asset_key,
            "url": urljoin(item_url, asset["href"]),
            "bbox_wgs84": _bounds(item.get("bbox"), f"STAC item {item_id} bbox"),
            "size_bytes": size,
            "checksum": asset.get("file:checksum"),
            "revision": asset.get("updated", properties.get("updated")),
            "datetime": properties.get("datetime"),
            "start_datetime": properties.get("start_datetime"),
            "end_datetime": properties.get("end_datetime"),
            "native_crs": asset.get("proj:code", asset.get("proj:epsg",
                properties.get("proj:code", properties.get("proj:epsg")))),
            "resolution": asset.get("gsd", properties.get("gsd")),
            "license": collection.get("license"),
            "providers": collection.get("providers"),
        })
    return result


def plan_assets(
    source: RasterSource,
    footprints: Mapping[str, list[tuple[float, float, float, float]]],
    *, get_json: Callable[[str], dict] = _read_json,
) -> dict:
    """Plan intersecting item assets for individual regions, deduplicated."""
    regions = {}
    for name, boxes in footprints.items():
        if not isinstance(name, str) or not name or not boxes:
            raise ValueError("Each region needs a name and at least one footprint")
        regions[name] = [box(*_bounds(bounds, f"{name} footprint")) for bounds in boxes]
    if not regions:
        raise ValueError("At least one region is required")
    assets = discover_assets(source, get_json=get_json,
                             **({"footprints": [list(geometry.bounds)
                                                for geometries in regions.values()
                                                for geometry in geometries]}
                                if source.index_layer is not None else {}))
    selected = []
    for asset in assets:
        extent = box(*asset["bbox_wgs84"])
        matches = sorted(name for name, geometries in regions.items()
                         if any(extent.intersects(geometry) for geometry in geometries))
        if matches:
            selected.append({**asset, "regions": matches, "cache_state": "unknown"})
    return {
        "source": {"collection_url": source.collection_url, "asset_key": source.asset_key},
        "footprints_wgs84": {
            name: [list(geometry.bounds) for geometry in geometries]
            for name, geometries in regions.items()
        },
        "assets": selected,
        "regions_without_intersecting_assets": sorted(
            name for name in regions if not any(name in asset["regions"] for asset in selected)),
        "footprints_without_intersecting_assets": {
            name: [list(geometry.bounds) for geometry in geometries
                   if not any(box(*asset["bbox_wgs84"]).intersects(geometry)
                              for asset in selected)]
            for name, geometries in regions.items()
        },
        "known_asset_bytes": sum(asset["size_bytes"] or 0 for asset in selected),
        "unknown_size_count": sum(asset["size_bytes"] is None for asset in selected),
        "coverage": "item bounding boxes only; valid-data coverage not checked",
    }


def _describe_requests(requests: list[RasterRequest]) -> tuple[dict, list, list]:
    """Validate selected requests without reading upstream asset metadata."""
    grouped: dict[RasterSource, list[RasterRequest]] = {}
    source_identities: dict[tuple[str, str], RasterSource] = {}
    seen_requests: set[tuple[str, str]] = set()
    fallbacks = []
    inputs = []
    for request in requests:
        if not isinstance(request.region, str) or not request.region:
            raise ValueError("Each raster request needs a region")
        if request.role not in ("dsm", "imagery", "reference_imagery", "wide_dem"):
            raise ValueError(f"Unknown raster role: {request.role!r}")
        if (request.region, request.role) in seen_requests:
            raise ValueError(f"Duplicate raster request for {request.region}: {request.role}")
        seen_requests.add((request.region, request.role))
        if type(request.allow_partial) is not bool or (
                request.allow_partial and request.role not in ("imagery", "reference_imagery")):
            raise ValueError("Partial coverage is only supported for imagery")
        if request.source is not None and request.source.index_layer is not None and (
                type(request.source.index_layer) is not int or request.source.index_layer <= 0):
            raise ValueError("Invalid raster tile index layer")
        if not request.footprints:
            raise ValueError(f"{request.region} {request.role} has no acquisition footprints")
        footprints = [list(bounds) for bounds in sorted({
            _bounds(bounds, f"{request.region} {request.role} footprint")
            for bounds in request.footprints
        })]
        if request.layer_id is not None and (
            isinstance(request.layer_id, bool) or not isinstance(request.layer_id, int)
            or request.layer_id <= 0
        ):
            raise ValueError("LINZ layer ID must be a positive integer or absent")
        for value, label in ((request.survey_name, "Survey name"),
                             (request.mapping_evidence, "Mapping evidence")):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{label} must be a non-empty string or absent")
        if request.source is None:
            if (request.layer_id is None or not isinstance(request.fallback_reason, str)
                    or not request.fallback_reason.strip()):
                raise ValueError("Export fallback requires a layer ID and an explicit reason")
            fallbacks.append({
                "region": request.region, "role": request.role,
                "layer_id": request.layer_id, "footprints_wgs84": footprints,
                "reason": request.fallback_reason, "estimated_bytes": None,
            })
        else:
            if request.fallback_reason is not None:
                raise ValueError("A STAC request cannot also specify an export fallback reason")
            if (not isinstance(request.source, RasterSource)
                    or not isinstance(request.source.collection_url, str)
                    or not request.source.collection_url
                    or not isinstance(request.source.asset_key, str)
                    or not request.source.asset_key
                    or (request.source.expected_collection_id is not None
                        and (not isinstance(request.source.expected_collection_id, str)
                             or not request.source.expected_collection_id))):
                raise ValueError("A collection URL and explicit raster asset key are required")
            identity = (request.source.collection_url, request.source.asset_key)
            previous = source_identities.get(identity)
            if previous is not None and previous != request.source:
                raise ValueError("Conflicting collection identity for the same raster source")
            source_identities[identity] = request.source
            grouped.setdefault(request.source, []).append(request)
        inputs.append({
            "region": request.region, "role": request.role,
            "footprints_wgs84": footprints, "layer_id": request.layer_id,
            "survey_name": request.survey_name, "mapping_evidence": request.mapping_evidence,
            "source": ({"collection_url": request.source.collection_url,
                        "asset_key": request.source.asset_key,
                        "expected_collection_id": request.source.expected_collection_id,
                        **({"index_layer": request.source.index_layer}
                           if request.source.index_layer is not None else {})}
                       if request.source else None),
            **({"allow_partial": True} if request.allow_partial else {}),
        })
    if not requests:
        raise ValueError("At least one raster request is required")
    return grouped, sorted(inputs, key=lambda item: (item["region"], item["role"])), \
        sorted(fallbacks, key=lambda item: (item["region"], item["role"]))


def describe_requests(requests: list[RasterRequest]) -> dict:
    """Describe selected inputs without resolving or refreshing STAC assets."""
    _, inputs, fallbacks = _describe_requests(requests)
    return {"inputs": inputs, "export_fallbacks": fallbacks}


def plan_batch(
    requests: list[RasterRequest], *, get_json: Callable[[str], dict] = _read_json,
) -> dict:
    """Plan a batch of selected area/role inputs without downloading rasters.

    A request without a verified STAC source remains an export fallback. No
    survey is inferred from a similar title or substituted across areas.
    """
    grouped, inputs, fallbacks = _describe_requests(requests)

    assets = []
    coverage = []
    footprint_coverage = []
    capture_coverage = []
    capture_surveys = []
    for source, source_requests in grouped.items():
        # A region may require the same collection for different input roles.
        by_region: dict[str, list[tuple[float, float, float, float]]] = {}
        for request in source_requests:
            by_region.setdefault(request.region, []).extend(request.footprints)
        planned = plan_assets(source, by_region, get_json=get_json)
        if source.collection_url == NATIONAL_DSM_COLLECTION:
            collection = get_json(source.collection_url)
            if (source.expected_collection_id is not None
                    and collection.get("id") != source.expected_collection_id):
                raise ValueError("National DSM collection identity changed during planning")
            collection_assets = collection.get("assets")
            capture = collection_assets.get("capture_area") if isinstance(collection_assets, dict) else None
            if (not isinstance(capture, dict) or not isinstance(capture.get("href"), str)
                    or not capture["href"]):
                raise ValueError("National DSM collection has no capture-area metadata")
            capture_url = urljoin(source.collection_url, capture["href"])
            document = get_json(capture_url)
            if document.get("type") == "Feature":
                geometries = [document.get("geometry")]
            elif document.get("type") == "FeatureCollection" and isinstance(document.get("features"), list):
                features = document["features"]
                if any(not isinstance(feature, dict) for feature in features):
                    raise ValueError("National DSM capture-area feature is invalid")
                geometries = [feature.get("geometry") for feature in features]
            else:
                raise ValueError("National DSM capture-area metadata is not GeoJSON")
            if not geometries or any(not isinstance(geometry, dict) for geometry in geometries):
                raise ValueError("National DSM capture-area geometry is missing")
            try:
                area = unary_union([shape(geometry) for geometry in geometries])
            except (TypeError, ValueError, KeyError, GEOSException) as exc:
                raise ValueError("National DSM capture-area geometry is invalid") from exc
            if area.is_empty or not area.is_valid:
                raise ValueError("National DSM capture-area geometry is empty or invalid")
            dates = collection_assets.get("capture_dates")
            if (not isinstance(dates, dict) or not isinstance(dates.get("href"), str)
                    or not dates["href"]):
                raise ValueError("National DSM collection has no capture-dates metadata")
            dates_url = urljoin(source.collection_url, dates["href"])
            date_document = get_json(dates_url)
            features = date_document.get("features")
            if date_document.get("type") != "FeatureCollection" or not isinstance(features, list):
                raise ValueError("National DSM capture-dates metadata is not GeoJSON")
            survey_geometries = []
            for feature in features:
                if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict) \
                        or not isinstance(feature.get("properties"), dict):
                    raise ValueError("National DSM capture-dates feature is invalid")
                properties = feature["properties"]
                if any(not isinstance(properties.get(key), str) or not properties[key]
                       for key in ("id", "title", "source")):
                    raise ValueError("National DSM capture-dates source identity is missing")
                try:
                    geometry = shape(feature["geometry"])
                except (TypeError, ValueError, KeyError, GEOSException) as exc:
                    raise ValueError("National DSM survey geometry is invalid") from exc
                if geometry.is_empty or not geometry.is_valid:
                    raise ValueError("National DSM survey geometry is empty or invalid")
                survey_geometries.append((geometry, {key: properties.get(key)
                                          for key in ("id", "title", "source", "flown_from", "flown_to")}))
            for request in source_requests:
                for footprint in request.footprints:
                    requested = box(*footprint)
                    state = ("covered" if area.covers(requested) else
                             "intersects" if area.intersects(requested) else "outside")
                    capture_coverage.append({
                        "region": request.region, "role": request.role,
                        "footprint_wgs84": list(footprint), "state": state,
                        "source_url": capture_url, "checksum": capture.get("file:checksum"),
                    })
                    surveys = sorted((properties for geometry, properties in survey_geometries
                                      if geometry.intersects(requested)), key=lambda item: item["id"])
                    capture_surveys.append({
                        "region": request.region, "role": request.role,
                        "footprint_wgs84": list(footprint), "surveys": surveys,
                        "source_url": dates_url, "checksum": dates.get("file:checksum"),
                    })
        for asset in planned["assets"]:
            extent = box(*asset["bbox_wgs84"])
            uses = sorted(
                ({"region": request.region, "role": request.role,
                  "layer_id": request.layer_id} for request in source_requests
                 if any(extent.intersects(box(*footprint)) for footprint in request.footprints)),
                key=lambda use: (use["region"], use["role"]),
            )
            assets.append({**asset, "uses": uses})
        for request in source_requests:
            for footprint in request.footprints:
                area = box(*footprint)
                extents = [box(*asset["bbox_wgs84"]) for asset in planned["assets"]
                           if box(*asset["bbox_wgs84"]).intersects(area)]
                if not extents:
                    state = "none"
                    coverage.append({"region": request.region, "role": request.role,
                                     "footprint_wgs84": list(footprint)})
                elif unary_union(extents).covers(area):
                    state = "fully_bounded"
                else:
                    state = "partial"
                footprint_coverage.append({"region": request.region, "role": request.role,
                                           "footprint_wgs84": list(footprint),
                                           "bbox_coverage": state})

    assets.sort(key=lambda asset: (asset["collection_url"], asset["item_id"], asset["asset_key"]))
    fallbacks.sort(key=lambda item: (item["region"], item["role"]))
    coverage.sort(key=lambda item: (item["region"], item["role"], item["footprint_wgs84"]))
    footprint_coverage.sort(
        key=lambda item: (item["region"], item["role"], item["footprint_wgs84"]))
    capture_coverage.sort(key=lambda item: (item["region"], item["role"], item["footprint_wgs84"]))
    capture_surveys.sort(key=lambda item: (item["region"], item["role"], item["footprint_wgs84"]))
    return {
        "assets": assets,
        "inputs": inputs,
        "export_fallbacks": fallbacks,
        "footprints_without_intersecting_assets": coverage,
        "footprint_bbox_coverage": footprint_coverage,
        "capture_coverage": capture_coverage,
        "capture_surveys": capture_surveys,
        "known_asset_bytes": sum(asset["size_bytes"] or 0 for asset in assets),
        "unknown_size_count": sum(asset["size_bytes"] is None for asset in assets),
        "unknown_export_size_count": len(fallbacks),
        "coverage": "item bounding boxes only; valid-data coverage not checked",
    }


def region_manifest(plan: dict, region: str, processing: dict) -> dict:
    """Fingerprint planned source metadata, not the contents of acquired files."""
    inputs = plan.get("inputs")
    assets = plan.get("assets")
    if not isinstance(inputs, list) or not isinstance(assets, list):
        raise ValueError("A batch plan with inputs and assets is required")
    if not isinstance(region, str) or not region or not isinstance(processing, dict):
        raise ValueError("A region and processing parameters are required")
    region_inputs = [item for item in inputs if item["region"] == region]
    if not region_inputs:
        raise ValueError(f"No raster inputs planned for {region!r}")
    selected_assets = []
    for asset in assets:
        uses = [use for use in asset["uses"] if use["region"] == region]
        if not uses:
            continue
        selected_assets.append({
            "collection_url": asset["collection_url"],
            "collection_id": asset["collection_id"],
            "item_id": asset["item_id"],
            "asset_key": asset["asset_key"],
            "url": asset["url"],
            "bbox_wgs84": asset["bbox_wgs84"],
            "checksum": asset["checksum"],
            "revision": asset["revision"],
            "datetime": asset["datetime"],
            "start_datetime": asset["start_datetime"], "end_datetime": asset["end_datetime"],
            "native_crs": asset["native_crs"],
            "resolution": asset["resolution"],
            "license": asset["license"],
            "providers": asset["providers"],
            "roles": sorted(use["role"] for use in uses),
        })
    manifest = {
        "schema_version": 1,
        "region": region,
        "inputs": region_inputs,
        "assets": selected_assets,
        "processing": processing,
        "content_verified": False,
    }
    try:
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Source manifest must contain valid JSON values") from exc
    return {**manifest, "fingerprint": hashlib.sha256(encoded).hexdigest()}
