"""Pack ERA5 daily solar radiation (Open-Meteo archive JSON) into small files the
map loads per building: climate/era5/<lat>_<lon>.json.

    python tools/pack_era5_daily.py <dir of Open-Meteo JSON files>

Each file: {"lat", "lon", "start": "YYYY-MM-DD", "q": 7, "days": N,
"v": base64 of one byte per day = round(MJ/m2 * q), 255 = missing}.
One byte a day keeps 86 years of a cell to ~42 kB. Source: ERA5 reanalysis
(Copernicus Climate Change Service) via Open-Meteo, CC BY 4.0.
Fetch a cell:
    https://archive-api.open-meteo.com/v1/archive?latitude=..&longitude=..
      &start_date=1940-01-01&end_date=2025-12-31&daily=shortwave_radiation_sum
      &models=era5&timezone=Pacific%2FAuckland
"""
import base64, glob, json, sys
from pathlib import Path

Q = 7
OUT = Path(__file__).resolve().parents[1] / "climate" / "era5"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for f in sorted(glob.glob(str(Path(sys.argv[1]) / "cell_*.json"))):
        d = json.loads(Path(f).read_text())
        if "daily" not in d:
            continue
        v = d["daily"]["shortwave_radiation_sum"]
        b = bytes(255 if x is None else min(254, max(0, round(x * Q))) for x in v)
        lat, lon = round(d["latitude"] * 4) / 4, round(d["longitude"] * 4) / 4
        name = f"{lat:.2f}_{lon:.2f}"
        if any(i["name"] == name for i in index):
            continue
        (OUT / f"{name}.json").write_text(json.dumps({
            "lat": lat, "lon": lon, "elev": d.get("elevation"), "start": d["daily"]["time"][0], "q": Q, "days": len(b),
            "v": base64.b64encode(b).decode()}, separators=(",", ":")))
        index.append({"name": name, "lat": lat, "lon": lon, "elev": d.get("elevation")})
    (OUT / "index.json").write_text(json.dumps({
        "source": "ERA5 reanalysis (Copernicus Climate Change Service) via Open-Meteo, CC BY 4.0",
        "cells": index}, separators=(",", ":")))
    print(len(index), "cells ->", OUT)


if __name__ == "__main__":
    main()
