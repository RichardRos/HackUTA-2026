import time 

import pandas as pd
import requests
import config

URL = "https://mesonet.agron.iastate.edu/json/cli.py"

def get_year(station_id: str, year: int) -> list[dict]:
    for attempt in range(4):
        try:
            response = requests.get(URL, params={"station": station_id, "year": year}, timeout=60)
            if response.status_code == 200:
                return response.json().get("results", [])
        except (requests.ConnectionError, requests.Timeout):
                pass
        time.sleep(3*2**attempt)
    raise RuntimeError(f"IEM request failed to fetch {station_id} {year} ")

def fetch_obs(row: pd.Series) -> pd.DataFrame:
    start = pd.Timestamp(config.START_DATE).date()
    end = pd.Timestamp(config.END_DATE).date()

    records = []
    for year in range(start.year, end.year + 1):
        records += get_year(row["station_id"], year)
        time.sleep(1)

    df = pd.DataFrame(records)
    if df.empty:
            raise RuntimeError(f"No climate reports for {row['station_id']}; check the station ID")
    if not {"valid", "high"}.issubset(df.columns):
            raise RuntimeError(f"Unexpected columns from IEM: {list(df.columns)}")


    df["climate_date"] = pd.to_datetime(df["valid"]).dt.date

    df["high_f"] = pd.to_numeric(df["high"], errors="coerce")
    df = df.dropna(subset=["high_f"])
    df = df[(df["climate_date"] >= start) & (df["climate_date"] <= end)]
    df = df.drop_duplicates(subset=["climate_date"], keep="last")

    df["location"] = row["location"]
    df["station_id"] = row["station_id"]
    return df[["location", "station_id", "climate_date", "high_f"]]


def main():
    frames = []
    for _, row in config.load_locations().iterrows():
        print(f"Fetching observations for {row['location']} ({row['station_id']})")
        frames.append(fetch_obs(row))

    df = pd.concat(frames, ignore_index=True)
    out = config.DATA_DIR / "obs_daily_high.parquet"
    df.to_parquet(out, index=False)

    print(f"saved {len(df):,} rows -> {out}")
    print(df.head(10))
    print(df.groupby("location")["high_f"].describe())


if __name__ == "__main__":
    main()



