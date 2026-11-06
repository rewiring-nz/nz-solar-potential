"""
Historic wholesale electricity prices, half-hour by half-hour, for the map's
nodes, adjusted for inflation -- for the wholesale price comparison page.

SOURCES (both public, both refreshed by this script):
  * Electricity Authority, Final energy prices: the price at every grid node
    for every half-hour trading period since October 1996, $/MWh, nominal.
    Monthly files (Datasets/Wholesale/DispatchAndPricing/FinalEnergyPrices/
    ByMonth/YYYYMM_FinalEnergyPrices.csv) and, for the newest weeks, daily
    files (FinalEnergyPrices/YYYYMMDD_FinalEnergyPrices.csv) on the EA's
    public blob store. A monthly file marked _incomplete is skipped for the
    daily files of that month.
  * Stats NZ, Consumers price index, All groups (series CPIQ.SE9A),
    quarterly, from the latest CPI release's index-numbers CSV.

WHAT IS COMPUTED, per node:
  * Each trading period's time: periods count half-hours from LOCAL midnight
    of the trading date, so a daylight-saving day has 46 or 50 of them. The
    period's start is local midnight (Pacific/Auckland) converted to UTC plus
    30 minutes per period before it; nothing assumes 48 a day.
  * Groups, at the period's midpoint, in local time:
      sun     the sun above the horizon at the node's town (pvlib);
      peak    the bill model's peak hours (economics.js peak_hours, 7-10am
              and 6-9pm, every day);
      night   11pm to 7am;
      all     every period.
  * Real dollars: price x CPI(latest quarter) / CPI(the period's quarter).
  * For the page (wholesale_prices.json): for each day, the sum and count of
    nominal and real prices in each group, so the average over ANY range of
    days is exact (sum of sums / sum of counts) -- a simple time average,
    each half-hour weighted equally.

Raw per-month extracts are cached under data/wholesale/raw/<NODE>/ so a
re-run fetches only new months; a copy is in gs://rewiring-solar-data/wholesale/
(gcloud storage rsync -r data/wholesale gs://rewiring-solar-data/wholesale).
December 2025 exists at the EA only as daily files (its monthly file is
_incomplete) and the EA keeps daily files about a year, so that copy is the
lasting one for that month.

Run:
    python tools/fetch_wholesale_prices.py            # fetch new months, rebuild
    python tools/fetch_wholesale_prices.py --build    # rebuild from the cache
    python tools/fetch_wholesale_prices.py --months 202605   # refetch a month
tests/test_wholesale.py checks the daylight-saving days, the groups, the
inflation arithmetic and the file against the cache.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "wholesale" / "raw"
OUT = ROOT / "wholesale_prices.json"
CPI_CACHE = ROOT / "data" / "wholesale" / "cpi_all_groups.csv"

BLOB = "https://emidatasets.blob.core.windows.net/publicdata"
PREFIX = "Datasets/Wholesale/DispatchAndPricing/FinalEnergyPrices/"
NZ = ZoneInfo("Pacific/Auckland")

# The map's nodes: Frankton for Queenstown, Cromwell for Central Otago
# (Wanaka, Cromwell), Benmore as the South Island reference the EA reports.
NODES = {
    "FKN0331": {"name": "Frankton (Queenstown)", "lat": -45.03, "lon": 168.66},
    "CML0331": {"name": "Cromwell (Central Otago)", "lat": -45.04, "lon": 169.20},
    "BEN2201": {"name": "Benmore (South Island reference)", "lat": -44.57, "lon": 170.20},
}
PEAK_HOURS = [(7, 10), (18, 21)]          # economics.js ECON_DEFAULTS.peak_hours
NIGHT = (23, 7)                           # 11pm to 7am
UA = {"User-Agent": "Mozilla/5.0 (rewiring-solar wholesale fetch)"}


def _get(url: str, timeout=120) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def list_files(prefix: str) -> list[str]:
    """Every blob name under a prefix (the listing pages 5,000 at a time)."""
    names, marker = [], ""
    while True:
        url = f"{BLOB}?restype=container&comp=list&prefix={prefix}" + (f"&marker={marker}" if marker else "")
        xml = _get(url).decode()
        names += re.findall(r"<Name>([^<]+)</Name>", xml)
        m = re.search(r"<NextMarker>([^<]+)</NextMarker>", xml)
        if not m:
            return names
        marker = m.group(1)


def sources() -> dict[str, list[str]]:
    """Month (YYYYMM) -> the files that hold it: the monthly file, or the
    daily files where the monthly one is missing or incomplete."""
    by_month: dict[str, list[str]] = {}
    for n in list_files(PREFIX + "ByMonth/"):
        m = re.search(r"/(\d{6})_FinalEnergyPrices(_incomplete)?\.csv$", n)
        if m and not m.group(2):
            by_month[m.group(1)] = [n]
    daily: dict[str, list[str]] = {}
    for n in list_files(PREFIX):
        m = re.search(r"FinalEnergyPrices/(\d{8})_FinalEnergyPrices\.csv$", n)
        if m:
            daily.setdefault(m.group(1)[:6], []).append(n)
    for ym, files in daily.items():
        if ym not in by_month:
            by_month[ym] = sorted(files)
    return dict(sorted(by_month.items()))


def extract(names: list[str]) -> dict[str, list[tuple[str, int, float]]]:
    """The map's nodes' rows from EA files: node -> [(date, period, $/MWh)]."""
    out = {n: [] for n in NODES}
    for name in names:
        text = _get(f"{BLOB}/{name}", timeout=600).decode("utf-8-sig")
        rd = csv.reader(io.StringIO(text))
        head = next(rd)
        i_d, i_p, i_n, i_v = (head.index(c) for c in
                              ("TradingDate", "TradingPeriod", "PointOfConnection", "DollarsPerMegawattHour"))
        for row in rd:
            if row[i_n] in out and row[i_v] != "":
                out[row[i_n]].append((row[i_d], int(row[i_p]), float(row[i_v])))
    return out


def published_months() -> set[str]:
    """Months wholesale_prices.json already holds whole, for every node."""
    if not OUT.exists():
        return set()
    old = json.loads(OUT.read_text())
    have = None
    for v in old["nodes"].values():
        days: dict[str, int] = {}
        for r in v["days"]:
            days[r[0][:7].replace("-", "")] = days.get(r[0][:7].replace("-", ""), 0) + 1
        full = {ym for ym, n in days.items()
                if n == (dt.date(int(ym[:4]) + int(ym[4:]) // 12, int(ym[4:]) % 12 + 1, 1)
                         - dt.date(int(ym[:4]), int(ym[4:]), 1)).days}
        have = full if have is None else have & full
    return have or set()


def fetch(months: list[str] | None = None, full: bool = False) -> int:
    """Fetch the months neither the raw cache nor the published file holds,
    and always the newest two: their daily files keep arriving, and a price
    can still be revised in the weeks after. --full ignores the published
    file (needed when a group's definition changes)."""
    src = sources()
    held = set() if full else published_months()
    if months:      # asked for by name: always fetched, to pick up a revision
        todo = [ym for ym in src if ym in months]
    else:
        todo = [ym for ym in src if ym not in held
                and not all((RAW / n / f"{ym}.csv").exists() for n in NODES)]
    for ym in sorted(src)[-2:]:
        if ym not in todo:
            todo.append(ym)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        results = ex.map(lambda ym: (ym, extract(src[ym])), todo)
    for ym, rows in results:
        for node, rs in rows.items():
            (RAW / node).mkdir(parents=True, exist_ok=True)
            with open(RAW / node / f"{ym}.csv", "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["date", "period", "dollars_per_mwh"])
                w.writerows(sorted(rs))
        print(f"{ym}: " + ", ".join(f"{n} {len(r)}" for n, r in rows.items()), flush=True)
    return len(todo)


# ---- inflation ----------------------------------------------------------------
CPI_PAGE = "https://www.stats.govt.nz/information-releases/consumers-price-index-{m}-{y}-quarter/"


def fetch_cpi() -> None:
    """Stats NZ's newest CPI release: its index-numbers CSV, All groups."""
    today = dt.date.today()
    y, m = today.year, (today.month - 1) // 3 * 3 + 3     # this quarter, then back
    for _ in range(8):
        if _:
            m -= 3
            if m <= 0:
                m, y = m + 12, y - 1
        name = {3: "march", 6: "june", 9: "september", 12: "december"}[m]
        try:
            page = _get(CPI_PAGE.format(m=name, y=y)).decode()
        except Exception:
            continue
        hit = re.search(r'(/assets/Uploads/Consumers-price-index/[^"&]*?index-numbers\.csv)',
                        page.replace("\\/", "/"))
        if not hit:
            continue
        text = _get("https://www.stats.govt.nz" + hit.group(1)).decode("utf-8-sig")
        rows = [r for r in csv.DictReader(io.StringIO(text)) if r["Series_reference"] == "CPIQ.SE9A"
                and r["Data_value"] not in ("", "NA")]
        CPI_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(CPI_CACHE, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["period", "cpi"])
            for r in rows:
                w.writerow([r["Period"], r["Data_value"]])
        print(f"CPI: {len(rows)} quarters to {rows[-1]['Period']} from {name} {y}")
        return
    raise RuntimeError("no Stats NZ CPI release found in the last two years")


def load_cpi() -> dict[tuple[int, int], float]:
    """(year, quarter-ending month) -> CPI All groups (June 2017 = 1000)."""
    out = {}
    for r in csv.DictReader(open(CPI_CACHE)):
        y, m = r["period"].split(".")
        out[(int(y), int(m))] = float(r["cpi"])
    return out


def cpi_quarter(d: dt.date) -> tuple[int, int]:
    return (d.year, (d.month - 1) // 3 * 3 + 3)


# ---- the half-hours -------------------------------------------------------------
def period_times(date: dt.date, n_periods: int) -> list[dt.datetime]:
    """UTC start of each trading period of a trading date: local midnight
    plus 30 minutes per period before it (46 or 50 on daylight-saving days)."""
    start = dt.datetime(date.year, date.month, date.day, tzinfo=NZ).astimezone(dt.timezone.utc)
    return [start + dt.timedelta(minutes=30 * i) for i in range(n_periods)]


def periods_in_day(date: dt.date) -> int:
    """46, 48 or 50. Both midnights go to UTC before subtracting: Python
    subtracts two datetimes that share a tzinfo by wall clock, which gives
    48 on every day."""
    nxt = date + dt.timedelta(days=1)
    a = dt.datetime(date.year, date.month, date.day, tzinfo=NZ).astimezone(dt.timezone.utc)
    b = dt.datetime(nxt.year, nxt.month, nxt.day, tzinfo=NZ).astimezone(dt.timezone.utc)
    return int((b - a).total_seconds() // 1800)


def in_hours(hour: float, spans) -> bool:
    return any(a <= hour < b for a, b in spans)


def is_night(hour: float) -> bool:
    return hour >= NIGHT[0] or hour < NIGHT[1]


def build() -> None:
    import numpy as np
    import pandas as pd
    import pvlib

    cpi = load_cpi()
    latest_q = max(cpi)
    base = cpi[latest_q]
    old_days = ({k: v["days"] for k, v in json.loads(OUT.read_text())["nodes"].items()}
                if OUT.exists() else {})
    nodes_out = {}
    for node, meta in NODES.items():
        files = sorted((RAW / node).glob("*.csv"))
        if not files:
            raise SystemExit(f"{node}: nothing in {RAW / node}; run without --build")
        df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values(["date", "period"]).drop_duplicates(["date", "period"])
        days = sorted(df["date"].unique())
        # one period list per day, with its UTC midpoint and local hour
        mids, local_hour, d_index = [], [], []
        by_day = df.groupby("date")
        for d in days:
            g = by_day.get_group(d)
            n = periods_in_day(d)
            starts = period_times(d, n)
            for p in g["period"]:
                if 1 <= p <= n:
                    t = starts[p - 1] + dt.timedelta(minutes=15)
                    mids.append(t)
                    lt = t.astimezone(NZ)
                    local_hour.append(lt.hour + lt.minute / 60)
                else:
                    raise SystemExit(f"{node} {d}: trading period {p} on a day of {n}")
        df = df.reset_index(drop=True)
        ok = pd.Series([m is not None for m in mids])
        df = df[ok.values].reset_index(drop=True)
        mids = pd.DatetimeIndex([m for m in mids if m is not None])
        hours = np.array([h for h in local_hour if h is not None])
        elev = pvlib.solarposition.get_solarposition(mids, meta["lat"], meta["lon"])["apparent_elevation"].to_numpy()
        price = df["dollars_per_mwh"].to_numpy()
        groups = {
            "all": np.ones(len(df), bool),
            "sun": elev > 0,
            "peak": np.array([in_hours(h, PEAK_HOURS) for h in hours]),
            "night": np.array([is_night(h) for h in hours]),
        }
        out_days = []
        for d, idx in df.groupby("date").indices.items():
            row = [d.isoformat()]
            for gname in ("all", "sun", "peak", "night"):
                m = groups[gname][idx]
                pr = price[idx][m]
                row += [round(float(pr.sum()), 2), None, int(m.sum())]
            out_days.append(row)
        # days the raw cache does not hold come from the published file (a
        # refresh fetches only the newest months)
        mine = {r[0] for r in out_days}
        out_days += [list(r) for r in old_days.get(node, []) if r[0] not in mine]
        out_days.sort(key=lambda r: r[0])
        # real = nominal x CPI(latest) / CPI(the day's quarter); every half-hour
        # of a day is in one quarter, so the day's sums scale together. A
        # quarter Stats NZ has not yet published (the current one) is taken at
        # the latest index: those prices are already today's dollars to within
        # that quarter's inflation.
        for r in out_days:
            q = cpi_quarter(dt.date.fromisoformat(r[0]))
            f = 1.0 if q > latest_q else base / cpi[q]
            for g in range(4):
                r[2 + 3 * g] = round(r[1 + 3 * g] * f, 2)
        wrong = [r[0] for r in out_days if r[3] != periods_in_day(dt.date.fromisoformat(r[0]))]
        if wrong:
            raise SystemExit(f"{node}: {len(wrong)} days without every half-hour, e.g. {wrong[:5]}")
        gaps = [a[0] for a, b in zip(out_days, out_days[1:])
                if dt.date.fromisoformat(a[0]) + dt.timedelta(days=1) != dt.date.fromisoformat(b[0])]
        if gaps:
            raise SystemExit(f"{node}: days missing after {gaps[:5]}")
        days = [dt.date.fromisoformat(out_days[0][0]), dt.date.fromisoformat(out_days[-1][0])]
        nodes_out[node] = {"name": meta["name"], "days": out_days}
        print(f"{node}: {len(out_days)} days, {days[0]} to {days[-1]}")
    out = {
        "source": "Electricity Authority, Final energy prices (emi.ea.govt.nz), half-hourly, $/MWh",
        "cpi": f"Stats NZ CPI All groups (CPIQ.SE9A), real dollars of the {latest_q[0]} quarter to "
               f"{dt.date(2000, latest_q[1], 1).strftime('%B')} (index {base:g})",
        "cpi_quarter": f"{latest_q[0]}-{latest_q[1]:02d}",
        "groups": {"all": "every half-hour", "sun": "sun above the horizon at the node's town",
                   "peak": "7-10am and 6-9pm, every day", "night": "11pm to 7am"},
        "columns": ["date"] + [f"{g}_{k}" for g in ("all", "sun", "peak", "night")
                               for k in ("sum_nominal", "sum_real", "count")],
        "nodes": nodes_out,
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    print(f"wrote {OUT.name} ({OUT.stat().st_size / 1e6:.1f} MB)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="rebuild from the cache only")
    ap.add_argument("--months", nargs="*", help="only these YYYYMM months")
    ap.add_argument("--full", action="store_true", help="refetch every month the raw cache lacks")
    a = ap.parse_args()
    if not a.build:
        fetch_cpi()
        fetch(a.months, a.full)
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
