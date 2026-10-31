"""LINZ NZ Suburbs and Localities as build regions.

A locality region's id is <territorial-authority>/<town-city>/<suburb-locality>
(src/region_ids.py), each segment the slug of the ASCII name in LINZ layer
config.LINZ_SUBURBS_LOCALITIES_LAYER:

  territorial_authority_ascii -> first segment. A locality listed under
      several councils ("Queenstown-Lakes District, Southland District")
      goes under the first one listed.
  major_name_ascii            -> second segment; NO_TOWN when LINZ gives no
      town or city (bays, islands, lakes and some rural localities).
  name_ascii                  -> third segment. Where two localities in the
      same council and town share a name, the LINZ id is appended
      ("halfway-bay-1234"); tools/find_area.py prints that form.

Preparing a region writes into data/regions/<id>/:

  building_outlines.geojson  LINZ building outlines whose suburb_locality,
      town_city and territorial_authority attributes match the locality.
      Selecting by attribute, not by polygon, means localities never share
      a building, and matches LINZ's own assignment of each building.
      They are read from one GeoPackage per council the locality lists,
      exported once and cached under data/sources/linz/ (src/linz_exports.py);
      WFS is the fallback when the export cannot be used, and the only
      source with SOLAR_OUTLINE_SOURCE=wfs. Both give the same selection.
  locality.json  the LINZ attributes, the outline source and its LINZ layer
      version, and the region's WGS84 bbox: the
      buildings' extent plus OUTLINE_PAD_M. The locality polygon is not
      used for the bbox -- rural localities reach far into empty land, and
      imagery is fetched for the whole bbox (~0.35 GB/km2 at 0.1 m).

No building-count ceiling is applied to locality regions.

Usage:
  python src/localities.py prepare <region-id>
"""

import json
import sys
import time
import unicodedata
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.region_ids import is_locality_id, validate

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REGIONS_DIR = DATA_DIR / "regions"
WFS_URL = "https://data.linz.govt.nz/services;key={key}/wfs"
LOCALITY_FILE = "locality.json"
NO_TOWN = "no-town"
OUTLINE_PAD_M = 150.0
MAX_FEATURES = 200_000
IMAGERY_GB_PER_KM2 = 0.35   # 0.1 m urban aerial imagery, src/fetch_regions.py
LOCALITY_FIELDS = ("id", "name", "name_ascii", "additional_name", "type",
                   "major_name", "major_name_ascii", "major_name_type",
                   "territorial_authority", "territorial_authority_ascii",
                   "population_estimate")


def slug(text: str) -> str:
    """Lower-case ASCII words joined by '-': "Hāwea Flat" -> "hawea-flat"."""
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    out, dash = [], False
    for ch in folded.lower():
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif out and not dash:
            out.append("-")
            dash = True
    return "".join(out).strip("-")[:64]


def territorial_authorities(props: dict) -> list[str]:
    """The council names a locality lists, in LINZ's order."""
    return [t.strip() for t in (props.get("territorial_authority") or "").split(",") if t.strip()]


