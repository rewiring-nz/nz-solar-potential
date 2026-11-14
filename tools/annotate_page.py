#!/usr/bin/env python3
"""Build a page where the owner pins what is wrong on a roof drawing.

A pick between two drawings says which is better, not what is wrong: on the
13 Nov house review the owner preferred production's roof lines on 8 of 24
houses and the picks did not say which line or why. Here each roof is shown
once, large, beside the plain photo; the owner clicks the spot that is
wrong and types a note there. Each pin is stored as a fraction of the image
and the roof's map bounds are in the answer key, so a pin converts to NZTM
and to the face or line it sits on.

    tools/annotate_page.py --layout ~/ab/on.json --region pilot \
        --ids 4734808 4734825 --out ~/annotate --title "Point roof notes"

--layout is a tools/layout_ab.py output (facets, obstructions, panels in
EPSG:4326). Writes out/index.html, out/img/<key>_{photo,draw}.jpg and
out/answer_key.json ({key, id, bounds}). Publish index.html with img/ as
supporting files and the db capability; answers go to the "notes"
collection, stay in the viewer's browser too, and "Copy my notes" gives
them as text to paste into the chat.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PAD_M = 5.0
PX = 900

PAGE = r"""<title>__TITLE__</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&display=swap">
<style>
:root { --bg:#f3f5f7; --panel:#fff; --fg:#1d2630; --muted:#5d6a78; --line:#d5dce3; --pick:#1f6f4a; --pick-bg:#e3f2ea; --pin:#d6249f;
  --sans:"Source Sans 3",system-ui,-apple-system,"Segoe UI",sans-serif; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#12171c; --panel:#1b222a; --fg:#e6ebf0; --muted:#9aa7b4; --line:#33404d; --pick:#6fd3a2; --pick-bg:#18352a; --pin:#ff6ad0; color-scheme:dark } }
:root[data-theme="dark"] { --bg:#12171c; --panel:#1b222a; --fg:#e6ebf0; --muted:#9aa7b4; --line:#33404d; --pick:#6fd3a2; --pick-bg:#18352a; --pin:#ff6ad0; color-scheme:dark }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--fg); font-family:var(--sans); line-height:1.45 }
.wrap { max-width:1180px; margin:0 auto; padding:20px 16px 60px }
h1 { font-size:1.5rem; margin:0 0 6px } .intro { color:var(--muted); margin:0 0 12px; max-width:70ch }
.legend { display:flex; gap:16px; flex-wrap:wrap; color:var(--muted); font-size:.9rem; margin-bottom:10px }
.sw { display:inline-block; width:12px; height:12px; border-radius:2px; margin-right:5px; vertical-align:-1px }
.dots { display:flex; flex-wrap:wrap; gap:5px; margin:8px 0 }
.dot { width:20px; height:20px; border-radius:50%; border:1px solid var(--line); background:var(--panel); padding:0; cursor:pointer }
.dot.done { background:var(--pick); border-color:var(--pick) } .dot.cur { outline:2px solid var(--fg); outline-offset:1px }
.tools { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:10px } .count { color:var(--muted) }
.btn { font:inherit; padding:7px 13px; border-radius:6px; border:1px solid var(--line); background:var(--panel); color:var(--fg); cursor:pointer }
.btn.on { background:var(--pick-bg); border-color:var(--pick); color:var(--pick) }
.stage { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px }
.imgs { display:grid; grid-template-columns:1fr 1fr; gap:12px } @media (max-width:760px) { .imgs { grid-template-columns:1fr } }
.imgs h2 { font-size:.95rem; margin:0 0 5px; color:var(--muted); font-weight:600 }
.frame { position:relative; line-height:0 } .frame img { width:100%; height:auto; border-radius:6px; border:1px solid var(--line) }
#draw { cursor:crosshair }
.pin { position:absolute; width:24px; height:24px; margin:-12px 0 0 -12px; border-radius:50%; background:var(--pin); color:#fff; font:700 13px/24px var(--sans); text-align:center; border:2px solid #fff; pointer-events:none }
.pins { margin-top:12px; display:grid; gap:8px }
.pinrow { display:grid; grid-template-columns:28px 1fr auto; gap:8px; align-items:start }
.pinrow .n { width:24px; height:24px; border-radius:50%; background:var(--pin); color:#fff; font-weight:700; text-align:center; line-height:24px; font-size:13px; margin-top:6px }
textarea { font:inherit; width:100%; min-height:44px; padding:7px 9px; border-radius:6px; border:1px solid var(--line); background:var(--bg); color:var(--fg); resize:vertical }
label.gen { display:block; margin-top:12px; color:var(--muted); font-size:.9rem }
.nav { display:flex; justify-content:space-between; margin-top:14px; gap:10px; flex-wrap:wrap }
.status { color:var(--muted); font-size:.85rem; margin-left:10px } #dump { margin-top:8px; min-height:120px }
</style>
<div class="wrap">
  <h1>__TITLE__</h1>
  <p class="intro">Each roof is shown as the plain photo and as the build drew it. Click the drawing wherever something is wrong and say what, in a few words: a missing or wrong roof line, a face that should be two, a missed vent, panels where none would go, and so on. Add as many pins as you like. If the drawing is right, press "Nothing wrong". Do as many roofs as you have time for.</p>
  <div class="legend"><span><i class="sw" style="background:#e02020"></i>Roof faces</span><span><i class="sw" style="background:#f0a020"></i>Obstructions</span><span><i class="sw" style="background:#3a55e0"></i>Panels</span></div>
  <div class="dots" id="dots"></div>
  <div class="tools"><span class="count" id="count"></span><button class="btn" id="copy">Copy my notes</button><span class="status" id="status"></span></div>
  <textarea id="dump" readonly hidden></textarea>
  <section class="stage">
    <b id="title"></b>
    <div class="imgs">
      <div><h2>Photo</h2><div class="frame"><img id="photo" alt="Aerial photo of the roof" width="900" height="900"></div></div>
      <div><h2>Drawing: click where it is wrong</h2><div class="frame" id="frame"><img id="draw" alt="The build's drawing of the roof" width="900" height="900"></div></div>
    </div>
    <div class="pins" id="pins"></div>
    <label class="gen" for="note">Anything about the whole roof (optional)
      <textarea id="note" placeholder="e.g. the panels are fine but the whole outline sits 1 m too far north"></textarea></label>
    <div class="nav"><button class="btn" id="prev">← Previous</button><button class="btn" id="ok">Nothing wrong</button><button class="btn" id="next">Next →</button></div>
  </section>
</div>
<script>
const ITEMS = __ITEMS__, STORE = "__STORE__";
const notes = {}; let cur = 0, db = null; const timers = {};
const $ = id => document.getElementById(id);
try { Object.assign(notes, JSON.parse(localStorage.getItem(STORE) || "{}")); } catch (e) {}
function keep() { try { localStorage.setItem(STORE, JSON.stringify(notes)); } catch (e) {} }
function entry(k) { const v = notes[k] = notes[k] || {}; v.pins = v.pins || []; return v; }
function done(it) { const v = notes[it.key]; return !!v && (v.ok || (v.pins || []).some(p => (p.note || "").trim()) || (v.note || "").trim().length > 0); }
function save(k) {
  const v = notes[k]; v.at = new Date().toISOString(); keep(); marks();
  if (!db) { $("status").textContent = "Saved in this browser. Use Copy my notes when done."; return; }
  db.doc("notes/" + k).set(v).then(() => { $("status").textContent = ""; })
    .catch(() => { $("status").textContent = "Saved in this browser only. Use Copy my notes when done."; });
}
function later(k) { clearTimeout(timers[k]); timers[k] = setTimeout(() => save(k), 700); }
function marks() {
  $("count").textContent = ITEMS.filter(done).length + " of " + ITEMS.length + " roofs noted";
  const d = $("dots"); d.innerHTML = "";
  ITEMS.forEach((it, i) => { const b = document.createElement("button");
    b.className = "dot" + (done(it) ? " done" : "") + (i === cur ? " cur" : ""); b.setAttribute("aria-label", "Roof " + (i + 1));
    b.onclick = () => { cur = i; render(); }; d.appendChild(b); });
  $("ok").classList.toggle("on", !!(notes[ITEMS[cur].key] || {}).ok);
}
function render() {
  const it = ITEMS[cur], v = entry(it.key);
  $("title").textContent = "Roof " + (cur + 1) + " of " + ITEMS.length;
  $("photo").src = it.photo; $("draw").src = it.draw;
  const f = $("frame"); f.querySelectorAll(".pin").forEach(e => e.remove());
  const list = $("pins"); list.innerHTML = "";
  v.pins.forEach((p, i) => {
    const m = document.createElement("div"); m.className = "pin"; m.textContent = i + 1;
    m.style.left = (p.x * 100) + "%"; m.style.top = (p.y * 100) + "%"; f.appendChild(m);
    const row = document.createElement("div"); row.className = "pinrow";
    row.innerHTML = '<div class="n"></div><textarea></textarea><button class="btn">Remove</button>';
    row.querySelector(".n").textContent = i + 1;
    const ta = row.querySelector("textarea"); ta.value = p.note || ""; ta.placeholder = "What is wrong here?";
    ta.setAttribute("aria-label", "Note for pin " + (i + 1));
    ta.oninput = () => { p.note = ta.value; v.ok = false; later(it.key); };
    row.querySelector("button").onclick = () => { v.pins.splice(i, 1); save(it.key); render(); };
    list.appendChild(row);
  });
  $("note").value = v.note || "";
  marks();
  const nx = ITEMS[cur + 1]; if (nx) { new Image().src = nx.photo; new Image().src = nx.draw; }
}
$("draw").addEventListener("click", e => {
  const r = $("draw").getBoundingClientRect(), it = ITEMS[cur], v = entry(it.key);
  v.pins.push({ x: +((e.clientX - r.left) / r.width).toFixed(4), y: +((e.clientY - r.top) / r.height).toFixed(4), note: "" });
  v.ok = false; save(it.key); render();
  const t = $("pins").querySelectorAll("textarea"); if (t.length) t[t.length - 1].focus();
});
$("note").addEventListener("input", () => { const k = ITEMS[cur].key; entry(k).note = $("note").value; later(k); });
$("ok").onclick = () => { const k = ITEMS[cur].key, v = entry(k); v.ok = !v.ok; save(k); };
$("prev").onclick = () => { if (cur > 0) { cur--; render(); } };
$("next").onclick = () => { if (cur < ITEMS.length - 1) { cur++; render(); } };
$("copy").onclick = async () => {
  const txt = "Roof notes (" + STORE + "): " + JSON.stringify(notes);
  try { await navigator.clipboard.writeText(txt); $("copy").textContent = "Copied: paste it into the chat"; }
  catch (e) { $("dump").value = txt; $("dump").hidden = false; $("dump").select(); }
};
render();
(async () => {
  try {
    db = await claude.use("db"); if (!db) return;
    const snap = await db.collection("notes").get();
    snap.docs.forEach(d => { const x = d.data(); if (x && !notes[d.id]) notes[d.id] = x; });
    keep(); render();
  } catch (e) { db = null; }
})();
</script>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--layout", required=True)
    ap.add_argument("--region", required=True)
    ap.add_argument("--ids", type=int, nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Roof notes")
    a = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import from_bounds
    from shapely.geometry import shape
    from shapely.ops import transform as tf, unary_union
    from src.region_build import area_paths

    T = Transformer.from_crs(4326, 2193, always_xy=True).transform
    by = {}
    for f in json.loads(Path(a.layout).expanduser().read_text())["features"]:
        q = f["properties"]
        if q.get("kind") in ("facet", "obstruction", "panel"):
            by.setdefault(int(q["building_id"]), []).append((q["kind"], tf(T, shape(f["geometry"]))))
    out = Path(a.out).expanduser()
    (out / "img").mkdir(parents=True, exist_ok=True)
    img = rasterio.open(area_paths(a.region)["imagery"])
    items, key = [], []
    for n, bid in enumerate(a.ids, 1):
        feats = by.get(bid)
        if not feats:
            print(f"  {bid}: not in the layout, skipped")
            continue
        x0, y0, x1, y1 = unary_union([g for _, g in feats]).bounds
        s = max(x1 - x0, y1 - y0) + 2 * PAD_M
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        b = (cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2)
        px = np.moveaxis(img.read([1, 2, 3], window=from_bounds(*b, transform=img.transform), boundless=True), 0, -1)
        k = f"r{n:02d}"
        for tag in ("photo", "draw"):
            fig = plt.figure(figsize=(PX / 100, PX / 100), dpi=100)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.imshow(px, extent=(b[0], b[2], b[1], b[3]))
            if tag == "draw":
                for kind, col, lw, fill in (("panel", "#3a55e0", 0.6, 0.45), ("obstruction", "#f0a020", 1.2, 0.5), ("facet", "#e02020", 2.0, 0)):
                    for kd, g in feats:
                        if kd != kind:
                            continue
                        for p in (g.geoms if hasattr(g, "geoms") else [g]):
                            xs, ys = p.exterior.xy
                            if fill:
                                ax.fill(xs, ys, color=col, alpha=fill, lw=0)
                            ax.plot(xs, ys, color=col if kind != "panel" else "white", lw=lw)
            ax.set_xlim(b[0], b[2]); ax.set_ylim(b[1], b[3]); ax.set_axis_off()
            fig.savefig(out / "img" / f"{k}_{tag}.jpg", dpi=100, pil_kwargs={"quality": 82})
            plt.close(fig)
        items.append({"key": k, "photo": f"img/{k}_photo.jpg", "draw": f"img/{k}_draw.jpg"})
        # a pin (x, y) is a fraction from the image's top-left:
        # east = x0 + x * (x1 - x0), north = y1 - y * (y1 - y0)
        key.append({"key": k, "id": bid, "bounds": [round(v, 2) for v in b]})
    store = "notes-" + "".join(c if c.isalnum() else "-" for c in a.title.lower())
    (out / "index.html").write_text(PAGE.replace("__TITLE__", a.title).replace("__ITEMS__", json.dumps(items)).replace("__STORE__", store))
    (out / "answer_key.json").write_text(json.dumps(key, indent=1))
    print(f"{len(items)} roofs -> {out / 'index.html'}; key {out / 'answer_key.json'}")


if __name__ == "__main__":
    main()
