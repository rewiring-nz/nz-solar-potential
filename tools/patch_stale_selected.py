"""Rebuild every building whose selected faces are newer than the layouts.

RESUME-SAFE BY CONSTRUCTION, which redo_aspect_fast is not: that driver
fingerprints each building, predicts, and patches whatever changed. Restart
it after a preemption and the already-predicted buildings fingerprint as
UNCHANGED -- they were changed by the run that died -- so they are silently
never patched. The VM is preemptible and this has now bitten twice.

Here the question is asked of the filesystem instead: a selected_faces file
written after the district layouts were last written is, by definition, a
building the layouts do not yet reflect. Interrupt it anywhere and running
it again resumes exactly where it stopped.

Usage: python tools/patch_stale_selected.py [--patch]
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
SEL = Path("data/selected_faces")
LAYOUTS = Path("data/panel_layouts.geojson")
PY_ = sys.executable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch", action="store_true")
    a = ap.parse_args()
    import geopandas as gpd
    import config
    from src.region_build import area_paths
    if not LAYOUTS.exists():
        raise SystemExit("no data/panel_layouts.geojson")
    cutoff = LAYOUTS.stat().st_mtime
    stale = {int(p.stem) for p in SEL.glob("*.json")
             if p.stat().st_mtime > cutoff}
    print(f"{len(stale)} selected readings newer than the layouts", flush=True)
    if not stale:
        return
    d = Path("data/regions")
    regions = sorted({p.name for p in d.iterdir() if p.is_dir()}
                     | set(config.REGIONS))
    total = 0
    for region in regions:
        op = Path(area_paths(region)["outlines"])
        if not op.exists():
            continue
        ids = sorted(stale & {int(x) for x in gpd.read_file(op)["building_id"]})
        if not ids:
            continue
        print(f"{region}: {len(ids)} to rebuild", flush=True)
        total += len(ids)
        if not a.patch:
            continue
        for i in range(0, len(ids), 60):
            chunk = ids[i:i + 60]
            rc = subprocess.run(
                [PY_, "src/patch_buildings.py", *map(str, chunk),
                 "--area", region, "--skip-tiles"],
                env={**os.environ, "SOLAR_SELECTED_FACES": "1"}).returncode
            print(f"{region}: chunk rc={rc}", flush=True)
    print(f"TOTAL {total}", flush=True)


if __name__ == "__main__":
    main()
