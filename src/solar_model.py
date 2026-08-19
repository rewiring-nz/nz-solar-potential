"""
Annual/daily kWh per roof facet, from slope + aspect + latitude via pvlib,
bias-corrected against NASA POWER's actual (cloud-adjusted) irradiance so
the estimate reflects real Queenstown weather, not idealised clear-sky
sun.

Method:
1. pvlib's Ineichen clear-sky model gives hourly GHI/DNI/DHI for the
   pilot's location over a representative year.
2. NASA POWER's climatology API gives real monthly-average GHI
   (ALLSKY_SFC_SW_DWN) alongside its own clear-sky reference
   (CLRSKY_SFC_SW_DWN) for the same point. The ratio of the two is an
   empirical "how much does cloud cut irradiance this month" factor,
   applied to every clear-sky hour in that month before anything else.
3. For each (tilt, azimuth) combination, pvlib transposes the
   cloud-corrected horizontal irradiance onto the tilted panel plane and
   the result is integrated over the year -> kWh/m2/year actually
   expected on a facet with that slope/aspect.
4. That's cached in a small lookup table (5 degree slope bins x 10 degree
   aspect bins = a few hundred pvlib runs total) rather than re-run per
   facet -- there are 3600+ facets in the pilot, but only ~350 distinct
   (slope, aspect) bins.
5. kWh -> panel output uses the PVWatts-style linear approximation:
   dc_kWh = POA_kWh_per_m2 * panel_rated_kW (since panel_rated_w is
   defined at the 1000 W/m2 STC reference, "kWh of POA irradiance per m2"
   converts directly to "equivalent full-rated-power hours"), then
   config.PV_ASSUMPTIONS' inverter efficiency and system derate are
   applied on top. This ignores temperature derating specifically (rolled
   into the flat system_derate_pct instead) -- a real design would model
   cell temperature separately, documented simplification for the pilot.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pvlib
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

SLOPE_BIN_DEG = 5
ASPECT_BIN_DEG = 10
MONTH_NAMES = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def pilot_location():
    min_lon, min_lat, max_lon, max_lat = config.PILOT_BBOX
    lat, lon = (min_lat + max_lat) / 2, (min_lon + max_lon) / 2
    return lat, lon


def fetch_nasa_power_monthly_factors(lat, lon):
    """Returns dict {month_abbr: actual/clearsky GHI ratio}, e.g. {'JAN': 0.69, ...}."""
    url = "https://power.larc.nasa.gov/api/temporal/climatology/point"
    params = {
        "parameters": "ALLSKY_SFC_SW_DWN,CLRSKY_SFC_SW_DWN",
        "community": "RE",
        "longitude": lon,
        "latitude": lat,
        "format": "JSON",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    param = resp.json()["properties"]["parameter"]
    allsky, clrsky = param["ALLSKY_SFC_SW_DWN"], param["CLRSKY_SFC_SW_DWN"]
    return {m: allsky[m] / clrsky[m] for m in MONTH_NAMES}


def build_poa_lookup_table(lat, lon, tz="Pacific/Auckland", year=2023):
    """Returns dict {(slope_bin_deg, aspect_bin_deg): annual_poa_kwh_per_m2}."""
    location = pvlib.location.Location(lat, lon, tz=tz, altitude=310)  # ~Queenstown lake level
    times = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="1h", tz=tz)

    clearsky = location.get_clearsky(times, model="ineichen")
    solpos = location.get_solarposition(times)

    monthly_factor = fetch_nasa_power_monthly_factors(lat, lon)
    month_abbr_by_num = dict(enumerate(MONTH_NAMES, start=1))
    factor_series = times.month.map(lambda m: monthly_factor[month_abbr_by_num[m]])
    factor_series = pd.Series(factor_series, index=times)

    ghi = clearsky["ghi"] * factor_series
    dni = clearsky["dni"] * factor_series
    dhi = clearsky["dhi"] * factor_series

    lookup = {}
    for slope_bin in range(0, config.MAX_ROOF_SLOPE_DEG + SLOPE_BIN_DEG, SLOPE_BIN_DEG):
        for aspect_bin in range(0, 360, ASPECT_BIN_DEG):
            poa = pvlib.irradiance.get_total_irradiance(
                surface_tilt=slope_bin,
                surface_azimuth=aspect_bin,
                dni=dni, ghi=ghi, dhi=dhi,
                solar_zenith=solpos["apparent_zenith"],
                solar_azimuth=solpos["azimuth"],
            )
            annual_kwh_per_m2 = poa["poa_global"].sum() / 1000  # Wh -> kWh (hourly W values summed = Wh)
            lookup[(slope_bin, aspect_bin)] = annual_kwh_per_m2

    return lookup, monthly_factor


def _nearest_bin(value, bin_size, max_value=None):
    b = round(value / bin_size) * bin_size
    if max_value is not None:
        b = min(b, max_value)
    return int(b) % 360 if max_value is None else int(b)


class SolarModel:
    def __init__(self, lat=None, lon=None):
        if lat is None or lon is None:
            lat, lon = pilot_location()
        self.lat, self.lon = lat, lon
        self.lookup, self.monthly_factor = build_poa_lookup_table(lat, lon)

    def annual_poa_kwh_per_m2(self, slope_deg, aspect_deg):
        slope_bin = _nearest_bin(slope_deg, SLOPE_BIN_DEG, max_value=config.MAX_ROOF_SLOPE_DEG)
        aspect_bin = _nearest_bin(aspect_deg, ASPECT_BIN_DEG)
        return self.lookup[(slope_bin, aspect_bin)]

    def facet_yield(self, facet, n_panels):
        """Returns dict with kwp, dc_kwh_year, ac_kwh_year, ac_kwh_day_avg for
        n_panels sitting on this facet."""
        assumptions = config.PV_ASSUMPTIONS
        kwp = n_panels * assumptions["panel_rated_power_w"] / 1000
        poa = self.annual_poa_kwh_per_m2(facet["slope_deg"], facet["aspect_deg"])
        dc_kwh_year = poa * kwp
        ac_kwh_year = (dc_kwh_year
                       * (assumptions["inverter_efficiency_pct"] / 100)
                       * (1 - assumptions["system_derate_pct"] / 100))
        return {
            "kwp": kwp,
            "dc_kwh_year": dc_kwh_year,
            "ac_kwh_year": ac_kwh_year,
            "ac_kwh_day_avg": ac_kwh_year / 365,
        }
