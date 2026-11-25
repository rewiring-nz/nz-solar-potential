"""National catalogue of buildable LINZ localities for the Build data tree.

One record per LINZ NZ Suburbs and Localities feature (layer
config.LINZ_SUBURBS_LOCALITIES_LAYER) that has at least one building outline
(layer config.LINZ_BUILDING_OUTLINES_LAYER) under src.localities.outline_filter,
the same filter a build uses. Lakes and inland bays are dropped before
counting, as tools/find_area.py does; coastal bays and islands stay only if
LINZ assigns them buildings.

A refresh takes about a minute: one attribute-only WFS 1.0.0 request for all
localities (about 6,600 features, 0.2 s), then one WFS 2.0.0
resultType=hits request per locality (about 0.09 s each, COUNT_WORKERS in
parallel), then simplified polygons for the buildable ones in batches of
GEOMETRY_BATCH ids. Because it is that cheap, the catalogue is prebuilt and
cached under data/pipeline_web/cache/ rather than computed on demand.

Outputs:
  national_catalog.json      regions with ids, names, building counts, bbox
  national_localities.geojson simplified polygons (about 100 m) for map colour

The API key is never written to either file or to any error text.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src import localities as L  # noqa: E402
from tools.pipeline_webui.common import DATA, read_json, web_dir, write_json_atomic  # noqa: E402

SCHEMA = 1
WATER_TYPES = ("Lake", "Inland Bay")
COUNT_WORKERS = 8
GEOMETRY_BATCH = 150
GEOMETRY_WORKERS = 4
SIMPLIFY_DEGREES = 0.001  # about 100 m: 4.9 MB nationally; zoomed in, the 5 m bbox layer draws detail
CATALOG_FILE = "national_catalog.json"
GEOMETRY_FILE = "national_localities.geojson"
_NUMBER_MATCHED = re.compile(r'numberMatched="(\d+)"')


class CatalogError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def catalog_path(data: Path = DATA) -> Path:
    return web_dir(data) / "cache" / CATALOG_FILE


def geometry_path(data: Path = DATA) -> Path:
    return web_dir(data) / "cache" / GEOMETRY_FILE


def _default_get(url, params, timeout):
    import requests
    return requests.get(url, params=params, timeout=timeout)


def _get(http_get, api_key: str, params: dict, timeout: int = 180):
    try:
        resp = (http_get or _default_get)(L.WFS_URL.format(key=api_key), params, timeout)
        resp.raise_for_status()
        return resp
    except Exception as exc:  # the exception text can carry the keyed URL
        raise CatalogError(f"LINZ WFS request failed ({type(exc).__name__})") from None


def fetch_localities(api_key: str, http_get: Callable | None = None) -> list[dict]:
    """Attributes of every LINZ locality, no geometry."""
    params = {
        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
        "typeName": f"data.linz.govt.nz:layer-{config.LINZ_SUBURBS_LOCALITIES_LAYER}",
        "outputFormat": "json", "maxFeatures": L.MAX_FEATURES, "cql_filter": "id > 0",
        "propertyName": ",".join(L.LOCALITY_FIELDS),
    }
    data = _get(http_get, api_key, params).json()
    feats = data.get("features") if isinstance(data, dict) else None
    if not feats:
        raise CatalogError("LINZ returned no localities")
    if len(feats) >= L.MAX_FEATURES:
        raise CatalogError("LINZ locality response was truncated")
    return [f["properties"] for f in feats]


def count_buildings(api_key: str, props: dict, http_get: Callable | None = None) -> int:
    """Building outlines a build of this locality would fetch (hits only)."""
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": f"data.linz.govt.nz:layer-{config.LINZ_BUILDING_OUTLINES_LAYER}",
        "resultType": "hits", "cql_filter": L.outline_filter(props),
    }
    text = _get(http_get, api_key, params, 60).text
    match = _NUMBER_MATCHED.search(text or "")
    if not match:
        raise CatalogError("LINZ hit count missing numberMatched")
    return int(match.group(1))


def _round(c):
    if c and isinstance(c[0], (int, float)):
        return [round(c[0], 4), round(c[1], 4)]
    return [_round(x) for x in c]


def fetch_geometries(api_key: str, ids: list[int], http_get: Callable | None = None) -> dict[int, dict]:
    """Simplified WGS84 polygons by LINZ id."""
    from shapely.geometry import mapping, shape
    params = {
        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
        "typeName": f"data.linz.govt.nz:layer-{config.LINZ_SUBURBS_LOCALITIES_LAYER}",
        "outputFormat": "json", "maxFeatures": len(ids) + 1, "srsName": "EPSG:4326",
        "cql_filter": "id IN (" + ",".join(str(int(i)) for i in ids) + ")",
        "propertyName": "id,shape",
    }
    data = _get(http_get, api_key, params, 300).json()
    out = {}
    for feat in (data.get("features") or []) if isinstance(data, dict) else []:
        if not feat.get("geometry"):
            continue
        simple = mapping(shape(feat["geometry"]).simplify(SIMPLIFY_DEGREES, preserve_topology=True))
        out[int(feat["properties"]["id"])] = {"type": simple["type"],
                                              "coordinates": _round(simple["coordinates"])}
    return out


def _bounds(geometry: dict) -> list[float]:
    from shapely.geometry import shape
    w, s, e, n = shape(geometry).bounds
    return [round(w, 5), round(s, 5), round(e, 5), round(n, 5)]


def _area_km2(geometry: dict) -> float:
    from shapely.geometry import shape
    from shapely.ops import transform
    from pyproj import Transformer
    project = Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True).transform
    return round(transform(project, shape(geometry)).area / 1e6, 3)


def record(props: dict, ambiguous: set[int], buildings: int) -> dict:
    region = L.display_region_id(props, ambiguous)
    ta, town, _ = region.split("/")
    return {
        "region": region, "linz_id": props["id"], "name": props.get("name"),
        "type": props.get("type"), "town": props.get("major_name") or None,
        "council": L.territorial_authorities(props)[0] if L.territorial_authorities(props) else None,
        "councils": L.territorial_authorities(props), "ta_slug": ta, "town_slug": town,
        "buildings": buildings,
    }


def build(api_key: str, http_get: Callable | None = None, geometry: bool = True,
          progress: Callable[[str, int, int], None] | None = None,
          workers: int = COUNT_WORKERS) -> tuple[dict, dict | None]:
    """(catalogue, geometry FeatureCollection or None)."""
    def note(stage, done, total):
        if progress:
            progress(stage, done, total)

    t0 = time.time()
    localities = fetch_localities(api_key, http_get)
    ambiguous = L.ambiguous_ids(localities)
    candidates = [p for p in localities if p.get("type") not in WATER_TYPES]
    note("counting buildings", 0, len(candidates))
    counts: dict[int, int] = {}
    lock = threading.Lock()

    def count(p):
        n = count_buildings(api_key, p, http_get)
        with lock:
            counts[p["id"]] = n
            note("counting buildings", len(counts), len(candidates))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(count, candidates))
    regions = [record(p, ambiguous, counts[p["id"]]) for p in candidates if counts.get(p["id"])]
    regions.sort(key=lambda r: r["region"])
    if len({r["region"] for r in regions}) != len(regions):
        raise CatalogError("catalogue contains duplicate region ids", 500)

    collection = None
    if geometry:
        ids = [r["linz_id"] for r in regions]
        batches = [ids[i:i + GEOMETRY_BATCH] for i in range(0, len(ids), GEOMETRY_BATCH)]
        shapes: dict[int, dict] = {}
        note("fetching boundaries", 0, len(ids))

        def fetch(batch):
            got = fetch_geometries(api_key, batch, http_get)
            with lock:
                shapes.update(got)
                note("fetching boundaries", len(shapes), len(ids))

        with ThreadPoolExecutor(max_workers=GEOMETRY_WORKERS) as pool:
            list(pool.map(fetch, batches))
        features = []
        for r in regions:
            geom = shapes.get(r["linz_id"])
            if geom is None:
                continue
            r["bbox"] = _bounds(geom)
            r["area_km2"] = _area_km2(geom)
            r["centroid"] = [round((r["bbox"][0] + r["bbox"][2]) / 2, 5),
                             round((r["bbox"][1] + r["bbox"][3]) / 2, 5)]
            features.append({"type": "Feature", "id": r["linz_id"],
                             "properties": {"region": r["region"], "linz_id": r["linz_id"]},
                             "geometry": geom})
        collection = {"type": "FeatureCollection", "features": features}

    catalog = {
        "schema": SCHEMA,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "build_seconds": round(time.time() - t0, 1),
        "source": {"localities_layer": config.LINZ_SUBURBS_LOCALITIES_LAYER,
                   "outlines_layer": config.LINZ_BUILDING_OUTLINES_LAYER,
                   "localities_total": len(localities),
                   "water_excluded": len(localities) - len(candidates),
                   "no_buildings_excluded": len(candidates) - len(regions)},
        "regions": regions,
    }
    return catalog, collection


def save(catalog: dict, collection: dict | None, data: Path = DATA) -> None:
    if collection is not None:
        path = geometry_path(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    write_json_atomic(catalog_path(data), catalog)


def load(data: Path = DATA) -> dict | None:
    catalog = read_json(catalog_path(data))
    if not isinstance(catalog, dict) or catalog.get("schema") != SCHEMA:
        return None
    return catalog


class Refresher:
    """Runs one catalogue rebuild at a time in the background."""

    def __init__(self, data: Path, api_key_fn: Callable[[], str], http_get: Callable | None = None):
        self.data = Path(data)
        self.api_key_fn = api_key_fn
        self.http_get = http_get
        self.lock = threading.Lock()
        self.state = {"status": "idle"}

    def status(self) -> dict:
        with self.lock:
            return dict(self.state)

    def start(self) -> dict:
        with self.lock:
            if self.state.get("status") == "running":
                return dict(self.state)
            self.state = {"status": "running", "stage": "starting", "done": 0, "total": 0,
                          "started_at": time.time()}
        threading.Thread(target=self._run, daemon=True).start()
        return self.status()

    def _progress(self, stage, done, total):
        with self.lock:
            self.state.update(stage=stage, done=done, total=total)

    def _run(self):
        try:
            key = self.api_key_fn()
            if not key:
                raise CatalogError("LINZ_API_KEY is not set (environment, .env or Secret Manager)", 503)
            catalog, collection = build(key, self.http_get, progress=self._progress)
            save(catalog, collection, self.data)
            with self.lock:
                self.state = {"status": "succeeded", "built_at": catalog["built_at"],
                              "regions": len(catalog["regions"])}
        except CatalogError as exc:
            with self.lock:
                self.state = {"status": "failed", "error": str(exc)}
        except Exception as exc:  # never echo text that could carry a keyed URL
            with self.lock:
                self.state = {"status": "failed", "error": f"catalogue refresh failed ({type(exc).__name__})"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-geometry", action="store_true", help="skip locality boundaries")
    args = parser.parse_args(argv)
    from tools.pipeline import _linz_key_value
    key = _linz_key_value()
    if not key:
        print("LINZ_API_KEY is not set (environment, .env or Secret Manager)", file=sys.stderr)
        return 2

    def progress(stage, done, total):
        if total and (done % 500 == 0 or done == total):
            print(f"  {stage}: {done:,}/{total:,}", flush=True)

    catalog, collection = build(key, geometry=not args.no_geometry, progress=progress)
    save(catalog, collection)
    src = catalog["source"]
    print(f"{len(catalog['regions']):,} localities with buildings "
          f"({src['water_excluded']:,} water and {src['no_buildings_excluded']:,} without buildings "
          f"excluded) in {catalog['build_seconds']} s -> {catalog_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
