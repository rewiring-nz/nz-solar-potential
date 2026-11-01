#!/usr/bin/env python3
"""Build a blind review page for the owner from rendered roofs.

An agent's reading of a render is not a measurement (AGENTS.md): better or
worse comes from the owner. This writes a page where each roof is shown as
two drawings with the versions hidden and shuffled (or as one drawing, to
rate a single layout), and the owner gives each drawing a score out of 10,
ticks the faults it shows, and picks the better side. The answer key, with
the agent's own calls recorded first, is written beside it and never
published.

    tools/review_page.py --pairs pairs.json --out ~/review_v70 --title "v70 movers"

pairs.json: [{"id": 5374556, "a": "v69.jpg", "a_label": "v69",
              "b": "v70.jpg", "b_label": "v70", "mine": "v70"}, ...]
("b" may be left out to rate one drawing.) Writes out/index.html,
out/img/*.jpg and out/answer_key.json. Publish index.html with img/ as
supporting files and the db capability; answers go to the "answers"
collection, are kept in the viewer's browser too, and "Copy my answers"
gives them as text to paste into the chat.
"""
import argparse
import json
import random
import shutil
from pathlib import Path

FAULTS = [
    ("outline", "Building outline wrong"),
    ("lines", "Roof lines wrong"),
    ("ridge", "Panels across a ridge or valley"),
    ("obstruction", "Panels over obstructions"),
    ("off_roof", "Panels over empty area"),
    ("racking", "Racking wrong direction"),
    ("missed", "Empty space with no panels"),
]

