"""
Household and regional electricity-use profiles for the bill tool, from EMI.

WHAT EMI PUBLISHES, AND WHAT IT DOES NOT. The Electricity Authority's EMI
site publishes average residential consumption per connection by regional
council (Residential consumption league table, report ZSZG4D) and half-hourly
metered offtake from the grid at every grid exit point (Wholesale datasets,
Metered_data/Grid_export). It publishes no half-hourly split between homes
and businesses at any level: its business and residential demand reports are
monthly (reports PVES0V and 0YUCE0), and it holds no ICP-level half-hourly
data. So:

  * a home's yearly use is EMI's average for its regional council;
  * the hour-of-day shape of each season, and how much more a winter day uses
    than a summer one, come from the half-hourly offtake at the grid exit
    points that supply the town. That load is every connection the network
    serves -- homes, shops, hotels -- so it is the town's shape, not a single
    house's; in Queenstown and Wanaka it is mostly homes and accommodation.

The shapes carry what actually happens: ripple-controlled hot water overnight
(the 3% an hour floor from midnight to 5 am), morning and evening peaks, and
the midday dip.

    python tools/build_load_profiles.py --emi-dir ../solar-map/data/sources/emi/grid_export

Files: YYYYMM_Grid_export.csv from
https://emidatasets.blob.core.windows.net/publicdata/Datasets/Wholesale/Metered_data/Grid_export/
Writes load_profiles.js (loaded by rooftop-solar.html and the bill tests).
"""

import argparse
import collections
import csv
import glob
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SEASON = {12: "summer", 1: "summer", 2: "summer", 3: "autumn", 4: "autumn", 5: "autumn",
          6: "winter", 7: "winter", 8: "winter", 9: "spring", 10: "spring", 11: "spring"}
SEASONS = ["summer", "autumn", "winter", "spring"]

# EMI Residential consumption league table (ZSZG4D), year ended 31 Dec 2025:
# average residential consumption per connection, kWh, network offtake.
COUNCIL_KWH_2025 = {
    "Southland": 8123.80, "Canterbury": 8051.12, "Otago": 7829.06, "Tasman": 7240.93,
    "Hawke's Bay": 7007.01, "Wellington": 6886.34, "Auckland": 6831.69, "Marlborough": 6794.63,
    "Bay of Plenty": 6785.00, "Nelson": 6780.30, "Waikato": 6746.93, "Gisborne": 6538.98,
    "Manawatu-Wanganui": 6411.39, "Taranaki": 6223.78, "Northland": 6161.64, "West Coast": 5751.95,
}

# Towns and the grid exit points that supply them (EMI POC codes).
REGIONS = {
    "queenstown": {"label": "Queenstown (Frankton grid exit point)", "pocs": ["FKN0331"],
                   "council": "Otago", "lat": -45.02, "lon": 168.70},
    "wanaka_cromwell": {"label": "Wanaka and Cromwell (Cromwell grid exit point)", "pocs": ["CML0331"],
                        "council": "Otago", "lat": -44.85, "lon": 169.15},
    "wellington": {"label": "Wellington (Central Park, Kaiwharawhara, Wilton, Takapu Road)",
                   "pocs": ["CPK0331", "CPK0111", "KWA0111", "WIL0331", "TKR0331"],
                   "council": "Wellington", "lat": -41.29, "lon": 174.78},
    # every network offtake except the Tiwai smelter
    "national": {"label": "New Zealand (all network offtake)", "pocs": None,
                 "council": None, "lat": None, "lon": None},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emi-dir", required=True)
    ap.add_argument("--out", default=str(ROOT / "load_profiles.js"))
    a = ap.parse_args()
    files = sorted(glob.glob(str(Path(a.emi_dir) / "*_Grid_export.csv")))
    if len(files) < 12:
        raise SystemExit(f"need 12 months of Grid_export files, found {len(files)}")
    files = files[-12:]
    hours = {k: {s: [0.0] * 24 for s in SEASONS} for k in REGIONS}
    days = {k: collections.Counter() for k in REGIONS}
    seen_day = {k: set() for k in REGIONS}
    for f in files:
        for r in csv.DictReader(open(f)):
            if r["Flow_Direction"] != "X" or r["Unit_Measure"] != "kWh":
                continue
            poc = r["POC"]
            d = date.fromisoformat(r["Trading_Date"])
            s = SEASON[d.month]
            tp = [float(r.get(f"TP{i}") or 0) for i in range(1, 51)]
            # Trading period 1 starts at local midnight. A daylight-saving day
            # has 46 or 50 periods; the first 48 are taken (one hour is
            # misplaced on two days a year).
            hr = [tp[2 * h] + tp[2 * h + 1] for h in range(24)]
            for k, R in REGIONS.items():
                if R["pocs"] is None:
                    if poc.startswith("TWI"):
                        continue
                elif poc not in R["pocs"]:
                    continue
                for h in range(24):
                    hours[k][s][h] += hr[h]
                if (d, s) not in seen_day[k]:
                    seen_day[k].add((d, s))
                    days[k][s] += 1
    out = {"source": ("EMI (Electricity Authority): Wholesale/Metered_data/Grid_export "
                      f"{Path(files[0]).name[:6]}-{Path(files[-1]).name[:6]}; "
                      "Residential consumption league table (ZSZG4D), year to 31 Dec 2025"),
           "council_kwh": COUNCIL_KWH_2025, "regions": {}}
    for k, R in REGIONS.items():
        per_day = {s: sum(hours[k][s]) / max(1, days[k][s]) for s in SEASONS}
        mean = sum(per_day[s] * days[k][s] for s in SEASONS) / max(1, sum(days[k].values()))
        out["regions"][k] = {
            "label": R["label"], "council": R["council"], "lat": R["lat"], "lon": R["lon"],
            # each season's day vs the year's average day
            "season_factor": {s: round(per_day[s] / mean, 4) for s in SEASONS},
            # share of the day's use in each hour (local time), per season
            "shape": {s: [round(v / sum(hours[k][s]), 5) for v in hours[k][s]] for s in SEASONS},
        }
    js = ("// Generated by tools/build_load_profiles.py from EMI data -- do not edit by hand.\n"
          "// Household use per regional council, and each town's hour-of-day shape per season.\n"
          "const BT_EMI = " + json.dumps(out, indent=1) + ";\n"
          "if (typeof module !== \"undefined\") module.exports = { BT_EMI };\n")
    Path(a.out).write_text(js)
    for k, R in out["regions"].items():
        print(k, R["season_factor"])


if __name__ == "__main__":
    main()
