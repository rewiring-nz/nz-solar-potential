"""Rasters from LINZ's open S3 stores: no API key, no export queue.

LINZ publishes every elevation and aerial survey as cloud-optimised GeoTIFFs,
indexed by STAC, at nz-elevation and nz-imagery on S3. Reading from there
needs no key at all, so a region can be fetched by anyone (the Exports API
needs a key with export permission, and an export waits in a queue).

    fetch(collection_url, bbox_wgs84, out_path, res=None)

cuts the region out of every tile it touches into one GeoTIFF on the NZTM
grid (optionally resampled to `res` metres), reading only the blocks it needs.

    vrt_of_tiles(collection_url, geom_nztm, vrt_path, tile_dir)

downloads the tiles a geometry touches and writes a VRT over them -- used for
the wide 8 m DEM, which must cover Otago AND Auckland without being one
rectangle spanning the country between them.
"""

import hashlib
import json
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
INDEX_DIR = DATA / "s3_index"


def tile_index(collection_url):
    """{item id: (lon/lat bbox, tiff url)}, cached per collection."""
    if not collection_url.endswith("/"):
        collection_url += "/"
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    cache = INDEX_DIR / (hashlib.sha1(collection_url.encode()).hexdigest()[:16] + ".json")
    if cache.exists():
        return json.loads(cache.read_text())
    coll = json.load(urllib.request.urlopen(collection_url + "collection.json", timeout=60))
    hrefs = [l["href"].removeprefix("./") for l in coll["links"] if l["rel"] == "item"]

    def item(h):
        for attempt in range(4):
            try:
                d = json.load(urllib.request.urlopen(collection_url + h, timeout=60))
                asset = next(iter(d["assets"].values()))
                return d["id"], (d["bbox"], collection_url + asset["href"].removeprefix("./"))
            except Exception:
                if attempt == 3:
                    raise
    with ThreadPoolExecutor(32) as ex:
        idx = dict(ex.map(item, hrefs))
    cache.write_text(json.dumps(idx))
    return idx


def tiles_touching(collection_url, bbox_wgs84):
    from shapely.geometry import box
    b = box(*bbox_wgs84)
    return [url for bb, url in tile_index(collection_url).values() if box(*bb).intersects(b)]


def _nztm_bounds(bbox_wgs84):
    from pyproj import Transformer
    t = Transformer.from_crs(4326, 2193, always_xy=True)
    xs, ys = zip(*[t.transform(x, y) for x in (bbox_wgs84[0], bbox_wgs84[2])
                   for y in (bbox_wgs84[1], bbox_wgs84[3])])
    return min(xs), min(ys), max(xs), max(ys)


def fetch(collection_url, bbox_wgs84, out_path, res=None, resampling="average"):
    """The region cut from the collection's tiles into one GeoTIFF; out_path."""
    out_path = Path(out_path)
    if out_path.exists():
        print(f"  {out_path.name} exists, skipping")
        return out_path
    urls = tiles_touching(collection_url, bbox_wgs84)
    if not urls:
        raise RuntimeError(f"no tile of {collection_url} touches {bbox_wgs84}")
    vrt = out_path.with_suffix(".src.vrt")
    subprocess.run(["gdalbuildvrt", "-q", str(vrt), *[f"/vsicurl/{u}" for u in urls]], check=True)
    x0, y0, x1, y1 = _nztm_bounds(bbox_wgs84)
    tmp = out_path.with_suffix(".part.tif")
    cmd = ["gdal_translate", "-q", "-projwin", str(x0), str(y1), str(x1), str(y0),
           "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE", "-co", "BIGTIFF=IF_SAFER"]
    if res:
        cmd += ["-tr", str(res), str(res), "-r", resampling]
    subprocess.run(cmd + [str(vrt), str(tmp)], check=True,
                   env={"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "5",
                        "GDAL_HTTP_RETRY_DELAY": "2", "PATH": "/usr/bin:/bin:/usr/local/bin"})
    tmp.rename(out_path)
    vrt.unlink(missing_ok=True)
    print(f"  {out_path.name}: {len(urls)} tiles from {collection_url.split('amazonaws.com/')[-1]}")
    return out_path


def vrt_of_tiles(collection_url, geom_wgs84, vrt_path, tile_dir):
    """Download every tile touching `geom_wgs84` (a shapely geometry) and
    write a VRT over them. Returns vrt_path."""
    from shapely.geometry import box
    tile_dir = Path(tile_dir)
    tile_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for tid, (bb, url) in sorted(tile_index(collection_url).items()):
        if not box(*bb).intersects(geom_wgs84):
            continue
        p = tile_dir / url.rsplit("/", 1)[-1]
        if not p.exists():
            part = p.with_suffix(".part")
            urllib.request.urlretrieve(url, part)
            part.rename(p)
        paths.append(str(p))
    if not paths:
        raise RuntimeError(f"no tile of {collection_url} touches the geometry")
    subprocess.run(["gdalbuildvrt", "-q", str(vrt_path), *paths], check=True)
    print(f"  {Path(vrt_path).name}: {len(paths)} tiles")
    return Path(vrt_path)


def remote_vrt(collection_url, bbox_wgs84, vrt_path):
    """A VRT over the remote tiles touching bbox, read on demand over HTTP
    (nothing downloaded); None when no tile touches it."""
    urls = tiles_touching(collection_url, bbox_wgs84)
    if not urls:
        return None
    subprocess.run(["gdalbuildvrt", "-q", str(vrt_path), *[f"/vsicurl/{u}" for u in urls]], check=True)
    return Path(vrt_path)
