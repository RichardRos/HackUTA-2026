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
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

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

# Past window for the stats: today−37 .. today−5 (ERA5 lag). ERA5 ends ~6 days back and the
# first/last local days are partial, so this leaves >= 30 complete truth days.
PAST_DAYS = 37
# Point forecast: today .. today+4, because D+3 of a UTC−6 point ends on UTC day +4.
FORECAST_DAYS = 5
ERA5_LAG_DAYS = 5
TIMEOUT_S = 15
# Each feature asks only for the columns and dates it uses: the past runs (previous_dayL, the
# track record) and the latest run (the forecast) are separate calls. Measured 2026-11-15 for
# 11 points: past 2.4 s / 1.1 MB + latest 1.3 s / 0.1 MB, vs 13.2 s / 4.7 MB for one big call.
BATCH_POINTS = 12          # past-runs points per HTTP call (parallel above that)
MAX_PARALLEL = 4
CACHE_TTL_S = 30 * 60
STATION_RADIUS_KM = 25
POINT_GRID_DEG = 0.1       # the point endpoint keeps ~11 km precision
ROUTE_GRID_DEG = 0.25      # route points snap to the 0.25° model grid (GFS/ECMWF resolution)
NAME_RADIUS_KM = 50        # a named waypoint this close labels a spot in the "why" line
COMMON = {"timezone": "GMT", "wind_speed_unit": "ms"}
# What each feature reads from the past runs: {variable: leads}.
POINT_PAST_COLUMNS = {TEMP: [1, 2, 3], WIND: [1], GUST: [1], PRECIP: [1]}  # daily high + lead-1 rankings
POINT_LATEST_VARS = [TEMP]                                                   # D+1..D+3 highs

# Odyssey route hazard, per point per hour on the road. Tune here.
HAZARD = {
    "precip_per_mm": 3.0,                      # precipitation mm × 3
    "wind_threshold_ms": 8.0, "wind_per_ms": 1.0,    # max(0, wind − 8) × 1
    "gust_threshold_ms": 15.0, "gust_per_ms": 1.5,   # max(0, gust − 15) × 1.5
    "ice_temp_c": 1.0, "ice_precip_mm": 0.1, "ice": 5.0,  # +5 if temp ≤ 1 °C and precip > 0.1 mm
    "heat_threshold_c": 35.0, "heat_per_c": 0.5,     # max(0, temp − 35) × 0.5
    "temp_spread": 0.5, "wind_spread": 0.5,          # model disagreement (uncertainty)
}
TIME_WEIGHT = 1.0          # route score = Σ hazard × segment hours + TIME_WEIGHT × total hours
MAX_SPACING_KM = 75        # preferred spacing ...
MAX_POINTS_PER_ROUTE = 8   # ... but at most 8 points, so long routes space out
COARSE_SPACING_KM = 100    # beyond this the route is flagged coarse_sampling
HORIZON_H = 96


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


# ---------------------------------------------------------------- fetching (batched, cached per point)

log = logging.getLogger("world_wx")
if not log.handlers:  # uvicorn doesn't configure the root logger; make the cost line visible
    _h = logging.StreamHandler()
    _fmt = logging.Formatter("%(asctime)sZ %(name)s %(levelname)s %(message)s")
    _fmt.converter = time.gmtime  # UTC everywhere
    _h.setFormatter(_fmt)
    log.addHandler(_h)
    log.setLevel(logging.INFO)

_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def snap(points, deg: float) -> list[tuple[float, float]]:
    """Snap to a deg° grid. The snapped point is what we fetch and the cache key, so nearby
    points (and shared stretches of different routes) are fetched once."""
    return [(round(round(float(p[0]) / deg) * deg, 4), round(round(float(p[1]) / deg) * deg, 4)) for p in points]


