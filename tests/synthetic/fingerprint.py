"""Canonical fingerprint of every output the synthetic region produced.

JSON/GeoJSON files are parsed and re-serialised with sorted keys, volatile
fields (timestamps, git shas, elapsed seconds) dropped; binary files are
hashed. Prints {relative path: sha256 or canonical-json digest} plus a few
headline numbers so a diff says WHAT moved."""
import hashlib
import json
import sys
from pathlib import Path

W = Path(sys.argv[1]).resolve()
VOLATILE = {"generated", "generated_utc", "finished_utc", "built_utc", "timestamp",
            "commit", "git_sha", "sha", "seconds", "elapsed_s", "built_at", "created",
            "time", "date", "version_time", "build_time", "git"}


def scrub(o):
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in sorted(o.items()) if k not in VOLATILE}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


out, headline = {}, {}
roots = [W / "data" / "regions" / "zz_harness", W / "data" / "out" / "zz_harness"]
for root in roots:
    if not root.exists():
        continue
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix in (".tif", ".laz", ".log"):
            continue
        rel = str(p.relative_to(W))
        # inputs, and the build keys (which carry code hashes, so they change
        # with every commit by design)
        if p.name in ("building_outlines.geojson", "task.json", "built_from.json"):
            continue
        b = p.read_bytes()
        if p.suffix == ".pmtiles":
            import re, subprocess
            txt = subprocess.run(["tippecanoe-decode", str(p)], capture_output=True, text=True).stdout
            txt = re.sub(r'^"(description|generator_options|name)".*$', "", txt, flags=re.M)
            b = txt.replace(str(W), "<W>").encode()
        if p.suffix in (".json", ".geojson"):
            try:
                o = scrub(json.loads(b))
                b = json.dumps(o, sort_keys=True).encode()
                if p.name == "panel_layouts.geojson":
                    fs = o.get("features", [])
                    headline["panels"] = sum(1 for f in fs if f["properties"].get("kind") == "panel")
                    headline["features"] = len(fs)
                if p.name == "solar_potential.geojson":
                    fs = o.get("features", [])
                    headline["kwh_total"] = round(sum(f["properties"].get("ac_kwh_year") or 0 for f in fs), 3)
                    headline["per_building"] = {f["properties"]["building_id"]:
                                                (f["properties"].get("panel_count"),
                                                 f["properties"].get("facet_count"),
                                                 f["properties"].get("ac_kwh_year"))
                                                for f in fs}
            except Exception:
                pass
        out[rel] = hashlib.sha256(b).hexdigest()[:16]
print(json.dumps({"headline": headline, "files": out}, indent=1, sort_keys=True))
