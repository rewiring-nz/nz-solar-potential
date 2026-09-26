"""
The served output's contract: the names the map reads, frozen.

WHY. The geometry will keep changing after launch -- that is the plan -- and
the map must not break when it does. What the browser depends on is not the
pipeline's internals but a handful of files and the property names inside
them. This module is the list of those names, written out literally rather
than imported from the code that produces them, so a change to what ships is
a deliberate edit here and not a side effect of a refactor:

  tests/test_output_contract.py fails if emit_region's lists drift from this
  tests/synthetic/run.py         validates a real region's output against it

To change what ships: edit here, bump CONTRACT_VERSION, update preview.html in
the same commit, and say why. Adding an OPTIONAL property is backwards
compatible; removing or renaming anything is not.

Every region also records its MODEL VERSION (summary.json "model") and every
building its own ("mv" in its detail record): the geometry code that laid it
out and the solar code that priced it, six characters each
(src/build_keys.model_version). When a building's numbers move between two
builds, which half changed says why.
"""

import json
import re
import subprocess
from pathlib import Path

CONTRACT_VERSION = 1

# buildings.pmtiles, layer "buildings": what the map draws and the panel reads.
BUILDING_LAYER = "buildings"
BUILDING_REQUIRED = ["building_id"]
BUILDING_ALLOWED = [
    "building_id", "address", "address_count",
    "kwp", "panel_count", "ac_kwh_year", "ac_kwh_day_avg",
    "facet_area_m2", "facet_count", "obstruction_count",
    "avg_poa_kwh_m2", "roof_confidence",
    "no_estimate_reason", "no_estimate_text",
    "cov_poa_5", "cov_poa_10", "cov_poa_15", "cov_poa_25", "cov_poa_50",
    "cov_poa_75", "cov_poa_100",
    "fill_panels_10", "fill_panels_20", "fill_panels_30", "fill_panels_40",
    "fill_panels_50", "fill_panels_60", "fill_panels_70", "fill_panels_80",
    "fill_panels_90", "fill_panels_100",
    "fill_kwh_10", "fill_kwh_20", "fill_kwh_30", "fill_kwh_40", "fill_kwh_50",
    "fill_kwh_60", "fill_kwh_70", "fill_kwh_80", "fill_kwh_90", "fill_kwh_100",
    "fill_panels_arrays", "fill_kwh_arrays",
    "sys_kwh_7", "sys_kwh_10", "sys_kwh_14", "sys_kwh_17", "sys_kwh_20",
    "sys_kwh_27", "sys_kwh_34", "sys_kwh_45", "sys_kwh_68",
    "btype", "use", "name", "img_dx", "img_dy",
]

# panel_layouts.pmtiles, layer "layout": facets, panels and obstructions.
LAYOUT_LAYER = "layout"
LAYOUT_ALLOWED = ["kind", "building_id", "fill_rank", "fill_order", "array_id",
                  "array_size", "ac_kwh_year", "slope_deg", "aspect_deg",
                  "roof_confidence", "poa_kwh_m2_yr", "panel_count", "btype"]
LAYOUT_REQUIRED = {
    "facet": ["kind", "building_id", "slope_deg", "aspect_deg", "poa_kwh_m2_yr",
              "panel_count", "btype"],
    "panel": ["kind", "building_id", "fill_rank", "fill_order", "array_id",
              "array_size", "ac_kwh_year", "btype"],
    "obstruction": ["kind", "building_id", "btype"],
    # A building with no usable roof surface is retained as a footprint
    # feature so its absence from the estimate is explicit. The map draws the
    # building's no_estimate_reason from buildings.pmtiles; this layout copy
    # remains in the region's layout stream and is not styled as a panel.
    "no_estimate": ["kind", "building_id", "btype"],
}

# detail/13/x/y.json: {building_id: {...}}, fetched when a building is clicked.
DETAIL_ALLOWED = ["horizon_b64", "horizon_far_b64", "tshade", "horizon_beam_pct", "mv"]
DETAIL_REQUIRED = ["mv"]

