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

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
INDEX_DIR = DATA / "s3_index"

# Lossless 2x2 Float32 NZTM GeoTIFF containing [[1, 2], [3, 4]].
_LERC_PROBE = (
    "SUkqAAgAAAARAAABAwABAAAAAgAAAAEBAwABAAAAAgAAAAIBAwABAAAAIAAAAAMBAwABAAAA"
    "R4gAAAYBAwABAAAAAQAAABEBBAABAAAAFgIAABUBAwABAAAAAQAAABYBAwABAAAAAgAAABcB"
    "BAABAAAAXwAAABwBAwABAAAAAQAAAFMBAwABAAAAAwAAAA6DDAADAAAAVAEAAIKEDAAGAAAA"
    "bAEAAK+HAwAgAAAAnAEAALGHAgA6AAAA3AEAAICkAgByAAAA4gAAAPLFBAACAAAA2gAAAAAA"
    "AAAEAAAAAAAAADxHREFMTWV0YWRhdGE+CiAgPEl0ZW0gbmFtZT0iQ09NUFJFU1NJT05fUkVW"
    "RVJTSUJJTElUWSIgZG9tYWluPSJJTUFHRV9TVFJVQ1RVUkUiPkxPU1NMRVNTPC9JdGVtPgo8"
    "L0dEQUxNZXRhZGF0YT4KAAAAAAAAACBAAAAAAAAAIEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAACATzJBAAAAANASU0EAAAAAAAAAAAEAAQAAAAcAAAQAAAEAAQABBAAA"
    "AQABAAIEsYcwAAAAAQixhwkAMAAGCAAAAQCOIwAMAAABAJEIBAwAAAEAKSNOWkdEMjAwMCAv"
    "IE5ldyBaZWFsYW5kIFRyYW5zdmVyc2UgTWVyY2F0b3IgMjAwMHxOWkdEMjAwMHwATGVyYzIg"
    "BAAAAEF4kV4CAAAAAgAAAAEAAAAEAAAACAAAAF8AAAAGAAAAAAAAAAAAAAAAAAAAAADwPwAA"
    "AAAAABBAAAAAAAAAgD8AAIBAAQAAgD8AAABAAABAQAAAgEA="
)


def _reader_env(reader):
    if reader not in ("GTiff", "LIBERTIFF"):
        raise ValueError(f"Unsupported TIFF reader: {reader}")
    env = os.environ.copy()
    if reader == "LIBERTIFF":
        env["GDAL_SKIP"] = " ".join(
            dict.fromkeys([*env.get("GDAL_SKIP", "").split(), "GTiff"]))
    else:
        env["GDAL_SKIP"] = " ".join(
            item for item in env.get("GDAL_SKIP", "").split() if item != "GTiff")
    return env


def require_raster_support():
    """Select a working CLI TIFF reader and verify Rasterio can decode LERC."""
    requested_reader = os.environ.get("SOLAR_TIFF_READER", "auto").upper()
    if requested_reader not in ("AUTO", "GTIFF", "LIBERTIFF"):
        raise RuntimeError("SOLAR_TIFF_READER must be auto, GTiff, or LIBERTIFF.")
    missing = [name for name in ("gdalbuildvrt", "gdal_translate")
               if shutil.which(name) is None]
    guidance = (
        "Native GDAL tools are separate from Python requirements.txt. "
        "macOS: install GDAL with `brew bundle --file=Brewfile`; "
        "Ubuntu/WSL: install `gdal-bin`. Installing a Python lerc package "
        "does not enable the system TIFF codec. "
        "See docs/data-maintainers/troubleshooting.md#lerc-raster-dependency-check."
    )
    if missing:
        raise RuntimeError("Missing GDAL commands: " + ", ".join(missing) + ". " + guidance)
    reader_names = {"GTIFF": "GTiff", "LIBERTIFF": "LIBERTIFF"}
    candidates = (("GTiff", "LIBERTIFF") if requested_reader == "AUTO"
                 else (reader_names[requested_reader],))
    with tempfile.TemporaryDirectory(prefix="solar-lerc-probe-") as directory:
        probe = Path(directory) / "lerc.tif"
        probe.write_bytes(base64.b64decode(_LERC_PROBE))
        decoded = Path(directory) / "decoded.asc"
        failures = []
        selected_reader = None
        for reader in candidates:
            decoded.unlink(missing_ok=True)
            result = subprocess.run(
                ["gdal_translate", "-q", "-of", "AAIGrid", str(probe), str(decoded)],
                env=_reader_env(reader), capture_output=True, text=True)
            try:
                cli_values = [[float(value) for value in row.split()]
                              for row in decoded.read_text().splitlines()[-2:]]
            except (OSError, ValueError):
                cli_values = None
            if result.returncode == 0 and cli_values == [[1.0, 2.0], [3.0, 4.0]]:
                selected_reader = reader
                break
            failures.append(f"{reader}: {result.stderr.strip() or 'probe pixels incorrect'}")
        if selected_reader is None:
            tried = "; ".join(failures)
            raise RuntimeError(
                f"No available GDAL TIFF reader can decode required LERC terrain tiles. "
                f"{guidance}\n{tried}")
        try:
            import rasterio
        except ImportError as exc:
            raise RuntimeError(
                "Rasterio is missing from the selected Python. Install "
                f"requirements with `{sys.executable} -m pip install -r requirements.txt`.") from exc
        try:
            with rasterio.open(probe) as dataset:
                values = dataset.read(1).tolist()
        except rasterio.errors.RasterioError as exc:
            raise RuntimeError(
                "The selected Python's Rasterio cannot decode LERC. Install the "
                f"project requirements with `{sys.executable} -m pip install -r "
                "requirements.txt`; use a Rasterio build with LERC-enabled "
                f"GDAL. {guidance}") from exc
        if values != [[1.0, 2.0], [3.0, 4.0]]:
            raise RuntimeError("Rasterio LERC probe returned incorrect pixel values.")
        return selected_reader


