"""Teach ERA5's daily sunlight the dark days a weather station records.

    python tools/station_qmap.py <NIWA daily radiation CSV> [--cell -45.00_168.75] [--radius 0.5 | --cells=a,b,c]

Built 24 Oct from Queenstown Aero AWS for the Wakatipu only:
    --cells=-45.00_168.75,-45.00_168.25,-45.25_168.75

ERA5 is a 25 km reanalysis: day by day it follows Queenstown Airport's
pyranometer closely (r 0.92 over 1991-2026, the same day-to-day persistence),
but it smooths the extremes. Very dark winter days (clearness under 0.15) are
2.7% of the station's winter days and 0.5% of ERA5's, so low-solar runs built
from ERA5 alone come out too short (Josh, 24 Oct; low_solar.js).

The fix keeps ERA5's 86-year sequence of weather and corrects how dark its
dark days are: for each calendar month, each day's sunlight as a share of the
month's average is mapped from its place in ERA5's distribution to the same
place in the station's (quantile mapping, over the days both have). The map
is written to climate/era5/index.json under "qmaps" for the valley cells within
--radius degrees of the station, and low_solar.js applies it.

Input: a DataHub "Climate station data (daily)" radiation CSV (Earth Sciences
NZ / NIWA National Climate Database, non-commercial licence). Only the
mapping's quantiles are written; the station's daily values are not.
"""
import argparse
import base64
import csv
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ERA5 = ROOT / "climate" / "era5"
QS = np.round(np.concatenate([[0.0, 0.5, 1.0, 2.0, 3.0], np.arange(5.0, 96.0, 5.0), [97.0, 98.0, 99.0, 99.5, 100.0]]), 1)
MAX_CELL_ELEV_M = 800


def h0(lat, doy):
    """Daily sunlight above the atmosphere on a level surface, MJ/m2."""
    phi = math.radians(lat)
    dec = math.radians(23.45) * math.sin(2 * math.pi * (284 + doy) / 365)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(dec))))
    e0 = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    return 24 * 3600 / math.pi * 1367e-6 * e0 * (math.cos(phi) * math.cos(dec) * math.sin(ws)
                                                   + ws * math.sin(phi) * math.sin(dec))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--cell", default="-45.00_168.75")
    ap.add_argument("--radius", type=float, default=0.5)
    ap.add_argument("--cells", help="comma-separated; the cells to map, in place of --radius (Queenstown first: "
                    "the Wakatipu's Queenstown, Glenorchy and Kingston cells)")
    a = ap.parse_args()

    station, meta = {}, {}
    with open(a.csv, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                v = float(r["Radiation [MJ/m2]"])
            except (KeyError, ValueError):
                continue
            # observation time is 10:59 or 11:59 UTC: the end of the NZ day
            station[r["Observation time UTC"][:10]] = v
    name = Path(a.csv).name
    d = json.loads((ERA5 / f"{a.cell}.json").read_text())
    raw = base64.b64decode(d["v"])
    t0 = dt.date.fromisoformat(d["start"])
    lat0, lon0 = (float(x) for x in a.cell.split("_"))
    pairs = [[] for _ in range(12)]
    for k, s in station.items():
        day = dt.date.fromisoformat(k)
        i = (day - t0).days
        # a day under 3% of the sunlight above the atmosphere is a fault (one
        # day logged as 0 MJ, 2 Jun 2013), not weather
        if 0 <= i < len(raw) and raw[i] != 255 and s >= 0.03 * h0(lat0, day.timetuple().tm_yday):
            pairs[day.month - 1].append((raw[i] / d["q"], s))
    months = []
    for m in range(12):
        e = np.array([p[0] for p in pairs[m]]); s = np.array([p[1] for p in pairs[m]])
        re, rs = e / e.mean(), s / s.mean()
        # level: the station's month over ERA5's. Not used for the roof's
        # output (its own curve sets that); used for the day's clearness, which
        # splits it into beam and diffuse -- ERA5 runs ~5% dim here
        months.append({"level": round(float(s.mean() / e.mean()), 4),
                       "era5": [round(float(x), 4) for x in np.percentile(re, QS)],
                       "station": [round(float(x), 4) for x in np.percentile(rs, QS)],
                       "days": int(len(e))})
    ix = json.loads((ERA5 / "index.json").read_text())
    cells = a.cells.split(",") if a.cells else [c["name"] for c in ix["cells"]
             if math.hypot(c["lat"] - lat0, c["lon"] - lon0) <= a.radius
             and (c.get("elev") is None or c["elev"] <= MAX_CELL_ELEV_M)]
    ix.setdefault("qmaps", [])
    ix["qmaps"] = [q for q in ix["qmaps"] if q.get("station_file") != name]
    ix["qmaps"].append({
        "station": "Queenstown Aero AWS (agent 5451)" if name.startswith("5451") else name,
        "station_file": name,
        "source": "Earth Sciences NZ / NIWA National Climate Database via DataHub (non-commercial licence); "
                  "quantiles of daily share-of-month-average only",
        "period": f"{min(station)} to {max(station)}",
        "quantiles": [float(q) for q in QS], "cells": cells, "months": months})
    (ERA5 / "index.json").write_text(json.dumps(ix, separators=(",", ":")))
    for m, mo in enumerate(months):
        print(f"month {m + 1:2d}: {mo['days']} days, level {mo['level']:.3f}; 1st pct share ERA5 {mo['era5'][2]:.2f} -> station {mo['station'][2]:.2f}; "
              f"5th {mo['era5'][5]:.2f} -> {mo['station'][5]:.2f}")
    print("cells:", cells)


if __name__ == "__main__":
    main()