PAGE = r"""<title>__TITLE__</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&family=JetBrains+Mono:wght@500&display=swap">
<style>
:root {
  --bg: #f3f5f7; --panel: #ffffff; --fg: #1d2630; --muted: #5d6a78; --line: #d5dce3;
  --accent: #c27a12; --pick: #1f6f4a; --pick-bg: #e3f2ea; --bad: #a3371f; --bad-bg: #f8e4de;
  --sans: "Source Sans 3", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "JetBrains Mono", ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #12171c; --panel: #1b2229; --fg: #e5eaef; --muted: #9aa6b2; --line: #2e3842;
  --accent: #e9a03b; --pick: #6fd3a2; --pick-bg: #18352a; --bad: #f08a70; --bad-bg: #3a1f18; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #12171c; --panel: #1b2229; --fg: #e5eaef; --muted: #9aa6b2; --line: #2e3842;
  --accent: #e9a03b; --pick: #6fd3a2; --pick-bg: #18352a; --bad: #f08a70; --bad-bg: #3a1f18; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font-family: var(--sans); font-size: 16px; line-height: 1.5; }
.wrap { max-width: 1040px; margin: 0 auto; padding-block: 20px 40px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 16px; }
h1 { font-size: 1.6rem; margin: 0; text-wrap: balance; letter-spacing: -0.01em; }
.intro { margin: 4px 0 0; color: var(--muted); max-width: 72ch; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 0.9rem; color: var(--muted); }
.sw { display: inline-block; width: 12px; height: 12px; border-radius: 2px; vertical-align: -1px; margin-right: 6px; }
.dots { display: flex; flex-wrap: wrap; gap: 5px; }
.dot { width: 16px; height: 16px; border-radius: 3px; border: 1px solid var(--line); background: var(--panel); padding: 0; cursor: pointer; }
.dot.done { background: var(--pick); border-color: var(--pick); }
.dot.cur { outline: 2px solid var(--accent); outline-offset: 1px; }
.count { font-family: var(--mono); font-size: 0.85rem; color: var(--muted); font-variant-numeric: tabular-nums; }
.tools { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 6px; }
.btn { font: inherit; font-size: 0.9rem; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--line); background: var(--panel); color: var(--fg); cursor: pointer; }
#dump { width: 100%; box-sizing: border-box; font-family: var(--mono); font-size: 0.8rem; }
.stage { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; display: grid; gap: 14px; }
.pairhead { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.sides { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.sides.one { grid-template-columns: minmax(0, 520px); }
.side { display: grid; gap: 8px; min-width: 0; align-content: start; }
.side img { width: 100%; height: auto; border-radius: 4px; border: 3px solid transparent; display: block; }
.side.chosen img { border-color: var(--pick); }
.side h2 { font-family: var(--mono); font-size: 0.9rem; font-weight: 500; color: var(--muted); margin: 0; }
.lbl { font-size: 0.85rem; color: var(--muted); }
.scores { display: grid; grid-template-columns: repeat(10, 1fr); gap: 3px; }
.scores button { font: inherit; font-family: var(--mono); font-size: 0.85rem; padding: 6px 0; border-radius: 4px; border: 1px solid var(--line); background: var(--bg); color: var(--fg); cursor: pointer; min-width: 0; }
.scores button.on { background: var(--pick); border-color: var(--pick); color: var(--panel); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chips button { font: inherit; font-size: 0.85rem; padding: 4px 9px; border-radius: 999px; border: 1px solid var(--line); background: var(--bg); color: var(--fg); cursor: pointer; }
.chips button.on { background: var(--bad-bg); border-color: var(--bad); color: var(--bad); }
.verdicts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.verdicts button { font: inherit; font-weight: 600; padding: 10px 8px; border-radius: 6px; border: 1px solid var(--line); background: var(--bg); color: var(--fg); cursor: pointer; }
.verdicts button.on { background: var(--pick-bg); border-color: var(--pick); color: var(--pick); }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
label.note { display: grid; gap: 4px; font-size: 0.9rem; color: var(--muted); }
textarea { font: inherit; color: var(--fg); background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 8px; min-height: 2.6em; resize: vertical; }
.nav { display: flex; justify-content: space-between; gap: 8px; align-items: center; flex-wrap: wrap; }
.status { font-size: 0.9rem; color: var(--muted); min-height: 1.5em; }
.status.err { color: var(--accent); }
.done-card { background: var(--pick-bg); border: 1px solid var(--pick); border-radius: 8px; padding: 14px 16px; }
@media (max-width: 680px) { .sides { grid-template-columns: 1fr; } .verdicts { grid-template-columns: 1fr 1fr; } }
</style>

<div class="wrap">
  <header>
    <h1>__TITLE__</h1>
    <p class="intro">__INTRO__</p>
  </header>
  <div class="legend">
    <span><i class="sw" style="background:#e02020"></i>Roof faces</span>
    <span><i class="sw" style="background:#f0a020"></i>Obstructions</span>
    <span><i class="sw" style="background:#3a55e0"></i>Panels</span>
  </div>
  <div>
    <div class="dots" id="dots" aria-label="Roofs"></div>
    <div class="tools"><span class="count" id="count">loading…</span>
      <button class="btn" id="copy">Copy my answers</button></div>
    <textarea id="dump" readonly hidden aria-label="Your answers as text"></textarea>
  </div>
  <section class="stage" aria-live="polite">
    <div class="pairhead"><b id="title">Roof 1</b><span class="status" id="status"></span></div>
    <div class="sides" id="sides"></div>
    <div class="verdicts" id="verdicts" role="group" aria-label="Which fits better">
      <button data-v="a">A fits better</button>
      <button data-v="same">About the same</button>
      <button data-v="b">B fits better</button>
      <button data-v="unsure">Can't tell</button>
    </div>
    <label class="note" for="note">Note (optional)
      <textarea id="note" placeholder="e.g. both miss the north wing"></textarea></label>
    <div class="nav">
      <button class="btn" id="prev">← Previous</button>
      <button class="btn" id="next">Next →</button>
    </div>
  </section>
  <div class="done-card" id="done" hidden>All roofs reviewed. Press <b>Copy my answers</b> at the top and paste them into the chat with Claude.</div>
</div>

<script>
const ITEMS = __ITEMS__;
const FAULTS = __FAULTS__;
const STORE = "__STORE__";
const answers = {};   // key -> {choice, score:{a,b}, faults:{a:[],b:[]}, note}
let cur = 0, db = null;
const $ = id => document.getElementById(id);
try { Object.assign(answers, JSON.parse(localStorage.getItem(STORE) || "{}")); } catch (e) {}
function keepLocal() { try { localStorage.setItem(STORE, JSON.stringify(answers)); } catch (e) {} }

function sidesOf(it) { return it.b ? ["a", "b"] : ["a"]; }
function complete(it) {
  const v = answers[it.key];
  if (!v) return false;
  if (sidesOf(it).some(s => !(v.score && v.score[s]))) return false;
  return !it.b || !!v.choice;
}
function entry(k) {
  const v = answers[k] = answers[k] || {};
  v.score = v.score || {}; v.faults = v.faults || {};
  return v;
}

function render() {
  const it = ITEMS[cur], v = answers[it.key] || {}, two = !!it.b;
  $("title").textContent = `Roof ${cur + 1} of ${ITEMS.length}`;
  const box = $("sides"); box.innerHTML = ""; box.className = "sides" + (two ? "" : " one");
  sidesOf(it).forEach(s => {
    const name = two ? s.toUpperCase() + (s === "a" ? " · left" : " · right") : "This layout";
    const d = document.createElement("div");
    d.className = "side" + (v.choice === s ? " chosen" : "");
    d.innerHTML = `<h2>${name}</h2><img alt="${name} drawing of the roof" width="336" height="315">
      <div class="lbl">How good is it, out of 10?</div><div class="scores"></div>
      <div class="lbl">What's wrong with it?</div><div class="chips"></div>`;
    d.querySelector("img").src = it[s];
    const sc = d.querySelector(".scores");
    for (let n = 1; n <= 10; n++) {
      const b = document.createElement("button"); b.textContent = n;
      b.setAttribute("aria-label", `${name}: ${n} out of 10`);
      if (v.score && v.score[s] === n) b.className = "on";
      b.onclick = () => { entry(it.key).score[s] = n; save(it.key); render(); };
      sc.appendChild(b);
    }
    const ch = d.querySelector(".chips");
    FAULTS.forEach(([fk, label]) => {
      const b = document.createElement("button"); b.textContent = label;
      const on = !!(v.faults && (v.faults[s] || []).includes(fk));
      b.className = on ? "on" : ""; b.setAttribute("aria-pressed", on);
      b.onclick = () => {
        const e = entry(it.key), f = new Set(e.faults[s] || []);
        f.has(fk) ? f.delete(fk) : f.add(fk); e.faults[s] = [...f];
        save(it.key); render();
      };
      ch.appendChild(b);
    });
    box.appendChild(d);
  });
  $("verdicts").hidden = !two;
  document.querySelectorAll("#verdicts button").forEach(b => b.classList.toggle("on", b.dataset.v === v.choice));
  if (document.activeElement !== $("note")) $("note").value = v.note || "";
  const n = ITEMS.filter(complete).length;
  $("count").textContent = `${n} of ${ITEMS.length} reviewed`;
  $("done").hidden = n < ITEMS.length;
  $("dots").innerHTML = "";
  ITEMS.forEach((p, i) => {
    const d = document.createElement("button");
    d.className = "dot" + (complete(p) ? " done" : "") + (i === cur ? " cur" : "");
    d.setAttribute("aria-label", `Roof ${i + 1}`);
    d.onclick = () => { cur = i; render(); };
    $("dots").appendChild(d);
  });
  const nx = ITEMS[cur + 1]; if (nx) sidesOf(nx).forEach(s => { new Image().src = nx[s]; });
}

async function save(k) {
  const v = answers[k];
  v.at = new Date().toISOString();
  keepLocal();
  if (!db) { $("status").textContent = "Saved in this browser. Use Copy my answers when done."; $("status").className = "status"; return; }
  try { await db.doc("answers/" + k).set(v); $("status").textContent = ""; $("status").className = "status"; }
  catch (e) { $("status").textContent = "Saved in this browser only. Use Copy my answers when done."; $("status").className = "status err"; }
}

document.querySelectorAll("#verdicts button").forEach(b => b.onclick = () => {
  const it = ITEMS[cur]; entry(it.key).choice = b.dataset.v; save(it.key); render();
});
$("prev").onclick = () => { if (cur > 0) { cur--; render(); } };
$("next").onclick = () => { if (cur < ITEMS.length - 1) { cur++; render(); } };
let noteTimer = null;
$("note").addEventListener("input", () => {
  const k = ITEMS[cur].key; entry(k).note = $("note").value;
  clearTimeout(noteTimer); noteTimer = setTimeout(() => save(k), 800);
});
$("copy").onclick = async () => {
  const txt = "Review answers (" + STORE + "): " + JSON.stringify(answers);
  try { await navigator.clipboard.writeText(txt); $("copy").textContent = "Copied: paste it into the chat"; }
  catch (e) { $("dump").value = txt; $("dump").hidden = false; $("dump").select(); $("copy").textContent = "Copy the text below into the chat"; }
};

cur = Math.max(0, ITEMS.findIndex(p => !complete(p)));
render();
(async () => {
  try {
    db = await claude.use("db");
    if (!db) return;
    const snap = await db.collection("answers").get();
    snap.docs.forEach(d => { const x = d.data(); if (x && !answers[d.id]) answers[d.id] = x; });
    keepLocal();
    cur = Math.max(0, ITEMS.findIndex(p => !complete(p)));
    render();
  } catch (e) { db = null; }
})();
</script>
"""

