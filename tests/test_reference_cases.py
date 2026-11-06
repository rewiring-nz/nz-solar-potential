"""
Reference cases: small worked examples a person can check with a calculator.

Each case runs the build's own function on inputs simple enough to work out
by hand, and states the hand working beside the expected answer. The output
is a table: case, expected, got, and how the expected figure was reached.
If a case fails, either the code changed what it computes or the working
was wrong; the table says which number to look at.

The money side (loan, bills, rounding, cost) is in tests/test_reference_cases.mjs.

Run:  python tests/test_reference_cases.py
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np                       # noqa: E402
from shapely.geometry import box         # noqa: E402

import config                            # noqa: E402

rows, failures = [], []


def case(name, expected, got, how, tol=1e-6):
    ok = (abs(expected - got) <= tol) if isinstance(expected, (int, float)) else expected == got
    rows.append((("ok  " if ok else "FAIL"), name, expected, got, how))
    if not ok:
        failures.append(name)


# ---- power from panels (src/solar_model.py SolarModel.facet_yield) -----------
from src.solar_model import SolarModel   # noqa: E402

pv = config.PV_ASSUMPTIONS
m = SolarModel.__new__(SolarModel)       # no weather needed: sunlight is given
m.annual_poa_kwh_per_m2 = lambda slope, aspect: 1500.0
y = m.facet_yield({"slope_deg": 25, "aspect_deg": 0}, 28)
case("28 panels are 14 kW", 14.0, y["kwp"], "28 × 500 W = 14,000 W")

y1 = m.facet_yield({"slope_deg": 25, "aspect_deg": 0}, 1, shading_factor=0.9)
hand = 1500 * 0.9 * 0.5 * 0.97 * (1 - 0.1379)
case("one panel's year, 1,500 kWh/m² of sun, 10% shade", round(hand, 2), round(y1["ac_kwh_year"], 2),
     "1,500 × 0.9 shade × 0.5 kW × 0.97 inverter × (1 − 0.1379 losses) = 564.46 kWh")
case("assumptions are the ones in the working above",
     (500, 97.0, 13.79), (pv["panel_rated_power_w"], pv["inverter_efficiency_pct"], pv["system_derate_pct"]),
     "config.PV_ASSUMPTIONS")
case("total losses 16.4%", 16.4, round((1 - 0.97 * (1 - 0.1379)) * 100, 1),
     "1 − 0.97 × 0.8621 = 0.164")
case("panel area", 2.2238, round(config.PANEL_WIDTH_M * config.PANEL_HEIGHT_M, 4), "1.134 m × 1.961 m")

# ---- shade blend (SolarModel.kept_sun) -------------------------------------
m.beam_share = lambda slope, aspect: 0.7
case("shade: 70% direct sun, half blocked; 90% of sky open", 0.62, round(m.kept_sun(25, 0, 0.5, 0.9), 6),
     "0.7 × 0.5 + 0.3 × 0.9 = 0.35 + 0.27 = 0.62")

# ---- slope and direction of a roof plane (src/roof_partition.py) ------------
from src.roof_partition import _fit_plane, _slope_aspect   # noqa: E402

s, a = _slope_aspect((0.5, 0.0, 0.0))
case("roof rising 0.5 m per m to the east: slope", 26.57, round(s, 2), "atan(0.5) = 26.57°")
case("...and it faces west", 270.0, round(a, 2), "it falls towards the west: 270°")
s, a = _slope_aspect((0.0, -1.0, 0.0))
case("roof rising 1 m per m to the south: slope", 45.0, round(s, 2), "atan(1) = 45°")
case("...and it faces north", 0.0, round(a, 2), "it falls towards the north: 0°")
xs, ys = np.meshgrid(np.arange(0, 10.0), np.arange(0, 6.0))
pts = np.column_stack([xs.ravel() + 1.2e6, ys.ravel() + 5.0e6, 0.25 * xs.ravel() + 3.0])
pl = _fit_plane(pts)
case("plane fitted to points on z = 0.25x + 3", 0.25, round(float(pl[0]), 9),
     "60 points exactly on the plane; the fit returns its slope in x (NZTM-sized coordinates)")

# ---- panels on a face (src/panel_fitting.py fit_panels_on_facet) -----------
from src.panel_fitting import fit_panels_on_facet   # noqa: E402


def panels_on(w, h, slope):
    f = {"geometry": box(0, 0, w, h), "slope_deg": slope, "aspect_deg": 0.0,
         "plane": (0, 0, 0), "building_id": 1, "facet_id": 0}
    return fit_panels_on_facet(f)


case("flat 5 × 3 m face", 4, len(panels_on(5, 3, 0)),
     "0.1 m setback: (5 − 0.2) ÷ 1.134 = 4 across, (3 − 0.2) ÷ 1.961 = 1 row")
case("flat 10 × 4.4 m face", 16, len(panels_on(10, 4.4, 0)),
     "(10 − 0.2) ÷ 1.134 = 8 across, (4.4 − 0.2) ÷ 1.961 = 2 rows")
case("6 × 4.2 m (plan) face at 30°", 10, len(panels_on(6, 4.2, 30)),
     "slope length 4.2 ÷ cos 30° = 4.85 m: (4.85 − 0.2) ÷ 1.961 = 2 rows of (6 − 0.2) ÷ 1.134 = 5")
P = [p["geometry"] for p in panels_on(10, 4.4, 0)]
case("no two panels overlap", 0.0, round(sum(P[i].intersection(P[j]).area
                                              for i in range(len(P)) for j in range(i + 1, len(P))), 6),
     "sum of every pair's shared area")
case("every panel inside the face less its setback", True,
     all(box(0.1 - 1e-6, 0.1 - 1e-6, 9.9 + 1e-6, 4.3 + 1e-6).contains(p) for p in P), "0.1 m in from each edge")

# ---- the heat map shades the way panels do (src/build_heatmap_raster.py) ------
import src.build_heatmap_raster as HM   # noqa: E402


class _Sun:
    lookup = {(25, 0): 1500.0}
    hourly = {"beam_lookup": {(25, 0): 990.0}}


_b = HM.build_beam_share_array(_Sun())[5, 0]
m.beam_share = lambda slope, aspect: 990.0 / 1500.0
case("heat map: half the direct sun blocked keeps", round(m.kept_sun(25, 0, 0.5, 1.0), 4),
     round(_b * 0.5 + (1 - _b), 4), "0.66 direct × 0.5 + 0.34 sky = 0.67, as a panel (it was 0.5: the whole light scaled)")
case("heat map slopes go as steep as panels", config.MAX_ROOF_SLOPE_DEG, HM.MAX_SLOPE_DEG, "config.MAX_ROOF_SLOPE_DEG")

# ---- "the best N panels" are the first N in fill order (bake_density_deciles) --
from src.bake_density_deciles import bake, SYSTEM_PANEL_STEPS   # noqa: E402

_sq = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
_panels = [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [_sq]},
            "properties": {"kind": "panel", "building_id": 7, "fill_rank": 1, "fill_order": i + 1,
                           "ac_kwh_year": 700 - 10 * i, "array_size": 40}} for i in range(40)]
_sp = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Polygon",
       "coordinates": [_sq]}, "properties": {"building_id": 7}}]}
bake(_sp, {"type": "FeatureCollection", "features": _panels})
_n = min(n for n in SYSTEM_PANEL_STEPS if n >= 6)
case(f"the best {_n} panels' year, 40 panels in one rank", sum(700 - 10 * i for i in range(_n)),
     _sp["features"][0]["properties"][f"sys_kwh_{_n}"],
     f"700 + 690 + ... the first {_n} by fill order (it took the shadiest {_n} of the rank)")

# ---- the sunlight model reproduces its calibration (src/solar_model.py) ------
# A flat roof at the calibration point must get SolarView's measured year: the
# monthly cloud factors are solved for exactly that. Needs the wide elevation
# model (data/dem_wide_mosaic.tif) for the mountains; skipped without it.
import src.solar_model as SM   # noqa: E402

_dem = next((p for p in (ROOT / "data" / "dem_wide_mosaic.tif",
                         ROOT.parent / "solar-map" / "data" / "dem_wide_mosaic.tif") if p.exists()), None)
if _dem is not None:
    SM.DEM_WIDE_PATH = _dem
    lat, lon = SM.pilot_location()
    sv_year = sum(SM.SOLARVIEW_MEASURED_GHI_KWH_M2_DAY[k] * d for k, d in zip(SM.MONTH_NAMES, SM.DAYS_IN_MONTH))
    fac = SM.solarview_calibrated_monthly_factors(lat, lon, None)
    flat = SM.build_poa_lookup_table(lat, lon, horizon_profile=SM._pilot_horizon_profile(lat, lon),
                                     monthly_factor=fac)[0][(0, 0)]
    case("a flat roof at the calibration point gets SolarView's year", round(sv_year, 1), round(flat, 1),
         "sum of SolarView's measured daily GHI × days in each month (kWh/m²)", tol=0.005 * sv_year)
else:
    print("skip: calibration case needs data/dem_wide_mosaic.tif")

# ---- report ------------------------------------------------------------------
w = max(len(r[1]) for r in rows)
for st, name, exp, got, how in rows:
    print(f"{st} {name:<{w}}  expected {exp!s:<22} got {got!s:<22} {how}")
print(f"\n{len(rows) - len(failures)} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
