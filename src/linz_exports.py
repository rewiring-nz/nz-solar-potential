"""LINZ building outlines, one GeoPackage per territorial authority.

The LINZ Data Service Exports API (Koordinates) produces a layer cropped to a
predefined crop feature. Crop layer TA_CROP_LAYER ("Territorial Authorities")
has one feature per council, so one export is one council's building
outlines: the source of truth locality regions select from
(src/localities.py). One download per council replaces a WFS query per
locality, and every locality in that council reads from the same cached file.

Exports are cropped by the council polygon, not filtered by attribute (the
Exports API has no attribute filter). Readers therefore still select by the
outlines' own suburb_locality, town_city and territorial_authority fields.

Cache, under data/sources/linz/layer-<layer>/<council-slug>/:

  building_outlines.gpkg  the export's GeoPackage, EPSG:2193.
  manifest.json           layer, layer version and its publish time, crop
                          feature, export id, download time and feature
                          count: what the outlines came from.

A cached council is reused while LINZ still publishes the same layer version;
when the version changes it is exported again. If the version cannot be
checked, the cache is used as is.

Exports need a LINZ API key whose permissions include exports. Creating,
polling and downloading an export costs nothing.

Usage:
  python src/linz_exports.py fetch "Queenstown-Lakes District" [--refresh]
"""

import io
import json
import sys
import time
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API = "https://data.linz.govt.nz/services/api/v1.x"
BUILDING_OUTLINES_LAYER = 101290
TA_CROP_LAYER = 3036
GPKG_MIME = "application/x-ogc-gpkg"
CRS = "EPSG:2193"
SOURCES_DIR = Path(__file__).resolve().parent.parent / "data" / "sources" / "linz"
GPKG_NAME = "building_outlines.gpkg"
MANIFEST_NAME = "manifest.json"
POLL_SECONDS = 5
EXPORT_TIMEOUT_S = 30 * 60


def _slug(text: str) -> str:
    from src.localities import slug
    return slug(text)


def cache_dir(territorial_authority: str, layer: int = BUILDING_OUTLINES_LAYER) -> Path:
    return SOURCES_DIR / f"layer-{layer}" / _slug(territorial_authority)


def read_manifest(territorial_authority: str,
                  layer: int = BUILDING_OUTLINES_LAYER) -> dict | None:
    p = cache_dir(territorial_authority, layer) / MANIFEST_NAME
    return json.loads(p.read_text()) if p.exists() else None


def _get(session: requests.Session, url: str, **kw) -> requests.Response:
    resp = session.get(url, timeout=kw.pop("timeout", 60), **kw)
    resp.raise_for_status()
    return resp


def crop_feature_url(session: requests.Session, territorial_authority: str) -> str:
    """The crop feature for a council name, matched exactly, then by slug."""
    feats = _get(session, f"{API}/exports/croplayers/{TA_CROP_LAYER}/cropfeatures/").json()
    wanted = territorial_authority.strip()
    for f in feats:
        if f.get("name", "").strip().casefold() == wanted.casefold():
            return f["url"]
    for f in feats:
        if _slug(f.get("name", "")) == _slug(wanted):
            return f["url"]
    raise LookupError(f"LINZ has no territorial-authority crop feature named {wanted!r}")


def layer_version(session: requests.Session, layer: int) -> dict:
    """The layer's current published version: {"id", "published_at"}."""
    j = _get(session, f"{API}/layers/{layer}/").json()
    version = j.get("version")
    vid = version.get("id") if isinstance(version, dict) else version
    return {"id": vid, "published_at": j.get("published_at")}


def _session(api_key: str) -> requests.Session:
    s = requests.Session()
    s.headers["Authorization"] = f"key {api_key}"
    return s


def _export(session: requests.Session, layer: int, crop_url: str, name: str) -> dict:
    body = {"name": name, "crs": CRS, "formats": {"vector": GPKG_MIME},
            "items": [{"item": f"{API}/layers/{layer}/"}], "extent": crop_url}
    resp = session.post(f"{API}/exports/", json=body, timeout=120)
    if resp.status_code in (401, 403):
        raise PermissionError(
            "LINZ refused the export: the API key needs the exports permission "
            "(data.linz.govt.nz > My account > API keys).")
    if resp.status_code >= 400:
        raise RuntimeError(f"LINZ export request failed ({resp.status_code}): {resp.text[:300]}")
    export = resp.json()
    deadline = time.monotonic() + EXPORT_TIMEOUT_S
    while export.get("state") == "processing":
        if time.monotonic() > deadline:
            raise TimeoutError(f"LINZ export {export.get('id')} still processing after "
                               f"{EXPORT_TIMEOUT_S // 60} minutes")
        time.sleep(POLL_SECONDS)
        export = _get(session, export["url"]).json()
    if export.get("state") != "complete":
        raise RuntimeError(f"LINZ export {export.get('id')} ended {export.get('state')!r}")
    return export


def _download_gpkg(session: requests.Session, export: dict, dest: Path) -> None:
    # download_url redirects to a signed URL that rejects the API key header,
    # so the redirect is followed without it.
    first = session.get(export["download_url"], timeout=120, allow_redirects=False)
    if first.is_redirect or first.status_code in (301, 302, 303, 307, 308):
        resp = requests.get(first.headers["Location"], timeout=600)
    else:
        resp = first
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        members = [m for m in zf.namelist() if m.lower().endswith(".gpkg")]
        if len(members) != 1:
            raise RuntimeError(f"expected one .gpkg in LINZ export {export.get('id')}, "
                               f"found {members or 'none'}")
        tmp = dest.with_suffix(".gpkg.tmp")
        with zf.open(members[0]) as src, open(tmp, "wb") as out:
            while chunk := src.read(1 << 20):
                out.write(chunk)
        tmp.replace(dest)


