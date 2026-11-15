import os
from pathlib import Path
import pandas as pd


DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "jma_seamless"]
BENCHMARK_MODELS = ["ncep_nbm_conus"]

LEADS = [1,2,3]

START_DATE = "2025-01-01"
# Last day of data to fetch. The nightly run sets END_DATE=yesterday (UTC); default = the backtest end.
END_DATE = os.environ.get("END_DATE") or "2026-11-12"
pd.Timestamp(END_DATE)  # fail fast on a malformed END_DATE

# Fixed leaderboard: validation and test windows never move, whatever END_DATE is.
# Data for days up to TEST_END is frozen in the cache (incremental fetches never replace it).
TEST_END = "2026-11-12"

# Incremental fetches: re-fetch the last few cached days (late data, partial last day).
# FULL_REFRESH=1 re-downloads everything from START_DATE.
OVERLAP_DAYS = 3
FULL_REFRESH = os.environ.get("FULL_REFRESH") == "1"

TEMP_UNIT = "fahrenheit"

def load_locations():
    locations_file =  pd.read_csv(DATA_DIR / "locations.csv")
    required = {"location", "station_id", "lat", "lon", "std_utc_offset_hours", "kalshi_series"}
    missing = required - set(locations_file.columns)
    if missing:
        raise ValueError(f"Locations file is missing required columns: {missing}")
    return locations_file


def incremental_start(cached_last: pd.Timestamp | None) -> pd.Timestamp:
    """First day to fetch: START_DATE on a full refresh or empty cache, else last cached day - overlap."""
    if FULL_REFRESH or cached_last is None:
        return pd.Timestamp(START_DATE)
    return max(pd.Timestamp(START_DATE), pd.Timestamp(cached_last).normalize() - pd.Timedelta(days=OVERLAP_DAYS))


def merge_cache(old: pd.DataFrame | None, new: pd.DataFrame, key: list[str],
                climate_date: pd.Series) -> pd.DataFrame:
    """Add freshly fetched rows to the cache. New rows win on the same key, except for
    climate days <= TEST_END, which stay exactly as they were (the frozen test window)."""
    if old is None or FULL_REFRESH:
        return new
    new = new[pd.to_datetime(climate_date) > pd.Timestamp(TEST_END)]
    merged = pd.concat([old, new], ignore_index=True)
    return merged.drop_duplicates(subset=key, keep="last").sort_values(key, ignore_index=True)
