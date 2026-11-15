"""Global Nostos: the US blend (subtract recent bias, weight by 1/MAE) at any lat/lon, live.

Data, all Open-Meteo (free, no key), all UTC (timezone=GMT), wind in m/s:
  current   api.open-meteo.com/v1/forecast              live conditions
  forecasts previous-runs-api.open-meteo.com/v1/forecast one call gives the live run and the archive.
            Plain <var>_<model> is the latest run (the live forecast);
            <var>_previous_dayL_<model> is the run issued L days before the valid hour.
  truth     archive-api.open-meteo.com/v1/archive, models=era5.
            ERA5 is a ~25 km reanalysis, not a station, and it comes from ECMWF, so the
            rankings may slightly favor the ECMWF model. It runs ~5 days behind real time.
            models=era5 is pinned on purpose: the default "best match" fills the last days
            with the ECMWF IFS analysis, which would break the 5-day lag.

Source gaps (seen live 2026-11-15): JMA has no gusts at all, and ECMWF gusts exist only for
the latest run (no previous_dayL), so ECMWF has no gust track record. Gusts therefore blend
GUST_MODELS only; ECMWF and JMA still count for temperature, wind and rain.

Daily high (same rule as the US backtest): daily max over a local *standard-time* day,
per model and lead, vs the ERA5 daily max; bias/MAE over the last 30 days with full truth.
Tomorrow uses lead-1 stats on the current run's high, D+2 lead-2, D+3 lead-3.

No Kalshi, NWS CLI or NBM here; those stay US-only.
"""
import math
import threading
import time

import numpy as np
import pandas as pd
import requests

import blend
import config

CURRENT_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

TEMP, WIND, GUST, PRECIP = "temperature_2m", "wind_speed_10m", "wind_gusts_10m", "precipitation"
VARS = [TEMP, WIND, GUST, PRECIP]
CURRENT_VARS = [TEMP, WIND, GUST, PRECIP, "weather_code"]
GUST_MODELS = ["gfs_seamless", "icon_seamless", "gem_seamless"]
GUST_NOTE = "3 models (ECMWF/JMA lack gust history)"
UNITS = {TEMP: "°C", WIND: "m/s", GUST: "m/s", PRECIP: "mm"}
# Hourly MAE floors, same idea as blend.MAE_FLOOR (0.5 °F): no lucky model gets near-infinite weight.
HOURLY_MAE_FLOOR = {TEMP: 0.5 / 1.8, WIND: 0.2, GUST: 0.3, PRECIP: 0.05}

# 37 (not 35) past days: ERA5 ends ~6 days back and the first/last local days are partial,
# so 35 left only 29 complete truth days for the 30-day window.
PAST_DAYS = 37
# 5 (not 4) forecast days: D+3 of a western-hemisphere point (e.g. UTC-6) ends on UTC day +4.
# Routes still stay inside 96 h.
FORECAST_DAYS = 5
ERA5_LAG_DAYS = 5
TIMEOUT_S = 15
CACHE_TTL_S = 30 * 60
STATION_RADIUS_KM = 25
COMMON = {"timezone": "GMT", "wind_speed_unit": "ms"}


def models_for(var: str) -> list[str]:
    return GUST_MODELS if var == GUST else config.MODELS


def c_to_f(c):
    return c * 9 / 5 + 32


def std_offset_hours(lon: float) -> int:
    """Fixed local standard-time offset, round(lon / 15), no DST.

    NWS climate days are local standard time all year, and a fixed offset needs no
    time-zone database for an arbitrary point at sea or in the desert. It also keeps day
    boundaries from shifting by an hour inside the 30-day window when DST flips.
    """
    return int(np.clip(math.floor(lon / 15 + 0.5), -12, 12))


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------- fetching (batched, cached)

_cache: dict[tuple, tuple[float, list]] = {}
_cache_lock = threading.Lock()


def grid(points) -> list[tuple[float, float]]:
    """Round to 0.1° (~11 km): finer than the ~25 km models, and it makes the cache hit."""
    return [(round(float(lat), 1), round(float(lon), 1)) for lat, lon in points]


def _get(url: str, params: dict) -> list[dict]:
    """One batched call; always returns a list (Open-Meteo returns a bare object for 1 point)."""
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        raise RuntimeError(f"Open-Meteo unreachable ({url}): {exc}") from exc
    if r.status_code != 200:
        raise RuntimeError(f"Open-Meteo error {r.status_code} ({url}): {r.text[:300]}")
    body = r.json()
    return body if isinstance(body, list) else [body]


