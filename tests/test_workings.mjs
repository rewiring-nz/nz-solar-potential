/**
 * Tests for workings.js: a breakdown must be the calculation, and any step
 * that does not add up must be caught.
 *
 * Run:  node tests/test_workings.mjs
 */

import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { Wk } = require("../workings.js");

let pass = 0;
const failures = [];
function check(name, fn) {
  try { fn(); console.log(`  pass  ${name}`); pass++; }
  catch (e) { console.log(`  FAIL  ${name}: ${e.message}`); failures.push(name); }
}
const eq = (a, b, what) => { if (Math.abs(a - b) > 1e-9 * Math.max(1, Math.abs(b))) throw new Error(`${what}: ${a} vs ${b}`); };

check("an operation's value is its own arithmetic", () => {
  const a = Wk.input("a", 1312, "kWh/m²/yr"), b = Wk.input("b", 1.08, "×"), c = Wk.input("c", 0.93, "share");
  const p = Wk.product("p", "kWh/m²/yr", [a, b, c]);
  eq(p.value, 1312 * 1.08 * 0.93, "product");
  eq(Wk.sum("s", "", [a, b]).value, 1313.08, "sum");
  eq(Wk.diff("d", "", a, b).value, 1310.92, "diff");
  eq(Wk.quotient("q", "", a, b).value, 1312 / 1.08, "quotient");
  eq(Wk.complement("k", c).value, 0.07, "complement");
  if (!Wk.verify(p).ok) throw new Error("verify failed on a correct tree");
});

check("a tampered number is caught, however deep", () => {
  const a = Wk.input("a", 2, ""), b = Wk.input("b", 3, "");
  const inner = Wk.product("inner", "", [a, b]);
  const top = Wk.sum("top", "", [inner, Wk.input("c", 1, "")]);
  inner.value = 7;                    // someone "fixes" a number by hand
  const v = Wk.verify(top);
  if (v.ok) throw new Error("not caught");
  if (!v.problems.some(p => p.node === inner)) throw new Error("wrong node blamed");
});

check("a changed input with a stale result is caught", () => {
  const a = Wk.input("a", 2, ""), b = Wk.input("b", 3, "");
  const p = Wk.product("p", "", [a, b]);
  a.value = 4;                        // the input moved, the result did not
  if (Wk.verify(p).ok) throw new Error("stale result not caught");
});

check("tables add up their column, and check their rows", () => {
  const rows = [{ h: 0, kwh: 2, price: 0.3, cost: 0.6 }, { h: 1, kwh: 1, price: 0.3, cost: 0.3 }];
  const t = Wk.table("t", "$", [{ key: "h", label: "Hour" }, { key: "cost", label: "Cost" }], rows, "cost",
    { rowCheck: r => Math.abs(r.cost - r.kwh * r.price) < 1e-12 });
  eq(t.value, 0.9, "table total");
  if (!Wk.verify(t).ok) throw new Error("good table failed");
  rows[1].cost = 0.5;
  t.value = 1.1;                      // even with the total "fixed" to match
  if (Wk.verify(t).ok) throw new Error("bad row not caught");
});

check("clamping is flagged where it happens", () => {
  const c = Wk.clamp("c", "×", Wk.input("x", 9, "×"), 0.2, 5);
  eq(c.value, 5, "clamped");
  if (!c.flags.length) throw new Error("no flag");
  if (!Wk.verify(c).ok) throw new Error("clamp failed verify");
});

check("a step without a check is reported, not trusted", () => {
  const g = Wk.given("g", 10, "", []);
  const v = Wk.verify(g);
  if (!v.unchecked.includes(g)) throw new Error("unchecked step not reported");
  const g2 = Wk.given("g2", 10, "", [Wk.input("x", 5, "")], { check: k => k[0] * 2 });
  if (!Wk.verify(g2).ok || Wk.verify(g2).unchecked.length) throw new Error("checked step failed");
  const g3 = Wk.given("g3", 11, "", [Wk.input("x", 5, "")], { check: k => k[0] * 2 });
  if (Wk.verify(g3).ok) throw new Error("wrong given not caught");
});

check("the written step shows its own numbers", () => {
  const p = Wk.product("p", "kWh/yr", [Wk.input("a", 1000, "kWh/yr"), Wk.input("b", 0.86, "share")]);
  const f = Wk.formula(p);
  if (!/1,000 kWh\/yr × 86% = 860 kWh\/yr/.test(f)) throw new Error(f);
});

check("same() flags two figures that should match", () => {
  if (Wk.same(100, 100.00000001, "x") !== null) throw new Error("false alarm");
  if (Wk.same(100, 101, "x") === null) throw new Error("missed");
});

console.log(`\n${pass} passed, ${failures.length} failed`);
if (failures.length) process.exit(1);