def region_id(props: dict, with_id: bool = False) -> str:
    ta_ascii = (props.get("territorial_authority_ascii")
                or props.get("territorial_authority") or "").split(",")[0]
    town = props.get("major_name_ascii") or props.get("major_name")
    suburb = slug(props.get("name_ascii") or props["name"])
    if with_id:
        tail = f"-{props['id']}"
        suburb = suburb[:64 - len(tail)].rstrip("-") + tail
    return validate(f"{slug(ta_ascii)}/{slug(town) if town else NO_TOWN}/{suburb}")


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _wfs(api_key: str, layer: int, cql: str, property_names=None,
         max_features: int = MAX_FEATURES, timeout: int = 180) -> list[dict]:
    """WFS 1.0.0 GetFeature (2.0.0 returned empty results for LINZ layers)."""
    params = {
        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
        "typeName": f"data.linz.govt.nz:layer-{layer}", "outputFormat": "json",
        "maxFeatures": max_features, "cql_filter": cql,
    }
    if property_names:
        params["propertyName"] = ",".join(property_names)
    resp = requests.get(WFS_URL.format(key=api_key), params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if "features" not in data:
        raise RuntimeError(f"unexpected WFS response from layer {layer}")
    if len(data["features"]) >= max_features:
        raise RuntimeError(f"layer {layer} returned the {max_features:,}-feature maximum; "
                           "the result is truncated")
    return data["features"]


def search(api_key: str, text: str, territorial_authority: str | None = None) -> list[dict]:
    """Localities whose name or alternative names contain `text` (any case)."""
    pattern = _quote("%" + text.strip().replace("%", "") + "%")
    cql = f"(name ILIKE {pattern} OR name_ascii ILIKE {pattern} OR additional_name ILIKE {pattern})"
    if territorial_authority:
        cql += f" AND territorial_authority ILIKE {_quote('%' + territorial_authority + '%')}"
    feats = _wfs(api_key, config.LINZ_SUBURBS_LOCALITIES_LAYER, cql, LOCALITY_FIELDS)
    return sorted((f["properties"] for f in feats), key=_sort_key)


def in_territorial_authority(api_key: str, territorial_authority: str) -> list[dict]:
    """Every locality that lists `territorial_authority` (any case, substring)."""
    cql = f"territorial_authority ILIKE {_quote('%' + territorial_authority + '%')}"
    feats = _wfs(api_key, config.LINZ_SUBURBS_LOCALITIES_LAYER, cql, LOCALITY_FIELDS)
    return sorted((f["properties"] for f in feats), key=_sort_key)


def by_linz_id(api_key: str, linz_id: int) -> dict:
    feats = _wfs(api_key, config.LINZ_SUBURBS_LOCALITIES_LAYER, f"id = {int(linz_id)}",
                 LOCALITY_FIELDS)
    if not feats:
        raise LookupError(f"no LINZ locality with id {linz_id}")
    return feats[0]["properties"]


def _sort_key(p: dict):
    return (p.get("territorial_authority") or "", p.get("major_name") or "", p.get("name") or "")


def ambiguous_ids(localities: list[dict]) -> set[int]:
    """LINZ ids whose plain region id is shared with another locality."""
    seen: dict[str, list[int]] = {}
    for p in localities:
        seen.setdefault(region_id(p), []).append(p["id"])
    return {i for ids in seen.values() if len(ids) > 1 for i in ids}


def display_region_id(props: dict, ambiguous: set[int]) -> str:
    return region_id(props, with_id=props["id"] in ambiguous)


def resolve(api_key: str, region: str) -> dict:
    """The LINZ locality a region id names, or LookupError."""
    if not is_locality_id(region):
        raise LookupError(f"{region!r} is not a locality id <council>/<town>/<suburb>")
    ta_slug, _, suburb = region.split("/")
    candidates = []
    words = [w for w in suburb.split("-") if w]
    if words and words[-1].isdigit():
        try:
            candidates.append(by_linz_id(api_key, int(words[-1])))
        except LookupError:
            pass
    pattern = "%" + "%".join(words) + "%"
    cql = (f"name_ascii ILIKE {_quote(pattern)} AND "
           f"territorial_authority_ascii ILIKE {_quote('%' + '%'.join(ta_slug.split('-')) + '%')}")
    candidates += [f["properties"] for f in
                   _wfs(api_key, config.LINZ_SUBURBS_LOCALITIES_LAYER, cql, LOCALITY_FIELDS)]
    plain = {p["id"]: p for p in candidates if region_id(p) == region}
    exact = {p["id"]: p for p in candidates if region_id(p, with_id=True) == region}
    if exact:
        return next(iter(exact.values()))
    if len(plain) == 1:
        return next(iter(plain.values()))
    if len(plain) > 1:
        options = ", ".join(region_id(p, with_id=True) for p in plain.values())
        raise LookupError(f"{region!r} names {len(plain)} LINZ localities; use one of: {options}")
    raise LookupError(f"no LINZ locality matches {region!r}; "
                      f"find the id with: python tools/find_area.py <name>")


def outline_filter(props: dict) -> str:
    """CQL selecting the building outlines LINZ assigns to this locality."""
    parts = [f"suburb_locality = {_quote(props['name'])}"]
    if props.get("major_name"):
        parts.append(f"town_city = {_quote(props['major_name'])}")
    else:
        parts.append("(town_city IS NULL OR town_city = '')")
    tas = territorial_authorities(props)
    if tas:
        parts.append("territorial_authority IN (" + ", ".join(_quote(t) for t in tas) + ")")
    return " AND ".join(parts)


def fetch_outlines(api_key: str, props: dict) -> dict:
    """Building outlines (EPSG:2193, the layer's default) in the locality, by WFS."""
    feats = _wfs(api_key, config.LINZ_BUILDING_OUTLINES_LAYER, outline_filter(props),
                 timeout=600)
    return _feature_collection(feats)


def _feature_collection(feats: list[dict]) -> dict:
    # The crs member is what LINZ's own responses carry; without it GeoJSON
    # readers assume WGS84 and every downstream stage misplaces the outlines.
    return {"type": "FeatureCollection", "features": feats,
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2193"}}}


def outlines_from_councils(api_key: str, props: dict, refresh: bool = False,
                           log=None) -> tuple[dict, dict]:
    """Outlines selected from the cached per-council GeoPackages
    (src/linz_exports.py), reading every council the locality lists.
    Returns the collection and the provenance of each council file."""
    from src import linz_exports
    log = log or (lambda msg: print(msg, file=sys.stderr))
    tas = territorial_authorities(props)
    if not tas:
        raise LookupError(f"LINZ locality {props.get('name')} lists no territorial authority")
    paths, sources = [], []
    for ta in tas:
        paths.append(linz_exports.council_geopackage(
            api_key, ta, refresh=refresh, layer=config.LINZ_BUILDING_OUTLINES_LAYER, log=log))
        manifest = linz_exports.read_manifest(ta, config.LINZ_BUILDING_OUTLINES_LAYER) or {}
        sources.append({k: manifest.get(k) for k in (
            "territorial_authority", "layer_version", "layer_published_at",
            "export_id", "downloaded_utc")})
    where = linz_exports.outline_where(props["name"], props.get("major_name"), tas)
    feats = linz_exports.read_outlines(paths, where, layer=config.LINZ_BUILDING_OUTLINES_LAYER)
    return _feature_collection(feats), {"method": "council-geopackage", "councils": sources}


def outline_source() -> str:
    """"geopackage" (default) or "wfs", from SOLAR_OUTLINE_SOURCE."""
    import os
    value = os.environ.get("SOLAR_OUTLINE_SOURCE", "geopackage").strip().lower()
    if value not in ("geopackage", "wfs"):
        raise ValueError("SOLAR_OUTLINE_SOURCE must be 'geopackage' or 'wfs'")
    return value


def load_outlines(api_key: str, props: dict, refresh: bool = False) -> tuple[dict, dict]:
    """A locality's outlines from the council GeoPackage, falling back to WFS
    (with the reason logged) when the export cannot be used."""
    if outline_source() == "geopackage":
        try:
            return outlines_from_councils(api_key, props, refresh=refresh)
        except Exception as exc:  # any export failure: WFS gives the same selection
            reason = f"{type(exc).__name__}: {exc}"
            print(f"  council GeoPackage unavailable ({reason}); using LINZ WFS",
                  file=sys.stderr)
            return fetch_outlines(api_key, props), {"method": "wfs", "fallback_reason": reason}
    return fetch_outlines(api_key, props), {"method": "wfs"}


def outlines_bbox_wgs84(outlines: dict, pad_m: float = OUTLINE_PAD_M) -> list[float]:
    """WGS84 [w, s, e, n] around EPSG:2193 outlines, padded by `pad_m`."""
    import pyproj
    xs, ys = [], []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for item in c:
                walk(item)

    for f in outlines["features"]:
        if f.get("geometry"):
            walk(f["geometry"]["coordinates"])
    if not xs:
        raise ValueError("no building outlines")
    to_wgs = pyproj.Transformer.from_crs("EPSG:2193", "EPSG:4326", always_xy=True)
    w, s = to_wgs.transform(min(xs) - pad_m, min(ys) - pad_m)
    e, n = to_wgs.transform(max(xs) + pad_m, max(ys) + pad_m)
    return [round(w, 6), round(s, 6), round(e, 6), round(n, 6)]


def bbox_km2(bbox_wgs84: list[float]) -> float:
    import pyproj
    to_nztm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True)
    x0, y0 = to_nztm.transform(bbox_wgs84[0], bbox_wgs84[1])
    x1, y1 = to_nztm.transform(bbox_wgs84[2], bbox_wgs84[3])
    return abs(x1 - x0) * abs(y1 - y0) / 1e6


def imagery_gb_estimate(bbox_wgs84: list[float]) -> float:
    """Approximate 0.1 m imagery download for the bbox (~0.35 GB/km2)."""
    return bbox_km2(bbox_wgs84) * IMAGERY_GB_PER_KM2


def read_locality(region: str) -> dict | None:
    p = REGIONS_DIR / region / LOCALITY_FILE
    if not p.exists():
        return None
    return json.loads(p.read_text())


def prepare(region: str, api_key: str, refresh: bool = False) -> dict:
    """Write building_outlines.geojson and locality.json for a locality region.

    Existing files are kept unless `refresh`, so re-runs and resumed builds
    neither refetch nor change the region's bbox."""
    validate(region)
    out_dir = REGIONS_DIR / region
    existing = read_locality(region)
    if existing and not refresh and (out_dir / "building_outlines.geojson").exists():
        return existing
    props = resolve(api_key, region)
    outlines, outline_provenance = load_outlines(api_key, props)
    if not outlines["features"]:
        raise LookupError(f"LINZ locality {props['name']} (id {props['id']}) has no "
                          "building outlines; choose another area")
    bbox = outlines_bbox_wgs84(outlines)
    record = {
        "region": region,
        "source": {"layer": config.LINZ_SUBURBS_LOCALITIES_LAYER,
                   "name": "LINZ NZ Suburbs and Localities"},
        "linz": {k: props.get(k) for k in LOCALITY_FIELDS},
        "outline_filter": outline_filter(props),
        "outline_source": {"layer": config.LINZ_BUILDING_OUTLINES_LAYER,
                           **outline_provenance},
        "buildings": len(outlines["features"]),
        "bbox": bbox,
        "bbox_note": f"WGS84 extent of the buildings plus {OUTLINE_PAD_M:g} m",
        "bbox_km2": round(bbox_km2(bbox), 2),
        "imagery_gb_estimate": round(imagery_gb_estimate(bbox), 1),
        "prepared_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, obj in (("building_outlines.geojson", outlines), (LOCALITY_FILE, record)):
        tmp = out_dir / (name + ".tmp")
        tmp.write_text(json.dumps(obj, indent=None if name.endswith("geojson") else 1))
        tmp.replace(out_dir / name)
    return record


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="fetch a locality's outlines and write locality.json")
    p.add_argument("region")
    p.add_argument("--refresh", action="store_true",
                   help="select the outlines again even if already prepared (the council "
                        "GeoPackage is re-exported only when LINZ publishes a new version; "
                        "force that with src/linz_exports.py fetch --refresh)")
    a = ap.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv()
    from src.project_secrets import linz_api_key
    api_key = linz_api_key()
    if not api_key:
        raise SystemExit("LINZ_API_KEY not set")
    try:
        rec = prepare(a.region, api_key, refresh=a.refresh)
    except (LookupError, ValueError) as exc:
        raise SystemExit(str(exc))
    linz = rec["linz"]
    print(f"{rec['region']}: {linz['name']} ({linz['type']}, LINZ id {linz['id']}), "
          f"{rec['buildings']} buildings, bbox {rec['bbox']} "
          f"({rec.get('bbox_km2', 0):.1f} km2, imagery ~{rec.get('imagery_gb_estimate', 0):.0f} GB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