def _cached(kind: str, points, params: dict, url: str) -> list[dict]:
    key = (kind, tuple(points), tuple(sorted(params.items())))
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
            return hit[1]
    coords = {"latitude": ",".join(str(p[0]) for p in points),
              "longitude": ",".join(str(p[1]) for p in points)}
    blocks = _get(url, {**coords, **params})
    if len(blocks) != len(points):
        raise RuntimeError(f"Open-Meteo returned {len(blocks)} locations for {len(points)} points")
    with _cache_lock:
        _cache[key] = (time.monotonic(), blocks)
    return blocks


def fetch_current(points):
    return _cached("current", points, {"current": ",".join(CURRENT_VARS), **COMMON}, CURRENT_URL)


def fetch_forecasts(points):
    hourly = [v + s for v in VARS for s in ["", *(f"_previous_day{l}" for l in config.LEADS)]]
    return _cached("forecasts", points, {"past_days": PAST_DAYS, "forecast_days": FORECAST_DAYS,
                                         "models": ",".join(config.MODELS), "hourly": ",".join(hourly),
                                         **COMMON}, PREVIOUS_RUNS_URL)


def fetch_truth(points, now: pd.Timestamp):
    end = (now.normalize() - pd.Timedelta(days=ERA5_LAG_DAYS)).date()
    start = (now.normalize() - pd.Timedelta(days=PAST_DAYS)).date()
    return _cached("truth", points, {"start_date": str(start), "end_date": str(end), "models": "era5",
                                     "hourly": ",".join(VARS), **COMMON}, ARCHIVE_URL)


# ---------------------------------------------------------------- parsing

def parse_forecasts(block: dict) -> pd.DataFrame:
    """Wide model-suffixed columns -> long rows: time, var, model, lead (0 = latest run), value.

    Models are matched against config.MODELS (suffix), so variable names with underscores parse safely.
    """
    h = block["hourly"]
    rows = []
    for col, values in h.items():
        if col == "time":
            continue
        model = next((m for m in config.MODELS if col.endswith("_" + m)), None)
        if model is None:
            raise ValueError(f"unexpected column {col}")
        var, _, prev = col.removesuffix("_" + model).partition("_previous_day")
        if var not in VARS:
            raise ValueError(f"unexpected variable in column {col}")
        rows.append(pd.DataFrame({"time": h["time"], "var": var, "model": model,
                                  "lead": int(prev) if prev else 0, "value": values}))
    out = pd.concat(rows, ignore_index=True)
    out["time"] = pd.to_datetime(out["time"], utc=True)
    out["value"] = pd.to_numeric(out["value"])
    return out.dropna(subset=["value"]).reset_index(drop=True)


def parse_truth(block: dict) -> pd.DataFrame:
    """ERA5 hourly -> long rows: time, var, value (nulls dropped: the last days aren't in yet)."""
    out = pd.DataFrame(block["hourly"]).melt(id_vars="time", var_name="var", value_name="value")
    out["time"] = pd.to_datetime(out["time"], utc=True)
    out["value"] = pd.to_numeric(out["value"])
    return out.dropna(subset=["value"]).reset_index(drop=True)


# ---------------------------------------------------------------- daily high (the US blend)

def local_daily_max(df: pd.DataFrame, offset: int, by: list[str]) -> pd.DataFrame:
    """Max over each local standard-time day; days without all 24 hours are dropped."""
    d = df.assign(date=(df["time"] + pd.Timedelta(hours=offset)).dt.tz_localize(None).dt.normalize())
    g = d.groupby(by + ["date"])["value"].agg(high="max", hours="size").reset_index()
    return g[g["hours"] == 24].drop(columns="hours").reset_index(drop=True)


def day_end_utc(date: pd.Timestamp, offset: int) -> pd.Timestamp:
    return pd.Timestamp(date).tz_localize("UTC") + pd.Timedelta(days=1) - pd.Timedelta(hours=offset)


