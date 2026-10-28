"""The data the map's "show the workings" needs, taken from the model itself.

WHY THIS EXISTS. Every number the map shows must be traceable by the reader
to the calculation behind it, and the traced version must BE the
calculation, never a description of it that can drift (Josh, 17 Oct). The
generation side is computed here in Python; the browser shows its workings.
So the browser is handed exactly the inputs the pipeline used, and this
module proves at emit time that those inputs reproduce the pipeline's own
kWh, panel by panel. A mismatch is a build failure, not a footnote.

WHAT IT CAPTURES, per region, by WATCHING THE REAL MODEL BEING BUILT rather
than re-deriving it (a second implementation is exactly the drift this
exists to prevent):

  * the annual sunlight table: kWh/m2/yr for every 5-degree tilt and
    10-degree direction bin -- src/solar_model.py's own lookup;
  * the hourly sunlight on each bin, captured from the
    pvlib.irradiance.get_total_irradiance calls that built that table, and
    reduced to an average day per season (global and diffuse-only). Its
    year adds up to the annual table exactly, which is checked here;
  * which measured-sunlight source calibrated the clouds (SolarView, a NIWA
    radiation station, NIWA sunshine hours or NASA POWER), recorded from the
    calibration call that actually returned, so the "Source" line on screen
    names the dataset the numbers came from.

Per panel, the tiles carry the tilt and direction bin and the shading factor
(emit_region.py), and check_panels() confirms that

    round(annual[bin] x shading x rated kW x inverter x (1 - losses))

equals the panel's ac_kwh_year for every panel, with Python's rounding. The
browser (generation.js) does the same arithmetic in the same order, and
tests/test_generation.mjs checks it on real panels exported from a build.

Not in the geometry closure (only emit_region imports this), so changing it
re-emits regions rather than re-laying every roof.
"""

import math

import config
from src import solar_model as SM

# Seasons as the bill tool and the curves use them: the months each stands for.
SEASONS = ["summer", "autumn", "winter", "spring"]
SEASON_MONTHS = {"summer": (12, 1, 2), "autumn": (3, 4, 5),
                 "winter": (6, 7, 8), "spring": (9, 10, 11)}
DOC_VERSION = 1

_SOURCE_FUNCS = [
    ("solarview", "solarview_calibrated_monthly_factors"),
    ("niwa_radiation", "niwa_station_measured_monthly_factors"),
    ("niwa_sunshine", "fetch_niwa_derived_monthly_factors"),
    ("nasa_power", "fetch_nasa_power_monthly_factors"),
]


def build_model_and_capture(lat, lon):
    """SolarModel(lat, lon), built normally, with its hourly plane-of-array
    series and its cloud-calibration source recorded as it is built. lat/lon
    None builds SolarModel() -- the pilot's own default location -- exactly as
    build_layout_geojson and apply_yield do (a stand-in point 500 m away put
    every pilot panel 0.4% off, caught by the emit check, 17 Oct)."""
    import pvlib
    captured, calls = {}, []
    orig_tot = pvlib.irradiance.get_total_irradiance

    def spy_tot(*args, **kw):
        r = orig_tot(*args, **kw)
        captured[(int(kw["surface_tilt"]), int(kw["surface_azimuth"]))] = (
            r["poa_global"].copy(), r["poa_diffuse"].copy())
        return r

    origs = {}
    for kind, name in _SOURCE_FUNCS:
        fn = getattr(SM, name)
        origs[name] = fn

        def wrap(*a, _fn=fn, _kind=kind, **k):
            out = _fn(*a, **k)
            calls.append((_kind, out))
            return out
        setattr(SM, name, wrap)
    pvlib.irradiance.get_total_irradiance = spy_tot
    try:
        model = SM.SolarModel() if lat is None or lon is None else SM.SolarModel(lat, lon)
    finally:
        pvlib.irradiance.get_total_irradiance = orig_tot
        for name, fn in origs.items():
            setattr(SM, name, fn)
    # The same table with an OPEN horizon, for the shape of each day only:
    # the browser takes the area's hills out per building with its own
    # tshade mask (generation.js genCurve), so a day already cut by the
    # area's horizon had them taken out twice.
    if model.horizon_profile is not None:
        open_cap = {}

        def spy_open(*args, **kw):
            r = orig_tot(*args, **kw)
            open_cap[(int(kw["surface_tilt"]), int(kw["surface_azimuth"]))] = (
                r["poa_global"].copy(), r["poa_diffuse"].copy())
            return r
        pvlib.irradiance.get_total_irradiance = spy_open
        try:
            SM.build_poa_lookup_table(model.lat, model.lon, horizon_profile=None)
        finally:
            pvlib.irradiance.get_total_irradiance = orig_tot
        captured = {k: v + open_cap[k] for k, v in captured.items()}
    used = next((k for k, out in calls if out is not None), None)
    if used is None:
        raise RuntimeError("could not tell which sunlight calibration the model used")
    # The recorded source must be the one whose factors the model holds.
    got = next(out for k, out in calls if k == used)
    # Between SOLARVIEW_BLEND_DEG the model mixes SolarView with the nearest
    # measuring station, weighted by distance.
    station = next((out for k, out in calls if k == "niwa_radiation" and out is not None), None)
    if used == "solarview" and station is not None:
        lo, hi = SM.SOLARVIEW_BLEND_DEG
        d = SM._deg_dist(model.lat, model.lon, *SM.SOLARVIEW_CAL_LOCATION)
        w = min(1.0, (hi - d) / (hi - lo))
        got = {m: w * got[m] + (1 - w) * station[m] for m in got}
        used = "blend"
    if any(abs(got[m] - model.monthly_factor[m]) > 1e-12 for m in SM.MONTH_NAMES):
        raise RuntimeError("recorded calibration does not match the model's factors")
    return model, captured, used


