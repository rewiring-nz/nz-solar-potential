"""The whole build, end to end, on a synthetic region -- no LiDAR needed.

WHY. The golden tests pin real roofs, but they need a region's DSM, point
cloud and imagery on disk, so everywhere except the build VM they skip. That
left no way to answer "did this refactor change the output?" on a laptop or
in a cloud session. This builds a tiny made-up region (make_region.py: eight
roofs of known shape -- gable, hip, L, flat with plant, lean-to, a shed, one
shaded by a tree -- with a fabricated point cloud, DSM, imagery and wide DEM)
in a throwaway copy of the repo, runs the real district stages on it, and
compares a fingerprint of every output file against reference.json.

On 24 Sep 2026 it found a real bug on its first run: the same code built twice
gave different layouts, because the parallel gate returned panels in worker
completion order.

These are REGRESSION tests, like the goldens: the reference records what the
code produced, not what is right. When a deliberate change moves it, re-record
and say why in the commit.

Run:        .venv/bin/python tests/synthetic/run.py
Re-record:  .venv/bin/python tests/synthetic/run.py --record
Keep:       --keep leaves the scratch copy for inspection

Needs tippecanoe (and tippecanoe-decode) on PATH for the tile stage; without
them the test SKIPS, the same way the goldens skip without region data.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REFERENCE = HERE / "reference.json"
REGION = "zz_harness"
STAGES = ["build_layout_geojson", "gate_panels", "rerank_layouts",
          "derive_solar_potential", "patch_roof_confidence",
          "bake_building_horizons", "build_heatmap_raster",
          "register_imagery", "emit_region"]


def build(work, py, log=print):
    """Copy the tracked tree into `work`, fabricate the region, run the stages.
    Returns the list of stages that failed."""
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.split("\n")
    for rel in filter(None, files):
        src = ROOT / rel
        # the served map's own tiles are big and no stage reads them
        if not src.is_file() or rel.startswith(("data/regions/", "data/pointcloud/", "data/terrain/",
                                                "data/heatmap_tiles/", "data/heatmaps/",
                                                "data/building_detail/")):
            continue
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run([py, str(HERE / "make_region.py"), str(work)], check=True,
                   stdout=subprocess.DEVNULL)
    env = {**os.environ, "SOLAR_SELECTED_FACES": "1", "PYTHONHASHSEED": "0"}
    failed = []
    for s in STAGES:
        r = subprocess.run([py, "src/run_stage.py", s, REGION], cwd=work, env=env,
                           capture_output=True, text=True)
        (work / f"_{s}.log").write_text(r.stdout + r.stderr)
        if r.returncode != 0:
            failed.append(s)
            log(f"  STAGE FAILED: {s}\n" + "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:]))
    return failed


def incremental_check(work, py, full_fp):
    """Make one building stale, run the incremental path, and require every
    output to come back byte-identical to the full build. Returns an error
    string, or None.

    This is the promise the incremental build rests on: patching a building
    produces what a full rebuild would have. Its layout features are deleted,
    its building record corrupted and its build key dropped, so nothing of
    the old answer can leak through."""
    bid = 990000003
    lay = work / "data/regions" / REGION / "panel_layouts.geojson"
    d = json.loads(lay.read_text())
    d["features"] = [f for f in d["features"] if f["properties"].get("building_id") != bid]
    lay.write_text(json.dumps(d))
    spp = work / "data/regions" / REGION / "solar_potential.geojson"
    d = json.loads(spp.read_text())
    for f in d["features"]:
        if f["properties"].get("building_id") == bid:
            f["properties"].update(panel_count=999, ac_kwh_year=1.0, kwp=0.0)
    spp.write_text(json.dumps(d))
    keys = work / "data/regions" / REGION / "built_from.json"
    k = json.loads(keys.read_text())
    if str(bid) not in k:
        return "the full build recorded no build key for #%d" % bid
    k.pop(str(bid))
    keys.write_text(json.dumps(k))
    env = {**os.environ, "SOLAR_SELECTED_FACES": "1", "PYTHONHASHSEED": "0",
           "SOLAR_FULL_FRACTION": "0.5"}
    r = subprocess.run([py, "tools/patch_stale_selected.py", "--regions", REGION, "--patch"],
                       cwd=work, env=env, capture_output=True, text=True)
    (work / "_incremental.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0 or "patch (1/9 stale)" not in r.stdout:
        return "patch step: " + (r.stdout + r.stderr).strip()[-300:]
    r = subprocess.run([py, "src/run_stage.py", "--force", "emit_region", REGION],
                       cwd=work, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        return "emit after patch: " + (r.stdout + r.stderr).strip()[-300:]
    r = subprocess.run([py, "tools/patch_stale_selected.py", "--regions", REGION],
                       cwd=work, env=env, capture_output=True, text=True)
    if "clean" not in r.stdout:
        return "after patching, the plan is not clean: " + r.stdout.strip()
    got = fingerprint(work, py)
    diff = [k for k in set(got["files"]) | set(full_fp["files"])
            if got["files"].get(k) != full_fp["files"].get(k)]
    return ("differs from the full build in " + ", ".join(sorted(diff))) if diff else None


def pack_check(work, py, full_fp):
    """Pack the region, delete its inputs, restore the pack, rebuild every
    stage, and require every output byte-identical to the build from the full
    survey. This is what makes deleting inputs after publishing safe for a
    geometry layer that keeps changing (src/pack_region.py)."""
    env = {**os.environ, "SOLAR_SELECTED_FACES": "1", "PYTHONHASHSEED": "0"}
    r = subprocess.run([py, "src/pack_region.py", REGION], cwd=work, env=env,
                       capture_output=True, text=True)
    (work / "_pack.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        return "pack: " + (r.stdout + r.stderr).strip()[-300:]
    for p in (work / "data/pointcloud").glob("*.laz"):
        p.unlink()
    for name in ("imagery_mosaic.tif", "dsm_mosaic.tif"):
        (work / "data/regions" / REGION / name).unlink()
    r = subprocess.run([py, "src/pack_region.py", REGION, "--restore"], cwd=work, env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return "restore: " + (r.stdout + r.stderr).strip()[-300:]
    for s in STAGES:
        r = subprocess.run([py, "src/run_stage.py", "--force", s, REGION], cwd=work, env=env,
                           capture_output=True, text=True)
        if r.returncode != 0:
            return f"rebuild from pack, {s}: " + (r.stdout + r.stderr).strip()[-300:]
    got = fingerprint(work, py)
    diff = [k for k in set(got["files"]) | set(full_fp["files"])
            if got["files"].get(k) != full_fp["files"].get(k)]
    return ("rebuilt from the pack, differs in " + ", ".join(sorted(diff))) if diff else None


def fingerprint(work, py):
    out = subprocess.run([py, str(HERE / "fingerprint.py"), str(work)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    if not (shutil.which("tippecanoe") and shutil.which("tippecanoe-decode")):
        print("  skip  synthetic region: tippecanoe not on PATH")
        return 0
    py = str(ROOT / ".venv" / "bin" / "python")
    if not Path(py).exists():
        py = sys.executable
    work = Path(tempfile.mkdtemp(prefix="solar_synth_"))
    try:
        failed = build(work, py)
        if failed:
            print(f"FAIL  {len(failed)} stage(s) failed: {', '.join(failed)}  (logs in {work})")
            a.keep = True
            return 1
        # The yield layer must round-trip: recomputing every kWh from the
        # stored geometry with the same model changes nothing (apply_yield).
        r = subprocess.run([py, "src/apply_yield.py", REGION], cwd=work,
                           capture_output=True, text=True)
        if r.returncode != 0 or " 0 changed" not in r.stdout or "WARNING" in r.stdout:
            print("  FAIL  apply_yield does not round-trip on fresh output:\n        "
                  + (r.stdout + r.stderr).strip()[-400:])
            a.keep = True
            return 1
        fp = fingerprint(work, py)
        if not a.record:
            bad = incremental_check(work, py, fp)
            if bad:
                print("  FAIL  incremental rebuild is not a full build: " + bad)
                a.keep = True
                return 1
            bad = pack_check(work, py, fp)
            if bad:
                print("  FAIL  " + bad)
                a.keep = True
                return 1
        if a.record:
            REFERENCE.write_text(json.dumps(fp, indent=1, sort_keys=True) + "\n")
            print(f"recorded {len(fp['files'])} outputs, {fp['headline'].get('panels')} panels "
                  f"-> {REFERENCE.relative_to(ROOT)}")
            return 0
        ref = json.loads(REFERENCE.read_text())
        if fp == ref:
            print(f"  pass  synthetic region: {len(fp['files'])} outputs identical "
                  f"({fp['headline'].get('panels')} panels)")
            return 0
        print("  FAIL  synthetic region differs from reference.json")
        for k in sorted(set(fp["files"]) | set(ref["files"])):
            if fp["files"].get(k) != ref["files"].get(k):
                print(f"        {k}: {ref['files'].get(k)} -> {fp['files'].get(k)}")
        hb, ha = ref["headline"], fp["headline"]
        for k in sorted(set(ha) | set(hb)):
            if ha.get(k) != hb.get(k):
                if k == "per_building":
                    for b in sorted(set(ha[k]) | set(hb[k])):
                        if ha[k].get(b) != hb[k].get(b):
                            print(f"        building {b} (panels, facets, kWh): {hb[k].get(b)} -> {ha[k].get(b)}")
                else:
                    print(f"        {k}: {hb.get(k)} -> {ha.get(k)}")
        print("  If the change is deliberate: tests/synthetic/run.py --record, and say why in the commit.")
        return 1
    finally:
        if a.keep:
            print(f"  kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
