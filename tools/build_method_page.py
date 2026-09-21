"""Render the domain graph as something a non-coder can actually read.

Josh, 21 Sep, after being shown a 1,554-node code graph: "This seems overly
complicated for what we are doing... this is not helping me understand, just
looks like a very complicated graph of nodes."

He is right, and the fix is not a better graph. The useful output of
Understand-Anything's domain pass is not its picture -- it is its TEXT: six
domains, twenty-two flows and a hundred-odd steps, each one naming a decision
the pipeline makes and the evidence it makes it on, written in roofs and sun
and money rather than in functions and modules. A force-directed rendering of
that buries the one thing worth reading.

So this takes .ua/domain-graph.json and lays it out as a document: every step
in order, with what it decides, what it decides it from, and where that lives
if he ever wants to point at it. He can flag any step that looks wrong; the
flags are his own and stay in his browser.

Usage: python tools/build_method_page.py [out.html]
"""

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / ".ua" / "domain-graph.json"


def esc(s):
    return html.escape(str(s or ""))


def main():
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "method.html"
    if not SRC.exists():
        print(f"no {SRC} -- run the understand-domain pass first")
        return 1
    g = json.loads(SRC.read_text())
    node = {n["id"]: n for n in g["nodes"]}
    flows_of, steps_of, cross = {}, {}, []
    for e in g["edges"]:
        if e["type"] == "contains_flow":
            flows_of.setdefault(e["source"], []).append(e["target"])
        elif e["type"] == "flow_step":
            steps_of.setdefault(e["source"], []).append(
                (e.get("weight", 0), e["target"]))
        elif e["type"] == "cross_domain":
            cross.append(e)
    domains = [n for n in g["nodes"] if n["type"] == "domain"]
    proj = g.get("project", {})

    n_steps = sum(len(v) for v in steps_of.values())
    parts = []
    toc = []
    for di, dm in enumerate(domains, 1):
        did = dm["id"]
        fl = flows_of.get(did, [])
        nst = sum(len(steps_of.get(f, [])) for f in fl)
        toc.append(f'<li><a href="#{esc(did)}"><span class="toc-n">{di}</span>'
                   f'<span>{esc(dm["name"])}</span>'
                   f'<span class="toc-c">{nst}</span></a></li>')
        sec = [f'<section class="domain" id="{esc(did)}">',
               f'<header class="dhead"><p class="eyebrow">Part {di} of {len(domains)}'
               f' &middot; {len(fl)} processes &middot; {nst} steps</p>',
               f'<h2>{esc(dm["name"])}</h2>',
               f'<p class="lede">{esc(dm.get("summary"))}</p>']
        meta = dm.get("domainMeta") or {}
        rules = meta.get("businessRules") or []
        if rules:
            sec.append('<div class="rules"><h3>Rules this part holds to</h3><ul>'
                        + "".join(f"<li>{esc(r)}</li>" for r in rules) + "</ul></div>")
        sec.append("</header>")

        for f in fl:
            fn = node[f]
            fm = fn.get("domainMeta") or {}
            trig = fm.get("entryPoint")
            sec.append(f'<article class="flow" id="{esc(f)}">')
            sec.append(f'<h3>{esc(fn["name"])}</h3>')
            sec.append(f'<p class="fsum">{esc(fn.get("summary"))}</p>')
            if trig:
                sec.append(f'<p class="trigger"><span>Starts when</span> '
                           f'<code>{esc(trig)}</code></p>')
            sec.append('<ol class="steps">')
            for i, (_, sid) in enumerate(
                    sorted(steps_of.get(f, []), key=lambda t: t[0]), 1):
                s = node[sid]
                fp = s.get("filePath")
                lr = s.get("lineRange") or []
                where = ""
                if fp:
                    loc = f"{fp}:{lr[0]}" if lr and lr[0] else fp
                    where = f'<span class="where">{esc(loc)}</span>'
                sec.append(
                    f'<li class="step" data-step="{esc(sid)}">'
                    f'<div class="sbody"><p class="sname">{esc(s["name"])}</p>'
                    f'<p class="ssum">{esc(s.get("summary"))}</p>{where}</div>'
                    f'<button class="flag" type="button" aria-pressed="false" '
                    f'title="Mark this step as looking wrong">Looks wrong</button>'
                    f'</li>')
            sec.append("</ol></article>")

        links = [e for e in cross if e["source"] == did]
        if links:
            sec.append('<div class="cross"><h3>What this hands to the rest</h3><ul>'
                        + "".join(
                            f'<li><a href="#{esc(e["target"])}">'
                            f'{esc(node[e["target"]]["name"])}</a>'
                            f'<span>{esc(e.get("description"))}</span></li>'
                            for e in links if e["target"] in node)
                        + "</ul></div>")
        sec.append("</section>")
        parts.append("\n".join(sec))

    page = f"""<title>How a Roof Estimate Is Made</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@400&display=swap">
<style>
:root {{
  --paper:#faf7f2; --surface:#ffffff; --ink:#1d1726; --body:#3a3347;
  --muted:#736b81; --line:#e6ded3; --line-soft:#f0e9e0;
  --ember:#c2410c; --plum:#7a1f6b; --flagbg:#fdf2e9;
  --shadow:0 1px 2px rgba(29,23,38,.05);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --paper:#141119; --surface:#1c1823; --ink:#f3eee7; --body:#cfc7d6;
    --muted:#9a91a6; --line:#302937; --line-soft:#262030;
    --ember:#f79b5c; --plum:#d089c4; --flagbg:#2a2030;
    --shadow:0 1px 2px rgba(0,0,0,.4);
  }}
}}
:root[data-theme="dark"] {{
  --paper:#141119; --surface:#1c1823; --ink:#f3eee7; --body:#cfc7d6;
  --muted:#9a91a6; --line:#302937; --line-soft:#262030;
  --ember:#f79b5c; --plum:#d089c4; --flagbg:#2a2030;
  --shadow:0 1px 2px rgba(0,0,0,.4);
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--paper); color:var(--body);
  font:400 16.5px/1.62 "Source Sans 3", ui-sans-serif, system-ui, sans-serif;
  -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:1160px; margin:0 auto; padding-inline:20px; padding-block:0 96px; }}
.masthead {{ padding-block:52px 30px; border-bottom:2px solid var(--ink); }}
.kicker {{
  font:400 12px/1 "IBM Plex Mono", ui-monospace, monospace;
  letter-spacing:.14em; text-transform:uppercase; color:var(--ember); margin:0 0 18px;
}}
h1 {{
  font:700 clamp(34px,5.6vw,56px)/1.05 "Fraunces", Georgia, serif;
  color:var(--ink); margin:0; letter-spacing:-.018em; text-wrap:balance;
}}
.standfirst {{ margin:16px 0 0; max-width:62ch; font-size:18px; color:var(--body); }}
.figures {{ display:flex; flex-wrap:wrap; gap:26px 40px; margin:28px 0 0; padding:0; list-style:none; }}
.figures div {{ display:flex; flex-direction:column; gap:2px; }}
.figures b {{
  font:600 26px/1 "Fraunces", Georgia, serif; color:var(--ink);
  font-variant-numeric:tabular-nums;
}}
.figures span {{ font-size:12.5px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); }}
.cols {{ display:grid; grid-template-columns:230px minmax(0,1fr); gap:56px; margin-top:44px; align-items:start; }}
@media (max-width:900px) {{ .cols {{ grid-template-columns:1fr; gap:28px; }} nav.toc {{ position:static !important; }} }}
nav.toc {{ position:sticky; top:calc(env(safe-area-inset-top, 0px) + 20px); }}
nav.toc h2 {{ font:400 11.5px/1 "IBM Plex Mono", monospace; letter-spacing:.14em;
  text-transform:uppercase; color:var(--muted); margin:0 0 14px; }}
nav.toc ol {{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:1px; }}
nav.toc a {{ display:grid; grid-template-columns:22px 1fr auto; gap:10px; align-items:baseline;
  padding:7px 8px; border-radius:4px; text-decoration:none; color:var(--body); font-size:14.5px; }}
nav.toc a:hover, nav.toc a:focus-visible {{ background:var(--line-soft); color:var(--ink); }}
.toc-n {{ font:400 12px/1.4 "IBM Plex Mono", monospace; color:var(--ember); }}
.toc-c {{ font:400 12px/1.4 "IBM Plex Mono", monospace; color:var(--muted); font-variant-numeric:tabular-nums; }}
.flagbar {{ margin-top:22px; padding:12px 12px 13px; border:1px solid var(--line);
  border-radius:6px; background:var(--surface); font-size:13.5px; color:var(--muted); }}
.flagbar b {{ color:var(--ember); font-variant-numeric:tabular-nums; }}
.flagbar button {{ margin-top:8px; font:inherit; font-size:12.5px; background:none;
  border:1px solid var(--line); color:var(--body); padding:4px 9px; border-radius:4px; cursor:pointer; }}
.domain {{ padding-block:0 60px; }}
.domain + .domain {{ border-top:1px solid var(--line); padding-block-start:52px; }}
.eyebrow {{ font:400 11.5px/1 "IBM Plex Mono", monospace; letter-spacing:.11em;
  text-transform:uppercase; color:var(--muted); margin:0 0 12px; }}
.domain h2 {{ font:600 clamp(26px,3.4vw,36px)/1.15 "Fraunces", Georgia, serif;
  color:var(--ink); margin:0; letter-spacing:-.012em; text-wrap:balance; }}
.lede {{ margin:14px 0 0; max-width:66ch; font-size:17px; }}
.rules {{ margin-top:22px; padding:16px 18px; border-left:3px solid var(--plum);
  background:var(--surface); border-radius:0 6px 6px 0; }}
.rules h3 {{ margin:0 0 8px; font:400 11.5px/1 "IBM Plex Mono", monospace;
  letter-spacing:.11em; text-transform:uppercase; color:var(--plum); }}
.rules ul {{ margin:0; padding-left:18px; display:flex; flex-direction:column; gap:6px; }}
.rules li {{ font-size:15px; max-width:66ch; }}
.flow {{ margin-top:38px; }}
.flow h3 {{ font:600 20px/1.3 "Fraunces", Georgia, serif; color:var(--ink); margin:0; }}
.fsum {{ margin:7px 0 0; max-width:66ch; color:var(--body); }}
.trigger {{ margin:10px 0 0; font-size:13.5px; color:var(--muted); }}
.trigger span {{ letter-spacing:.05em; text-transform:uppercase; font-size:11.5px; }}
.trigger code {{ font:400 13px/1 "IBM Plex Mono", monospace; color:var(--ink); }}
ol.steps {{ list-style:none; counter-reset:s; margin:16px 0 0; padding:0;
  display:flex; flex-direction:column; gap:1px; }}
li.step {{ counter-increment:s; display:grid; grid-template-columns:1fr auto; gap:14px;
  align-items:start; padding:14px 16px 15px 46px; position:relative;
  background:var(--surface); border:1px solid var(--line-soft); }}
li.step:first-child {{ border-radius:6px 6px 0 0; }}
li.step:last-child {{ border-radius:0 0 6px 6px; }}
li.step:only-child {{ border-radius:6px; }}
li.step + li.step {{ border-top:none; }}
li.step::before {{ content:counter(s); position:absolute; left:16px; top:15px;
  font:400 12px/1.5 "IBM Plex Mono", monospace; color:var(--muted);
  font-variant-numeric:tabular-nums; }}
li.step[data-flagged="1"] {{ background:var(--flagbg); border-color:var(--ember); }}
li.step[data-flagged="1"] + li.step {{ border-top:1px solid var(--ember); }}
.sname {{ margin:0; font-weight:600; color:var(--ink); font-size:15.5px; }}
.ssum {{ margin:5px 0 0; max-width:64ch; font-size:15px; }}
.where {{ display:inline-block; margin-top:8px; font:400 12px/1.4 "IBM Plex Mono", monospace;
  color:var(--muted); word-break:break-all; }}
button.flag {{ font:400 12px/1 "IBM Plex Mono", monospace; letter-spacing:.03em;
  background:none; border:1px solid var(--line); color:var(--muted);
  padding:6px 9px; border-radius:4px; cursor:pointer; white-space:nowrap; }}
button.flag:hover {{ border-color:var(--ember); color:var(--ember); }}
button.flag[aria-pressed="true"] {{ background:var(--ember); border-color:var(--ember); color:#fff; }}
:focus-visible {{ outline:2px solid var(--ember); outline-offset:2px; }}
.cross {{ margin-top:34px; padding-top:18px; border-top:1px dashed var(--line); }}
.cross h3 {{ margin:0 0 10px; font:400 11.5px/1 "IBM Plex Mono", monospace;
  letter-spacing:.11em; text-transform:uppercase; color:var(--muted); }}
.cross ul {{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:8px; }}
.cross li {{ display:flex; flex-wrap:wrap; gap:4px 10px; align-items:baseline; font-size:14.5px; }}
.cross a {{ color:var(--plum); font-weight:600; text-decoration:none;
  border-bottom:1px solid currentColor; }}
.cross span {{ color:var(--muted); max-width:60ch; }}
footer.note {{ margin-top:56px; padding-top:22px; border-top:1px solid var(--line);
  font-size:14px; color:var(--muted); max-width:70ch; }}
footer.note code {{ font:400 13px/1 "IBM Plex Mono", monospace; color:var(--body); }}
@media (prefers-reduced-motion:reduce) {{ * {{ animation:none !important; transition:none !important; }} }}
</style>

<div class="wrap">
  <header class="masthead">
    <p class="kicker">{esc(proj.get("name", "Rooftop solar"))}</p>
    <h1>How a roof estimate is made</h1>
    <p class="standfirst">Every decision the pipeline makes between a laser scan and
    the number on the map — what it decides, and what it decides it from. Written
    to be read by someone who does not read code, so that a mistake in the
    <em>method</em> can be caught without opening a file.</p>
    <div class="figures">
      <div><b>{len(domains)}</b><span>Parts</span></div>
      <div><b>{sum(len(v) for v in flows_of.values())}</b><span>Processes</span></div>
      <div><b>{n_steps}</b><span>Decisions</span></div>
    </div>
  </header>

  <div class="cols">
    <nav class="toc" aria-label="Contents">
      <h2>Contents</h2>
      <ol>{"".join(toc)}</ol>
      <div class="flagbar">
        <span id="flagcount"><b>0</b> steps flagged</span>
        <button type="button" id="clearflags">Clear all</button>
      </div>
    </nav>
    <main>
      {"".join(parts)}
      <footer class="note">
        Extracted from the code by the <strong>understand-domain</strong> pass of
        Understand&nbsp;Anything, then laid out as a document rather than a graph.
        Rebuild after the pipeline changes with
        <code>python tools/build_method_page.py</code>. The step summaries are
        written from the code and its comments — where one disagrees with what the
        pipeline actually does, that disagreement is itself worth knowing about.
        Flags are stored in this browser only.
      </footer>
    </main>
  </div>
</div>

<script>
(function () {{
  var KEY = "roof-method-flags";
  var flags = {{}};
  try {{ flags = JSON.parse(localStorage.getItem(KEY) || "{{}}") || {{}}; }} catch (e) {{ flags = {{}}; }}
  var countEl = document.getElementById("flagcount");

  function paint() {{
    var n = 0;
    document.querySelectorAll("li.step").forEach(function (li) {{
      var on = !!flags[li.dataset.step];
      if (on) n++;
      li.dataset.flagged = on ? "1" : "0";
      var b = li.querySelector("button.flag");
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.textContent = on ? "Flagged" : "Looks wrong";
    }});
    countEl.innerHTML = "<b>" + n + "</b> step" + (n === 1 ? "" : "s") + " flagged";
  }}

  function save() {{
    try {{ localStorage.setItem(KEY, JSON.stringify(flags)); }} catch (e) {{}}
  }}

  document.querySelectorAll("li.step button.flag").forEach(function (b) {{
    b.addEventListener("click", function () {{
      var id = b.closest("li.step").dataset.step;
      if (flags[id]) {{ delete flags[id]; }} else {{ flags[id] = 1; }}
      save(); paint();
    }});
  }});
  document.getElementById("clearflags").addEventListener("click", function () {{
    flags = {{}}; save(); paint();
  }});
  paint();
}})();
</script>
"""
    out_path.write_text(page, encoding="utf-8")
    print(f"{out_path}  ({out_path.stat().st_size/1000:.0f} kB)  "
          f"{len(domains)} parts, {sum(len(v) for v in flows_of.values())} processes, "
          f"{n_steps} decisions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
