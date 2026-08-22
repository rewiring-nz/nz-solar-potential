"""
Export seasonal generation-curve shapes to data/seasonal_curves.json for the
frontend's per-building season charts.

For every (slope, aspect) bin: per season (NZ: summer=DJF, autumn=MAM,
winter=JJA, spring=SON), a 24-value hourly curve in kW of AC output per kWp
of installed panels --
- "avg":  mean hour-of-day output over the season's days, cloud-adjusted
          with the same calibrated monthly factors the yield model uses.
- "peak": the season's best clear-sky day (max daily clear-sky POA total),
          no cloud derate -- "a very sunny day in that season".
Both share the pilot's terrain-horizon adjustment (direct beam zeroed when
the sun sits behind mountains), reused straight off the SolarModel instance
so these curves can never drift from the yield model's calibration.

kW per kWp = POA W/m2 / 1000 (STC ratio) x inverter efficiency x (1 - system
losses) -- the same derates as facet_yield, so a building's curve scaled by
its placed kWp integrates to roughly its reported annual kWh.

Usage: python src/build_seasonal_curves.py
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pvlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.solar_model import SolarModel, horizon_angle_at, MONTH_NAMES

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SLOPE_STEP = 10   # coarser than the yield lookup on purpose: curve SHAPE
ASPECT_STEP = 30  # varies slowly with orientation, and this keeps the JSON small
SEASONS = {"summer": (12, 1, 2), "autumn": (3, 4, 5), "winter": (6, 7, 8), "spring": (9, 10, 11)}


def main():
    model = SolarModel()  # pilot location; calibrated factors + terrain horizon come along
    location = pvlib.location.Location(model.lat, model.lon, tz="Pacific/Auckland", altitude=310)
    times = pd.date_range("2023-01-01", "2023-12-31 23:00", freq="1h", tz="Pacific/Auckland")
    clearsky = location.get_clearsky(times, model="ineichen")
    solpos = location.get_solarposition(times)

    dni_cs, ghi_cs, dhi_cs = clearsky["dni"], clearsky["ghi"], clearsky["dhi"]
    if model.horizon_profile is not None:
        horizon = horizon_angle_at(model.horizon_profile, solpos["azimuth"].to_numpy())
        blocked = solpos["apparent_elevation"].to_numpy() < horizon
        dni_cs = dni_cs.where(~blocked, 0.0)
        ghi_cs = ghi_cs.where(~blocked, dhi_cs)

    month_abbr = dict(enumerate(MONTH_NAMES, start=1))
    factor = pd.Series(times.month.map(lambda m: model.monthly_factor[month_abbr[m]]), index=times)

    a = config.PV_ASSUMPTIONS
    derate = (a["inverter_efficiency_pct"] / 100) * (1 - a["system_derate_pct"] / 100)

    curves = {}
    for slope in range(0, config.MAX_ROOF_SLOPE_DEG + SLOPE_STEP, SLOPE_STEP):
        for aspect in range(0, 360, ASPECT_STEP):
            poa_cs = pvlib.irradiance.get_total_irradiance(
                surface_tilt=slope, surface_azimuth=aspect,
                dni=dni_cs, ghi=ghi_cs, dhi=dhi_cs,
                solar_zenith=solpos["apparent_zenith"], solar_azimuth=solpos["azimuth"],
            )["poa_global"].clip(lower=0)
            poa_avg = poa_cs * factor          # cloud-adjusted
            kw_avg = poa_avg / 1000 * derate   # kW per kWp
            kw_peak = poa_cs / 1000 * derate

            entry = {"avg": [], "peak": []}
            for months in SEASONS.values():
                in_season = times.month.isin(months)
                sa = kw_avg[in_season]
                entry["avg"].append([round(v, 3) for v in sa.groupby(sa.index.hour).mean().reindex(range(24), fill_value=0)])
                sp = kw_peak[in_season]
                best_day = sp.resample("1D").sum().idxmax().date()
                day = sp[sp.index.date == best_day]
                entry["peak"].append([round(v, 3) for v in day.groupby(day.index.hour).mean().reindex(range(24), fill_value=0)])
            curves[f"{slope}_{aspect}"] = entry

    out = {"slope_step": SLOPE_STEP, "aspect_step": ASPECT_STEP,
           "max_slope": config.MAX_ROOF_SLOPE_DEG,
           "seasons": list(SEASONS), "curves": curves}
    path = DATA_DIR / "seasonal_curves.json"
    path.write_text(json.dumps(out))
    print(f"Saved {path} ({path.stat().st_size / 1e3:.0f}KB, {len(curves)} orientation bins)")


if __name__ == "__main__":
    main()
