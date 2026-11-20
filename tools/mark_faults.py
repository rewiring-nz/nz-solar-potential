#!/usr/bin/env python3
"""A page where the owner points at what is wrong on each roof.

A verdict on a whole roof ("worse", 4/10) does not say which panel is wrong
or why. Here every panel of a snapshot (tools/cases_sheet.py snap) is drawn
on the photo as its own clickable shape: pick a fault, click the panels it
applies to, or click bare roof to pin a fault to a spot. The marks carry the
panel's index in the snapshot and NZTM coordinates, so a fix can be scored
against them: a marked panel must be gone or moved, a "panels should go
here" spot must be covered.

    python tools/mark_faults.py page <out dir> <snapshot label>
    python tools/mark_faults.py read <marks.json>        # summary of pasted marks
    python tools/mark_faults.py score <marks.json> <snapshot label>
    python tools/mark_faults.py score-all <snapshot label>   # every marks file so far
    python tools/mark_faults.py measure <marks.json>     # what sets the marked apart

page writes <out dir>/index.html and one photo per roof (0.05 m a pixel).
Marks are kept in the browser and leave it by "Copy my marks" (pasted into
the chat) or "Download"; save them as data/fault_marks/<label>.json.

How a set of marks becomes a rule for every roof:

1. measure. Every panel on the marked roofs, marked or not, gets the same
   measurements (width of its face, share of it outside its face, over an
   obstruction, off the roof, ...). For each fault the table gives the one
   measurement and threshold that takes in every marked panel and the fewest
   unmarked ones. A rule comes from a measurement that separates the two; a
   fault no measurement separates needs evidence the snapshot lacks (the
   returns, the photo) before any rule is written.
2. The rule goes in the build behind a SOLAR_* switch, never keyed on a
   building.
3. score-all. The rebuilt street is scored against every marks file so far,
   so a fix for this round's marks cannot quietly undo an earlier round's.
   Roofs ticked right must keep every panel.
4. tools/markup_benchmark.py on the drawn roofs: no harm away from the street.
5. The owner marks the new round. Marks on what a rule changed say whether
   the rule was right; a line mark has no right answer in it, so only this
   step judges moved lines.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# (key, label, colour, applies to, category). "panel": click panels. "spot":
# click the roof. A fault with no category is not offered on the page; its key
# stays so marks made on earlier pages still read and score.
FAULTS = [
    ("line_wrong", "Roof line is wrong here", "#e6c700", "spot", "Roof lines"),
    ("ridge", "Panel is over a ridge or roof line", "#e6194b", "panel", "Roof lines"),
    ("outline_wrong", "Real roof edge is here", "#00b7eb", "spot", "Building outline"),
    ("off_roof", "Panel is off the roof", "#911eb4", "panel", "Building outline"),
    ("missed_obstruction", "Obstruction not marked", "#f032e6", "spot", "Obstructions"),
    ("not_obstruction", "This is not an obstruction", "#4363d8", "spot", "Obstructions"),
    ("obstruction", "Panel is over an obstruction", "#f58231", "panel", "Obstructions"),
    ("add_panels", "Panels should go here", "#1a9c3c", "spot", "Panels"),
    ("wrong_way", "Panel is the wrong way round", "#008080", "panel", "Panels"),
    ("remove", "Panel should not be here", "#000000", "panel", "Panels"),
    ("comment", "Comment", "#444444", "spot", "Comment"),
    ("stray", "Stray panel, not part of an array", "#9a6324", "panel", ""),
]

PAGE = r"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Mark the faults: __LABEL__</title>
<style>
body{font:15px system-ui;margin:0;background:#f4f4f2;color:#111}
#bar{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid #ccc;padding:8px 12px}
#bar b{margin-right:8px}
.tag{display:inline-block;margin:3px 4px 3px 0;padding:5px 10px;border-radius:16px;border:2px solid #bbb;background:#fff;cursor:pointer;font:inherit}
.tag i{display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:6px;vertical-align:-1px}
.tag.on{border-color:#111;background:#111;color:#fff}
.grp{color:#555;font-size:13px;margin-right:6px}
.cat{font:inherit;font-weight:600;padding:7px 14px;margin:0 6px 4px 0;border:2px solid #bbb;border-radius:6px;background:#fff;cursor:pointer}
.cat.on{border-color:#111;background:#111;color:#fff}
.cm{position:absolute;z-index:3;width:40%;box-sizing:border-box;font:15px system-ui;line-height:normal;padding:6px;border:2px solid #111;border-radius:6px}
#act button,.card button{font:inherit;padding:5px 10px;margin-right:6px;cursor:pointer}
main{max-width:1100px;margin:0 auto;padding:10px 12px 80px}
.card{background:#fff;border:1px solid #ccc;border-radius:8px;margin:18px 0;padding:10px}
.card h2{font-size:17px;margin:2px 0 4px}.card p{margin:2px 0 8px;color:#444}
.wrap{position:relative;line-height:0}
.wrap img{width:100%;height:auto;display:block}
.wrap svg{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}
.face{fill:none;stroke:#ff2a2a;stroke-width:3}
.obs{fill:#ffa500;fill-opacity:.5;stroke:none}
.pan{fill:#1f4fff;fill-opacity:.42;stroke:#fff;stroke-width:1.5;cursor:pointer}
.pan.extra{fill-opacity:.08;stroke:#7fd4ff}
.pan:hover{fill-opacity:.7}
.hide .face,.hide .obs,.hide .pan{display:none}
textarea{width:100%;box-sizing:border-box;font:inherit;margin-top:8px;height:44px}
.row{margin-top:8px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.count{color:#555}
</style>
<div id=bar>
 <div id=cats></div>
 <div><span id=opts></span><span class=grp id=hint></span></div>
 <div id=act style="margin-top:6px"><button id=copy>Copy my marks</button><button id=dl>Download</button>
 <span class=count id=total></span> &nbsp; Click a mark again to remove it. Marks are saved in this browser as you go.</div>
</div>
<main id=main></main>
<script>
const LABEL="__LABEL__", FAULTS=__FAULTS__, ROOFS=__ROOFS__;
const STORE="fault_marks_"+LABEL, F=Object.fromEntries(FAULTS.map(f=>[f[0],f]));
let marks={}; try{marks=JSON.parse(localStorage.getItem(STORE)||"{}")}catch(e){}
const CATS=[...new Set(FAULTS.map(f=>f[4]).filter(Boolean))];
let cat=CATS[0], armed=FAULTS.find(f=>f[4]==cat)[0];
const $=id=>document.getElementById(id), NS="http://www.w3.org/2000/svg";
function keep(){try{localStorage.setItem(STORE,JSON.stringify(marks))}catch(e){} total()}
function rec(id){return marks[id]||(marks[id]={panels:{},spots:[],note:"",right:false})}
function total(){let n=0,r=0;for(const k in marks){const m=marks[k];const c=Object.keys(m.panels).length+m.spots.length;n+=c;if(c||m.right||m.note)r++}
 $("total").textContent=n+" marks on "+r+" of "+ROOFS.length+" roofs"}
function tags(){$("cats").innerHTML="";$("opts").innerHTML="";
 for(const c of CATS){const b=document.createElement("button");b.className="cat"+(c==cat?" on":"");b.textContent=c;
  b.onclick=()=>{cat=c;armed=FAULTS.find(f=>f[4]==c)[0];tags()};$("cats").appendChild(b)}
 const fs=FAULTS.filter(f=>f[4]==cat);
 if(fs.length>1)for(const f of fs){const b=document.createElement("button");b.className="tag"+(f[0]==armed?" on":"");
  b.innerHTML="<i style='background:"+f[2]+"'></i>"+f[1];b.onclick=()=>{armed=f[0];tags()};$("opts").appendChild(b)}
 $("hint").textContent=armed=="comment"?"Click the spot on the photo, then type your comment.":F[armed][3]=="panel"?"Now click the panel or panels.":"Now click the spot on the photo."}
function el(n,a){const e=document.createElementNS(NS,n);for(const k in a)e.setAttribute(k,a[k]);return e}
function paint(r,svg){const m=rec(r.id);svg.querySelectorAll(".mk").forEach(e=>e.remove());
 r.panels.forEach((p,i)=>{const t=m.panels[i];if(!t)return;const f=F[t];
  svg.appendChild(el("polygon",{class:"mk",points:p.px.map(q=>q.join(",")).join(" "),fill:f[2],"fill-opacity":.75,stroke:"#fff","stroke-width":2,"pointer-events":"none"}))});
 m.spots.forEach((s,j)=>{const f=F[s.tag],g=el("g",{class:"mk",style:"cursor:pointer"});
  g.appendChild(el("circle",{cx:s.px[0],cy:s.px[1],r:r.size/45,fill:f[2],stroke:"#fff","stroke-width":r.size/300}));
  const tx=el("text",{x:s.px[0]+r.size/36,y:s.px[1]+r.size/110,"font-size":r.size/42,fill:"#fff",stroke:"#000","stroke-width":r.size/260,"paint-order":"stroke","font-family":"system-ui","font-weight":600});
  tx.textContent=s.text||f[1];g.appendChild(tx);
  g.onclick=e=>{e.stopPropagation();m.spots.splice(j,1);keep();paint(r,svg);sum(r)};svg.appendChild(g)})}
function sum(r){const m=rec(r.id),c=Object.keys(m.panels).length+m.spots.length;
 $("n"+r.id).textContent=c?c+" mark"+(c>1?"s":""):"no marks yet"}
for(const r of ROOFS){const c=document.createElement("div");c.className="card";
 c.innerHTML="<h2>"+r.title+"</h2><p>"+r.sub+"</p><div class=wrap><img loading=lazy src='"+r.id+".jpg'></div>"+
  "<div class=row><button id=h"+r.id+">Hide the drawing</button><label><input type=checkbox id=ok"+r.id+"> This roof is right as drawn</label>"+
  "<button id=clr"+r.id+">Clear this roof</button><span class=count id=n"+r.id+"></span></div>"+
  "<textarea id=t"+r.id+" placeholder='Anything else about this roof (optional)'></textarea>";
 $("main").appendChild(c);
 const svg=el("svg",{viewBox:"0 0 "+r.size+" "+r.size});c.querySelector(".wrap").appendChild(svg);
 for(const o of r.obstructions)svg.appendChild(el("polygon",{class:"obs",points:o.map(q=>q.join(",")).join(" ")}));
 for(const o of r.faces)svg.appendChild(el("polygon",{class:"face",points:o.map(q=>q.join(",")).join(" ")}));
 r.panels.forEach((p,i)=>{const e=el("polygon",{class:"pan"+(p.shown?"":" extra"),points:p.px.map(q=>q.join(",")).join(" ")});
  e.onclick=ev=>{ev.stopPropagation();const m=rec(r.id);
   if(F[armed][3]!="panel"){spot(ev);return}
   if(m.panels[i]==armed)delete m.panels[i];else m.panels[i]=armed;keep();paint(r,svg);sum(r)};svg.appendChild(e)});
 function spot(ev){const b=svg.getBoundingClientRect(),k=r.size/b.width,x=(ev.clientX-b.left)*k,y=(ev.clientY-b.top)*k;
  const s={tag:armed,px:[Math.round(x),Math.round(y)],xy:[+(r.x0+x*r.res).toFixed(2),+(r.y1-y*r.res).toFixed(2)]};
  const add=()=>{rec(r.id).spots.push(s);keep();paint(r,svg);sum(r)};
  if(armed!="comment"){add();return}
  const i=document.createElement("input");i.className="cm";i.placeholder="Type your comment, then press Enter";
  i.style.left=Math.min(100*x/r.size,58)+"%";i.style.top=Math.min(100*y/r.size,93)+"%";c.querySelector(".wrap").appendChild(i);i.focus();
  let done=false;const fin=ok=>{if(done)return;done=true;const t=i.value.trim();i.remove();if(ok&&t){s.text=t;add()}};
  i.onkeydown=e=>{if(e.key=="Enter")fin(true);if(e.key=="Escape")fin(false)};i.onblur=()=>fin(true)}
 svg.onclick=ev=>{if(F[armed][3]=="spot")spot(ev)};
 $("h"+r.id).onclick=()=>{const on=svg.classList.toggle("hide");$("h"+r.id).textContent=on?"Show the drawing":"Hide the drawing"};
 $("clr"+r.id).onclick=()=>{delete marks[r.id];$("ok"+r.id).checked=false;$("t"+r.id).value="";keep();paint(r,svg);sum(r)};
 const m=marks[r.id];if(m){$("ok"+r.id).checked=!!m.right;$("t"+r.id).value=m.note||""}
 $("ok"+r.id).onchange=e=>{rec(r.id).right=e.target.checked;keep()};
 $("t"+r.id).oninput=e=>{rec(r.id).note=e.target.value;keep()};
 paint(r,svg);sum(r)}
function out(){const roofs=[];for(const r of ROOFS){const m=marks[r.id];if(!m)continue;
  const ps=Object.entries(m.panels).map(([i,t])=>({panel:+i,fault:t,xy:r.panels[i].xy}));
  if(!ps.length&&!m.spots.length&&!m.right&&!m.note)continue;
  roofs.push({id:r.id,right:!!m.right,note:m.note||"",panels:ps,spots:m.spots.map(s=>s.text?{fault:s.tag,xy:s.xy,text:s.text}:{fault:s.tag,xy:s.xy})})}
 return JSON.stringify({label:LABEL,marked:new Date().toISOString().slice(0,10),roofs})}
$("copy").onclick=async()=>{const t=out();try{await navigator.clipboard.writeText(t);$("copy").textContent="Copied: paste it into the chat"}
 catch(e){prompt("Copy this and paste it into the chat",t)}};
$("dl").onclick=()=>{const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([out()],{type:"application/json"}));a.download="fault_marks_"+LABEL+".json";a.click()};
tags();total();
</script>
"""