def _feature_count(path: Path) -> int:
    import pyogrio
    return int(pyogrio.read_info(path)["features"])


def council_geopackage(api_key: str, territorial_authority: str, refresh: bool = False,
                       layer: int = BUILDING_OUTLINES_LAYER, log=print) -> Path:
    """Path of the cached council GeoPackage, exporting it when missing,
    when `refresh`, or when LINZ publishes a newer layer version."""
    out_dir = cache_dir(territorial_authority, layer)
    gpkg = out_dir / GPKG_NAME
    manifest = read_manifest(territorial_authority, layer)
    session = _session(api_key)
    current = None
    if manifest and gpkg.exists() and not refresh:
        try:
            current = layer_version(session, layer)
        except requests.RequestException as exc:
            log(f"  LINZ layer {layer} version check failed ({type(exc).__name__}); "
                f"using cached {territorial_authority} outlines")
            return gpkg
        if current["id"] == manifest.get("layer_version"):
            return gpkg
        log(f"  LINZ layer {layer} is now version {current['id']} "
            f"(cached {manifest.get('layer_version')}); exporting {territorial_authority} again")
    current = current or layer_version(session, layer)
    crop_url = crop_feature_url(session, territorial_authority)
    log(f"  exporting LINZ layer {layer} for {territorial_authority} (GeoPackage, {CRS})")
    export = _export(session, layer, crop_url, f"{_slug(territorial_authority)}-outlines")
    out_dir.mkdir(parents=True, exist_ok=True)
    _download_gpkg(session, export, gpkg)
    record = {
        "layer": layer,
        "layer_version": current["id"],
        "layer_published_at": current["published_at"],
        "territorial_authority": territorial_authority,
        "crop_feature": crop_url,
        "crs": CRS,
        "export_id": export.get("id"),
        "downloaded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "features": _feature_count(gpkg),
    }
    tmp = out_dir / (MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(record, indent=1))
    tmp.replace(out_dir / MANIFEST_NAME)
    log(f"  cached {record['features']:,} outlines in {gpkg}")
    return gpkg


def _sql_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def outline_where(suburb_locality: str, town_city: str | None,
                  territorial_authorities: list[str]) -> str:
    """SQL WHERE selecting a locality's outlines; the same rule as
    src/localities.outline_filter (LINZ's own assignment of each building)."""
    parts = [f"suburb_locality = {_sql_quote(suburb_locality)}"]
    if town_city:
        parts.append(f"town_city = {_sql_quote(town_city)}")
    else:
        parts.append("(town_city IS NULL OR town_city = '')")
    if territorial_authorities:
        parts.append("territorial_authority IN ("
                     + ", ".join(_sql_quote(t) for t in territorial_authorities) + ")")
    return " AND ".join(parts)


def _wfs_value(value):
    """Property values written as LINZ's WFS GeoJSON writes them, so outlines
    from either source read the same downstream."""
    import datetime as dt
    if value is None:
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%dZ") if value.time() == dt.time() else \
            value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dt.date):
        return value.strftime("%Y-%m-%dZ")
    return value


def read_outlines(gpkgs: list[Path], where: str,
                  layer: int = BUILDING_OUTLINES_LAYER) -> list[dict]:
    """GeoJSON features (EPSG:2193) matching `where` across council files,
    one per building_id, in WFS feature form."""
    import pyogrio
    from shapely.geometry import mapping
    seen: dict[int, dict] = {}
    for path in gpkgs:
        gdf = pyogrio.read_dataframe(path, where=where)
        if gdf.crs is not None and gdf.crs.to_epsg() != 2193:
            gdf = gdf.to_crs(2193)
        fields = [c for c in gdf.columns if c != gdf.geometry.name]
        for row in gdf.itertuples(index=False):
            geom = getattr(row, gdf.geometry.name)
            if geom is None or geom.is_empty:
                continue
            # The GeoPackage stores every outline as a MultiPolygon; WFS gives
            # single-part outlines as Polygon, which downstream code expects.
            if geom.geom_type == "MultiPolygon" and len(geom.geoms) == 1:
                geom = geom.geoms[0]
            props = {c: _wfs_value(getattr(row, c)) for c in fields}
            bid = props.get("building_id")
            seen[bid] = {"type": "Feature", "id": f"layer-{layer}.{bid}",
                         "geometry": mapping(geom), "geometry_name": "shape",
                         "properties": props}
    return [seen[k] for k in sorted(seen, key=lambda b: (b is None, b))]


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch", help="export and cache one council's building outlines")
    p.add_argument("territorial_authority", help='LINZ council name, e.g. "Queenstown-Lakes District"')
    p.add_argument("--refresh", action="store_true", help="export again even if cached")
    a = ap.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv()
    from src.project_secrets import linz_api_key
    api_key = linz_api_key()
    if not api_key:
        raise SystemExit("LINZ_API_KEY not set")
    try:
        path = council_geopackage(api_key, a.territorial_authority, refresh=a.refresh)
    except (LookupError, PermissionError, RuntimeError, TimeoutError) as exc:
        raise SystemExit(str(exc))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
