#!/usr/bin/env python3
"""The 100 reference roofs and their ledger.

The plan (owner, 24 Nov): take 100 example roofs, refine the method until
every one is right, then scale. A roof is RIGHT when, against the hand
markup and within 0.5 m:

  outline  the roof edge is the drawn one: 95% of each edge lies on the
           other, and they overlap 0.90 or more;
  lines    no drawn line is missing (95% of the drawn line length is read)
           and no line is invented (95% of the read line length is drawn);
  faces    every drawn face has its own read face (overlap 0.8 or more, or
           95% of each one's edge within 0.5 m of the other's) and
           the two have the same number of faces (a face under 0.5 m wide is
           inside the tolerance and is neither required nor counted).

Each roof is read by a model that did not train on it (five folds,
data/line_set/folds.json), so a right roof says something about the next
roof and not about memory. The 30 held-back roofs of data/roof_set.json are
never in the 100.

    roof_ledger.py pick                       -> data/roof100.json
    roof_ledger.py round <label> <maps dir>   -> data/roof100/<label>.json, data/preview/roof100/<label>.html
        (SOLAR_LINE_* switches from the environment; the reader's own roof edge)
    roof_ledger.py diff <label a> <label b>   roofs that changed state
"""
import json, os, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SET = ROOT / "data/roof100.json"
N_BY_CLASS = {"home": 50, "centre home": 20, "larger": 30}
UNUSED_MAX_M = 0.5


def pick():
    import random
    base = json.loads((ROOT / "data/line_set/cmp/J_own_base.json").read_text())      # class of every drawn roof with an unseen reading
    rs = json.loads((ROOT / "data/roof_set.json").read_text())
    hold = {str(h["id"]) for h in rs["holdout"]}; dev = {str(d["id"]) for d in rs["dev"]}
    labels = json.loads((ROOT / "data/roof_labels.json").read_text())["buildings"]
    unused = json.loads((ROOT / "data/line_set/faces_joined_side_unused_m.json").read_text())
    # a reference roof is one the marker flagged as fully marked ("I marked EVERY line on this roof"),
    # with no problem noted, whose every drawn line closes a face (0.5 m or less of line dividing
    # nothing): on any other roof a read line that is not drawn cannot be called invented
    whole = lambda i: labels[i].get("complete") and not labels[i].get("problem") and unused.get(i, 0) <= UNUSED_MAX_M
    out = []
    for cls, n in N_BY_CLASS.items():
        ids = sorted(i for i, v in base.items() if v["cls"] == cls and i not in hold)
        random.Random(100).shuffle(ids)
        ids = [i for i in ids if whole(i)]
        ids.sort(key=lambda i: i not in dev)                 # roofs already in the development set first
        out += [{"id": i, "cls": cls} for i in ids[:n]]
    SET.write_text(json.dumps({"rule": "50 homes outside the town centre, 20 town-centre homes, 30 larger roofs, all hand-drawn and flagged fully marked, every drawn line closing a face, none held back", "roofs": out}, indent=1))
    print(len(out), "roofs ->", SET)


def state(v):
    o = v["edge"] >= 0.90 and v["edge_found"] >= 0.95 and v["edge_real"] >= 0.95
    l = v["line_found"] >= 0.95 and v["line_real"] >= 0.95
    f = v.get("faces_off", int(v["face_min"] < 0.8)) == 0 and v["read_wide"] == v["drawn_wide"]
    return {"outline": o, "lines": l, "faces": f, "missing": v["line_found"] < 0.95, "invented": v["line_real"] < 0.95}


