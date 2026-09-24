"""Plan, and apply, the incremental build: rebuild exactly the stale buildings.

A building is STALE when the key it was built from -- its selected-faces
reading, its drawn markup and the geometry code (src/build_keys.py) -- differs
from what it would be built from now. Every building in every region is
checked, not only those with a reading: until 24 Sep 2026 this looked at
readings alone, so a code change made nothing stale and a building without a
reading was never considered at all.

Each region gets a mode:
  clean  nothing stale
  patch  a few stale buildings: patch_buildings rebuilds just them
  full   no layouts yet, no keys recorded (a build from before keys existed),
         or more than FULL_FRACTION stale -- patching runs one building at a
         time in one process, the full build fans across every core, so past
         a tenth of the region the full build is the faster way

RESUME-SAFE BY CONSTRUCTION. A key is recorded only after its building is in
the layouts, so a run the VM kills part-way leaves the unfinished buildings
stale, and the next run picks up exactly those. (redo_aspect_fast, deleted,
fingerprinted before patching and silently skipped the half-done ones.)

Usage:
    python tools/patch_stale_selected.py                  # report
    python tools/patch_stale_selected.py --plan plan.json # write the plan
    python tools/patch_stale_selected.py --patch          # patch "patch" regions
    ... [--regions a b]
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PY_ = sys.executable
FULL_FRACTION = float(os.environ.get("SOLAR_FULL_FRACTION", "0.10"))  # env: tests only
CHUNK = 60


def plan(regions):
    import geopandas as gpd
    from src.region_build import area_paths
    from src.build_keys import stage_code_hash, stale_buildings, load_keys, GEOMETRY_STAGE
    gh = stage_code_hash(GEOMETRY_STAGE)
    out = {}
    for region in regions:
        paths = area_paths(region)
        if not Path(paths["outlines"]).exists():
            continue
        ids = [int(x) for x in gpd.read_file(paths["outlines"])["building_id"]]
        stale = stale_buildings(region, ids, gh)
        if not Path(paths["panel_layouts"]).exists():
            mode, why = "full", "no layouts yet"
        elif not load_keys(region):
            mode, why = "full", "no build keys recorded (built before keys existed)"
        elif not stale:
            mode, why = "clean", "nothing stale"
        elif len(stale) > FULL_FRACTION * len(ids):
            mode, why = "full", f"{len(stale)}/{len(ids)} stale, over {FULL_FRACTION:.0%}"
        else:
            mode, why = "patch", f"{len(stale)}/{len(ids)} stale"
        out[region] = {"mode": mode, "why": why, "stale": stale, "total": len(ids)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch", action="store_true")
    ap.add_argument("--plan", default=None, help="write the plan as JSON here")
    ap.add_argument("--regions", nargs="*", default=None)
    a = ap.parse_args()
    from src.region_build import all_areas
    regions = a.regions or all_areas()
    p = plan(regions)
    for region, r in p.items():
        print(f"{region}: {r['mode']} ({r['why']})", flush=True)
    if a.plan:
        Path(a.plan).write_text(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "stale"}
                                            for k, v in p.items()}, indent=1))
    total = sum(len(r["stale"]) for r in p.values() if r["mode"] == "patch")
    failed = []
    if a.patch:
        for region, r in p.items():
            if r["mode"] != "patch":
                continue
            ids = r["stale"]
            for i in range(0, len(ids), CHUNK):
                chunk = ids[i:i + CHUNK]
                rc = subprocess.run(
                    [PY_, "src/patch_buildings.py", *map(str, chunk),
                     "--area", region, "--skip-tiles", "--skip-bake"],
                    env={**os.environ, "SOLAR_SELECTED_FACES": "1"}).returncode
                print(f"{region}: chunk rc={rc}", flush=True)
                if rc != 0:
                    failed.append(f"{region} chunk {i // CHUNK} (rc={rc})")
        # Once, at the end, instead of once per chunk: a district-wide pass over
        # the MERGED layouts, only where a merged file still exists. The
        # per-region ship path bakes each region inside emit_region.
        if total and Path("data/panel_layouts.geojson").exists():
            subprocess.run([PY_, "src/bake_density_deciles.py"], check=True)
    print(f"TOTAL {total} to patch", flush=True)
    # A failed chunk used to be printed and forgotten: this exited 0 either
    # way, so run_district_build.sh could never stop an incremental build
    # that had not actually applied its patches.
    if failed:
        print(f"FAILED: {len(failed)} chunk(s): " + ", ".join(failed), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