# cells.json: {"z/x/y/btype": {...}}, summed per cell by combine.
CELL_REQUIRED = ["n", "n_est", "panel_count", "fitted_kwp", "fitted_kwh", "area"] \
    + [f"kwh_{p}" for p in (5, 10, 15, 25, 50, 75, 100)] \
    + [f"kwp_{p}" for p in (5, 10, 15, 25, 50, 75, 100)]

# summary.json: what the deploy gate, status and combine read.
SUMMARY_REQUIRED = ["region", "n", "n_est", "panel_count", "kwp", "kwh", "ladder",
                    "by_type", "centroid", "assumptions", "model", "contract"]

# addresses.json: [[label, lon, lat, building_id], ...]
ADDRESS_ROW_LEN = 4

_MV = re.compile(r"^g(unknown|[0-9a-f]{6})-y(unknown|mixed|[0-9a-f]{6})$")


def validate_layout_feature(layer, properties):
    """Return a schema problem for one layout tile feature, else None."""
    if layer != LAYOUT_LAYER:
        return f"panel_layouts.pmtiles: layer {layer!r}"
    kind = properties.get("kind")
    required = LAYOUT_REQUIRED.get(kind)
    if required is None:
        return f"panel_layouts.pmtiles: kind {kind!r} is not in the layout contract"
    extra = set(properties) - set(LAYOUT_ALLOWED)
    missing = [key for key in required if key not in properties]
    if extra or missing:
        return (f"panel_layouts.pmtiles: kind {kind!r} extra {sorted(extra)} "
                f"missing {missing}")
    return None


def _decode(pmtiles):
    """Every feature's (layer, properties) from a pmtiles file, via
    tippecanoe-decode."""
    r = subprocess.run(["tippecanoe-decode", str(pmtiles)], capture_output=True, text=True,
                       check=True)
    out = []
    doc = json.loads(r.stdout)
    for tile in doc.get("features", []):
        for layer in tile.get("features", []):
            name = layer.get("properties", {}).get("layer")
            for f in layer.get("features", []):
                out.append((name, f.get("properties", {})))
    return out


def validate_region(out_dir):
    """Problems with one region's emitted output (data/out/<region>/), as a
    list of strings; empty when it keeps the contract."""
    out_dir = Path(out_dir)
    bad = []
    s = json.loads((out_dir / "summary.json").read_text())
    bad += [f"summary.json: missing {k}" for k in SUMMARY_REQUIRED if k not in s]
    if s.get("contract") != CONTRACT_VERSION:
        bad.append(f"summary.json: contract {s.get('contract')} != {CONTRACT_VERSION}")

    feats = _decode(out_dir / "buildings.pmtiles")
    if not feats:
        bad.append("buildings.pmtiles: no features")
    for layer, p in feats:
        if layer != BUILDING_LAYER:
            bad.append(f"buildings.pmtiles: layer {layer!r}")
            break
        extra = set(p) - set(BUILDING_ALLOWED)
        miss = [k for k in BUILDING_REQUIRED if k not in p]
        if extra or miss:
            bad.append(f"buildings.pmtiles: extra {sorted(extra)} missing {miss}")
            break

    for layer, p in _decode(out_dir / "panel_layouts.pmtiles"):
        problem = validate_layout_feature(layer, p)
        if problem:
            bad.append(problem)
            break

    for f in sorted((out_dir / "detail").rglob("*.json")):
        for bid, d in json.loads(f.read_text()).items():
            extra = set(d) - set(DETAIL_ALLOWED)
            miss = [k for k in DETAIL_REQUIRED if k not in d]
            if extra or miss or not _MV.match(str(d.get("mv", ""))):
                bad.append(f"{f.relative_to(out_dir)} #{bid}: extra {sorted(extra)} missing {miss} "
                           f"mv={d.get('mv')!r}")
                break

    cells = json.loads((out_dir / "cells.json").read_text())
    for k, c in list(cells.items())[:50]:
        miss = [x for x in CELL_REQUIRED if x not in c]
        if miss:
            bad.append(f"cells.json {k}: missing {miss}")
            break

    for row in json.loads((out_dir / "addresses.json").read_text())[:50]:
        if len(row) != ADDRESS_ROW_LEN:
            bad.append(f"addresses.json: row of {len(row)}")
            break
    return bad
