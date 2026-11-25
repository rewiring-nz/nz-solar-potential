// Address search matching (address_search.js): the rules in its header.
const assert = require("assert");
const S = require("../address_search.js");

const rows = (labels) => labels.map(addr => ({ addr, at: { lng: 0, lat: 0, id: 1 } }));
const towns = ["Queenstown", "Cromwell", "Frankton", "Arrowtown"];
const places = S.placeWords(towns);
const TOWNS = towns.map(t => ({ addr: t + " (area)", town: {} }));
const full = rows([
  "8 Elspeth Street, Cromwell", "10 Elspeth Street, Cromwell", "12 Elspeth Street, Cromwell",
  "9A Elspeth Street, Cromwell", "2 Elspeth Street, Cromwell", "20 Elspeth Street, Cromwell",
  "1 Cromwell Lane, Frankton, Queenstown", "6/60 Hallenstein Street, Queenstown",
  "14 Frankton Road, Queenstown", "3 Ōtāhuhu Place, Queenstown",
]);
const old = rows(["12 Elspeth Street", "8 Elspeth Street", "14 Frankton Road"]);
const labels = (r) => r.map(x => x.addr);
let n = 0;
const t = (name, fn) => { fn(); n++; };

t("town in the query", () =>
  assert.deepStrictEqual(labels(S.search([full], "10 Elspeth Street Cromwell", { places })),
                         ["10 Elspeth Street, Cromwell"]));
t("Google Maps paste", () =>
  assert.deepStrictEqual(labels(S.search([full], "10 Elspeth St, Cromwell 9310, New Zealand", { places })),
                         ["10 Elspeth Street, Cromwell"]));
t("abbreviation and case", () =>
  assert.strictEqual(S.search([full], "10 elspeth st", { places })[0].addr, "10 Elspeth Street, Cromwell"));
t("numbers in numeric order", () =>
  assert.deepStrictEqual(labels(S.search([full], "elspeth", { places })),
    ["2 Elspeth Street, Cromwell", "8 Elspeth Street, Cromwell", "9A Elspeth Street, Cromwell",
     "10 Elspeth Street, Cromwell", "12 Elspeth Street, Cromwell", "20 Elspeth Street, Cromwell"]));
t("number still being typed", () =>
  assert.deepStrictEqual(labels(S.search([full], "1", { places })).slice(0, 3),
    ["1 Cromwell Lane, Frankton, Queenstown", "10 Elspeth Street, Cromwell", "12 Elspeth Street, Cromwell"]));
t("a finished number is exact", () =>
  assert.deepStrictEqual(labels(S.search([full], "1 elspeth", { places })), []));
t("unit numbers", () =>
  assert.strictEqual(S.search([full], "60 hallenstein", { places })[0].addr, "6/60 Hallenstein Street, Queenstown"));
t("macrons", () =>
  assert.strictEqual(S.search([full], "otahuhu", { places })[0].addr, "3 Ōtāhuhu Place, Queenstown"));
t("towns first", () =>
  assert.strictEqual(S.search([TOWNS, full], "cromwell", { places })[0].addr, "Cromwell (area)"));
t("town word optional on labels without a town", () =>
  assert.deepStrictEqual(labels(S.search([old], "12 Elspeth Street Cromwell", { places })), ["12 Elspeth Street"]));
t("a word that is no town still has to match", () =>
  assert.deepStrictEqual(labels(S.search([old], "12 Elspeth Street Wanaka", { places })), []));

t("a town name that starts the street is required", () =>
  assert.deepStrictEqual(labels(S.search([rows(["123 Aubrey Road", "123 Frankton Road"])], "123 Frankton Rd", { places })),
                         ["123 Frankton Road"]));
t("one line per label", () =>
  assert.strictEqual(S.search([rows(["9A Elspeth Street", "9A Elspeth Street"])], "9a elspeth", { places }).length, 1));
t("misspelt street suggests it, with the number when it exists", () => {
  const s = S.suggest([full], "10 Elspath Stret Cromwel", { places });
  assert.strictEqual(s[0].label, "10 Elspeth Street, Cromwell");
  assert.ok(s[0].row);
});
t("missing number suggests the street", () => {
  const s = S.suggest([old], "10 Elspeth Street Cromwell", { places });
  assert.strictEqual(s[0].label, "Elspeth Street");
  assert.ok(s[0].streetOnly);
  assert.deepStrictEqual(labels(S.search([old], s[0].text, { places })).length, 2);
});
t("misspelt town", () =>
  assert.strictEqual(S.suggest([TOWNS], "Cromwel", { places })[0].label, "Cromwell (area)"));
t("transposed letters", () =>
  assert.strictEqual(S.suggest([full], "Elsepth", { places })[0].label, "Elspeth Street, Cromwell"));
t("nothing close, nothing suggested", () =>
  assert.deepStrictEqual(S.suggest([full], "Zzyzx", { places }), []));
t("edit distance", () => {
  assert.strictEqual(S.editDistance("elspath", "elspeth", 2), 1);
  assert.strictEqual(S.editDistance("elsepth", "elspeth", 2), 1);
});
console.log(`address search: ${n} checks passed`);
