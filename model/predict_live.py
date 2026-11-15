"""Nostos live forecast: tomorrow's official daily high for all 20 cities.

1. Fetch the latest hourly forecasts (Open-Meteo *forecast* API, same 5 models + NBM).
2. Turn them into daily highs with the same climate-day rule as the backtest (daily_high.to_daily).
3. Apply each model's current bias correction and inverse-MAE weight, computed by the same
   blend.window_stats as the backtest: errors over the 30 days ending D-2, using only
   official highs whose CLI report was published before issued_at (= now, UTC).
4. Blend with the variant chosen on validation (stored by blend.py), and give NBM the same
   rolling correction (nbm_corrected).
5. Save data/live_forecast.parquet and upsert into nostos_wx.live_forecasts.

Run after blend.py:  python predict_live.py
"""
import pandas as pd

import blend
import config
import load_to_db
from daily_high import to_daily
from fetch_forecasts import get_with_retries

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
LEAD = 1                    # tomorrow = the night-before forecast in the backtest
HISTORY_DAYS = 45           # enough history for the 30-day window ending D-2
COLS = ["location", "model", "role", "lead_days", "climate_date", "day_start", "issued_at",
        "high_f", "bias_f", "weight"]


def fetch_latest(row: pd.Series, now: pd.Timestamp) -> pd.DataFrame:
    """Latest hourly run for one city, UTC, today .. today+3 (covers tomorrow's climate day)."""
    params = {
        "latitude": row["lat"],
        "longitude": row["lon"],
        "start_date": now.strftime("%Y-%m-%d"),
        "end_date": (now + pd.Timedelta(days=3)).strftime("%Y-%m-%d"),
        "hourly": "temperature_2m",
        "models": ",".join(config.MODELS + config.BENCHMARK_MODELS),
        "timezone": "UTC",
        "temperature_unit": config.TEMP_UNIT,
    }
    wide = pd.DataFrame(get_with_retries(params, url=FORECAST_URL)["hourly"])
    wide["time"] = pd.to_datetime(wide["time"], utc=True)
    long = wide.melt(id_vars="time", var_name="column", value_name="temp_f").dropna(subset=["temp_f"])
    long["model"] = long["column"].str.removeprefix("temperature_2m_")
    long["location"] = row["location"]
    long["lead"] = LEAD
    return long.rename(columns={"time": "target_time"})[["location", "model", "lead", "target_time", "temp_f"]]


def live_daily_highs(locs: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    hourly = pd.concat([fetch_latest(row, now) for _, row in locs.iterrows()], ignore_index=True)
    daily = to_daily(hourly, locs)
    # "Tomorrow" is per city: the local-standard-time date after today's.
    daily["climate_date"] = pd.to_datetime(daily["climate_date"])
    local_now = now + pd.to_timedelta(daily["std_utc_offset_hours"], unit="h")
    tomorrow = local_now.dt.tz_localize(None).dt.normalize() + pd.Timedelta(days=1)
    daily = daily[daily["climate_date"] == tomorrow].copy()
    daily["day_start"] = (daily["climate_date"].dt.tz_localize("UTC")
                          - pd.to_timedelta(daily["std_utc_offset_hours"], unit="h"))
    daily["issued_at"] = now
    assert (daily["issued_at"] < daily["day_start"]).all(), "tomorrow has already started somewhere"
    return daily.drop(columns=["hours", "std_utc_offset_hours"])


def main():
    now = pd.Timestamp.now(tz="UTC").floor("s")
    locs = config.load_locations()
    live = live_daily_highs(locs, now)
    missing = {(l, m) for l in locs["location"] for m in config.MODELS + config.BENCHMARK_MODELS} \
        - set(zip(live["location"], live["model"]))
    if missing:
        print(f"WARNING: no full 24-hour forecast for {sorted(missing)}")

    # History: backtest lead-1 forecasts and official highs from the cache (blend.py's inputs).
    hist, obs = blend.load()
    first_target = live["climate_date"].min()
    hist = hist[(hist["lead"] == LEAD) & (hist["climate_date"] < first_target)
                & (hist["climate_date"] >= first_target - pd.Timedelta(days=HISTORY_DAYS))]
    both = pd.concat([hist, live[hist.columns.intersection(live.columns)]], ignore_index=True)
    is_live = both["issued_at"] == now

    # Same rolling stats as the backtest; published_at <= issued_at(now) is enforced inside.
    stats = blend.window_stats(blend.input_forecasts(both), obs)
    stats = stats[stats["issued_at"] == now].copy()
    used = stats.dropna(subset=["bias"])
    assert (used["max_used_published_at"] <= now).all(), "used an observation published after now"
    assert (used["last_used_day"] <= used["climate_date"] - pd.Timedelta(days=LEAD + 1)).all()

    chosen = pd.read_parquet(config.DATA_DIR / "nostos_daily_high.parquet", columns=["variant"])["variant"].iloc[0]
    blended = blend.blend(stats)
    blended["high_f"] = blended[chosen]

    # Per-model weight share, consistent with the chosen variant.
    stats["w"] = blend.weights(stats) if chosen == "inv_mae_corrected" else 1.0
    stats["weight"] = stats["w"] / stats.groupby(blend.DAY_KEYS)["w"].transform("sum")
    stats["bias_f"] = stats["bias"] if chosen != "avg_raw" else None

    nbm_corr = blend.benchmark_corrected(both, obs)
    nbm_corr = nbm_corr[nbm_corr["issued_at"] == now].rename(columns={"bias": "bias_f"})

    raw = both[is_live & both["model"].isin(config.BENCHMARK_MODELS)]
    out = pd.concat([
        blended.assign(model="nostos", role="nostos", bias_f=None, weight=None),
        stats.assign(role="input"),
        raw.assign(role="benchmark", bias_f=None, weight=None),
        nbm_corr.assign(role="benchmark", weight=None),
    ], ignore_index=True)
    out = out.merge(live[["location", "climate_date", "day_start"]].drop_duplicates(),
                    on=["location", "climate_date"])
    out["lead_days"] = LEAD
    out = out[COLS].dropna(subset=["high_f"])
    out["climate_date"] = out["climate_date"].dt.date
    out.to_parquet(config.DATA_DIR / "live_forecast.parquet", index=False)

    with load_to_db.connect(autocommit=True) as conn:
        load_to_db.apply_schema(conn)
    with load_to_db.connect() as conn, conn.transaction():
        load_to_db.upsert(conn, "nostos_wx.live_forecasts", out, COLS,
                          ["location", "model", "climate_date", "issued_at"])

    print(f"\nNostos live forecast, issued_at {now:%Y-%m-%d %H:%M} UTC, variant {chosen}, "
          f"bias from official highs through {used['last_used_day'].max():%Y-%m-%d}")
    table = out.pivot(index=["location", "climate_date"], columns="model", values="high_f")
    order = ["nostos", "nbm_corrected", *config.BENCHMARK_MODELS, *config.MODELS]
    print(table[[c for c in order if c in table]].round(1).reset_index()
          .rename(columns={"location": "city", "climate_date": "date"}).to_string(index=False))


if __name__ == "__main__":
    main()