def _source_text(kind, lat, lon):
    km = lambda dlat, dlon: round(math.hypot(dlat, dlon * math.cos(math.radians(lat))) * 111.2)
    if kind == "solarview":
        slat, slon = SM.SOLARVIEW_CAL_LOCATION
        return {"kind": kind, "name": "SolarView, Queenstown", "km": km(lat - slat, lon - slon),
                "text": "Measured monthly sunlight from the SolarView record in Queenstown, "
                        "applied to a clear-sky model of this town's sun (pvlib, Ineichen)."}
    if kind == "blend":
        lo, hi = SM.SOLARVIEW_BLEND_DEG
        w = min(1.0, (hi - SM._deg_dist(lat, lon, *SM.SOLARVIEW_CAL_LOCATION)) / (hi - lo))
        sv, st = _source_text("solarview", lat, lon), _source_text("niwa_radiation", lat, lon)
        return {"kind": kind, "name": f"SolarView and {st['name']}", "km": sv["km"],
                "text": f"A mix of two measured records, weighted by distance: "
                        f"{round(w * 100)}% SolarView in Queenstown ({sv['km']} km away) and "
                        f"{round((1 - w) * 100)}% {st['name']} ({st['km']} km away). "
                        f"Applied to a clear-sky model of this town's sun (pvlib, Ineichen)."}
    if kind == "niwa_radiation":
        name, slat, slon, _ = min(SM.NIWA_MEASURED_GHI_STATIONS,
                                  key=lambda s: SM._deg_dist(lat, lon, s[1], s[2]))
        d = km(lat - slat, lon - slon)
        return {"kind": kind, "name": f"NIWA {name}", "km": d,
                "text": f"NIWA measured solar radiation at {name} (about {d} km away), monthly "
                        f"normals, applied to a clear-sky model of this town's sun (pvlib, Ineichen)."}
    if kind == "niwa_sunshine":
        st = SM._nearest_niwa_station(lat, lon) or {}
        d = km(lat - st.get("lat", lat), lon - st.get("lon", lon))
        return {"kind": kind, "name": "NIWA sunshine hours", "km": d,
                "text": f"NIWA sunshine-hour normals (station about {d} km away), converted to "
                        f"sunlight with the Angstrom-Prescott relation."}
    return {"kind": kind, "name": "NASA POWER", "km": None,
            "text": "NASA POWER satellite climatology (1-degree grid), applied to a clear-sky "
                    "model of this town's sun (pvlib, Ineichen)."}


