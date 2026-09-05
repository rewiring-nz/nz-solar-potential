#!/usr/bin/env node
/**
 * Recompute the `faces` stored in data/roof_labels.json with the geometry the
 * labelling tool uses NOW.
 *
 * The tool exports faces alongside the lines, and the build reads those faces
 * rather than re-deriving them, so that the geometry shipped is the geometry
 * Josh approved on screen (see src/roof_line_source.drawn_faces). The cost of
 * that choice is that a fix to facesFor() reaches new exports only; every roof
 * already in the file keeps the faces the OLD code drew. This closes the gap:
 * it lifts the geometry block straight out of tools/label_template.html --
 * one implementation, not a Python copy that can drift -- and re-runs it over
 * every building, using the same outlines and courtyards the bundle showed.
 *
 * tools/ingest_labels.py runs this after every merge, so it is normally not
 * run by hand. Standalone:
 *
 *     node tools/refresh_label_faces.js            # rewrite data/roof_labels.json
 *     node tools/refresh_label_faces.js --check    # report what would change
 *
 * Outlines come from the bundle (mark_roofs.html), which is the only place the
 * footprint AND its holes exist in the tool's own coordinates.
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const TEMPLATE = path.join(ROOT, "tools", "label_template.html");
const LABELS = path.join(ROOT, "data", "roof_labels.json");
const BUNDLES = [path.join(ROOT, "mark_roofs.html"),
                 path.join(ROOT, "data", "label_set", "mark_roofs.html")];

function geometry() {
  // The block from the first geometry constant to the closure that binds it
  // to the page. Everything facesFor needs lives inside it, by design.
  const t = fs.readFileSync(TEMPLATE, "utf8");
  const a = t.indexOf("const MIN_FACE_M2");
  const b = t.indexOf("const faces = () =>");
  if (a < 0 || b < 0) throw new Error("geometry block not found in label_template.html");
  return new Function(t.slice(a, b) + "\nreturn {facesFor, faceArea, inFace, polyArea};")();
}

function bundleRoofs(file) {
  const m = fs.readFileSync(file, "utf8").match(/const ROOFS = (\[.*?\]);\n/s);
  if (!m) throw new Error(`no ROOFS array in ${file}`);
  const out = {};
  for (const r of JSON.parse(m[1])) out[String(r.id)] = { outline: r.outline, holes: r.holes || [] };
  return out;
}

// Python's json.dumps(indent=1) byte for byte, so a refresh that changes
// nothing produces no diff. Two things JSON.stringify does differently and
// must not: it leaves non-ASCII unescaped, and it sorts integer-like keys --
// which every building id is -- so the file would come out reordered and a
// one-roof change would read as a 120,000-line diff.
function dumps(obj) {
  return JSON.stringify(obj, null, 1)
    .replace(/[\u007f-\uffff]/g, c => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
}
function dumpsDoc(doc, order) {
  const nest = s => s.split("\n").map((l, i) => (i ? "  " + l : l)).join("\n");
  const rows = order.map(k => `  ${JSON.stringify(k)}: ${nest(dumps(doc.buildings[k]))}`);
  return `{\n "tool": ${JSON.stringify(doc.tool)},\n "buildings": {\n${rows.join(",\n")}\n }\n}`;
}
function keyOrder(raw) {
  // the order the ids appear in the file, which JSON.parse throws away
  return [...raw.matchAll(/^  "([^"]+)": \{/gm)].map(m => m[1]);
}

// The same face record the tool's Download button writes, from the same code.
function faceRecords(G, roof, rec) {
  const nopanel = rec.nopanel || [];
  return G.facesFor(roof.outline, rec.lines || [], roof.holes).map(f => {
    const rnd = ring => ring.map(q => [+q[0].toFixed(3), +q[1].toFixed(3)]);
    const o = { ring: rnd(f) };
    if ((f.holes || []).length) o.holes = f.holes.map(rnd);
    o.m2 = +G.faceArea(f).toFixed(1);
    o.usable = !nopanel.some(pt => G.inFace(pt, f));
    o.drawn = !!f.drawn;
    return o;
  });
}

function main() {
  const args = process.argv.slice(2);
  const check = args.includes("--check");
  const bi = args.indexOf("--bundle");
  const bundle = bi >= 0 ? args[bi + 1] : BUNDLES.find(f => fs.existsSync(f));
  if (!bundle || !fs.existsSync(bundle)) {
    console.error("no bundle found: pass --bundle mark_roofs.html");
    return 2;
  }
  const G = geometry();
  const roofs = bundleRoofs(bundle);
  const raw = fs.readFileSync(LABELS, "utf8");
  const doc = JSON.parse(raw);
  const order = keyOrder(raw);
  if (order.length !== Object.keys(doc.buildings || {}).length)
    throw new Error("could not recover the building order from the file");
  const before = dumpsDoc(doc, order);

  let changed = 0, missing = 0, holes = 0;
  const notes = [];
  const sum = fs_ => fs_.reduce((s, f) => s + (f.m2 || 0), 0);
  for (const [bid, rec] of Object.entries(doc.buildings || {})) {
    const roof = roofs[bid];
    if (!roof) { missing++; continue; }
    const faces = faceRecords(G, roof, rec);
    const old = rec.faces || [];
    if (JSON.stringify(old) !== JSON.stringify(faces)) {
      changed++;
      const cut = faces.some(f => f.holes);
      if (cut) holes++;
      notes.push(`  #${bid}  faces ${old.length} -> ${faces.length}   ` +
                 `m2 ${sum(old).toFixed(0)} -> ${sum(faces).toFixed(0)}` +
                 (cut ? "   (islands cut out)" : ""));
    }
    rec.faces = faces;
  }
  const after = dumpsDoc(doc, order);
  console.log(`${Object.keys(doc.buildings).length} roofs, ${changed} with different faces, ` +
              `${holes} carrying holes, ${missing} not in the bundle (left as they were)`);
  if (notes.length) console.log(notes.join("\n"));
  if (check) { console.log(after === before ? "\n--check: no change" : "\n--check: nothing written"); return 0; }
  if (after === before) { console.log("nothing to write"); return 0; }
  fs.writeFileSync(LABELS, after);
  console.log(`wrote ${LABELS}`);
  return 0;
}

process.exit(main());
