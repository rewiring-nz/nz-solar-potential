"""Spatially resolve LINZ imagery tile indexes to verified STAC item links."""

from __future__ import annotations

import fcntl
import os
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

INDEX_DIR = Path(__file__).resolve().parents[1] / "data" / "sources" / "linz" / "imagery_tiles"
PAGE_SIZE = 10000


def fetch_tile_index(layer: int, api_key: str) -> list[dict]:
    if not api_key:
        raise ValueError("A LINZ API key is required to fetch the imagery tile index")
    features = []
    seen = set()
    while True:
        params = {
            "service": "WFS", "version": "1.0.0", "request": "GetFeature",
            "typeName": f"data.linz.govt.nz:layer-{layer}", "outputFormat": "json",
            "maxFeatures": PAGE_SIZE, "startIndex": len(features), "srsName": "EPSG:2193",
        }
        try:
            response = requests.get(
                f"https://data.linz.govt.nz/services;key={api_key}/wfs", params=params, timeout=180)
            response.raise_for_status()
            document = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"Imagery tile index request failed ({type(exc).__name__})") from None
        if (not isinstance(document, dict) or document.get("type") != "FeatureCollection"
                or not isinstance(document.get("features"), list)
                or not (document.get("crs") or {}).get("properties", {}).get("name", "").endswith("::2193")):
            raise ValueError("Imagery tile index response is not an EPSG:2193 feature collection")
        page = document["features"]
        for feature in page:
            identifier = feature.get("id")
            if not isinstance(identifier, str) or identifier in seen:
                raise ValueError("Imagery tile index pagination returned missing or repeated feature IDs")
            seen.add(identifier)
        features.extend(page)
        if len(page) < PAGE_SIZE:
            break
    if not features:
        raise ValueError("Imagery tile index is empty")
    return features


def load_tile_index(layer: int):
    import geopandas as gpd
    from src.project_secrets import linz_api_key

    if type(layer) is not int or layer <= 0:
        raise ValueError("Invalid imagery tile index layer")
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    path = INDEX_DIR / f"{layer}.gpkg"
    with (INDEX_DIR / f"{layer}.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            frame = gpd.read_file(path)
        else:
            frame = gpd.GeoDataFrame.from_features(
                fetch_tile_index(layer, linz_api_key()), crs="EPSG:2193")
            _validate(frame)
            fd, temporary = tempfile.mkstemp(prefix=f".{layer}-", suffix=".gpkg", dir=INDEX_DIR)
            os.close(fd)
            try:
                os.unlink(temporary)
                frame.to_file(temporary, layer="tiles", driver="GPKG", index=False)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    _validate(frame)
    return frame


def _validate(frame) -> None:
    if (frame.empty or frame.crs is None or frame.crs.to_epsg() != 2193
            or frame.geometry.isna().any() or frame.geometry.is_empty.any()
            or not frame.geometry.is_valid.all()):
        raise ValueError("Imagery tile index is empty, invalid, or not EPSG:2193")


def tile_item_name(properties: dict, available: set[str]) -> str:
    """Some LDS TILENAME fields are truncated; verify full component names first."""
    fields = {str(key).upper(): str(value) for key, value in properties.items()
              if key != "geometry" and value is not None}
    candidates = []
    if all(key in fields for key in ("MAPSHEET", "SCALE", "TILE")):
        candidates.append("_".join(fields[key] for key in ("MAPSHEET", "SCALE", "TILE")))
    candidates.extend(Path(value).stem for value in fields.values())
    matches = {name for name in candidates if name in available}
    if len(matches) != 1:
        raise ValueError("Imagery tile index cannot be matched uniquely to STAC item links")
    return matches.pop()


def intersecting_item_urls(layer: int, item_urls: list[str], footprints: list) -> list[str]:
    import geopandas as gpd
    from shapely.geometry import box

    frame = load_tile_index(layer)
    areas = gpd.GeoSeries([box(*bounds) for bounds in footprints], crs=4326).segmentize(0.01).to_crs(2193)
    candidates = frame.loc[frame.intersects(areas.union_all())]
    urls = {Path(unquote(urlparse(url).path)).stem: url for url in item_urls}
    if len(urls) != len(item_urls):
        raise ValueError("Imagery collection has ambiguous item filenames")
    available = set(urls)
    names = {tile_item_name(row, available) for row in candidates.to_dict("records")}
    return sorted(urls[name] for name in names)