def _build_vrt(paths, destination, reader=None):
    destination = Path(destination)
    reader = reader or require_raster_support()
    with tempfile.TemporaryDirectory(prefix=".vrt-", dir=destination.parent) as directory:
        temporary = Path(directory) / destination.name
        subprocess.run(
            ["gdalbuildvrt", "-q", "-strict", str(temporary),
             *[p if p.startswith("/vsicurl/") else str(Path(p).resolve()) for p in paths]],
            env=_reader_env(reader), check=True)
        tree = ET.parse(temporary)
        # GDAL may relativize even absolute inputs against the temporary directory.
        for source in tree.iter("SourceFilename"):
            if source.get("relativeToVRT") == "1" and source.text:
                source.text = str((temporary.parent / source.text).resolve())
                source.set("relativeToVRT", "0")
        tree.write(temporary, encoding="utf-8")
        temporary.replace(destination)


def _rasterio_crop(vrt, output, bounds, res, resampling):
    """Use the validated Python decoder when the CLI TIFF reader is disabled."""
    import math
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window, from_bounds

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                      GDAL_HTTP_MAX_RETRY="5", GDAL_HTTP_RETRY_DELAY="2"), \
            rasterio.open(vrt) as source:
        x0, y0, x1, y1 = bounds
        if res:
            width = max(1, math.floor((x1 - x0) / res + 0.5))
            height = max(1, math.floor((y1 - y0) / res + 0.5))
            transform = from_origin(x0, y1, res, res)
            method = Resampling[resampling]
        else:
            window = from_bounds(x0, y0, x1, y1, source.transform)
            col = math.floor(window.col_off + 1e-8)
            row = math.floor(window.row_off + 1e-8)
            width = math.ceil(window.col_off + window.width - 1e-8) - col
            height = math.ceil(window.row_off + window.height - 1e-8) - row
            transform = source.window_transform(Window(col, row, width, height))
            method = Resampling.nearest
        profile = source.profile.copy()
        profile.update(driver="GTiff", width=width, height=height, transform=transform,
                       tiled=True, blockxsize=256, blockysize=256,
                       compress="deflate", BIGTIFF="IF_SAFER")
        with WarpedVRT(source, crs=source.crs, transform=transform,
                       width=width, height=height,
                       resampling=method) as cropped:
            with rasterio.open(output, "w", **profile) as destination:
                destination.colorinterp = source.colorinterp
                for _, window in destination.block_windows(1):
                    destination.write(cropped.read(window=window), window=window)


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
    reader = require_raster_support()
    urls = tiles_touching(collection_url, bbox_wgs84)
    if not urls:
        raise RuntimeError(f"no tile of {collection_url} touches {bbox_wgs84}")
    vrt = out_path.with_suffix(".src.vrt")
    _build_vrt([f"/vsicurl/{u}" for u in urls], vrt, reader)
    x0, y0, x1, y1 = _nztm_bounds(bbox_wgs84)
    tmp = out_path.with_suffix(".part.tif")
    cmd = ["gdal_translate", "-q", "-projwin", str(x0), str(y1), str(x1), str(y0),
           "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE", "-co", "BIGTIFF=IF_SAFER"]
    if res:
        cmd += ["-tr", str(res), str(res), "-r", resampling]
    env = os.environ.copy()
    env.update(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="5",
               GDAL_HTTP_RETRY_DELAY="2")
    try:
        if reader == "LIBERTIFF":
            _rasterio_crop(vrt, tmp, (x0, y0, x1, y1), res, resampling)
        else:
            env = _reader_env(reader)
            env.update(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="5",
                       GDAL_HTTP_RETRY_DELAY="2")
            subprocess.run(cmd + [str(vrt), str(tmp)], check=True, env=env)
        tmp.replace(out_path)
    finally:
        tmp.unlink(missing_ok=True)
        vrt.unlink(missing_ok=True)
    print(f"  {out_path.name}: {len(urls)} tiles from {collection_url.split('amazonaws.com/')[-1]}")
    return out_path


def vrt_of_tiles(collection_url, geom_wgs84, vrt_path, tile_dir):
    """Download every tile touching `geom_wgs84` (a shapely geometry) and
    write a VRT over them. Returns vrt_path."""
    from shapely.geometry import box
    reader = require_raster_support()
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
    _build_vrt(paths, vrt_path, reader)
    print(f"  {Path(vrt_path).name}: {len(paths)} tiles")
    return Path(vrt_path)


def remote_vrt(collection_url, bbox_wgs84, vrt_path):
    """A VRT over the remote tiles touching bbox, read on demand over HTTP
    (nothing downloaded); None when no tile touches it."""
    reader = require_raster_support()
    urls = tiles_touching(collection_url, bbox_wgs84)
    if not urls:
        return None
    _build_vrt([f"/vsicurl/{u}" for u in urls], vrt_path, reader)
    return Path(vrt_path)