def daily_stats(fc: pd.DataFrame, truth: pd.DataFrame, offset: int, now: pd.Timestamp) -> pd.DataFrame:
    """Per model x lead: bias and MAE (°F) of the daily high over the last 30 full-truth days.

    Same definitions as blend.window_stats: bias = mean error, mae = mean |error - bias|.
    Only days that ended before `now` count (ERA5's lag already guarantees it; this makes it explicit).
    """
    t = local_daily_max(truth[truth["var"] == TEMP], offset, [])
    t = t[t["date"].map(lambda d: day_end_utc(d, offset)) <= now].nlargest(blend.WINDOW_DAYS, "date")
    f = local_daily_max(fc[(fc["var"] == TEMP) & (fc["lead"] > 0)], offset, ["model", "lead"])
    e = f.merge(t, on="date", suffixes=("", "_obs"))
    e["error"] = c_to_f(e["high"]) - c_to_f(e["high_obs"])
    e["bias"] = e.groupby(["model", "lead"])["error"].transform("mean")
    e["abs_dev"] = (e["error"] - e["bias"]).abs()
    stats = e.groupby(["model", "lead"]).agg(n_days=("error", "size"), bias_f=("error", "mean"),
                                             mae_f=("abs_dev", "mean"), last_used_day=("date", "max"))
    full = pd.MultiIndex.from_product([config.MODELS, config.LEADS], names=["model", "lead"])
    stats = stats.reindex(full).reset_index()
    stats["n_days"] = stats["n_days"].fillna(0).astype(int)
    thin = stats["n_days"] < blend.MIN_DAYS
    stats.loc[thin, ["bias_f", "mae_f"]] = np.nan
    return stats


def daily_forecast(fc: pd.DataFrame, stats: pd.DataFrame, offset: int, now: pd.Timestamp) -> pd.DataFrame:
    """D+1..D+3 local days: one row per model with raw high, bias, weight share and the Nostos high."""
    latest = fc[(fc["var"] == TEMP) & (fc["lead"] == 0) & (fc["time"] >= now.floor("h"))]
    highs = local_daily_max(latest, offset, ["model"])
    local_today = (now + pd.Timedelta(hours=offset)).tz_localize(None).normalize()
    out = []
    for lead in config.LEADS:
        day = local_today + pd.Timedelta(days=lead)
        assert day_end_utc(day, offset) - pd.Timedelta(days=1) > now, "target day already started"
        d = highs[highs["date"] == day].merge(stats[stats["lead"] == lead], on="model", how="left")
        if d.empty:
            continue
        d["high_f"] = c_to_f(d["high"])
        # blend.weights groups by location/lead/climate_date and falls back to the median weight.
        d = d.assign(location="point", climate_date=day, mae=d["mae_f"])
        d["w"] = blend.weights(d)
        d["weight"] = d["w"] / d["w"].sum()
        d["corrected_f"] = d["high_f"] - d["bias_f"].fillna(0.0)
        d["nostos_f"] = (d["weight"] * d["corrected_f"]).sum()
        d["spread_f"] = d["corrected_f"].std(ddof=0)
        out.append(d)
    if not out:
        raise RuntimeError("no complete forecast day returned for this point")
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------- hourly stats (per variable)