def call_weight(n_vars: int, n_days: float) -> float:
    """Open-Meteo's fractional call count for ONE location.

    Pricing FAQ: >10 variables or >2 weeks counts extra, fractionally ("2 weeks with 15
    variables = 1.5 calls", "4 weeks = 3.0"), read here as multiplicative. Conservative: every
    model × variable counts as a variable and every location is charged.
    """
    return max(1.0, n_vars / 10) * max(1.0, n_days / 14)


class Cost:
    """Estimated weighted Open-Meteo calls for one request (cache hits are free)."""

    def __init__(self):
        self.calls = []
        self.total = self.uncached = 0.0

    def add(self, name, fetched, cached, n_vars, n_days):
        w = call_weight(n_vars, n_days)
        self.total += fetched * w
        self.uncached += (fetched + cached) * w
        self.calls.append({"call": name, "points_fetched": fetched, "points_cached": cached,
                           "variables": n_vars, "days": n_days, "weight_per_point": round(w, 2),
                           "weighted_calls": round(fetched * w, 1)})

    def summary(self) -> dict:
        return {"weighted_calls_est": round(self.total, 1), "if_uncached_est": round(self.uncached, 1),
                "calls": self.calls}


def _get(url: str, params: dict) -> list[dict]:
    """One batched call; always returns a list (Open-Meteo returns a bare object for 1 point)."""
    if key := os.getenv("OPEN_METEO_API_KEY"):  # paid customer- endpoints: their own, higher limits
        url, params = url.replace("https://", "https://customer-", 1), {**params, "apikey": key}
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        raise RuntimeError(f"Open-Meteo unreachable ({url}): {exc}") from exc
    if r.status_code != 200:
        raise RuntimeError(f"Open-Meteo error {r.status_code} ({url}): {r.text[:300]}")
    body = r.json()
    return body if isinstance(body, list) else [body]


def _fetch(name: str, url: str, points, params: dict, n_vars: int, n_days: int,
           cost: Cost | None = None, batch: int | None = None) -> list[dict]:
    """Blocks for `points` (in order). Each point is cached on its own under (call, point, params);
    only the missing ones are fetched, `batch` points per HTTP call, in parallel."""
    pkey = (name, tuple(sorted(params.items())))
    uniq = list(dict.fromkeys(points))
    found, missing = {}, []
    with _cache_lock:
        for p in uniq:
            hit = _cache.get((pkey, p))
            if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
                found[p] = hit[1]
            else:
                missing.append(p)

    def one(b):
        coords = {"latitude": ",".join(str(p[0]) for p in b), "longitude": ",".join(str(p[1]) for p in b)}
        blocks = _get(url, {**coords, **params})
        if len(blocks) != len(b):
            raise RuntimeError(f"Open-Meteo returned {len(blocks)} locations for {len(b)} points")
        return list(zip(b, blocks))

    if missing:
        size = batch or len(missing)
        batches = [missing[i:i + size] for i in range(0, len(missing), size)]
        if len(batches) == 1:
            results = [one(batches[0])]
        else:
            with ThreadPoolExecutor(min(MAX_PARALLEL, len(batches))) as pool:
                results = list(pool.map(one, batches))
        with _cache_lock:
            for pairs in results:
                for p, blk in pairs:
                    _cache[(pkey, p)] = (time.monotonic(), blk)
                    found[p] = blk
    if cost is not None:
        cost.add(name, len(missing), len(uniq) - len(missing), n_vars, n_days)
    return [found[p] for p in points]


def _day(ts) -> pd.Timestamp:
    """UTC calendar day of ts, naive (for start_date / end_date)."""
    ts = pd.Timestamp(ts)
    return (ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo else ts).normalize()


def _range(start: pd.Timestamp, end: pd.Timestamp) -> tuple[dict, int]:
    return {"start_date": str(start.date()), "end_date": str(end.date())}, (end - start).days + 1