def round_(label, maps):
    out = ROOT / f"data/roof100/{label}.json"; out.parent.mkdir(parents=True, exist_ok=True)
    page = ROOT / f"data/preview/roof100/{label}"; page.mkdir(parents=True, exist_ok=True)
    raw = out.with_suffix(".all.json")
    subprocess.run([sys.executable, str(ROOT / "tools/experiments/roof_compare.py"), "score", maps, os.environ.get("ROOF100_EDGE", "regions_own"), str(raw)],
                   cwd=ROOT, env=dict(os.environ, SOLAR_TILES_DIR=str(page / "tiles")), check=True, stdout=subprocess.DEVNULL)
    allr = json.loads(raw.read_text()); raw.unlink()
    roofs = json.loads(SET.read_text())["roofs"]
    led = {r["id"]: dict(allr[r["id"]], **state(allr[r["id"]])) for r in roofs if r["id"] in allr}
    out.write_text(json.dumps(led, indent=1))
    report(label, led)
    write_page(label, led)


def write_page(label, led):
    """One self-contained page (the tiles are inside it: a viewer that opens the file alone still shows them)."""
    import base64
    page = ROOT / f"data/preview/roof100/{label}"
    rows = sorted(led.items(), key=lambda kv: (kv[1]["outline"] + kv[1]["lines"] + kv[1]["faces"], kv[1]["fiou"]))
    b = lambda ok, t: f'<span class="{"y" if ok else "n"}">{t}</span>'
    img = lambda i: "data:image/jpeg;base64," + base64.b64encode((page / "tiles" / f"{i}.jpg").read_bytes()).decode()
    cards = "".join(f'<div class="c"><img loading="lazy" src="{img(i)}"><div>#{i} · {v["cls"]} {b(v["outline"], "outline")}{b(v["lines"], "lines")}{b(v["faces"], "faces")}'
                    f'{"<i> missing lines</i>" if v["missing"] else ""}{"<i> invented lines</i>" if v["invented"] else ""}</div></div>' for i, v in rows)
    right = sum(v["outline"] and v["lines"] and v["faces"] for v in led.values())
    (page / "index.html").write_text(f'<!doctype html><meta charset=utf8><title>Roof 100: {label}</title><style>body{{font:14px system-ui;margin:16px;background:#111;color:#eee}}'
        f'.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:12px}}img{{width:100%}}.y,.n{{margin-left:6px;padding:1px 6px;border-radius:3px}}.y{{background:#1c6b2f}}.n{{background:#8a2020}}i{{color:#f0b040}}</style>'
        f'<h2>Round {label}: {right} of {len(led)} roofs right</h2><p>Left: hand markup (yellow). Right: read by a model that never saw the roof (red). Green: that part is right. Worst first.</p><div class="g">{cards}</div>')
    print("page:", page / "index.html")


def page(label):
    write_page(label, json.loads((ROOT / f"data/roof100/{label}.json").read_text()))


def report(label, led):
    print(f"round {label}: {len(led)} roofs")
    for cls in ("all",) + tuple(N_BY_CLASS):
        r = [v for v in led.values() if cls == "all" or v["cls"] == cls]
        print("  %-12s %3d  right %3d  outline %3d  lines %3d  faces %3d   missing lines %3d  invented lines %3d"
              % (cls, len(r), sum(v["outline"] and v["lines"] and v["faces"] for v in r), sum(v["outline"] for v in r), sum(v["lines"] for v in r),
                 sum(v["faces"] for v in r), sum(v["missing"] for v in r), sum(v["invented"] for v in r)))


def diff(a, b):
    A, B = (json.loads((ROOT / f"data/roof100/{x}.json").read_text()) for x in (a, b))
    ok = lambda v: v["outline"] and v["lines"] and v["faces"]
    print("newly right:", [i for i in A if i in B and not ok(A[i]) and ok(B[i])]); print("no longer right:", [i for i in A if i in B and ok(A[i]) and not ok(B[i])])
    for k in ("outline", "lines", "faces"):
        print(f"  {k}: gained {sum(1 for i in A if i in B and not A[i][k] and B[i][k])}, lost {sum(1 for i in A if i in B and A[i][k] and not B[i][k])}")


if __name__ == "__main__":
    {"pick": pick, "round": round_, "diff": diff, "page": page}[sys.argv[1]](*sys.argv[2:])
