#!/usr/bin/env python3
"""Decks and tallies for the volunteer roof check (check_roofs.html).

Design and rules: docs/maintainers/roof-check.md; scoring in
tools/roof_check_score.py.

    roof_check.py deck <label> --snapshot street_v85 [--snapshot ...]
                  [--markup 16] [--planted 0.5] [--snapshots DIR] [--out DIR]
        -> <out>/deck.json, <out>/img/*.jpg   (public: what the page shows)
           <out>/key.json                     (private: which roof, aspect and
                                               known answer each card is)
    roof_check.py tally <deck dir> <answers.json or dir> [...]
        -> <deck dir>/tally.json and a summary; answers are the inbox's
           round files (gs://solar-potential-map-data/roof-checks/inbox/) or
           the page's "Download my answers" file.

A deck holds three kinds of card, drawn the same way so they cannot be told
apart on the page:

  real     a roof as a build drew it (a snapshot from tools/cases_sheet.py
           snap); the answer is what the volunteers decide.
  markup   the owner's hand markup (data/roof_labels.json) drawn as if the
           map had made it: gold "right" on outline, lines and obstructions.
  planted  a copy of a real or markup roof with one fault put in on purpose
           (outline slid 1.2 m, a roof line taken out, an obstruction taken
           out or made up, panels hanging off the roof): gold "wrong" on that
           aspect.

A real card is gold too where the owner marked that same snapshot in
tools/mark_faults.py (data/fault_marks/<label>.json): an aspect with a mark
is gold "wrong"; a roof ticked right is gold "right" on every aspect.

Card and photo ids are random, and the building id is only in the key, so
the deck shown to volunteers does not say which cards are gold. The 30
held-back roofs of data/roof_set.json are never put in a deck.
"""
import argparse
import json
import random
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import roof_check_score as S  # noqa: E402

PAD_M = 4.0
MAX_PX = 900                 # longest side of a photo on the page
OUTLINE_SLIDE_M = 1.2        # a planted outline fault: the drawing slid this far
FAKE_OBST_M = 1.0            # a planted made-up obstruction: a square this wide
MIN_MERGE_EDGE_M = 2.0       # a planted missing line: faces sharing at least this much edge
PANEL_OFF_FRAC = 0.6
MARKUP_OTHER_PHOTO = {"pilot"}   # areas whose markup was traced on another photo         # a planted off-roof panel hangs this share of its width past the edge

# Faults from tools/mark_faults.py, by the aspect they count against.
MARK_ASPECT = {
    "line_wrong": "lines", "ridge": "panels", "outline_wrong": "outline", "off_roof": "panels",
    "missed_obstruction": "obstructions", "not_obstruction": "obstructions", "obstruction": "panels",
    "add_panels": "panels", "wrong_way": "panels", "remove": "panels", "stray": "panels",
}


def holdout_ids():
    rs = json.loads((ROOT / "data/roof_set.json").read_text())
    return {str(h["id"]) for h in rs["holdout"]}


# --- geometry: a roof is {outline, faces, obstructions, panels} of shapely polygons in NZTM ---

def roof_from_snapshot(entry, cap_panels=True):
    import pyproj
    from shapely.affinity import translate
    from shapely.geometry import shape
    from shapely.ops import transform, unary_union
    import cases_sheet as CS
    T = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    feats = [(f["properties"], translate(transform(T, shape(f["geometry"])), *entry["shift"]))
             for f in entry["features"]]
    faces = [g for q, g in feats if q["kind"] == "facet"]
    if not faces:
        return None
    cap = CS.HOME_CAP_PANELS if sum(g.area for g in faces) < CS.HOME_CAP_ROOF_M2 else 10 ** 9
    panels = [g for q, g in feats if q["kind"] == "panel"
              and (not cap_panels or (q.get("fill_order") or 0) <= cap)]
    return dict(outline=unary_union(faces).buffer(0.05).buffer(-0.05), faces=faces,
                obstructions=[g for q, g in feats if q["kind"] == "obstruction"], panels=panels)


