/**
 * Tests for facesFor(), the planar subdivision the labelling tool builds from
 * Josh's lines -- the geometry the build ships for every roof he has marked.
 *
 * These exist because of 32 Frankton Road (#4725584): 219 drawn lines, and the
 * tool exported one 4,962 m2 face on a 4,032 m2 footprint beside 37 real faces.
 * Two things were missing, and each has a case below: the courtyard was never
 * part of the arrangement, so lines that ended on it closed nothing; and an
 * island -- a dormer drawn as a closed loop -- was never subtracted from the
 * face around it, so areas summed to more than the roof.
 *
 * The geometry is lifted straight out of tools/label_template.html, the same
 * way tools/refresh_label_faces.js does, so the code under test is the code
 * in the tool and not a copy of it.
 *
 * Run:  node tests/test_faces.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(ROOT, "tools", "label_template.html"), "utf8");
const a = html.indexOf("const MIN_FACE_M2");
const b = html.indexOf("const faces = () =>");
if (a < 0 || b < 0) throw new Error("geometry block not found in label_template.html");
const G = new Function(html.slice(a, b) + "\nreturn {facesFor, faceArea, inFace, polyArea};")();

let pass = 0;
const failures = [];
function check(name, fn) {
  try { fn(); console.log(`  pass  ${name}`); pass++; }
  catch (e) { console.log(`  FAIL  ${name}\n        ${e.message}`); failures.push(name); }
}
function near(x, y, tol, what) {
  if (Math.abs(x - y) > tol) throw new Error(`${what}: ${x} vs ${y}`);
}
function eq(x, y, what) {
  if (x !== y) throw new Error(`${what}: ${JSON.stringify(x)} vs ${JSON.stringify(y)}`);
}

// A closed ring the way the tool stores outlines: first point repeated last.
const box = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];
const line = (ax, ay, bx, by, kind = "ridge") => ({ kind, a: [ax, ay], b: [bx, by] });
const areas = faces => faces.map(G.faceArea).sort((p, q) => q - p);
const sum = faces => faces.reduce((s, f) => s + G.faceArea(f), 0);

console.log("faces:");

check("a ridge across a square gives two halves, both his", () => {
  const f = G.facesFor(box(0, 0, 10, 10), [line(0, 5, 10, 5)]);
  eq(f.length, 2, "faces");
  near(areas(f)[0], 50, 1e-6, "half");
  eq(f.every(x => x.drawn), true, "drawn");
});

check("no lines at all: one face, not his", () => {
  const f = G.facesFor(box(0, 0, 10, 10), []);
  eq(f.length, 1, "faces");
  near(G.faceArea(f[0]), 100, 1e-6, "area");
  eq(f[0].drawn, false, "drawn");
});

check("an island is a hole in the face around it, and the areas add up", () => {
  // a 2 x 2 dormer drawn as a closed loop, touching nothing
  const loop = [line(4, 4, 6, 4, "cliff"), line(6, 4, 6, 6, "cliff"),
                line(6, 6, 4, 6, "cliff"), line(4, 6, 4, 4, "cliff")];
  const f = G.facesFor(box(0, 0, 10, 10), loop);
  eq(f.length, 2, "faces");
  const [big, small] = f.slice().sort((p, q) => G.faceArea(q) - G.faceArea(p));
  near(G.faceArea(small), 4, 1e-6, "island");
  near(G.faceArea(big), 96, 1e-6, "surround less island");
  eq(big.holes.length, 1, "surround carries the island as a hole");
  near(sum(f), 100, 1e-6, "sum equals the roof");
  // a no-panel click on the dormer is on the dormer, not on the wing
  eq(G.inFace([5, 5], small), true, "click hits island");
  eq(G.inFace([5, 5], big), false, "click misses surround");
  eq(big.drawn && small.drawn, true, "both his: the loop bounds both");
});

check("a courtyard is open air: cut out of the roof, never a face", () => {
  const f = G.facesFor(box(0, 0, 10, 10), [], [box(3, 3, 7, 3 + 4)]);
  eq(f.length, 1, "faces");
  near(G.faceArea(f[0]), 84, 1e-6, "roof less courtyard");
  eq(f[0].holes.length, 1, "courtyard is a hole");
});

check("a line ending on the courtyard edge closes a face (32 Frankton Road)", () => {
  // ring-shaped roof; two ridges from the street edge to the courtyard edge
  const yard = box(3, 3, 7, 7);
  const f = G.facesFor(box(0, 0, 10, 10), [line(0, 5, 3, 5), line(7, 5, 10, 5)], [yard]);
  eq(f.length, 2, "the ring splits into two");
  near(areas(f)[0], 42, 1e-6, "top half");
  near(areas(f)[1], 42, 1e-6, "bottom half");
  eq(f.every(x => x.drawn), true, "both his");
});

check("stubs that close nothing do not claim the roof (#4735242)", () => {
  // a dangling ridge from the eave, and a floating one in the middle
  const f = G.facesFor(box(0, 0, 10, 10), [line(0, 5, 3, 5), line(6, 2, 8, 4)]);
  eq(f.length, 1, "one face");
  near(G.faceArea(f[0]), 100, 1e-6, "the whole roof");
  eq(f[0].some(q => q[0] === 3 && q[1] === 5), false, "the stub is despiked off the ring");
  eq(f[0].drawn, false, "not his");
});

check("a line overshooting the eave grows the boundary, but the strip is not his", () => {
  // 10 x 10 roof, a ridge from y=0 to y=13: 23% of its length is outside, so
  // the boundary grows to the hull. The two halves are his; the triangles
  // between eave and hull are bounded by 3 m of ridge and 5.8 m of hull.
  const f = G.facesFor(box(0, 0, 10, 10), [line(5, 0, 5, 13)]);
  const his = f.filter(x => x.drawn), not = f.filter(x => !x.drawn);
  eq(his.length, 2, "two halves claimed");
  near(sum(his), 100, 1e-6, "claimed area is the roof");
  eq(not.length >= 1, true, "the overshoot strip exists");
  eq(not.every(x => G.polyArea(x) > 0), true, "and has area");
});

check("exported rings carry the fields the build reads", () => {
  const f = G.facesFor(box(0, 0, 10, 10), [line(0, 5, 10, 5)]);
  for (const x of f) {
    eq(Array.isArray(x.holes), true, "holes array");
    eq(typeof x.drawn, "boolean", "drawn flag");
    eq(x.comp, undefined, "no internals leak");
  }
});

console.log(`\n${pass} passed, ${failures.length} failed`);
if (failures.length) {
  console.log("failed: " + failures.join(", "));
  process.exit(1);
}
