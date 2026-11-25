"""Carry build keys across a code change that provably leaves some buildings alone.

A building's key holds the hash of the geometry code (src/build_keys.py), so
any edit to that code marks all ~33,000 buildings stale: an 11-hour re-lay on
the VM. Where every changed line acts only on roofs built from a photo reading
(src/vision_roof.py: `from_reading` faces), the other buildings would rebuild
to the same bytes. This records the current key for exactly those:

  - the building has no photo reading (vision_roof.reading_path is None), and
  - its recorded key is the one it would have had under --from-hash, so a
    building that was already stale for another reason stays stale.

Buildings with a reading keep their old key and rebuild in the next
run_district_build.sh. The claim "only reading roofs change" is the caller's:
state it, with the commits, where the build is recorded (BACKLOG.md).

Usage (on the VM, after the sync, before the build):
    python tools/rekey_unaffected.py --from-hash <12 hex> [--regions a b] [--apply]
Without --apply it only reports.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-hash", required=True, help="geometry hash the buildings were built with")
    ap.add_argument("--regions", nargs="*", default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    from src.build_keys import (GEOMETRY_STAGE, building_key, keys_path, load_keys,
                                stage_code_hash)
    from src.region_build import all_areas
    from src.vision_roof import reading_path
    new = stage_code_hash(GEOMETRY_STAGE)
    if new == a.from_hash:
        print("geometry code unchanged: nothing to do")
        return
    tot = [0, 0, 0]
    for region in a.regions or all_areas():
        keys = load_keys(region)
        if not keys:
            continue
        moved = read = other = 0
        for bid, key in list(keys.items()):
            if reading_path(int(bid)) is not None:
                read += 1
            elif key == building_key(int(bid), a.from_hash, region):
                keys[bid] = building_key(int(bid), new, region)
                moved += 1
            else:
                other += 1
        print(f"{region}: {moved} carried, {read} with a reading (rebuild), {other} already stale", flush=True)
        tot = [tot[0] + moved, tot[1] + read, tot[2] + other]
        if a.apply and moved:
            p = keys_path(region)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(keys, sort_keys=True))
            os.replace(tmp, p)
    print(f"TOTAL {tot[0]} carried, {tot[1]} to rebuild, {tot[2]} already stale; "
          f"{a.from_hash} -> {new}" + ("" if a.apply else " (report only)"))


if __name__ == "__main__":
    main()
