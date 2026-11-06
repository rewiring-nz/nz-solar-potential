"""
The historic wholesale price file (wholesale_prices.json, written by
tools/fetch_wholesale_prices.py) and the clock arithmetic behind it.

Each check states what a person can confirm by hand:
  * trading periods count half-hours from local midnight, so a daylight-saving
    day has 46 (spring) or 50 (autumn) of them;
  * every day from the first to the last is present with every half-hour;
  * peak (7-10am, 6-9pm) is 12 half-hours on every day; overnight
    (11pm-7am) is 16, less 2 on a spring day and plus 2 on an autumn day,
    because the clock change happens at 2-3am;
  * sunlight hours are longer in December than June;
  * real = nominal x latest CPI / that quarter's CPI;
  * where the raw cache is on this machine (data/wholesale/raw, not in git),
    a few days are recomputed from the Electricity Authority's rows.

Run:  python tests/test_wholesale.py
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import fetch_wholesale_prices as F  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def main() -> int:
    # ---- the clock -----------------------------------------------------------
    d = dt.date
    check("5 Apr 2026 (clocks back) has 50 half-hours", F.periods_in_day(d(2026, 4, 5)) == 50)
    check("27 Sep 2026 (clocks forward) has 46 half-hours", F.periods_in_day(d(2026, 9, 27)) == 46)
    check("1 Jun 2026 has 48 half-hours", F.periods_in_day(d(2026, 6, 1)) == 48)
    t = F.period_times(d(2026, 4, 5), 50)
    utc = dt.timezone.utc
    check("period 1 on 5 Apr 2026 starts 11:00 UTC (midnight NZDT, +13)",
          t[0] == dt.datetime(2026, 4, 4, 11, 0, tzinfo=utc))
    check("periods 5 and 7 on 5 Apr 2026 both start at 2am local (the hour repeats)",
          t[4].astimezone(F.NZ).hour == 2 and t[6].astimezone(F.NZ).hour == 2)
    check("period 48 of 5 Apr 2026 starts 10:30pm local",
          t[47].astimezone(F.NZ).strftime("%H:%M") == "22:30")
    t = F.period_times(d(2026, 9, 27), 46)
    check("period 5 on 27 Sep 2026 starts 3am NZDT (2-3am does not exist)",
          t[4].astimezone(F.NZ).strftime("%H:%M") == "03:00")
    check("CPI quarter of 15 Nov 2026 is the December quarter", F.cpi_quarter(d(2026, 11, 15)) == (2026, 12))

    # ---- the file --------------------------------------------------------------
    data = json.loads((ROOT / "wholesale_prices.json").read_text())
    cols = data["columns"]
    at = {c: i for i, c in enumerate(cols)}
    check("columns are date + sum_nominal, sum_real, count for all/sun/peak/night",
          cols == ["date"] + [f"{g}_{k}" for g in ("all", "sun", "peak", "night")
                              for k in ("sum_nominal", "sum_real", "count")])
    yq, mq = (int(x) for x in data["cpi_quarter"].split("-"))
    for node, v in data["nodes"].items():
        days = v["days"]
        first, last = d.fromisoformat(days[0][0]), d.fromisoformat(days[-1][0])
        check(f"{node}: every day from {first} to {last}",
              len(days) == (last - first).days + 1 and
              all(d.fromisoformat(a[0]) + dt.timedelta(days=1) == d.fromisoformat(b[0])
                  for a, b in zip(days, days[1:])))
        wrong = [r[0] for r in days if r[at["all_count"]] != F.periods_in_day(d.fromisoformat(r[0]))]
        check(f"{node}: every day has every half-hour (46/48/50)", not wrong, f"{len(wrong)} days, e.g. {wrong[:3]}")
        check(f"{node}: peak is 12 half-hours every day", all(r[at["peak_count"]] == 12 for r in days))
        bad_night = [r[0] for r in days
                     if r[at["night_count"]] != 16 + (r[at["all_count"]] - 48)]
        check(f"{node}: overnight is 16 half-hours, 14 or 18 on clock-change days", not bad_night,
              f"e.g. {bad_night[:3]}")
        dec = [r[at["sun_count"]] for r in days if r[0][5:10] == "12-21"]
        jun = [r[at["sun_count"]] for r in days if r[0][5:10] == "06-21"]
        check(f"{node}: sunlight on 21 Dec (31-33 half-hours) > 21 Jun (17-19)",
              all(31 <= x <= 33 for x in dec) and all(17 <= x <= 19 for x in jun), f"{set(dec)} {set(jun)}")
        check(f"{node}: no negative sums", all(r[i] >= 0 for r in days for i in range(1, len(cols))))
        check(f"{node}: data reaches within 45 days of today (checked when WHOLESALE_FRESH is set)",
              (d.today() - last).days <= 45 or not __import__("os").environ.get("WHOLESALE_FRESH"), f"last day {last}")

    # ---- pinned history: a refresh must not move old days ----------------------
    # Days are the sum of the Electricity Authority's 48 half-hour prices at
    # Benmore, read from its final-price files (recomputed independently from
    # freshly downloaded files on 7 Nov 2026). A refresh fetches only the
    # newest months; a change here means the build, not the EA, moved them.
    ben = {r[0]: r for r in data["nodes"]["BEN2201"]["days"]}
    for day, want in (("1996-10-15", 1592.20), ("2008-06-15", 10005.31),
                      ("2024-08-15", 18457.44), ("2025-12-31", 58.93)):
        check(f"BEN2201 {day}: the day's half-hour prices sum to ${want:,.2f}",
              abs(ben[day][at["all_sum_nominal"]] - want) < 0.005, str(ben[day][at["all_sum_nominal"]]))

    def mean(node: str, a: str, b: str) -> float:
        rows = [r for r in data["nodes"][node]["days"] if a <= r[0] <= b]
        return sum(r[at["all_sum_nominal"]] for r in rows) / sum(r[at["all_count"]] for r in rows)
    # Published: Mercury's quarterly operating reports give the Benmore
    # average as $261/MWh for Apr-Jun 2021 and $102/MWh for Apr-Jun 2020.
    for a_, b_, want in (("2021-04-01", "2021-06-30", 261), ("2020-04-01", "2020-06-30", 102)):
        got = mean("BEN2201", a_, b_)
        check(f"Benmore {a_[:7]} to {b_[:7]} averages ${want}/MWh as published", abs(got - want) < 0.6, f"{got:.2f}")

    # ---- inflation -------------------------------------------------------------
    cpi_path = F.CPI_CACHE
    node = data["nodes"]["BEN2201"]["days"]
    by_date = {r[0]: r for r in node}
    if cpi_path.exists():
        cpi = F.load_cpi()
        base = cpi[(yq, mq)]
        for day in ("1996-10-15", "2008-06-15", "2016-02-10", "2024-08-15"):
            r = by_date[day]
            q = F.cpi_quarter(d.fromisoformat(day))
            want = r[at["all_sum_nominal"]] * base / cpi[q]
            check(f"real {day} = nominal x {base:g} / {cpi[q]:g}",
                  abs(want - r[at["all_sum_real"]]) < 0.02 * 48, f"{want:.2f} vs {r[at['all_sum_real']]:.2f}")
    # the CPI release the file uses is the newest one: Stats NZ publishes a
    # quarter about 5 weeks after it ends, so the latest is never more than
    # about 5 months old
    q_end = dt.date(yq + (mq == 12), mq % 12 + 1, 1)
    check("CPI quarter is the latest published (ends within 160 days of today)",
          (d.today() - q_end).days <= 160 or not __import__("os").environ.get("WHOLESALE_FRESH"),
          data["cpi_quarter"])
    r = node[-1]
    check("days after the latest CPI quarter: real = nominal",
          d.fromisoformat(r[0]) <= dt.date(yq, mq, 28) or abs(r[at["all_sum_real"]] - r[at["all_sum_nominal"]]) < 0.01)
    r = by_date["2016-02-10"]
    check("2016 prices are smaller in dollars of the day than in today's (inflation)",
          r[at["all_sum_real"]] > 1.2 * r[at["all_sum_nominal"]])

    # ---- recompute days from the Electricity Authority's rows ----------------
    raw = F.RAW / "BEN2201"
    if raw.exists():
        for day in ("2024-08-15", "2026-04-05", "2025-09-28", "2001-06-20"):
            rows = [r for r in csv.DictReader(open(raw / f"{day[:4]}{day[5:7]}.csv")) if r["date"] == day]
            total = sum(float(r["dollars_per_mwh"]) for r in rows)
            got = by_date[day]
            check(f"BEN2201 {day}: {len(rows)} raw half-hours sum to the file's", len(rows) == got[at["all_count"]]
                  and abs(total - got[at["all_sum_nominal"]]) < 0.01, f"{total:.2f} vs {got[at['all_sum_nominal']]}")
            peak = sum(float(r["dollars_per_mwh"]) for r in rows
                       if (int(r["period"]) in range(15, 21) or int(r["period"]) in range(37, 43)))
            if len(rows) == 48:
                check(f"BEN2201 {day}: periods 15-20 and 37-42 are the peak hours", abs(peak - got[at["peak_sum_nominal"]]) < 0.01)
    else:
        print("skip raw recompute: data/wholesale/raw not on this machine")

    print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
