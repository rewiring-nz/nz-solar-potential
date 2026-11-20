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

page writes <out dir>/index.html and one photo per roof (0.05 m a pixel).
Marks are kept in the browser and leave it by "Copy my marks" (pasted into
the chat) or "Download"; save them as data/fault_marks/<label>.json.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# (key, label, colour, applies to). "panel": click panels. "spot": click the roof.
FAULTS = [
    ("ridge", "Over a ridge or roof line", "#e6194b", "panel"),
    ("obstruction", "Over an obstruction", "#f58231", "panel"),
    ("off_roof", "Off the roof / over empty space", "#911eb4", "panel"),
    ("wrong_way", "Wrong way round", "#008080", "panel"),
    ("stray", "Stray panel, not part of an array", "#9a6324", "panel"),
    ("remove", "Should not be here (other)", "#000000", "panel"),
    ("add_panels", "Panels should go here", "#1a9c3c", "spot"),
    ("missed_obstruction", "Obstruction not marked", "#f032e6", "spot"),
    ("not_obstruction", "Marked obstruction is not one", "#4363d8", "spot"),
    ("line_wrong", "Roof line or edge wrong here", "#e6c700", "spot"),
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
 <div><b>1. Pick a fault</b><span class=grp>then click panels:</span><span id=tp></span></div>
 <div><span class=grp style="margin-left:96px">or click a spot on the roof:</span><span id=ts></span></div>
 <div id=act style="margin-top:6px"><button id=copy>Copy my marks</button><button id=dl>Download</button>
 <span class=count id=total></span> &nbsp; Click a mark again to remove it. Marks are saved in this browser as you go.</div>
</div>
<main id=main></main>
<script>
const LABEL="__LABEL__", FAULTS=__FAULTS__, ROOFS=__ROOFS__;
const STORE="fault_marks_"+LABEL, F=Object.fromEntries(FAULTS.map(f=>[f[0],f]));
let marks={}; try{marks=JSON.parse(localStorage.getItem(STORE)||"{}")}catch(e){}
let armed=FAULTS[0][0];
const $=id=>document.getElementById(id), NS="http://www.w3.org/2000/svg";
function keep(){try{localStorage.setItem(STORE,JSON.stringify(marks))}catch(e){} total()}
function rec(id){return marks[id]||(marks[id]={panels:{},spots:[],note:"",right:false})}
function total(){let n=0,r=0;for(const k in marks){const m=marks[k];const c=Object.keys(m.panels).length+m.spots.length;n+=c;if(c||m.right||m.note)r++}
 $("total").textContent=n+" marks on "+r+" of "+ROOFS.length+" roofs"}
function tags(){for(const [host,kind] of [["tp","panel"],["ts","spot"]]){$(host).innerHTML="";
 for(const f of FAULTS.filter(f=>f[3]==kind)){const b=document.createElement("button");b.className="tag"+(f[0]==armed?" on":"");
  b.innerHTML="<i style='background:"+f[2]+"'></i>"+f[1];b.onclick=()=>{armed=f[0];tags()};$(host).appendChild(b)}}}
function el(n,a){const e=document.createElementNS(NS,n);for(const k in a)e.setAttribute(k,a[k]);return e}
function paint(r,svg){const m=rec(r.id);svg.querySelectorAll(".mk").forEach(e=>e.remove());
 r.panels.forEach((p,i)=>{const t=m.panels[i];if(!t)return;const f=F[t];
  svg.appendChild(el("polygon",{class:"mk",points:p.px.map(q=>q.join(",")).join(" "),fill:f[2],"fill-opacity":.75,stroke:"#fff","stroke-width":2,"pointer-events":"none"}))});
 m.spots.forEach((s,j)=>{const f=F[s.tag],g=el("g",{class:"mk",style:"cursor:pointer"});
  g.appendChild(el("circle",{cx:s.px[0],cy:s.px[1],r:r.size/45,fill:f[2],stroke:"#fff","stroke-width":r.size/300}));
  const tx=el("text",{x:s.px[0]+r.size/36,y:s.px[1]+r.size/110,"font-size":r.size/42,fill:"#fff",stroke:"#000","stroke-width":r.size/260,"paint-order":"stroke","font-family":"system-ui","font-weight":600});
  tx.textContent=f[1];g.appendChild(tx);
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
  rec(r.id).spots.push({tag:armed,px:[Math.round(x),Math.round(y)],xy:[+(r.x0+x*r.res).toFixed(2),+(r.y1-y*r.res).toFixed(2)]});keep();paint(r,svg);sum(r)}
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
  roofs.push({id:r.id,right:!!m.right,note:m.note||"",panels:ps,spots:m.spots.map(s=>({fault:s.tag,xy:s.xy}))})}
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
    names = {f[0]: f[1] for f in FAULTS}
    for k, v in n.most_common():
        print(f"{v:4d}  {names.get(k, k)}")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "page":
        page(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 3 and sys.argv[1] == "read":
        read(sys.argv[2])
    else:
        sys.exit(__doc__)
