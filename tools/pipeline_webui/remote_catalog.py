"""Read-only catalog of published and developer-preview regions.

Everything here is public HTTPS from Google Cloud Storage, so viewing the
published or developer targets needs no credentials and costs nothing beyond
public egress of a few KB of JSON per region.

- Published: ``dataBase`` from this checkout's ``site-config.js`` (the version
  the live site serves) plus the version folders in the tiles bucket.
- Developer: every ``devel/<alias>/`` folder in the preview bucket, each
  alias's ``site-config.js`` ``dataBase``, then that run's summaries.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from src.region_ids import validate
from tools.pipeline_webui.common import ROOT

CACHE_SECONDS = 10 * 60
LIVE_SITE = "https://rewiring-nz.github.io/nz-solar-potential/preview.html"
GCS = "https://storage.googleapis.com"
LISTING = GCS + "/storage/v1/b/{bucket}/o?{query}"
_DATA_BASE = re.compile(r"""dataBase\s*:\s*["']([^"']+)["']""")
_DATA_VERSION = re.compile(r"""dataVersion\s*:\s*["']([^"']+)["']""")
_ALIAS = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?")
SOURCES = ("developer", "published")

HttpGet = Callable[[str], bytes]


def _default_get(url: str) -> bytes:
    if not url.startswith(GCS + "/"):
        raise ValueError("remote catalog reads only storage.googleapis.com")
    req = urllib.request.Request(url, headers={"User-Agent": "nz-solar-pipeline-web"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed https host
        return resp.read(4 * 1024 * 1024)


def parse_site_config(text: str) -> dict:
    base = _DATA_BASE.search(text)
    version = _DATA_VERSION.search(text)
    return {"data_base": base.group(1) if base else None,
            "data_version": version.group(1) if version else None}


def _bucket(data_base: str) -> str | None:
    prefix = GCS + "/"
    if not data_base or not data_base.startswith(prefix):
        return None
    return data_base[len(prefix):].split("/", 1)[0]


def _with_view(url: str, centroid) -> str:
    if not centroid or len(centroid) != 2:
        return url
    lon, lat = centroid
    return f"{url}?lat={float(lat):.5f}&lng={float(lon):.5f}&z=14"


class RemoteCatalog:
    def __init__(self, http_get: HttpGet | None = None, preview_bucket: str | None = None,
                 site_config_text: Callable[[], str] | None = None,
                 now: Callable[[], float] = time.time, workers: int = 8):
        self._get = http_get or _default_get
        self._preview_bucket = preview_bucket
        self._site_config_text = site_config_text or (
            lambda: (ROOT / "site-config.js").read_text(encoding="utf-8"))
        self._now = now
        self._workers = workers
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}

    def preview_bucket(self) -> str:
        if self._preview_bucket is None:
            from tools import pipeline
            self._preview_bucket = pipeline._load_shared_cloud_config()["preview_bucket"]
        return self._preview_bucket.removeprefix("gs://")

    def _json(self, url: str):
        return json.loads(self._get(url).decode("utf-8"))

    def _prefixes(self, bucket: str, prefix: str = "") -> list[str]:
        query = urllib.parse.urlencode({"delimiter": "/", "prefix": prefix,
                                        "fields": "prefixes"})
        return self._json(LISTING.format(bucket=bucket, query=query)).get("prefixes", [])

    def _summaries(self, data_base: str, view_url: str, extra: dict) -> tuple[list, list]:
        """Return (features, warnings) for one served data folder."""
        summary = self._json(data_base + "data/build_summary.json")
        regions = []
        for region in summary.get("regions", []):
            try:
                regions.append(validate(region))
            except ValueError:
                continue
        warnings = []

        def one(region):
            try:
                return region, self._json(f"{data_base}data/summaries/{region}.json"), None
            except Exception as exc:  # noqa: BLE001 - reported per region
                return region, None, type(exc).__name__

        features = []
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            for region, row, err in pool.map(one, regions):
                if err or not isinstance(row, dict):
                    warnings.append(f"{region}: summary unavailable ({err or 'bad JSON'})")
                    continue
                centroid = row.get("centroid")
                if not (isinstance(centroid, list) and len(centroid) == 2):
                    warnings.append(f"{region}: summary has no centroid")
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": centroid},
                    "properties": {
                        "region": region, "state": "built",
                        "n": row.get("n"), "n_est": row.get("n_est"),
                        "kwp": row.get("kwp"), "kwh": row.get("kwh"),
                        "elevation": row.get("elevation"),
                        "built_at": row.get("built_at"), "git": row.get("git"),
                        "preview_url": _with_view(view_url, centroid),
                        **extra,
                    },
                })
        return features, warnings

    def published(self) -> dict:
        cfg = parse_site_config(self._site_config_text())
        base = cfg["data_base"]
        out = {"source": "published", "data_base": base,
               "data_version": cfg["data_version"], "site": LIVE_SITE,
               "versions": [], "warnings": []}
        bucket = _bucket(base or "")
        if not base or not bucket:
            out["warnings"].append("site-config.js has no storage.googleapis.com dataBase")
            return {**out, "type": "FeatureCollection", "features": []}
        try:
            out["versions"] = sorted(
                (p.rstrip("/") for p in self._prefixes(bucket) if re.fullmatch(r"v\d+/", p)),
                key=lambda v: int(v[1:]))
        except Exception as exc:  # noqa: BLE001
            out["warnings"].append(f"could not list {bucket} versions ({type(exc).__name__})")
        features, warnings = self._summaries(
            base, LIVE_SITE, {"source": "published", "data_version": cfg["data_version"]})
        out["warnings"].extend(warnings)
        return {**out, "type": "FeatureCollection", "features": features}

    def developer(self) -> dict:
        bucket = self.preview_bucket()
        out = {"source": "developer", "bucket": bucket, "aliases": [], "warnings": []}
        features = []
        for prefix in self._prefixes(bucket, "devel/"):
            alias = prefix.removeprefix("devel/").rstrip("/")
            if not _ALIAS.fullmatch(alias):
                continue
            folder = f"{GCS}/{bucket}/devel/{alias}/"
            entry = {"alias": alias, "preview_url": folder + "preview.html",
                     "data_base": None, "regions": 0, "built_at": None}
            out["aliases"].append(entry)
            try:
                cfg = parse_site_config(self._get(folder + "site-config.js").decode("utf-8"))
                base = cfg["data_base"]
                if not base or _bucket(base) != bucket:
                    raise ValueError("dataBase is outside the preview bucket")
                entry["data_base"] = base
                feats, warnings = self._summaries(
                    base, folder + "preview.html",
                    {"source": "developer", "alias": alias, "run": cfg["data_version"]})
            except Exception as exc:  # noqa: BLE001 - one alias must not hide the rest
                out["warnings"].append(f"{alias}: preview unavailable ({type(exc).__name__})")
                continue
            entry["regions"] = len(feats)
            entry["built_at"] = max((f["properties"]["built_at"] or "" for f in feats),
                                    default=None) or None
            out["warnings"].extend(f"{alias}: {w}" for w in warnings)
            features.extend(feats)
        return {**out, "type": "FeatureCollection", "features": features}

    def get(self, source: str, refresh: bool = False) -> dict:
        if source not in SOURCES:
            raise ValueError(f"unknown source: {source}")
        with self._lock:
            hit = self._cache.get(source)
            if hit and not refresh and self._now() - hit[0] < CACHE_SECONDS:
                return hit[1]
        result = self.published() if source == "published" else self.developer()
        result["fetched_at"] = self._now()
        with self._lock:
            self._cache[source] = (self._now(), result)
        return result
