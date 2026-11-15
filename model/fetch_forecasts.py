import time 
import pandas as pd
import requests
import config


URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

COLUMN_PATTERN = r"previous_day(\d+)_(.+)$"

def get_with_retries(params: dict, attempts: int =5, url: str = URL) -> dict:
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, timeout=180)
            if response.status_code == 200:
                return response.json()
            if response.status_code != 429 and response.status_code < 500:
                raise RuntimeError(f"Open-Metero error {response.status_code}: {response.text[:300]}")
        except(requests.ConnectionError, requests.Timeout):
            pass
        wait = 5 * 2 ** attempt
        print(f"retrying in {wait}s (attempt {attempt + 1}/{attempts})")
        time.sleep(wait)
    raise RuntimeError("Open-Metero request failed after retries")


def quarters(start: str, end: str) -> list[tuple[str, str]]:
    """Split [start, end] into calendar-quarter chunks (inclusive dates)."""
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    chunks = []
    cur = start_ts
    while cur <= end_ts:
        q_end = min((cur + pd.offsets.QuarterEnd(0)).normalize(), end_ts)
        chunks.append((cur.strftime("%Y-%m-%d"), q_end.strftime("%Y-%m-%d")))
        cur = q_end + pd.Timedelta(days=1)
    return chunks


def fetch_chunk(row: pd.Series, start: str, end: str) -> pd.DataFrame:
    params = {
        "latitude": row["lat"],
        "longitude": row["lon"],
        "start_date": start,
        "end_date": end,
        "hourly": ",".join(f"temperature_2m_previous_day{lead}" for lead in config.LEADS),
        "models": ",".join(config.MODELS + config.BENCHMARK_MODELS),
        "timezone": "UTC",
        "temperature_unit": config.TEMP_UNIT,
    }
    data = get_with_retries(params)
    wide = pd.DataFrame(data["hourly"])
    wide["time"] = pd.to_datetime(wide["time"], utc=True)

    long = wide.melt(id_vars="time", var_name="column", value_name="temp_f")
    parts = long["column"].str.extract(COLUMN_PATTERN)
    long["lead"] = parts[0].astype(int)
    long["model"] = parts[1]

    long["location"] = row["location"]
    long = long.rename(columns={"time": "target_time"}).dropna(subset=["temp_f"])
    return long[["location", "model", "lead", "target_time", "temp_f"]]


def fetch_end() -> str:
    """END_DATE + 1 day (UTC): the local climate day of END_DATE runs into the next UTC day
    (until 05:00-10:00 UTC), and daily_high.py needs all 24 hours. These are archived forecasts
    issued a day or more earlier, so this adds no information from after the forecast time."""
    return (pd.Timestamp(config.END_DATE) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def fetch(row: pd.Series, start_date: str) -> pd.DataFrame:
    parts = []
    for start, end in quarters(start_date, fetch_end()):
        parts.append(fetch_chunk(row, start, end))
        time.sleep(0.5)
    return pd.concat(parts, ignore_index=True)


def climate_dates(df: pd.DataFrame, locations: pd.DataFrame) -> pd.Series:
    """NWS climate day of each hourly row (local standard time), as in daily_high.py."""
    offset = df["location"].map(locations.set_index("location")["std_utc_offset_hours"])
    return (df["target_time"] + pd.to_timedelta(offset, unit="h")).dt.normalize().dt.tz_localize(None)


def main():
    out = config.DATA_DIR / "forecasts.parquet"
    cache = pd.read_parquet(out) if out.exists() and not config.FULL_REFRESH else None
    start = config.incremental_start(cache["target_time"].max().tz_localize(None) if cache is not None else None)
    start_date = start.strftime("%Y-%m-%d")

    locations = config.load_locations()
    frames, failed = [], []
    for _, row in locations.iterrows():
        print(f"Fetching forecasts for {row['location']} {start_date}..{fetch_end()}", flush=True)
        try:
            frames.append(fetch(row, start_date))
        except Exception as exc:  # keep going; report at the end
            print(f"  FAILED {row['location']}: {exc}", flush=True)
            failed.append(row["location"])

    new = pd.concat(frames, ignore_index=True)
    df = config.merge_cache(cache, new, ["location", "model", "lead", "target_time"],
                            climate_dates(new, locations))
    df.to_parquet(out, index=False)

    print(f"fetched {len(new):,} rows, cache now {len(df):,} rows "
          f"({df['target_time'].min():%Y-%m-%d}..{df['target_time'].max():%Y-%m-%d %H:%M} UTC) -> {out}")
    print(new.groupby(["model", "lead"]).size().unstack())  # fetched rows per model x lead
    print(f"failed cities: {failed or 'none'}")
    if failed:
        raise SystemExit(f"forecast fetch failed for {failed}; cache saved with the rest")


if __name__ == "__main__":
    main()