def hourly_stats(fc: pd.DataFrame, truth: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Per var x model x lead over the last 30 days of hourly ERA5: bias and MAE in native units.

    Precipitation gets no bias correction (weights only), so its MAE is mean |error|.
    Gusts only use GUST_MODELS.
    """
    t = truth[truth["time"] < now]
    t = t[t["time"] > t["time"].max() - pd.Timedelta(days=blend.WINDOW_DAYS)]
    f = fc[fc["lead"] > 0]
    f = f[(f["var"] != GUST) | f["model"].isin(GUST_MODELS)]
    e = f.merge(t, on=["time", "var"], suffixes=("", "_obs"))
    e["error"] = e["value"] - e["value_obs"]
    keys = ["var", "model", "lead"]
    e["bias"] = e.groupby(keys)["error"].transform("mean").where(e["var"] != PRECIP, 0.0)
    e["abs_dev"] = (e["error"] - e["bias"]).abs()
    stats = e.groupby(keys).agg(n_hours=("error", "size"), bias=("bias", "first"), mae=("abs_dev", "mean"),
                                last_used_time=("time", "max")).reset_index()
    full = pd.DataFrame([(v, m, l) for v in VARS for m in models_for(v) for l in config.LEADS], columns=keys)
    stats = full.merge(stats, on=keys, how="left")
    stats["n_hours"] = stats["n_hours"].fillna(0).astype(int)
    thin = stats["n_hours"] < blend.MIN_DAYS * 24
    stats.loc[thin, ["bias", "mae"]] = np.nan
    stats.loc[stats["var"] == PRECIP, "bias"] = np.nan  # not applied, so not reported
    return stats


def hourly_weights(stats: pd.DataFrame) -> pd.Series:
    """Unnormalised 1/MAE per row; a model without history gets the median weight of the others."""
    floor = stats["var"].map(HOURLY_MAE_FLOOR)
    mae = stats["mae"].fillna(stats.groupby(["var", "lead"])["mae"].transform("median")).fillna(floor * 10)
    return 1.0 / np.maximum(mae, floor)


# ---------------------------------------------------------------- the point endpoint

def _num(x, nd=2):
    return None if x is None or pd.isna(x) else round(float(x), nd)


def nearest_us_station(lat: float, lon: float) -> dict | None:
    locs = config.load_locations()
    dist = locs.apply(lambda r: haversine_km((lat, lon), (r["lat"], r["lon"])), axis=1)
    i = dist.idxmin()
    if dist[i] > STATION_RADIUS_KM:
        return None
    r = locs.loc[i]
    return {"location": r["location"], "station_id": r["station_id"], "distance_km": _num(dist[i], 1),
            "note": "station-verified model available"}


def point(lat: float, lon: float, now: pd.Timestamp | None = None) -> dict:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat must be in [-90, 90] and lon in [-180, 180]")
    now = (now or pd.Timestamp.now(tz="UTC")).floor("s")
    pts = grid([(lat, lon)])
    current = fetch_current(pts)[0]["current"]
    fc = parse_forecasts(fetch_forecasts(pts)[0])
    truth = parse_truth(fetch_truth(pts, now)[0])
    if truth.empty:
        raise RuntimeError("ERA5 returned no truth for this point")
    offset = std_offset_hours(pts[0][1])

    dstats = daily_stats(fc, truth, offset, now)
    used = dstats.dropna(subset=["bias_f"])
    assert used["last_used_day"].map(lambda d: day_end_utc(d, offset)).le(now).all(), "used truth from the future"
    daily = daily_forecast(fc, dstats, offset, now)

    hstats = hourly_stats(fc, truth, now)
    hstats["w"] = hourly_weights(hstats)
    hstats["weight"] = hstats["w"] / hstats.groupby(["var", "lead"])["w"].transform("sum")

    lead1 = daily[daily["lead"] == 1]
    rank = dstats[dstats["lead"] == 1].merge(lead1[["model", "weight"]], on="model", how="left")
    rank = rank.sort_values("mae_f", na_position="last")

    days = []
    for (lead, day), d in daily.groupby(["lead", "climate_date"]):
        days.append({
            "date": day.date().isoformat(), "lead_days": int(lead),
            "day_start_utc": (day_end_utc(day, offset) - pd.Timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "nostos_high_f": _num(d["nostos_f"].iloc[0], 1),
            "nostos_high_c": _num((d["nostos_f"].iloc[0] - 32) * 5 / 9, 1),
            "spread_f": _num(d["spread_f"].iloc[0]),
            "models": [{"model": r.model, "raw_high_f": _num(r.high_f, 1), "raw_high_c": _num(r.high, 1),
                        "bias_f": _num(r.bias_f), "weight": _num(r.weight, 3)}
                       for r in d.sort_values("weight", ascending=False).itertuples()],
        })

    hourly_rankings = {}
    for var, s in hstats[hstats["lead"] == 1].groupby("var", sort=False):
        s = s.sort_values("mae", na_position="last")
        hourly_rankings[var] = {
            "unit": UNITS[var], "lead_days": 1, "truth": "ERA5",
            "note": GUST_NOTE if var == GUST else f"{len(s)} models",
            "bias_corrected": var != PRECIP,
            "models": [{"model": r.model, "mae": _num(r.mae, 3), "bias": _num(r.bias, 3),
                        "weight": _num(r.weight, 3), "n_hours": int(r.n_hours)} for r in s.itertuples()],
        }

    return {
        "lat": lat, "lon": lon, "grid": {"lat": pts[0][0], "lon": pts[0][1]},
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "std_utc_offset_hours": offset,
        "current": {**current, "time": current["time"] + "Z",
                    "units": {**UNITS, "weather_code": "wmo code"}},
        "daily": days,
        "rankings": [{"model": r.model, "lead_days": 1, "mae_f": _num(r.mae_f), "bias_f": _num(r.bias_f),
                      "weight": _num(r.weight, 3), "n_days": int(r.n_days), "truth": "ERA5"}
                     for r in rank.itertuples()],
        "hourly_rankings": hourly_rankings,
        "truth_through": truth["time"].max().isoformat().replace("+00:00", "Z"),
        "truth_note": "ERA5 is a ~25 km ECMWF reanalysis, not a station; it may slightly favor ECMWF.",
        "nearest_us_station": nearest_us_station(lat, lon),
    }
