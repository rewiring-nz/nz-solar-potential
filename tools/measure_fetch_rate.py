"""How fast will the data sources actually serve a national build?

WHY. The compute divides across machines; the download does not. Queenstown
needed 116 GB of point cloud, elevation and imagery, the country about 26 TB,
and all of it comes from two services: OpenTopography's point-cloud bulk
store and LINZ's export API. Nobody has measured what either sustains, and
fifty machines on one API is how you find its rate limiter. That number, not
the machine count, is the floor for a national run
(docs/scaling-and-iteration.md). This measures it, in two parts:

  point clouds  download real tiles for a bbox at 1, 2, 4, 8, 16 parallel
                streams and report aggregate MB/s at each: where the curve
                flattens is the store's ceiling for one client
  LINZ export   time one real DSM export for a ~1 km2 box end to end
                (request, queue, build, download), the per-region overhead a
                fleet pays however many workers it has

Nothing is kept: tiles stream to /dev/null, the export goes to a temp dir.
Run it on the build VM (it needs LINZ_API_KEY and outbound access), once from
one machine and once from several at the same time, before sizing a fleet.

Usage:
    python tools/measure_fetch_rate.py --bbox 172.60 -43.55 172.65 -43.52
    python tools/measure_fetch_rate.py --region christchurch_central --tiles 32
    ... [--streams 1 2 4 8 16] [--no-export]
"""
import argparse
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _stream(url):
    import requests
    n = 0
    with requests.get(url, stream=True, timeout=120) as r:
        if r.status_code != 200:
            return 0, r.status_code
        for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
            n += len(chunk)
    return n, 200


def sweep(tile_urls, streams):
    rows = []
    for s in streams:
        todo = tile_urls[:max(s * 2, 4)]        # two tiles per stream, at least four
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=s) as ex:
            got = list(ex.map(_stream, todo))
        dt = time.time() - t0
        mb = sum(n for n, _ in got) / 1e6
        errs = sum(1 for _, code in got if code != 200)
        rows.append((s, len(todo), mb, dt, mb / dt if dt else 0.0, errs))
        print(f"  {s:>2} streams: {len(todo):>3} tiles, {mb:8.0f} MB in {dt:6.1f} s "
              f"= {mb / dt:7.1f} MB/s ({mb * 8 / dt / 1000:.2f} Gbit/s)"
              f"{f'  [{errs} failed]' if errs else ''}", flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"))
    ap.add_argument("--region")
    ap.add_argument("--tiles", type=int, default=32)
    ap.add_argument("--streams", nargs="*", type=int, default=[1, 2, 4, 8, 16])
    ap.add_argument("--no-export", action="store_true")
    a = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    key = os.environ.get("LINZ_API_KEY")
    if not key:
        raise SystemExit("LINZ_API_KEY not set")
    from src.region_build import area_bbox_wgs84
    from src.surveys import survey_for
    from src.fetch_pointcloud_regions import tiles_for_bbox_wgs84
    bbox = list(a.bbox) if a.bbox else area_bbox_wgs84(a.region)
    sv = survey_for(bbox)
    store = sv.get("pointcloud_bulk_url")
    names = tiles_for_bbox_wgs84(bbox, key)
    print(f"bbox {bbox}: survey {sv.get('name', 'default')}, {len(names)} point-cloud tiles")
    if names and store:
        urls = [f"{store}/{n}" for n in names[:a.tiles]]
        print(f"point clouds from {store}:")
        rows = sweep(urls, a.streams)
        best = max(rows, key=lambda r: r[4])
        print(f"  best: {best[4]:.0f} MB/s at {best[0]} streams. At that rate 26 TB takes "
              f"{26e6 / best[4] / 86400:.1f} days from ONE client; more clients only help "
              f"if the store is not already the limit -- run this from several at once.")
    if not a.no_export:
        from src.fetch_regions import fetch_raster_chunked
        w, s, e, n = bbox
        cx, cy = (w + e) / 2, (s + n) / 2
        box = [cx - 0.0065, cy - 0.0045, cx + 0.0065, cy + 0.0045]   # ~1 km2
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.time()
            fetch_raster_chunked(box, key, sv["dsm_layer"], "dsm", Path(tmp), "grid")
            dt = time.time() - t0
            size = sum(p.stat().st_size for p in Path(tmp).rglob("*") if p.is_file())
        print(f"LINZ DSM export, ~1 km2: {dt:.0f} s end to end ({size / 1e6:.1f} MB). "
              f"Per region that is overhead no parallelism removes.")


if __name__ == "__main__":
    main()
