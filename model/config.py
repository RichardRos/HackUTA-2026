from pathlib import Path
import pandas as pd


DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "jma_seamless"]
BENCHMARK_MODELS = ["ncep_nbm_conus"]

LEADS = [1,2,3]

START_DATE = "2025-01-01"
END_DATE = "2026-11-12"

TEMP_UNIT = "fahrenheit"

def load_locations():
    locations_file =  pd.read_csv(DATA_DIR / "locations.csv")
    required = {"location", "station_id", "lat", "lon", "std_utc_offset_hours", "kalshi_series"}
    missing = required - set(locations_file.columns)
    if missing:
        raise ValueError(f"Locations file is missing required columns: {missing}")
    return locations_file
    