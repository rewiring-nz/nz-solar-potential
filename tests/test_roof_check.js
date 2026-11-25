// Card choice for the roof check (roof_check.js).
const assert = require("assert");
const RC = require("../roof_check.js");

let seed = 1;
const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;

const aspects = ["outline", "lines", "obstructions", "panels"];
const cards = [];
for (let p = 0; p < 6; p++)
  for (const a of aspects)
    for (const v of ["real", "planted"]) cards.push({ card: `${p}${a}${v}`, photo: `p${p}`, aspect: a });

function check(name, fn) { fn(); console.log("  pass ", name); }

check("a round never holds two cards of one roof aspect", () => {
  const r = RC.pickRound(cards, new Set(), 20, rnd);
  const keys = r.map(RC.seenKey);
  assert.strictEqual(new Set(keys).size, keys.length);
  assert.strictEqual(r.length, 20);
});

check("a round takes every aspect in turn", () => {
  const r = RC.pickRound(cards, new Set(), 8, rnd);
  for (const a of aspects) assert.strictEqual(r.filter(c => c.aspect === a).length, 2);
});

check("seen roof aspects are not shown again, planted copy included", () => {
  const seen = new Set(["p0:lines", "p1:lines"]);
  const r = RC.pickRound(cards, seen, 24, rnd);
  assert.ok(!r.some(c => seen.has(RC.seenKey(c))));
  assert.strictEqual(r.length, 22);
});

check("an exhausted deck gives an empty round", () => {
  const all = new Set(cards.map(RC.seenKey));
  assert.strictEqual(RC.pickRound(cards, all, 10, rnd).length, 0);
});

check("volunteer ids are 16 hex characters", () => {
  assert.match(RC.newVolunteerId(rnd), /^[0-9a-f]{16}$/);
});