INTRO_PAIRS = ("Each roof is drawn twice, by two versions of the build; which is which is "
               "hidden and shuffled. Score each drawing out of 10 against the real roof in "
               "the photo, tick what's wrong with it, and pick the one that fits better. "
               "More panels is not automatically better.")
INTRO_ONE = ("Each roof is drawn as the map shows it now. Score the drawing out of 10 "
             "against the real roof in the photo and tick what's wrong with it.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Roof review")
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    src = json.loads(Path(a.pairs).read_text())
    out = Path(a.out).expanduser()
    (out / "img").mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    items, key = [], []
    for n, p in enumerate(src, 1):
        k = f"r{n:02d}"
        flip = p.get("b") and rng.random() < 0.5
        shown = {"a": "b" if flip else "a"}
        if p.get("b"):
            shown["b"] = "a" if flip else "b"
        item = {"key": k}
        for s, orig in shown.items():
            name = f"img/{k}_{s}.jpg"
            shutil.copy(p[orig], out / name)
            item[s] = name
        items.append(item)
        key.append({"key": k, "id": p["id"], **{s: p.get(f"{orig}_label", orig) for s, orig in shown.items()},
                    "mine": p.get("mine")})
    store = "review-" + "".join(c if c.isalnum() else "-" for c in a.title.lower())
    html = (PAGE.replace("__TITLE__", a.title)
            .replace("__INTRO__", INTRO_PAIRS if any(i.get("b") for i in items) else INTRO_ONE)
            .replace("__ITEMS__", json.dumps(items))
            .replace("__FAULTS__", json.dumps(FAULTS))
            .replace("__STORE__", store))
    (out / "index.html").write_text(html)
    (out / "answer_key.json").write_text(json.dumps(key, indent=1))
    print(f"{len(items)} roofs -> {out / 'index.html'}; answer key {out / 'answer_key.json'}")


if __name__ == "__main__":
    main()