def roof_from_markup(lab):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    faces = [Polygon(f["ring"]).buffer(0) for f in lab.get("faces") or [] if len(f.get("ring") or []) >= 4]
    faces = [f for f in faces if f.area > 0.2]
    if not faces:
        return None
    outline = Polygon(lab["outline"]).buffer(0) if len(lab.get("outline") or []) >= 4 \
        else unary_union(faces).buffer(0.05).buffer(-0.05)
    obst = [Polygon(o["ring"]).buffer(0) for o in lab.get("obstructions") or [] if len(o.get("ring") or []) >= 4]
    return dict(outline=outline, faces=faces, obstructions=obst, panels=[])


def plant(roof, aspect, rng):
    """A copy of the roof with one fault on `aspect`, or None when this roof cannot carry one."""
    from shapely.affinity import translate
    from shapely.geometry import box
    from shapely.ops import unary_union
    import math
    r = {k: list(v) if isinstance(v, list) else v for k, v in roof.items()}
    if aspect == "outline":
        a = rng.uniform(0, 2 * math.pi)
        dx, dy = OUTLINE_SLIDE_M * math.cos(a), OUTLINE_SLIDE_M * math.sin(a)
        r["outline"] = translate(roof["outline"], dx, dy)
        return r, f"outline slid {OUTLINE_SLIDE_M} m"
    if aspect == "lines":
        best = None
        for i, f in enumerate(roof["faces"]):
            for j in range(i + 1, len(roof["faces"])):
                shared = f.buffer(0.15).intersection(roof["faces"][j].boundary).length
                if shared >= MIN_MERGE_EDGE_M and (best is None or shared > best[0]):
                    best = (shared, i, j)
        if not best:
            return None
        _, i, j = best
        merged = unary_union([roof["faces"][i].buffer(0.1), roof["faces"][j].buffer(0.1)]).buffer(-0.1)
        if merged.geom_type != "Polygon":
            return None
        r["faces"] = [f for k, f in enumerate(roof["faces"]) if k not in (i, j)] + [merged]
        return r, f"line between two faces taken out ({best[0]:.1f} m)"
    if aspect == "obstructions":
        real = [o for o in roof["obstructions"] if o.area >= 0.3]
        if real and rng.random() < 0.5:
            big = max(real, key=lambda o: o.area)
            r["obstructions"] = [o for o in roof["obstructions"] if o is not big]
            return r, "largest obstruction taken out"
        for f in sorted(roof["faces"], key=lambda f: -f.area):
            inner = f.buffer(-1.5)
            if inner.is_empty:
                continue
            p = inner.representative_point()
            fake = box(p.x - FAKE_OBST_M / 2, p.y - FAKE_OBST_M / 2, p.x + FAKE_OBST_M / 2, p.y + FAKE_OBST_M / 2)
            if any(fake.distance(o) < 1.0 for o in roof["obstructions"]):
                continue
            r["obstructions"] = roof["obstructions"] + [fake]
            return r, "made-up obstruction added"
        return None
    if aspect == "panels":
        if len(roof["panels"]) < 4:
            return None
        c = roof["outline"].centroid
        far = sorted(roof["panels"], key=lambda p: -p.centroid.distance(c))[:3]
        moved = []
        for p in far:
            q = p.centroid
            d = math.hypot(q.x - c.x, q.y - c.y) or 1
            w = math.sqrt(p.area)
            moved.append(translate(p, (q.x - c.x) / d * w * PANEL_OFF_FRAC * 2,
                                   (q.y - c.y) / d * w * PANEL_OFF_FRAC * 2))
        if sum(m.difference(roof["outline"]).area for m in moved) < 0.25 * sum(m.area for m in moved):
            return None
        r["panels"] = [p for p in roof["panels"] if not any(p is f for f in far)] + moved
        return r, "three edge panels moved off the roof"
    raise ValueError(aspect)


def to_px(roof, x0, y1, res, layers):
    def rings(g):
        out = []
        for p in getattr(g, "geoms", [g]):
            if p.is_empty or p.geom_type != "Polygon":
                continue
            p = p.simplify(0.03)
            out.append([[round((x - x0) / res, 1), round((y1 - y) / res, 1)] for x, y in p.exterior.coords])
        return out
    return {k: [r for g in (v if isinstance(v, list) else [v]) for r in rings(g)] for k, v in roof.items()
            if k in layers}