def page(out_dir, label):
    import html
    import pyproj
    from PIL import Image
    from shapely.affinity import translate
    from shapely.geometry import shape
    from shapely.ops import transform, unary_union
    import cases_sheet as CS
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    snap = json.loads((CS.SNAPS / f"{label}.json").read_text())["buildings"]
    T = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    res = CS.PHOTO_RES_M
    roofs = []
    for c in CS._cases():
        e = snap.get(str(c["id"]))
        if not e:
            continue
        feats = [(f["properties"], translate(transform(T, shape(f["geometry"])), *e["shift"])) for f in e["features"]]
        faces = [g for q, g in feats if q["kind"] == "facet"]
        x0, y0, x1, y1 = unary_union(faces or [g for _, g in feats]).buffer(CS.PAD_M).bounds
        s = max(x1 - x0, y1 - y0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        x0, y0, x1, y1 = cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2
        img = CS._photo((x0, y0, x1, y1), CS.SNAPS / "tiles")
        Image.fromarray(img).save(out_dir / f"{c['id']}.jpg", quality=88)

        def px(g):
            return [[[round((x - x0) / res, 1), round((y1 - y) / res, 1)] for x, y in p.exterior.coords]
                    for p in getattr(g, "geoms", [g])]
        cap = CS.HOME_CAP_PANELS if sum(g.area for g in faces) < CS.HOME_CAP_ROOF_M2 else 10 ** 9
        panels = []
        for i, (q, g) in enumerate(feats):
            if q["kind"] == "panel":
                panels.append(dict(i=i, px=px(g)[0], xy=[round(g.centroid.x, 2), round(g.centroid.y, 2)],
                                   shown=(q.get("fill_order") or 0) <= cap))
        roofs.append(dict(
            id=c["id"], size=img.shape[0], res=res, x0=round(x0, 2), y1=round(y1, 2),
            title=f"#{c['id']} &nbsp; {html.escape(c['defect'].split(':')[0])}",
            sub=f"{len(panels)} panels &middot; {html.escape(e['region'])}"
                + ("".join("<br>You said: &ldquo;" + html.escape(q) + "&rdquo;" for q in c.get("quotes", [])[-1:])),
            faces=[r for q, g in feats if q["kind"] == "facet" for r in px(g)],
            obstructions=[r for q, g in feats if q["kind"] == "obstruction" for r in px(g)],
            panels=panels))
    (out_dir / "index.html").write_text(
        PAGE.replace("__LABEL__", label).replace("__FAULTS__", json.dumps(FAULTS)).replace("__ROOFS__", json.dumps(roofs)))
    print(len(roofs), "roofs to mark in", out_dir)


def read(path):
    d = json.loads(Path(path).expanduser().read_text())
    n = Counter()
    for r in d["roofs"]:
        for m in r["panels"] + r["spots"]:
            n[m["fault"]] += 1
        print(r["id"], "right" if r["right"] else "", len(r["panels"]), "panels,", len(r["spots"]), "spots", r["note"])
        for m in r["spots"]:
            if m.get("text"):
                print("    comment at", m["xy"], ":", m["text"])
    names = {f[0]: f[1] for f in FAULTS}
    for k, v in n.most_common():
        print(f"{v:4d}  {names.get(k, k)}")


REMOVE = {"ridge", "obstruction", "off_roof", "stray", "remove"}


def score(path, label):
    """The marks against another snapshot of the same roofs. A panel marked
    to go is resolved when no panel covers its centre; "wrong way round" when
    the panel there is turned or gone; "panels should go here" when a panel
    covers the spot; "obstruction not marked" when an obstruction is within
    0.5 m and no panel covers the spot; "is not one" when no obstruction
    covers it. A wrong roof line has no right answer in the marks: it is
    reported as changed (a face edge within 0.5 m before, none now) or
    unchanged, and still needs the owner's eye. A roof marked right must keep
    every panel."""
    import math
    import pyproj
    from shapely.affinity import translate
    from shapely.geometry import Point, shape
    from shapely.ops import transform
    import cases_sheet as CS
    marks = json.loads(Path(path).expanduser().read_text())
    base = json.loads((CS.SNAPS / f"street_{marks['label']}.json").read_text())["buildings"] \
        if (CS.SNAPS / f"street_{marks['label']}.json").exists() else \
        json.loads((CS.SNAPS / f"{marks['label']}.json").read_text())["buildings"]
    new = json.loads((CS.SNAPS / f"{label}.json").read_text())["buildings"]
    T = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform

    def geoms(e, kind):
        return [translate(transform(T, shape(f["geometry"])), *e["shift"])
                for f in e["features"] if f["properties"]["kind"] == kind]

    def turn(g):
        r = g.minimum_rotated_rectangle.exterior.coords
        a, b = (r[1][0] - r[0][0], r[1][1] - r[0][1]), (r[2][0] - r[1][0], r[2][1] - r[1][1])
        v = a if math.hypot(*a) >= math.hypot(*b) else b
        return math.degrees(math.atan2(v[1], v[0])) % 180
    tot, done = Counter(), Counter()
    names = {f[0]: f[1] for f in FAULTS}
    for r in marks["roofs"]:
        e, o = new.get(str(r["id"])), base.get(str(r["id"]))
        if not e or not o:
            print(r["id"], "not in both snapshots")
            continue
        pans, obs, faces = geoms(e, "panel"), geoms(e, "obstruction"), geoms(e, "facet")
        opans, ofaces = geoms(o, "panel"), geoms(o, "facet")
        line = []
        for m in r["panels"] + r["spots"]:
            p, k = Point(m["xy"]), m["fault"]
            if k == "comment":                              # words for a person, nothing to score
                continue
            over = [g for g in pans if g.contains(p)]
            if k in REMOVE:
                ok = not over
            elif k == "wrong_way":
                was = [g for g in opans if g.contains(p)]
                ok = not over or (was and abs((turn(over[0]) - turn(was[0]) + 90) % 180 - 90) > 45)
            elif k == "add_panels":
                ok = bool(over)
            elif k == "missed_obstruction":
                ok = not over and any(g.distance(p) <= 0.5 for g in obs)
            elif k == "not_obstruction":
                ok = not any(g.contains(p) for g in obs)
            elif k == "outline_wrong":                      # the click is on the true roof edge
                from shapely.ops import unary_union
                ok = bool(faces) and unary_union(faces).boundary.distance(p) <= 0.5
            else:                                           # line_wrong
                near = lambda fs: any(g.exterior.distance(p) <= 0.5 for g in fs)
                ok = near(ofaces) != near(faces) or (near(ofaces) and len(faces) != len(ofaces))
                k = "line_wrong"
            tot[k] += 1
            done[k] += bool(ok)
            line.append(("+" if ok else "-") + k)
        if r["right"]:
            same = len(pans) == len(opans) and all(any(g.contains(q.centroid) for g in pans) for q in opans)
            tot["right_kept"] += 1
            done["right_kept"] += same
            line.append(("+" if same else "-") + "right_kept")
        c = Counter(line)
        print(r["id"], f"{len(opans)} -> {len(pans)} panels ", " ".join(f"{k}x{v}" for k, v in sorted(c.items())))
    print()
    for k in tot:
        what = "roofs marked right, unchanged" if k == "right_kept" else names.get(k, k)
        print(f"{done[k]:3d} of {tot[k]:3d}  {what}" + ("  (changed, not judged)" if k == "line_wrong" else ""))


def measure(path):
    """Every panel and spot of the marked roofs, measured the same way, marked
    or not; and per fault the measurement that separates the marked panels
    from the unmarked with the fewest unmarked caught. Unmarked panels count
    as right only on roofs the owner marked or ticked, since those are the
    ones looked at."""
    import csv
    import math
    import statistics
    import pyproj
    from shapely.affinity import translate
    from shapely.geometry import Point, shape
    from shapely.ops import transform, unary_union
    import cases_sheet as CS
    path = Path(path).expanduser()
    marks = json.loads(path.read_text())
    lab = marks["label"]
    f = CS.SNAPS / f"street_{lab}.json"
    snap = json.loads((f if f.exists() else CS.SNAPS / f"{lab}.json").read_text())["buildings"]
    T = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform

    def sides(g):
        r = list(g.minimum_rotated_rectangle.exterior.coords)
        a, b = (r[1][0] - r[0][0], r[1][1] - r[0][1]), (r[2][0] - r[1][0], r[2][1] - r[1][1])
        la, lb = math.hypot(*a), math.hypot(*b)
        v = a if la >= lb else b
        return max(la, lb), math.degrees(math.atan2(v[1], v[0])) % 180

    rows, spots = [], []
    for r in marks["roofs"]:
        e = snap.get(str(r["id"]))
        if not e:
            continue
        ft = [(q["properties"], translate(transform(T, shape(q["geometry"])), *e["shift"])) for q in e["features"]]
        faces = [(q, g) for q, g in ft if q["kind"] == "facet"]
        pans = [(q, g) for q, g in ft if q["kind"] == "panel"]
        roof = unary_union([g for _, g in faces]) if faces else None
        obs = unary_union([g for q, g in ft if q["kind"] == "obstruction"])
        by_xy = {tuple(m["xy"]): m["fault"] for m in r["panels"]}
        for q, g in pans:
            c = g.centroid
            fault = next((v for k, v in by_xy.items() if g.contains(Point(k))), "")
            near = min(faces, key=lambda t: t[1].distance(c), default=None)
            row = dict(id=r["id"], x=round(c.x, 2), y=round(c.y, 2), fault=fault,
                       array_size=q.get("array_size") or 0, shading=q.get("shading_factor"),
                       over_obstruction=round(g.intersection(obs).area / g.area, 3),
                       obstruction_dist_m=round(g.distance(obs), 2) if not obs.is_empty else 99,
                       off_roof=round(1 - g.intersection(roof).area / g.area, 3) if roof else 1)
            if near:
                fq, fg = near
                long_side, face_turn = sides(fg)
                row.update(face_width_m=round(fg.area / long_side, 2), face_area_m2=round(fg.area, 1),
                           face_slope=fq.get("slope_deg"), face_confidence=fq.get("roof_confidence"),
                           face_read=int(bool(fq.get("from_selected"))),
                           outside_face=round(1 - g.intersection(fg).area / g.area, 3),
                           edge_dist_m=round(fg.exterior.distance(c), 2),
                           outer_edge_dist_m=round(roof.boundary.distance(c), 2),
                           turn_vs_face=round(abs((sides(g)[1] - face_turn + 45) % 90 - 45), 1))
            rows.append(row)
        for m in r["spots"]:
            p = Point(m["xy"])
            inside = [t for t in faces if t[1].contains(p)]
            edge = min((g.exterior.distance(p) for _, g in faces), default=99)
            outer = roof.boundary.distance(p) if roof else 99
            where = ("outer edge" if outer <= 0.5 else "inner line" if edge <= 0.5
                     else "inside a face" if inside else "outside the roof")
            spots.append(dict(id=r["id"], x=m["xy"][0], y=m["xy"][1], fault=m["fault"], where=where,
                              line_dist_m=round(edge, 2), outer_edge_dist_m=round(outer, 2),
                              panel_dist_m=round(min((g.distance(p) for _, g in pans), default=99), 2),
                              obstruction_dist_m=round(p.distance(obs), 2) if not obs.is_empty else 99,
                              face_read=int(bool(inside and inside[0][0].get("from_selected"))),
                              text=m.get("text", "")))
    for name, data in (("panels", rows), ("spots", spots)):
        if data:
            keys = list(dict.fromkeys(k for d in data for k in d))
            with open(path.with_name(f"measured_{lab}_{name}.csv"), "w", newline="") as fh:
                w = csv.DictWriter(fh, keys)
                w.writeheader()
                w.writerows(data)
    names = {x[0]: x[1] for x in FAULTS}
    good = [d for d in rows if not d["fault"]]
    print(f"{len(rows)} panels on {len(marks['roofs'])} roofs: {len(rows) - len(good)} marked, {len(good)} left as right")
    skip = {"id", "x", "y", "fault"}
    for k in sorted({d["fault"] for d in rows if d["fault"]}):
        bad = [d for d in rows if d["fault"] == k]
        best = []
        for key in rows[0]:
            if key in skip:
                continue
            b = [d[key] for d in bad if d.get(key) is not None]
            g = [d[key] for d in good if d.get(key) is not None]
            if len(b) < len(bad) or not g:
                continue
            lo, hi = min(b), max(b)
            best.append((sum(v <= hi for v in g), f"{key} <= {hi}", statistics.median(b), statistics.median(g)))
            best.append((sum(v >= lo for v in g), f"{key} >= {lo}", statistics.median(b), statistics.median(g)))
        print(f"\n{names.get(k, k)}: {len(bad)} panels")
        for caught, rule, mb, mg in sorted(best)[:4]:
            print(f"   {rule:34s} takes every marked panel and {caught:3d} of {len(good)} unmarked"
                  f"   (median marked {mb}, unmarked {mg})")
    print()
    for (k, w), n in sorted(Counter((d["fault"], d["where"]) for d in spots).items()):
        print(f"{n:4d}  {names.get(k, k)}: {w}")
    for d in spots:
        if d["text"]:
            print(f"   #{d['id']} comment ({d['where']}): {d['text']}")
    for r in marks["roofs"]:
        if r.get("note"):
            print(f"   #{r['id']} note: {r['note']}")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "page":
        page(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == "score":
        score(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 3 and sys.argv[1] == "score-all":
        for f in sorted((ROOT / "data" / "fault_marks").glob("street_*.json")):
            print(f"=== {f.name} ===")
            score(f, sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "measure":
        measure(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "read":
        read(sys.argv[2])
    else:
        sys.exit(__doc__)
