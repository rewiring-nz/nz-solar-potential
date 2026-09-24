"""Rebuild NAMED buildings with the current code and push just them live.

The iterate-by-full-rebuild loop takes hours; this takes minutes. It runs the
same per-building path as build_layout_geojson, swaps their features into the
region's layouts, gates them, runs the region's post-layout stages (rerank,
derive, roof confidence), records their build keys, then re-emits the region
and recombines the tiles -- so a patched building is byte-for-byte what a full
rebuild would produce (tests/synthetic checks it). Optionally commits.

Usage:
  python src/patch_buildings.py 5371108 4734850 ... [--area pilot] [--push]

The area flag is only needed for buildings outside the pilot region; ids from
several areas need one invocation per area. Never run while a district rebuild
is writing the same files.
"""
import argparse, json, subprocess, sys, time, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+", type=int)
    ap.add_argument("--area", default="pilot")
    ap.add_argument("--push", action="store_true")
    # A DISTRICT-WIDE POST-PROCESS DOES NOT BELONG INSIDE A 60-BUILDING
    # CHUNK. bake_density_deciles re-reads the whole merged layouts and
    # rewrites solar_potential for the entire district; running it per chunk
    # costs 13 s x however many chunks the driver splits the work into, for
    # a result only the LAST run keeps. patch_stale_selected passes this and
    # bakes once at the end.
    ap.add_argument("--skip-bake", action="store_true",
                    help="do not re-bake density deciles (caller will)")
    ap.add_argument("--skip-tiles", action="store_true",
                    help="patch the geojson only (for chained invocations; run tiles once at the end)")
    a = ap.parse_args()

    from src.region_build import area_paths, area_centroid_wgs84
    from src.solar_model import SolarModel
    import src.build_layout_geojson as blg

    t0 = time.time()
    c = area_centroid_wgs84(a.area)
    blg._init_worker(a.area, SolarModel(*c) if c else SolarModel())
    new_feats = {}
    for bid in a.ids:
        feats = blg._build_one(bid)
        if not feats:
            # A rebuild yielding NOTHING is a crash wearing a quiet face --
            # _build_one converts any exception into an empty list. Splicing
            # that in would DELETE the building from the live map. Refuse the
            # whole run instead: a patch must never ship less than it replaces
            # by accident. (A genuinely empty building would have been empty
            # in the district file already.)
            raise SystemExit(
                f"ABORT: #{bid} rebuilt to 0 features -- almost certainly a "
                f"pipeline exception. Run _build_one_inner({bid}) directly for "
                f"the traceback. Nothing was patched.")
        new_feats[bid] = feats
        n = sum(1 for f in feats if f["properties"]["kind"] == "panel")
        print(f"  #{bid}: {len(feats)} features, {n} panels  "
              f"({time.time()-t0:.0f}s)", flush=True)

    # run the same post-stages those buildings would get in a full build
    ids = set(a.ids)
    outline_order = [int(b) for b in blg._CTX["gdf"]["building_id"]]

    def patch(path):
        """Swap the rebuilt buildings in, keeping the file in the full build's
        order (buildings as the outlines list them, each building's features
        as its build emitted them). Appending them at the end instead made a
        patched file differ from a fully built one in order alone -- enough
        to change array ids and fill-order tie-breaks downstream."""
        d = json.load(open(path))
        before = len(d["features"])
        groups, order = {}, []
        for f in d["features"]:
            bid = f["properties"].get("building_id")
            if bid not in groups:
                groups[bid] = []
                order.append(bid)
            groups[bid].append(f)
        for bid in a.ids:
            if bid not in groups:
                order.append(bid)
            groups[bid] = new_feats[bid]
        rank = {b: i for i, b in enumerate(outline_order)}
        order.sort(key=lambda b: rank.get(b, len(rank)))   # stable: unknown ids keep their place
        d["features"] = [f for b in order for f in groups[b]]
        # the rebuilt buildings' kWh come from today's yield code; if the rest
        # of the file's did not, say so rather than claim either
        from src.build_keys import yield_code_hash
        if d.get("yield_model") != yield_code_hash():
            d["yield_model"] = "mixed"
        json.dump(d, open(path, "w"))
        print(f"  patched {path.name}: {before} -> {len(d['features'])} features", flush=True)

    region = area_paths(a.area)["panel_layouts"]
    patch(region)
    # gate just this area's new panels (in place, cheap for a handful of ids)
    from src.gate_panels import gate_area
    from src.pointcloud_source import PointCloudSource
    gate_area(a.area, PointCloudSource(), only_ids=ids)

    # THE SAME POST-STAGES A FULL BUILD RUNS, NOT A PRIVATE COPY OF THEM.
    # This used to splice the rebuilt buildings' aggregates into
    # solar_potential by hand -- and for its whole life that splice sat one
    # indent too deep, after a `continue`, and never ran. Even running, it
    # skipped rerank_layouts (array order and fill ranks), roof_confidence
    # and everything else a full build does after the layout, so a patched
    # building was not what a full build would have produced, which is the
    # one thing this script promises. Now it runs the stages themselves, on
    # the whole region: each is per-building and cheap, and derive carries
    # addresses and horizons over rather than recomputing them (neither
    # depends on the layouts). tests/synthetic checks that a patched building
    # comes out byte-identical to a full build.
    for stage in ("rerank_layouts", "derive_solar_potential", "patch_roof_confidence"):
        subprocess.run([sys.executable, "src/run_stage.py", "--force", stage, a.area],
                       check=True, cwd=ROOT)

    # THE MERGED FILES ARE NOT THE SHIP PATH ANY MORE. Since 22 Sep each region
    # emits its own tiles and combine_regions joins them (docs/scale-
    # architecture.md). The merged layouts are still maintained where a
    # checkout has them, for tools that have not moved yet, and skipped where
    # it does not.
    if (DATA / "panel_layouts.geojson").exists():
        patch(DATA / "panel_layouts.geojson")
        if not a.skip_bake:
            subprocess.run([sys.executable, "src/bake_density_deciles.py"],
                           check=True, cwd=ROOT)

    # WHAT EACH BUILDING WAS BUILT FROM, recorded so an interrupted district
    # run can resume and an incremental one knows what is stale: the reading,
    # the drawn markup and the geometry code (src/build_keys.py). The VM is
    # preemptible and two rebuilds were half-applied when this was a
    # fingerprint-then-patch driver; a key recorded only AFTER the building is
    # in the file is the one signal that survives being interrupted.
    from src.build_keys import record_keys
    record_keys(a.area, a.ids)

    if not a.skip_tiles:
        # Re-emit this region and recombine: tiles, cells, detail and summary
        # all come from the region files this patch just rewrote.
        subprocess.run([sys.executable, "src/emit_region.py", a.area], check=True, cwd=ROOT)
        subprocess.run([sys.executable, "src/combine_regions.py"], check=True, cwd=ROOT)
        print(f"  region re-emitted and tiles recombined ({time.time()-t0:.0f}s total)", flush=True)

    if a.push:
        subprocess.run(["git", "add", "data/panel_layouts.pmtiles", "data/buildings.pmtiles",
                        "data/building_cells.pmtiles", "data/summaries", "data/build_summary.json",
                        "data/building_detail"], cwd=ROOT, check=True)
        subprocess.run(["git", "-c", "user.name=Josh", "-c", "user.email=josh@ideatious.com",
                        "commit", "-q", "-m",
                        f"Patch buildings {' '.join(map(str, a.ids))} with current code"],
                       cwd=ROOT, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=ROOT, check=True)
        print("  pushed live", flush=True)

if __name__ == "__main__":
    main()
