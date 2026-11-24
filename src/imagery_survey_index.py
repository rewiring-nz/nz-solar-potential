"""Local snapshot of the LINZ imagery survey index and coverage ranking."""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

LAYER = 95677
MAX_FEATURES = 10000
WFS = "https://data.linz.govt.nz/services;key={key}/wfs"


def collection_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Imagery survey has no public S3 collection")
    parsed = urlparse(value)
    if (parsed.scheme != "s3" or parsed.netloc != "nz-imagery"
            or not parsed.path.endswith("/rgb/2193/") or parsed.query or parsed.fragment
            or ".." in parsed.path.split("/")):
        raise ValueError("Imagery survey is not a public RGB EPSG:2193 collection")
    return "https://nz-imagery.s3-ap-southeast-2.amazonaws.com" + parsed.path + "collection.json"


def cached_index(data: Path, api_key: str = "", *, refresh: bool = False):
    import fcntl
    directory = Path(data) / "pipeline_web" / "cache"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "imagery-surveys.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return load_index(directory / "imagery-surveys.gpkg", api_key, refresh=refresh)


def ranked_for_bounds(frame, footprints: list) -> list[dict]:
    import geopandas as gpd
    from shapely.geometry import box
    areas = gpd.GeoSeries([box(*bounds) for bounds in footprints], crs=4326).segmentize(0.01).to_crs(2193)
    return rank_surveys(frame, areas.union_all())


def fetch_index(api_key: str) -> dict:
    """Fetch the national index in NZTM; do not expose keyed URLs on errors."""
    if not api_key:
        raise ValueError("A LINZ API key is required to fetch the imagery survey index")
    params = {
        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
        "typeName": f"data.linz.govt.nz:layer-{LAYER}", "outputFormat": "json",
        "maxFeatures": MAX_FEATURES, "srsName": "EPSG:2193",
    }
    try:
        response = requests.get(WFS.format(key=api_key), params=params, timeout=180)
        response.raise_for_status()
        document = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"LINZ imagery survey index request failed ({type(exc).__name__})") from None
    if not isinstance(document, dict):
        raise ValueError("LINZ imagery survey index response is not an object")
    features = document.get("features")
    crs = (document.get("crs") or {}).get("properties", {}).get("name", "")
    if (document.get("type") != "FeatureCollection" or not isinstance(features, list)
            or not features or len(features) >= MAX_FEATURES or not crs.endswith("::2193")):
        raise ValueError("LINZ imagery survey index is empty, truncated, or not EPSG:2193")
    return document


def load_index(path: Path, api_key: str = "", *, refresh: bool = False):
    """Reuse a GeoPackage indefinitely; explicitly refresh to replace it."""
    import geopandas as gpd

    path = Path(path)
    if path.exists() and not refresh:
        frame = gpd.read_file(path)
    else:
        document = fetch_index(api_key)
        frame = gpd.GeoDataFrame.from_features(document["features"], crs="EPSG:2193")
        _validate(frame)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".imagery-index-", suffix=".gpkg", dir=path.parent)
        os.close(fd)
        try:
            os.unlink(temporary)
            frame.to_file(
                temporary, driver="GPKG", layer="imagery_surveys", index=False,
                metadata={"source": f"LINZ layer {LAYER}",
                          "retrieved_at": str(document.get("timeStamp", "unknown"))},
            )
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    _validate(frame)
    return frame


def _validate(frame) -> None:
    required = {"imagery_survey_id", "name", "imagery_id", "index_id",
                "ground_sample_distance", "flown_to", "s3", "geometry"}
    if (frame.empty or not required.issubset(frame.columns) or frame.crs is None
            or frame.crs.to_epsg() != 2193):
        raise ValueError("Imagery survey index is missing required fields or EPSG:2193 geometry")
    if frame.geometry.isna().any() or frame.geometry.is_empty.any() or not frame.geometry.is_valid.all():
        raise ValueError("Imagery survey index contains missing, empty, or invalid geometries")


def rank_surveys(frame, area) -> list[dict]:
    """Rank full coverage first, then detail and recency; partial coverage is explicit."""
    from shapely.geometry.base import BaseGeometry

    _validate(frame)
    if (not isinstance(area, BaseGeometry) or area.is_empty or not area.is_valid
            or area.area <= 0):
        raise ValueError("A valid non-empty EPSG:2193 area is required")
    matches = []
    for row in frame.itertuples(index=False):
        geometry = row.geometry
        if geometry is None or geometry.is_empty or not geometry.is_valid:
            continue
        intersection = geometry.intersection(area).area
        if intersection <= 0:
            continue
        resolution = row.ground_sample_distance
        if (isinstance(resolution, bool) or not isinstance(resolution, (int, float))
                or not math.isfinite(resolution) or resolution <= 0):
            continue
        imagery_id = row.imagery_id
        if (isinstance(imagery_id, bool) or not isinstance(imagery_id, (int, float))
                or not math.isfinite(imagery_id) or imagery_id <= 0
                or int(imagery_id) != imagery_id):
            continue
        index_id = row.index_id
        if (not isinstance(index_id, (int, float)) or not math.isfinite(index_id)
                or index_id <= 0 or int(index_id) != index_id):
            continue
        try:
            url = collection_url(row.s3)
        except ValueError:
            continue
        fraction = min(1.0, intersection / area.area)
        flown_to = str(row.flown_to or "")
        matches.append({
            "survey_id": row.imagery_survey_id, "name": row.name,
            "imagery_id": int(imagery_id), "index_id": int(index_id), "s3": row.s3,
            "collection_url": url,
            "resolution_m": float(resolution), "flown_to": flown_to,
            "coverage_fraction": fraction, "fully_covered": geometry.covers(area),
        })
    matches.sort(key=lambda item: (
        not item["fully_covered"], item["resolution_m"],
        -int(item["flown_to"][:10].replace("-", "")) if
        len(item["flown_to"]) >= 10 and item["flown_to"][:10].replace("-", "").isdigit() else 0,
        item["imagery_id"], str(item["survey_id"]),
    ))
    return matches
