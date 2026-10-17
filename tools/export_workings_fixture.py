"""Export real panels and the Python model's own numbers for them, so the
browser's arithmetic (generation.js) is tested against the pipeline in CI.

    python tools/export_workings_fixture.py kinloch > tests/fixtures/workings_kinloch.json

Writes the region's sunlight document (as emit ships it), every panel's
tile inputs (sb, ab, sh -- exactly as shipped) with the pipeline's
ac_kwh_year and the unrounded kWh Python computes from those inputs, and
each building's ac_kwh_year and roof figures from solar_potential.
tests/test_generation.mjs then requires the JavaScript to reproduce all of
it. Regenerate after any change to the solar model or the panel figures.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import workings_data as WD  # noqa: E402
from src.region_build import area_paths, area_centroid_wgs84  # noqa: E402


def main(region):
    paths = area_paths(region)
    lay = json.loads(paths["panel_layouts"].read_text())
    sp = json.loads(paths["solar_potential"].read_text())
    model, captured, kind = WD.build_model_and_capture(*(area_centroid_wgs84(region) or (None, None)))
    lat, lon = model.lat, model.lon
    checks = WD.tag_and_check_panels(lay, model)
    if checks["mismatched"] or checks["missing_inputs"]:
        raise SystemExit(f"region does not check out: {checks}")
    panels, faces, used = {}, {}, set()
    for f in lay["features"]:
        p = f["properties"]
        b = p.get("building_id")
        if p.get("kind") == "panel":
            used.add((p["sb"], p["ab"]))
            panels.setdefault(str(b), []).append({
                "sb": p["sb"], "ab": p["ab"], "sh": p["sh"], "ac_kwh_year": p["ac_kwh_year"],
                "py_exact": WD.panel_kwh(model, p["sb"], p["ab"], p["sh"]),
                "fill_rank": p.get("fill_rank"), "fill_order": p.get("fill_order")})
        elif p.get("kind") == "facet" and p.get("plane_slope_deg") is not None:
            sb, ab = WD.bins_of(p["plane_slope_deg"], p["plane_aspect_deg"])
            used.add((sb, ab))
    buildings = {}
    for f in sp["features"]:
        p = f["properties"]
        b = str(p["building_id"])
        keep = {k: p.get(k) for k in ("ac_kwh_year", "panel_count", "kwp", "facet_area_m2",
                                      "avg_poa_kwh_m2") if p.get(k) is not None}
        keep.update({k: v for k, v in p.items() if k.startswith("cov_poa_")})
        buildings[b] = keep
    doc = {"region": region, "assumptions": sp.get("assumptions", {}),
           "sun": WD.sun_doc(region, lat, lon, model, captured, kind, used),
           "panels": panels, "buildings": buildings}
    json.dump(doc, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main(sys.argv[1])
