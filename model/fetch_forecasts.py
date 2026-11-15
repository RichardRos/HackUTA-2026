import time 
import pandas as pd
import requests
import config


URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

COLUMN_PATTERN = r"previous_day(\d+)_(.+)$"

def get_with_retries(params: dict, attempts: int =5) -> dict:
    for attempt in range(attempts):
        try:
            response = requests.get(URL, params=params, timeout=60)
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


def fetch(row: pd.Series) -> pd.DataFrame:
    params = {
        "latitude": row["lat"],
        "longitude": row["lon"],
        "start_date": config.START_DATE,
        "end_date": config.END_DATE,
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

def main():
    frames = []
    for _, row in config.load_locations().iterrows():
        print(f"Fetching forecasts for {row['location']} {config.START_DATE}..{config.END_DATE}")
        frames.append(fetch(row))
        time.sleep(0.5)

        df = pd.concat(frames, ignore_index=True)
        out = config.DATA_DIR / "forecasts.parquet"
        df.to_parquet(out, index=False)

        print(f"saved {len(df):,} rows -> {out}")
        print(df.groupby(["model", "lead"]).size().unstack())  # rows per model x lead
        print(df.head())

if __name__ == "__main__":
    main()




    
    
                