def past_window(now: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    today = _day(now)
    return today - pd.Timedelta(days=PAST_DAYS), today - pd.Timedelta(days=ERA5_LAG_DAYS)


def fetch_current(points, cost=None):
    return _fetch("current", CURRENT_URL, points, {"current": ",".join(CURRENT_VARS), **COMMON},
                  len(CURRENT_VARS), 1, cost)


def fetch_past_runs(points, now: pd.Timestamp, columns: dict[str, list[int]], cost=None):
    """The track record: only <var>_previous_dayL for the requested {var: leads}, past window only."""
    hourly = [f"{v}_previous_day{l}" for v, leads in columns.items() for l in leads]
    dates, days = _range(*past_window(now))
    return _fetch("past_runs", PREVIOUS_RUNS_URL, points,
                  {**dates, "models": ",".join(config.MODELS), "hourly": ",".join(hourly), **COMMON},
                  len(hourly) * len(config.MODELS), days, cost, batch=BATCH_POINTS)


def fetch_latest_run(points, now: pd.Timestamp, variables: list[str], end: pd.Timestamp, cost=None):
    """The live forecast: plain <var>_<model> (latest run), today .. end only."""
    dates, days = _range(_day(now), _day(end))
    return _fetch("latest_run", PREVIOUS_RUNS_URL, points,
                  {**dates, "models": ",".join(config.MODELS), "hourly": ",".join(variables), **COMMON},
                  len(variables) * len(config.MODELS), days, cost, batch=BATCH_POINTS)


def fetch_truth(points, now: pd.Timestamp, cost=None):
    dates, days = _range(*past_window(now))
    return _fetch("era5", ARCHIVE_URL, points, {**dates, "models": "era5", "hourly": ",".join(VARS), **COMMON},
                  len(VARS), days, cost)


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


def hourly_model(fc: pd.DataFrame, truth: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """hourly_stats plus each model's weight (w raw, weight = share within var x lead)."""
    hstats = hourly_stats(fc, truth, now)
    hstats["w"] = hourly_weights(hstats)
    hstats["weight"] = hstats["w"] / hstats.groupby(["var", "lead"])["w"].transform("sum")
    return hstats


def hourly_blend(fc: pd.DataFrame, hstats: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Hourly Nostos for the next HORIZON_H hours, one row per UTC hour.

    An hour t hours ahead uses the lead-clip(ceil(t/24), 1, 3) stats. Temperature, wind and
    gusts subtract the model's bias; precipitation is weighted only. Wind, gusts and rain are
    clipped at >= 0. Spread = std of the bias-corrected model values (model disagreement).
    """
    f = fc[(fc["lead"] == 0) & (fc["time"] >= now.floor("h")) & (fc["time"] <= now + pd.Timedelta(hours=HORIZON_H))]
    f = f[(f["var"] != GUST) | f["model"].isin(GUST_MODELS)].copy()
    ahead_h = (f["time"] - now) / pd.Timedelta(hours=1)
    f["lead"] = np.clip(np.ceil(ahead_h / 24), 1, 3).astype(int)
    f = f.merge(hstats[["var", "model", "lead", "bias", "w"]], on=["var", "model", "lead"], how="left")
    f["corrected"] = f["value"] - f["bias"].fillna(0.0)
    f["wv"] = f["w"] * f["corrected"]
    g = f.groupby(["time", "var"])
    out = (g["wv"].sum() / g["w"].sum()).unstack("var")
    spread = g["corrected"].std(ddof=0).unstack("var")
    for v in (WIND, GUST, PRECIP):
        out[v] = out[v].clip(lower=0)
    out = out.rename(columns={TEMP: "temp_c", WIND: "wind_ms", GUST: "gust_ms", PRECIP: "precip_mm"})
    out["temp_spread_c"] = spread[TEMP]
    out["wind_spread_ms"] = spread[WIND]
    return out


# ---------------------------------------------------------------- Odyssey routes

def densify(waypoints, max_spacing_km=MAX_SPACING_KM, max_points=MAX_POINTS_PER_ROUTE):
    """Evenly spaced points along the polyline: spacing <= max_spacing_km where max_points allows,
    else wider (a 700 km route reaches COARSE_SPACING_KM at 8 points).

    Waypoints are [lat, lon] or [lat, lon, name]. Returns (points, km from start of each point).
    Points lie on the straight segments between waypoints (linear in lat/lon); distances are haversine.
    """
    wp = [(float(w[0]), float(w[1])) for w in waypoints]
    if len(wp) < 2:
        raise ValueError("a route needs at least 2 waypoints")
    seg = np.array([haversine_km(wp[i], wp[i + 1]) for i in range(len(wp) - 1)])
    cum = np.r_[0.0, np.cumsum(seg)]
    total = cum[-1]
    if total <= 0:
        raise ValueError("route has zero length")
    n = min(max_points, max(2, math.ceil(total / max_spacing_km) + 1))
    km = np.linspace(0.0, total, n)
    pts = []
    for d in km:
        i = min(int(np.searchsorted(cum, d, side="right")) - 1, len(seg) - 1)
        frac = 0.0 if seg[i] == 0 else (d - cum[i]) / seg[i]
        pts.append((wp[i][0] + frac * (wp[i + 1][0] - wp[i][0]), wp[i][1] + frac * (wp[i + 1][1] - wp[i][1])))
    return pts, km


def point_hours(km: np.ndarray, speed_kmh: float) -> tuple[np.ndarray, np.ndarray]:
    """ETA offset (h from departure) of each point, and the road hours each point stands for
    (half the segment before + half after; they sum to the total trip hours)."""
    eta_h = km / speed_kmh
    seg_h = np.diff(eta_h)
    return eta_h, np.r_[seg_h, 0] / 2 + np.r_[0, seg_h] / 2


def hazard_parts(df: pd.DataFrame) -> pd.DataFrame:
    """Hazard components per point (rows of temp_c, wind_ms, gust_ms, precip_mm, spreads)."""
    H = HAZARD
    return pd.DataFrame({
        "rain": df["precip_mm"] * H["precip_per_mm"],
        "wind": (df["wind_ms"] - H["wind_threshold_ms"]).clip(lower=0) * H["wind_per_ms"],
        "gust": (df["gust_ms"] - H["gust_threshold_ms"]).clip(lower=0) * H["gust_per_ms"],
        "ice": ((df["temp_c"] <= H["ice_temp_c"]) & (df["precip_mm"] > H["ice_precip_mm"])) * H["ice"],
        "heat": (df["temp_c"] - H["heat_threshold_c"]).clip(lower=0) * H["heat_per_c"],
        "uncertainty": H["temp_spread"] * df["temp_spread_c"] + H["wind_spread"] * df["wind_spread_ms"],
    }, index=df.index).astype(float)


def _reason(row: pd.Series, parts: pd.Series) -> str:
    top = parts.idxmax()
    return {
        "rain": f"{row['precip_mm']:.1f} mm/h rain",
        "wind": f"wind {row['wind_ms']:.0f} m/s",
        "gust": f"gusts {row['gust_ms']:.0f} m/s",
        "ice": f"ice risk ({row['temp_c']:.1f} °C with {row['precip_mm']:.1f} mm/h)",
        "heat": f"heat {row['temp_c']:.0f} °C",
        "uncertainty": f"models disagree (±{row['temp_spread_c']:.1f} °C, ±{row['wind_spread_ms']:.1f} m/s)",
    }[top]


def _iso(ts: pd.Timestamp) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def score_route(samples: list[tuple[float, float]], tables: list[pd.DataFrame], eta_h, seg_h,
                depart: pd.Timestamp) -> tuple[float, pd.DataFrame]:
    """Score one route for one departure. Lower is better."""
    eta = [depart + pd.Timedelta(hours=float(h)) for h in eta_h]
    rows = [t.reindex([e.round("h")]).iloc[0] for t, e in zip(tables, eta)]
    df = pd.DataFrame(rows).reset_index(drop=True)
    if df[["temp_c", "wind_ms", "precip_mm"]].isna().any().any():
        raise ValueError("an ETA falls outside the forecast")
    df["gust_ms"] = df["gust_ms"].fillna(df["wind_ms"])  # no gust model at this hour: assume no gusts
    df[["temp_spread_c", "wind_spread_ms"]] = df[["temp_spread_c", "wind_spread_ms"]].fillna(0.0)
    parts = hazard_parts(df)
    df["hazard"] = parts.sum(axis=1)
    df["road_h"] = seg_h
    df["eta"] = [e.round("min") for e in eta]
    df["lat"] = [p[0] for p in samples]
    df["lon"] = [p[1] for p in samples]
    df["rain_mm"] = df["precip_mm"] * df["road_h"]  # rain met while driving this stretch
    df = pd.concat([df, parts.add_prefix("h_")], axis=1)
    score = float((df["hazard"] * df["road_h"]).sum() + TIME_WEIGHT * eta_h[-1])
    return score, df


def place_name(lat: float, lon: float, named: list[tuple[float, float, str]]) -> str | None:
    """The closest named waypoint within NAME_RADIUS_KM, else None."""
    if not named:
        return None
    dist, name = min((haversine_km((lat, lon), (a, b)), n) for a, b, n in named)
    return name if dist <= NAME_RADIUS_KM else None


def _near(lat: float, lon: float, named) -> str:
    return f"near {place_name(lat, lon, named) or f'{lat:.2f}, {lon:.2f}'}"


def _why(best: dict, other: dict | None, depart_at: pd.Timestamp, optimized: bool, named=()) -> str:
    agree = f"models agree within {best['max_temp_spread_c']:.1f}°C"
    if other is None:
        msg = f"Route {best['route']} is the only route; {agree}."
    else:
        bp, op = best["_parts"], other["_parts"]
        diff = (op - bp)  # hazard-hours the best route avoids, per component
        time_gap = other["duration_h"] - best["duration_h"]
        # Time explains the win when it's at least half the score gap (or the weather gap is tiny).
        if diff.max() <= 0.5 or TIME_WEIGHT * time_gap >= 0.5 * (other["score"] - best["score"]):
            msg = (f"Route {best['route']} is {time_gap:.1f} h faster than {other['route']} "
                   f"with similar weather; {agree}.") if time_gap > 0 else \
                  f"Route {best['route']} edges out {other['route']} (score {best['score']} vs {other['score']}); {agree}."
        else:
            top = diff.idxmax()
            w = other["_worst_rows"][top]
            where = _near(w["lat"], w["lon"], named)
            what = {
                "rain": f"avoids {other['rain_mm'] - best['rain_mm']:.1f} mm of rain {where}",
                "wind": f"avoids sustained wind up to {w['wind_ms']:.0f} m/s {where}",
                "gust": f"avoids gusts up to {w['gust_ms']:.0f} m/s {where}",
                "ice": f"avoids ice risk {where}",
                "heat": f"avoids {w['temp_c']:.0f} °C heat {where}",
                "uncertainty": f"avoids a stretch where models disagree by ±{w['temp_spread_c']:.1f}°C {where}",
            }[top]
            msg = f"Route {best['route']} {what} vs {other['route']}; {agree}."
    if optimized and best["_depart"] != depart_at:
        msg = msg[:-1] + f"; leave at {best['_depart']:%H:%M}Z instead of {depart_at:%H:%M}Z."
    return msg


def routes(routes_in: list[dict], depart_at: pd.Timestamp | None = None, speed_kmh: float = 80,
           optimize_departure: bool = False, window_hours: int = 24, now: pd.Timestamp | None = None) -> dict:
    """Rank routes (and optionally departure hours) by Σ hazard × road hours + total hours."""
    now = (now or pd.Timestamp.now(tz="UTC")).floor("s")
    depart_at = now.ceil("h") if depart_at is None else pd.Timestamp(depart_at)
    depart_at = depart_at.tz_localize("UTC") if depart_at.tzinfo is None else depart_at.tz_convert("UTC")
    if not routes_in:
        raise ValueError("send at least one route")
    if not speed_kmh > 0:
        raise ValueError("speed_kmh must be positive")
    if depart_at < now.floor("h"):
        raise ValueError(f"depart_at {_iso(depart_at)} is in the past (now {_iso(now)})")
    names = [r["name"] for r in routes_in]
    if len(set(names)) != len(names):
        raise ValueError("route names must be unique")

    named = [(float(w[0]), float(w[1]), str(w[2])) for r in routes_in for w in r["waypoints"]
             if len(w) > 2 and w[2]]
    plans = []
    for r in routes_in:
        samples, km = densify(r["waypoints"])
        eta_h, seg_h = point_hours(km, speed_kmh)
        plans.append({"name": r["name"], "samples": samples, "grid": snap(samples, ROUTE_GRID_DEG), "km": km,
                      "eta_h": eta_h, "seg_h": seg_h})

    last_ok = now + pd.Timedelta(hours=HORIZON_H)
    departures = [depart_at + pd.Timedelta(hours=k) for k in range(window_hours + 1 if optimize_departure else 1)]
    # Fetch only what the ETAs can reach: the latest arrival decides the leads and the last day.
    longest = max(float(p["eta_h"][-1]) for p in plans)
    latest_arrival = min(last_ok, departures[-1] + pd.Timedelta(hours=longest))
    max_lead = int(np.clip(math.ceil((latest_arrival - now) / pd.Timedelta(hours=24)), 1, 3))
    past_cols = {v: list(range(1, max_lead + 1)) for v in VARS}

    # Snapped points, deduped across routes: shared stretches are fetched (and cached) once.
    uniq = sorted({g for p in plans for g in p["grid"]})
    cost = Cost()
    past_b = fetch_past_runs(uniq, now, past_cols, cost)
    latest_b = fetch_latest_run(uniq, now, VARS, latest_arrival + pd.Timedelta(hours=1), cost)
    truth_b = fetch_truth(uniq, now, cost)
    tables = {}
    for g, pb, lb, tb in zip(uniq, past_b, latest_b, truth_b):
        fc = pd.concat([parse_forecasts(pb), parse_forecasts(lb)], ignore_index=True)
        truth = parse_truth(tb)
        if truth.empty:
            raise RuntimeError(f"ERA5 returned no truth at {g}")
        tables[g] = hourly_blend(fc, hourly_model(fc, truth, now), now)
    results = []
    for p in plans:
        tabs = [tables[g] for g in p["grid"]]
        tried = []
        for dep in departures:
            if dep + pd.Timedelta(hours=float(p["eta_h"][-1])) > last_ok:
                break  # ETAs must stay inside the 96 h forecast
            score, df = score_route(p["samples"], tabs, p["eta_h"], p["seg_h"], dep)
            tried.append((score, dep, df))
        if not tried:
            raise ValueError(f"route {p['name']} at {speed_kmh} km/h does not fit in the {HORIZON_H} h forecast "
                             f"from {_iso(depart_at)}")
        score, dep, df = min(tried, key=lambda t: t[0])
        w = df["hazard"].idxmax()
        parts = df[[c for c in df if c.startswith("h_")]].rename(columns=lambda c: c[2:])
        results.append({
            "route": p["name"], "score": round(score, 2), "depart_at": _iso(dep),
            "arrive_at": _iso((dep + pd.Timedelta(hours=float(p["eta_h"][-1]))).round("min")),
            "duration_h": round(float(p["eta_h"][-1]), 2), "distance_km": round(float(p["km"][-1]), 1),
            "spacing_km": round(float(p["km"][1]), 1), "n_points": len(df),
            "coarse_sampling": bool(p["km"][1] > COARSE_SPACING_KM),
            "rain_mm": round(float(df["rain_mm"].sum()), 2),
            "max_gust_ms": _num(df["gust_ms"].max(), 1),
            "max_temp_spread_c": round(float(df["temp_spread_c"].max()), 2),
            "worst_point": {"lat": _num(df.loc[w, "lat"], 4), "lon": _num(df.loc[w, "lon"], 4),
                            "eta": _iso(df.loc[w, "eta"]), "hazard": round(float(df.loc[w, "hazard"]), 2),
                            "near": place_name(df.loc[w, "lat"], df.loc[w, "lon"], named),
                            "reason": _reason(df.loc[w], parts.loc[w])},
            "departure_scores": [{"depart_at": _iso(d), "score": round(s, 2)} for s, d, _ in tried],
            "points": [{"lat": _num(r.lat, 4), "lon": _num(r.lon, 4), "km": round(float(k), 1),
                        "eta": _iso(r.eta), "temp_c": _num(r.temp_c, 1), "wind_ms": _num(r.wind_ms, 1),
                        "gust_ms": _num(r.gust_ms, 1), "precip_mm": _num(r.precip_mm, 2),
                        "spread": {"temp_c": _num(r.temp_spread_c), "wind_ms": _num(r.wind_spread_ms)},
                        "hazard": _num(r.hazard)} for r, k in zip(df.itertuples(), p["km"])],
            # internal, for the "why" line
            "_parts": parts.mul(df["road_h"], axis=0).sum(),
            "_worst_rows": {c: df.loc[parts[c].idxmax()] for c in parts},
            "_depart": dep,
        })

    results.sort(key=lambda r: r["score"])
    best = results[0]
    why = _why(best, results[1] if len(results) > 1 else None, depart_at, optimize_departure, named)
    for r in results:
        for k in ("_parts", "_worst_rows", "_depart"):
            r.pop(k)
    return {
        "issued_at": _iso(now), "speed_kmh": speed_kmh, "optimize_departure": optimize_departure,
        "window_hours": window_hours if optimize_departure else 0,
        "best": {"route": best["route"], "depart_at": best["depart_at"]},
        "why": why,
        "hazard_weights": {**HAZARD, "time_per_hour": TIME_WEIGHT},
        "routes": results,
        "openmeteo_cost": {**_log_cost("route", cost), "unique_points": len(uniq), "max_lead_days": max_lead},
    }


# ---------------------------------------------------------------- the point endpoint

def _log_cost(what: str, cost: Cost) -> dict:
    summary = cost.summary()
    log.info("%s request: ~%.1f weighted Open-Meteo calls (%.1f if nothing were cached) %s", what,
             summary["weighted_calls_est"], summary["if_uncached_est"],
             ", ".join(f"{c['call']} {c['points_fetched']}+{c['points_cached']}c×{c['weight_per_point']}"
                       for c in summary["calls"]))
    return summary


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
    pts = snap([(lat, lon)], POINT_GRID_DEG)
    cost = Cost()
    current = fetch_current(pts, cost)[0]["current"]
    past = parse_forecasts(fetch_past_runs(pts, now, POINT_PAST_COLUMNS, cost)[0])
    end = _day(now) + pd.Timedelta(days=FORECAST_DAYS - 1)
    latest = parse_forecasts(fetch_latest_run(pts, now, POINT_LATEST_VARS, end, cost)[0])
    fc = pd.concat([past, latest], ignore_index=True)
    truth = parse_truth(fetch_truth(pts, now, cost)[0])
    if truth.empty:
        raise RuntimeError("ERA5 returned no truth for this point")
    offset = std_offset_hours(pts[0][1])

    dstats = daily_stats(fc, truth, offset, now)
    used = dstats.dropna(subset=["bias_f"])
    assert used["last_used_day"].map(lambda d: day_end_utc(d, offset)).le(now).all(), "used truth from the future"
    daily = daily_forecast(fc, dstats, offset, now)

    hstats = hourly_model(fc, truth, now)

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
        "openmeteo_cost": _log_cost("point", cost),
    }
