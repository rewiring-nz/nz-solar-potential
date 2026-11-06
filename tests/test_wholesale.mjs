/**
 * The wholesale page's arithmetic (wholesale.js), on a made-up node a person
 * can check by hand, then on the real file.
 *
 * Run:  node tests/test_wholesale.mjs
 */
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const W = require("../wholesale.js");

let fails = 0;
function c(name, expected, got, how, tol = 1e-9) {
  const ok = typeof expected === "number" ? Math.abs(expected - got) <= tol : expected === got;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}: expected ${expected}, got ${got}  — ${how}`);
  if (!ok) fails++;
}

// columns as the file has them
const cols = ["date"];
for (const g of ["all", "sun", "peak", "night"]) for (const k of ["sum_nominal", "sum_real", "count"]) cols.push(`${g}_${k}`);
// day 1: 48 half-hours at $100/MWh; day 2: 50 half-hours (clocks back) at $200.
// real = nominal x 2 on both.
const day = (date, n, price) => [date, ...["all", "sun", "peak", "night"].flatMap(g => {
  const k = { all: n, sun: 20, peak: 12, night: 16 + (n - 48) }[g];
  return [price * k, 2 * price * k, k];
})];
const node = { days: [day("2026-04-04", 48, 100), day("2026-04-05", 50, 200)] };

let r = W.wsAverage(node, cols, "2026-04-04", "2026-04-05", false);
c("all hours, two days", (48 * 100 + 50 * 200) / 98 / 10, r.all,
  "(48 × $100 + 50 × $200) ÷ 98 half-hours = $151.02/MWh = 15.10 c/kWh: every half-hour counts once, not each day");
c("overnight, two days", (16 * 100 + 18 * 200) / 34 / 10, r.night, "(16 × 100 + 18 × 200) ÷ 34 ÷ 10");
c("half-hours counted", 98, r.halfHours, "48 + 50");
c("days counted", 2, r.days, "both days are in range");
r = W.wsAverage(node, cols, "2026-04-04", "2026-04-05", true);
c("real is the real column", 2 * (48 * 100 + 50 * 200) / 98 / 10, r.all, "the real sums are twice the nominal");
r = W.wsAverage(node, cols, "2026-04-05", "2026-04-05", false);
c("a one-day range takes only that day", 20, r.all, "$200/MWh = 20 c/kWh");
r = W.wsAverage(node, cols, "2027-01-01", "2027-12-31", false);
c("an empty range gives no number", null, r.all, "no half-hours, so no average");

c("1 year back from 5 Nov 2026", "2025-11-06", W.wsYearsBack("2026-11-05", 1), "6 Nov 2025 to 5 Nov 2026 is 365 days");
c("10 years back from 5 Nov 2026", "2016-11-06", W.wsYearsBack("2026-11-05", 10), "inclusive of both ends");
c("1 year back from 29 Feb 2028", "2027-03-01", W.wsYearsBack("2028-02-29", 1), "1 Mar 2027 to 29 Feb 2028 is 366 days");

const m = W.wsMonthly(node, cols, false);
c("monthly: one month", 1, m.length, "both days are April 2026");
c("monthly average equals the range average", W.wsAverage(node, cols, "2026-04-01", "2026-04-30", false).all, m[0].all, "same half-hours");

c("monthly, clipped to a range", 20, W.wsMonthly(node, cols, false, "2026-04-05", "2026-04-30")[0].all,
  "a range starting on the 5th takes only the 5th's $200, not April's whole average");
c("monthly, empty range", 0, W.wsMonthly(node, cols, false, "2030-01-01", "2030-12-31").length, "no months");

// ---- the real file ------------------------------------------------------------
const D = require("../wholesale_prices.json");
for (const [k, n] of Object.entries(D.nodes)) {
  const [first, last] = W.wsSpan(n);
  const a = W.wsAverage(n, D.columns, first, last, false);
  // the sum of monthly sums equals the whole: weight check
  const ms = W.wsMonthly(n, D.columns, false);
  const hh = ms.reduce((s, x) => s + x.halfHours, 0);
  const sum = ms.reduce((s, x) => s + x.all * x.halfHours, 0);
  c(`${k}: months add up to the whole period`, a.all, sum / hh, "Σ(month average × its half-hours) ÷ Σ half-hours", 1e-9);
  c(`${k}: 10-year box is 3,652 or 3,653 days`, true, [3652, 3653].includes(W.wsAverage(n, D.columns, W.wsYearsBack(last, 10), last, true).days), "ten years, two or three of them leap");
}
console.log(fails ? `\n${fails} failed` : "\nall passed");
process.exit(fails ? 1 : 0);
