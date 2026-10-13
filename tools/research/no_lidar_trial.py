"""Pretend a LiDAR town has none, build it both ways, and score against the LiDAR.

    python tools/research/no_lidar_trial.py setup  hawea wanaka_albert_town wanaka_west
    python tools/research/no_lidar_trial.py run    guess  hawea wanaka_albert_town wanaka_west
    python tools/research/no_lidar_trial.py run    photo  hawea wanaka_albert_town wanaka_west
    python tools/research/no_lidar_trial.py run    oracle hawea wanaka_albert_town wanaka_west   # true LiDAR faces: the ceiling
    python tools/research/no_lidar_trial.py compare hawea wanaka_albert_town wanaka_west

The question for the photo roof-shape model is not whether it reads pixels
well but whether a town built from it gets panels and kWh closer to the truth
than today's guess. Towns held out of its training (tools/train_photo_shape.py
TEST_REGIONS) are rebuilt exactly as Glenorchy is -- synthetic surface from
the 8 m DEM, the 2017-19 0.3 m photo as the only imagery, no point cloud --
once with the guess (hip 20 degrees, gables under 60 m2, flat from 400 m2) and
once with roofs read from the photo. Each building's panels and kWh are then
compared with its real LiDAR build.

Everything happens in a separate git worktree (~/trial-nolidar) with its own
data/regions, data/selected_faces and an empty point cloud, so the live build
on this machine is never touched. Region names get a _nl suffix there.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

LIVE = Path(__file__).resolve().parents[2]
TRIAL = Path(os.environ.get("NO_LIDAR_TRIAL_DIR", Path.home() / "trial-nolidar"))
RESULTS = TRIAL / "trial_results"


def sh(cmd, cwd=TRIAL, env=None):
    print("+", cmd if isinstance(cmd, str) else " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, shell=isinstance(cmd, str),
                   env={**os.environ, **(env or {})})


def setup(regions):
    sys.path.insert(0, str(LIVE))
    import config
    if not TRIAL.exists():
        sh(["git", "worktree", "add", "--detach", str(TRIAL), "HEAD"], cwd=LIVE)
    else:
        sh(["git", "checkout", "-q", "--detach", subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=LIVE, text=True).strip()])
    sh(["git", "checkout", "-q", "--", "config.py"])
    for name in (".venv",):
        if not (TRIAL / name).exists():
            (TRIAL / name).symlink_to(LIVE / name)
    data = TRIAL / "data"
    for name in ("dem_wide_mosaic.tif", "models", "photo03_tiles"):
        if not (data / name).exists():
            (data / name).symlink_to(LIVE / "data" / name)
    # A point cloud with no tile near the trial towns: the stages need the
    # directory to hold a tile, and must find no points under any roof.
    pc = data / "pointcloud"
    if pc.exists():
        shutil.rmtree(pc)
    pc.mkdir()
    from shapely.geometry import box
    import laspy
    towns = [box(*_nztm(config.REGIONS[r])) for r in regions]
    for t in sorted((LIVE / "data" / "pointcloud").glob("*.laz")):
        if t.name.startswith("."):
            continue
        with laspy.open(t) as f:
            h = f.header
            if not any(box(h.mins[0], h.mins[1], h.maxs[0], h.maxs[1]).buffer(2000).intersects(b) for b in towns):
                (pc / t.name).symlink_to(t)
                break
    (data / "selected_faces").mkdir(exist_ok=True)
    union = [min(config.REGIONS[r][0] for r in regions), min(config.REGIONS[r][1] for r in regions),
             max(config.REGIONS[r][2] for r in regions), max(config.REGIONS[r][3] for r in regions)]
    extra = "\n\n# --- no-LiDAR trial (tools/research/no_lidar_trial.py); never committed ---\n"
    for r in regions:
        extra += f"REGIONS[{r + '_nl'!r}] = {list(config.REGIONS[r])!r}\n"
        d = data / "regions" / f"{r}_nl"
        d.mkdir(parents=True, exist_ok=True)
        for f in ("building_outlines.geojson", "building_outlines_dedup.geojson"):
            shutil.copy2(LIVE / "data" / "regions" / r / f, d / f)
        img = d / "imagery_mosaic.tif"
        if img.exists() or img.is_symlink():
            img.unlink()
        img.symlink_to(LIVE / "data" / "regions" / r / "photo03_mosaic.vrt")
    extra += ("SURVEYS.insert(0, {\"name\": \"no-lidar-trial\", \"bbox\": %r, \"elevation\": \"synthetic\", "
              "\"dsm_layer\": None, \"dem_layer\": None, \"lidar_tile_index_layer\": None, "
              "\"imagery_layer\": 104460, \"reference_imagery_layer\": None, "
              "\"pointcloud_bulk_url\": None, \"pointcloud_tile_year\": None})\n" % union)
    with open(TRIAL / "config.py", "a") as f:
        f.write(extra)
    print("trial set up at", TRIAL)


def _nztm(bbox):
    from pyproj import Transformer
    t = Transformer.from_crs(4326, 2193, always_xy=True)
    x0, y0 = t.transform(bbox[0], bbox[1])
    x1, y1 = t.transform(bbox[2], bbox[3])
    return x0, y0, x1, y1


def run(variant, regions):
    names = [f"{r}_nl" for r in regions]
    py = str(TRIAL / ".venv" / "bin" / "python")
    sel = TRIAL / "data" / "selected_faces"
    shutil.rmtree(sel, ignore_errors=True)
    sel.mkdir()
    env = {"SOLAR_PHOTO_ROOFS": "1" if variant == "photo" else "0"}
    for n in names:
        (TRIAL / "data" / "regions" / n / "photo_roofs.json").unlink(missing_ok=True)
        sh([py, "src/synthesize_dsm.py", n], env=env)      # writes elevation_source: synthetic
    if variant == "photo":
        sh([py, "tools/predict_photo_shape.py", *names], env=env)
        for n in names:
            sh([py, "src/synthesize_dsm.py", n], env=env)
    elif variant == "oracle":
        # The ceiling: the same no-LiDAR build, roofs from the TRUE LiDAR faces.
        for r, n in zip(regions, names):
            _write_oracle_roofs(r, n)
            sh([py, "src/synthesize_dsm.py", n], env={"SOLAR_PHOTO_ROOFS": "1"})
        env = {"SOLAR_PHOTO_ROOFS": "1"}
    # The build's last guard fails here by design: it demands some faces chosen
    # from LiDAR, and this trial has no LiDAR at all. Every region is emitted
    # before it, so the per-region results are checked instead.
    try:
        sh(["bash", "src/run_district_build.sh", "--force", "--regions", " ".join(names)], env=env)
    except subprocess.CalledProcessError:
        missing = [n for n in names if not (TRIAL / "data" / "regions" / n / "solar_potential.geojson").exists()]
        if missing:
            raise
    out = RESULTS / variant
    out.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copy2(TRIAL / "data" / "regions" / n / "solar_potential.geojson", out / f"{n}.geojson")
    print("results in", out)


def _write_oracle_roofs(region, trial_name):
    """photo_roofs.json holding the LiDAR's own facets, in the photo format."""
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    to_nztm = Transformer.from_crs(4326, 2193, always_xy=True).transform
    fc = json.loads((LIVE / "data" / "regions" / region / "panel_layouts.geojson").read_text())
    out = {}
    for f in fc["features"]:
        p = f["properties"]
        if p.get("kind") != "facet":
            continue
        g = transform(to_nztm, shape(f["geometry"])).buffer(0)
        if g.geom_type != "Polygon" or g.area < 1:
            continue
        s = min(float(p.get("plane_slope_deg", p.get("slope_deg")) or 0), 45.0)
        a = p.get("plane_aspect_deg", p.get("aspect_deg"))
        out.setdefault(str(p["building_id"]), []).append(
            {"ring": [[round(x, 2), round(y, 2)] for x, y in g.exterior.coords],
             "slope": round(s, 1), "aspect": None if s < 5 or a is None else round(float(a), 1)})
    (TRIAL / "data" / "regions" / trial_name / "photo_roofs.json").write_text(
        json.dumps({"model": "lidar-oracle", "photo": None, "buildings": out}))