def sun_doc(region, lat, lon, model, captured, source_kind, used_bins):
    """The region's sunlight document (data/sun/<region>.json). Hourly days
    only for the bins this region's roofs use (used_bins: {(slope, aspect)});
    the annual table is whole."""
    slopes = list(range(0, config.MAX_ROOF_SLOPE_DEG + SM.SLOPE_BIN_DEG, SM.SLOPE_BIN_DEG))
    aspects = list(range(0, 360, SM.ASPECT_BIN_DEG))
    annual, curves, diffuse = [], {}, {}
    for s in slopes:
        arow = []
        for a in aspects:
            glob, dif = captured[(s, a)][:2]
            shape_glob, shape_dif = captured[(s, a)][2:] or (glob, dif)
            year = float(glob.sum()) / 1000.0
            if abs(year - model.lookup[(s, a)]) > 1e-6 * max(1.0, year):
                raise RuntimeError(f"[{region}] captured hours for bin {s}/{a} do not add up "
                                   f"to the model's annual table ({year} vs {model.lookup[(s, a)]})")
            arow.append(model.lookup[(s, a)])
            if (s, a) in used_bins:
                curves[f"{s}_{a}"] = _season_days(shape_glob)
                diffuse[f"{s}_{a}"] = _season_days(shape_dif)
        annual.append(arow)
    days = _days_per_season(next(iter(captured.values()))[0].index)
    return {
        "version": DOC_VERSION, "region": region, "lat": round(lat, 5), "lon": round(lon, 5),
        "source": _source_text(source_kind, lat, lon),
        "monthly_factor": {m: round(v, 4) for m, v in model.monthly_factor.items()},
        "slope_bins": slopes, "aspect_bins": aspects,
        # full precision: this is the number every panel's kWh starts from
        "annual_kwh_m2": annual,
        "seasons": SEASONS, "season_days": [days[s] for s in SEASONS],
        # average day per season, whole Wh/m2 in each hour (local clock), by
        # "slope_aspect". The browser rescales each bin so its year equals
        # annual_kwh_m2 exactly; the rounding only shapes the day.
        "day_wh_m2": curves, "day_diffuse_wh_m2": diffuse,
        "terrain_horizon": model.horizon_profile is not None,
        # the days above are open-horizon shapes; tshade adds each roof's hills
        "day_open_horizon": True,
    }


def _days_per_season(index):
    days = index.normalize().unique()
    return {s: int(sum(1 for d in days if d.month in SEASON_MONTHS[s])) for s in SEASONS}


def _season_days(series):
    out = []
    idx = series.index
    for s in SEASONS:
        m = series[idx.month.isin(SEASON_MONTHS[s])]
        ndays = len(m.index.normalize().unique())
        by_hour = m.groupby(m.index.hour).sum() / ndays
        out.append([int(round(float(by_hour.get(h, 0.0)))) for h in range(24)])
    return out


def bins_of(slope_deg, aspect_deg):
    """The table bin a face uses -- the model's own binning function."""
    return (SM._nearest_bin(slope_deg, SM.SLOPE_BIN_DEG, max_value=config.MAX_ROOF_SLOPE_DEG),
            SM._nearest_bin(aspect_deg, SM.ASPECT_BIN_DEG))


def panel_kwh(model, slope_bin, aspect_bin, shading):
    """One panel's kWh before rounding: the arithmetic SolarModel.facet_yield
    does, in its order. The pipeline then rounds each panel to a whole kWh;
    the browser shows that rounding as its own step."""
    a = config.PV_ASSUMPTIONS
    kwp = 1 * a["panel_rated_power_w"] / 1000
    poa = model.lookup[(slope_bin, aspect_bin)] * shading
    dc = poa * kwp
    return dc * (a["inverter_efficiency_pct"] / 100) * (1 - a["system_derate_pct"] / 100)


def round_tol(unshaded):
    """How far a shipped value may sit from the pipeline's rounded one: half a
    unit of rounding, plus the most that SHADE_DP-decimal shading can move a
    value whose unshaded size is `unshaded`. Exact, not a fudge."""
    return 0.5 + abs(unshaded) * 0.5 * 10 ** -SHADE_DP + 1e-9


SHADE_DP = 4   # decimals shipped; checked below with exactly what ships


def tag_and_check_panels(layouts, model):
    """Give every panel its bin and shading for the tiles, and confirm the
    shipped numbers reproduce its ac_kwh_year. Returns the check counts."""
    n = bad = missing = 0
    worst = []
    for f in layouts["features"]:
        p = f["properties"]
        if p.get("kind") != "panel":
            continue
        n += 1
        if p.get("plane_slope_deg") is None or p.get("shading_factor") is None:
            missing += 1
            continue
        sb, ab = bins_of(p["plane_slope_deg"], p["plane_aspect_deg"])
        sh = round(float(p["shading_factor"]), SHADE_DP)
        p["sb"], p["ab"], p["sh"] = sb, ab, sh
        want = p.get("ac_kwh_year")
        got = panel_kwh(model, sb, ab, sh)
        if want is None or abs(got - want) > round_tol(panel_kwh(model, sb, ab, 1.0)):
            bad += 1
            if len(worst) < 5:
                worst.append([p.get("building_id"), want, round(got, 4)])
    return {"panels": n, "mismatched": bad, "missing_inputs": missing, "examples": worst}
