#!/usr/bin/env python3
"""Every reported roof (data/roof_cases.json) as the map draws it, version
beside version.

THE RULE. Each build that is shown to the owner gets a snapshot of every
reported roof: its faces, obstructions and panels and the shift the map
moves it by. A sheet sets two snapshots side by side over the map's own
photo, so each release shows what every reported roof has become. A roof
is never dropped from the sheets, fixed or not.

THE REASON. Reported roofs lived in chat screenshots; a fix was claimed
from a different render, or never reached the map, and nobody looked at
the same roof again (tools/cases.py header). tools/render_built.py draws
the survey photo without the map's shift, which is not what the owner saw.

  snap  <label> [--layouts <dir>]   write data/case_snapshots/<label>.json
        from data/regions (the shift emit uses, register_imagery.drawing_shifts)
        or from a kept copy <dir>/<region>/{panel_layouts.geojson,image_shift.json}
        (the shift on file only: a roof led by a photo reading in that
        build may be drawn up to its reading's slide from where the map had it)
  sheet <out dir> <before label> <now label>   cases_<n>.jpg, 6 roofs a sheet

Solid blue panels are what the map shows by default: the first
HOME_CAP_PANELS in fill order on a roof under HOME_CAP_ROOF_M2 of faces,
every panel on a larger one (building type is not in the layout, so a small
business is drawn as a home). Outlined panels are the rest the slider can reach.
Held-back roofs (data/roof_set.json holdout) are skipped.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SNAPS = ROOT / "data" / "case_snapshots"
REGIONS = ROOT / "data" / "regions"
HOME_CAP_PANELS = 28        # src/emit_region.py HOME_CAP_KW at 500 W panels
HOME_CAP_ROOF_M2 = 350      # src/emit_region.py
PAD_M = 6.0
PER_SHEET = 6


def _cases():
    hold = {int(h["id"]) for h in json.loads((ROOT / "data/roof_set.json").read_text())["holdout"]}
    return [c for c in json.loads((ROOT / "data/roof_cases.json").read_text())["cases"]
            if int(c["id"]) not in hold]


def snap(label, layouts=None):
    from src.register_imagery import drawing_shifts
    base = Path(layouts).expanduser() if layouts else REGIONS
    cases = _cases()
    want = {int(c["id"]) for c in cases}
    found = {}
    # the case's own region first, then any other region that holds it
    order = sorted({c.get("region") for c in cases if c.get("region")}) + \
        sorted(p.parent.name for p in base.glob("*/panel_layouts.geojson"))
    seen = set()
    for reg in order:
        f = base / reg / "panel_layouts.geojson"
        if reg in seen or not f.exists() or not (want - set(found)):
            continue
        seen.add(reg)
        by = {}
        for ft in json.loads(f.read_text())["features"]:
            b = ft["properties"].get("building_id")
            if b in want and b not in found:
                by.setdefault(int(b), []).append(ft)
        if not by:
            continue
        if layouts:
            sf = base / reg / "image_shift.json"
            sh = json.loads(sf.read_text()) if sf.exists() else {}
        else:
            sh = drawing_shifts(REGIONS / reg)
        for b, feats in by.items():
            found[b] = dict(region=reg, shift=[float(v) for v in (sh.get(str(b)) or sh.get(b) or [0, 0, 0])[:2]],
                            features=feats)
    SNAPS.mkdir(parents=True, exist_ok=True)
    (SNAPS / f"{label}.json").write_text(json.dumps(
        dict(label=label, shift_source="image_shift.json" if layouts else "drawing_shifts", buildings=found)))
    print(label, ":", len(found), "of", len(want), "reported roofs have a layout; missing",
          sorted(want - set(found)))


def _draw(ax, entry, bounds, tiles, T):
    import numpy as np
    from shapely.affinity import translate
    from shapely.geometry import shape
    from shapely.ops import transform
    from src.register_imagery import RES_M
    x0, y0, x1, y1 = bounds
    img = np.moveaxis(np.asarray(tiles.window((x0, y0, x1, y1), int(round((y1 - y0) / RES_M)),
                                              int(round((x1 - x0) / RES_M)))), 0, -1)
    ax.imshow(img, extent=(x0, x1, y0, y1))
    n = 0
    feats = [(f["properties"], transform(T, shape(f["geometry"]))) for f in (entry or {}).get("features", [])]
    cap = HOME_CAP_PANELS if sum(g.area for q, g in feats if q["kind"] == "facet") < HOME_CAP_ROOF_M2 else 10 ** 9
    for q, g in feats:
        g = translate(g, *entry["shift"])
        for p in getattr(g, "geoms", [g]):
            x, y = p.exterior.xy
            if q["kind"] == "facet":
                ax.plot(x, y, color="red", lw=1.3)
            elif q["kind"] == "obstruction":
                ax.fill(x, y, color="orange", alpha=.55)
            elif q["kind"] == "panel":
                n += 1
                if (q.get("fill_order") or 0) <= cap:
                    ax.fill(x, y, color="#1f4fff", alpha=.6, lw=.3, ec="white")
                else:
                    ax.plot(x, y, color="#7fd4ff", lw=.5)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_axis_off()
    return n


def sheet(out_dir, before, now):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pyproj
    from shapely.geometry import shape
    from shapely.ops import transform, unary_union
    from src.register_imagery import _Tiles
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    A = json.loads((SNAPS / f"{before}.json").read_text())["buildings"]
    B = json.loads((SNAPS / f"{now}.json").read_text())["buildings"]
    T = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    rows = [c for c in _cases() if str(c["id"]) in A or str(c["id"]) in B]
    tiles = {}
    for page in range(0, len(rows), PER_SHEET):
        fig, ax = plt.subplots(3, 4, figsize=(24, 18.6))
        for a in ax.ravel():
            a.axis("off")
        for n, c in enumerate(rows[page:page + PER_SHEET]):
            ea, eb = A.get(str(c["id"])), B.get(str(c["id"]))
            reg = (eb or ea)["region"]
            if reg not in tiles:
                tiles[reg] = _Tiles(REGIONS / reg / "display_tiles")
            geom = unary_union([transform(T, shape(f["geometry"])) for e in (ea, eb) if e
                                for f in e["features"] if f["properties"]["kind"] == "facet"]
                               or [transform(T, shape(f["geometry"])) for e in (ea, eb) if e for f in e["features"]])
            x0, y0, x1, y1 = geom.buffer(PAD_M).bounds
            s = max(x1 - x0, y1 - y0)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            bounds = (cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2)
            na = _draw(ax.ravel()[2 * n], ea, bounds, tiles[reg], T)
            nb = _draw(ax.ravel()[2 * n + 1], eb, bounds, tiles[reg], T)
            ax.ravel()[2 * n].set_title(f"{before.upper()}  #{c['id']}  {na} panels\n{c['defect'][:70]}", fontsize=11)
            ax.ravel()[2 * n + 1].set_title(f"{now.upper()}  {nb} panels   reported {c['first_flagged']}", fontsize=11)
        plt.tight_layout(h_pad=2.5)
        plt.savefig(out_dir / f"cases_{page // PER_SHEET + 1}.jpg", dpi=60)
        plt.close(fig)
    print(len(rows), "reported roofs on", (len(rows) + PER_SHEET - 1) // PER_SHEET, "sheets in", out_dir)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "snap":
        snap(sys.argv[2], sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "--layouts" else None)
    elif len(sys.argv) == 5 and sys.argv[1] == "sheet":
        sheet(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(__doc__)