def _per_building(path):
    fc = json.loads(Path(path).read_text())
    out = {}
    for f in fc["features"]:
        p = f["properties"]
        out[int(p["building_id"])] = (float(p.get("panel_count") or 0), float(p.get("ac_kwh_year") or 0))
    return out


def compare(regions):
    report = {}
    for r in regions:
        truth = _per_building(LIVE / "data" / "regions" / r / "solar_potential.geojson")
        row = {}
        for variant in ("guess", "photo", "oracle"):
            p = RESULTS / variant / f"{r}_nl.geojson"
            if not p.exists():
                continue
            est = _per_building(p)
            ids = [b for b, (n, _) in truth.items() if n > 0 and b in est]
            tp = np.array([truth[b][0] for b in ids]); ep = np.array([est[b][0] for b in ids])
            tk = np.array([truth[b][1] for b in ids]); ek = np.array([est[b][1] for b in ids])
            ek_err = np.abs(ek - tk) / np.maximum(tk, 1)
            ep_err = np.abs(ep - tp) / np.maximum(tp, 1)
            row[variant] = {
                "buildings": len(ids),
                "panels_total_vs_lidar": float(ep.sum() / tp.sum()),
                "kwh_total_vs_lidar": float(ek.sum() / tk.sum()),
                "kwh_err_median_pct": float(100 * np.median(ek_err)),
                "kwh_err_p90_pct": float(100 * np.percentile(ek_err, 90)),
                "kwh_within_20pct": float((ek_err <= 0.2).mean()),
                "panels_err_median_pct": float(100 * np.median(ep_err)),
                "panels_err_p90_pct": float(100 * np.percentile(ep_err, 90)),
                "zero_where_lidar_has_panels": int((ep == 0).sum()),
            }
        report[r] = row
    print(json.dumps(report, indent=1))
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "compare.json").write_text(json.dumps(report, indent=1))


def main():
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "setup":
        setup(rest)
    elif cmd == "run":
        run(rest[0], rest[1:])
    elif cmd == "compare":
        compare(rest)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