# What each card draws: only the layers its question is about, plus the
# faint context it needs (a card carries nothing it does not draw).
LAYERS = {"outline": ("outline",), "lines": ("outline", "faces"),
          "obstructions": ("outline", "obstructions"), "panels": ("faces", "panels")}


def photo(roof, tiles):
    """The map photo around a roof: (jpeg bytes, x0, y1, metres a pixel, size)."""
    import io
    from PIL import Image
    from shapely.ops import unary_union
    import cases_sheet as CS
    x0, y0, x1, y1 = unary_union([roof["outline"]] + roof["faces"]).buffer(PAD_M).bounds
    s = max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    x0, y0, x1, y1 = cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2
    img = Image.fromarray(CS._photo((x0, y0, x1, y1), tiles))
    res = CS.PHOTO_RES_M
    if img.width > MAX_PX:
        res = res * img.width / MAX_PX
        img = img.resize((MAX_PX, MAX_PX), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue(), x0, y1, res, img.width


def deck(label, snapshots, snap_dir, markup_n, planted_share, out, seed):
    rng = random.Random(seed)
    hold = holdout_ids()
    tiles = snap_dir / "tiles"
    out.mkdir(parents=True, exist_ok=True)
    (out / "img").mkdir(exist_ok=True)
    roofs = []   # (building id, source, roof, marks per aspect or None)
    for sl in snapshots:
        snap = json.loads((snap_dir / f"{sl}.json").read_text())["buildings"]
        mf = ROOT / "data/fault_marks" / f"{sl}.json"
        marks = {str(r["id"]): r for r in json.loads(mf.read_text())["roofs"]} if mf.exists() else {}
        for bid, e in sorted(snap.items()):
            if bid in hold:
                continue
            r = roof_from_snapshot(e)
            if r:
                roofs.append((bid, f"snapshot:{sl}", r, marks.get(bid)))
    labels = json.loads((ROOT / "data/roof_labels.json").read_text())["buildings"]
    dev = [str(d["id"]) for d in json.loads((ROOT / "data/roof_set.json").read_text())["dev"]]
    # Homes first (the owner's review format is houses, one pick per aspect):
    # the reference roofs' homes, then the development roofs, then the rest.
    homes = [r["id"] for r in json.loads((ROOT / "data/roof100.json").read_text())["roofs"]
             if r["cls"] in ("home", "centre home")]
    pool = list(dict.fromkeys([b for b in homes + dev if b in labels and b not in hold]
                              + sorted(b for b in labels if b not in hold)))
    # Markup is gold only where it was traced on the map's own photo: the pilot's
    # was traced on the survey-year photo, which leans up to a few metres off
    # the map's, so a right drawing would look wrong on the card.
    pool = [b for b in pool if labels[b].get("complete") and not labels[b].get("problem")
            and labels[b].get("area") not in MARKUP_OTHER_PHOTO]
    seen_ids = {b for b, *_ in roofs}
    markups = []
    for b in pool:
        if len(markups) >= markup_n:
            break
        if b in seen_ids:
            continue
        r = roof_from_markup(labels[b])
        if r:
            markups.append((b, "markup", r, None))

    cards, key, photos = [], {}, {}

    def add(bid, source, roof, aspect, gold, why, img_key):
        cid = secrets.token_hex(6)
        ph = photos[img_key]
        cards.append(dict(card=cid, photo=ph["id"], size=ph["size"], aspect=aspect,
                          layers=to_px(roof, ph["x0"], ph["y1"], ph["res"], LAYERS[aspect])))
        kind = "planted" if (why or "").startswith("planted") else ("markup" if source == "markup" else "real")
        key[cid] = dict(roof=bid, aspect=aspect, gold=gold, kind=kind, source=source, why=why,
                        photo=ph["id"], x0=ph["x0"], y1=ph["y1"], res=ph["res"], size=ph["size"])

    for bid, source, roof, marks in roofs + markups:
        jpg, x0, y1, res, size = photo(roof, tiles)
        pid = secrets.token_hex(6)
        (out / "img" / f"{pid}.jpg").write_bytes(jpg)
        photos[bid] = dict(id=pid, x0=round(x0, 2), y1=round(y1, 2), res=round(res, 4), size=size)
        for asp in S.ASPECTS:
            if source == "markup":
                if asp == "panels":
                    continue
                add(bid, source, roof, asp, "right", "owner's markup", bid)
            else:
                gold, why = None, None
                if marks is not None:
                    hit = [s["fault"] for s in marks.get("spots", []) if MARK_ASPECT.get(s["fault"]) == asp]
                    hit += [p["fault"] for p in marks.get("panels", []) if MARK_ASPECT.get(p["fault"]) == asp]
                    if hit:
                        gold, why = "wrong", "owner marked: " + ", ".join(sorted(set(hit)))
                    elif marks.get("right"):
                        gold, why = "right", "owner ticked the roof right"
                add(bid, source, roof, asp, gold, why, bid)
            if rng.random() < planted_share:
                p = plant(roof, asp, rng)
                if p:
                    add(bid, source, p[0], asp, "wrong", "planted: " + p[1], bid)
    rng.shuffle(cards)
    deck_doc = dict(deck=label, aspects=list(S.ASPECTS), cards=cards)
    (out / "deck.json").write_text(json.dumps(deck_doc, separators=(",", ":")))
    (out / "key.json").write_text(json.dumps(dict(deck=label, cards=key), indent=1))
    n = lambda f: sum(1 for k in key.values() if f(k))
    print(f"{label}: {len(cards)} cards on {len(photos)} roofs -> {out}")
    print(f"  from builds {n(lambda k: k['source'] != 'markup')}, gold right {n(lambda k: k['gold'] == 'right')},"
          f" gold wrong {n(lambda k: k['gold'] == 'wrong')}"
          f" (planted {n(lambda k: (k['why'] or '').startswith('planted'))}), undecided {n(lambda k: k['gold'] is None)}")


def _answers(paths):
    out = []
    for p in paths:
        p = Path(p)
        files = sorted(p.glob("*.json")) if p.is_dir() else [p]
        for f in files:
            d = json.loads(f.read_text())
            for b in d if isinstance(d, list) else [d]:
                for a in b.get("answers", []):
                    out.append({**a, "volunteer": b["volunteer"], "deck": b.get("deck")})
    return out


def tally(deck_dir, paths):
    deck_dir = Path(deck_dir)
    k = json.loads((deck_dir / "key.json").read_text())
    key = k["cards"]
    answers = [a for a in _answers(paths) if a.get("deck") in (None, k["deck"])]
    decisions, rel = S.decide(answers, key)
    audit = S.gold_audit(answers, key, rel)
    for d in decisions.values():
        d["need"] = S.votes_needed(d)
    (deck_dir / "tally.json").write_text(json.dumps(
        dict(deck=k["deck"], volunteers=rel, roofs=decisions, gold_to_recheck=audit), indent=1))
    from collections import Counter
    print(f"{len(answers)} answers from {len({a['volunteer'] for a in answers})} people")
    for asp in S.ASPECTS:
        r = sum(1 for v in rel.values() if v.get(asp, {}).get("reliable"))
        print(f"  reliable on {asp:12s} {r}")
    print("  roofs:", dict(Counter(d["state"] for d in decisions.values())))
    for g in audit:
        print(f"  gold to recheck: roof {g['roof']} {g['aspect']} (key says {g['gold']}, "
              f"{g['against']} of {g['answers']} reliable answers disagree; {g['why']})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deck")
    d.add_argument("label")
    d.add_argument("--snapshot", action="append", default=[])
    d.add_argument("--snapshots", default=str(ROOT / "data/case_snapshots"))
    d.add_argument("--markup", type=int, default=16)
    d.add_argument("--planted", type=float, default=0.5, help="share of roof aspects given a planted copy")
    d.add_argument("--out")
    d.add_argument("--seed", type=int, default=7)
    t = sub.add_parser("tally")
    t.add_argument("deck_dir")
    t.add_argument("answers", nargs="+")
    a = ap.parse_args()
    if a.cmd == "deck":
        deck(a.label, a.snapshot, Path(a.snapshots), a.markup, a.planted,
             Path(a.out) if a.out else ROOT / "data/roof_check" / a.label, a.seed)
    else:
        tally(a.deck_dir, a.answers)


if __name__ == "__main__":
    main()
