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

def fetch_obs(row: pd.Series, start_date: pd.Timestamp) -> pd.DataFrame:
    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(config.END_DATE).date()

    records = []
    for year in range(start.year, end.year + 1):
        records += get_year(row["station_id"], year)
        time.sleep(1)

    df = pd.DataFrame(records)
    if df.empty:
            raise RuntimeError(f"No climate reports for {row['station_id']}; check the station ID")
    if not {"valid", "high", "product"}.issubset(df.columns):
            raise RuntimeError(f"Unexpected columns from IEM: {list(df.columns)}")


    df["climate_date"] = pd.to_datetime(df["valid"]).dt.date

    # CLI product IDs start with the UTC issuance time, e.g. 202611130746-KEWX-CDUS44-CLIAUS
    df["published_at"] = pd.to_datetime(df["product"].str[:12], format="%Y%m%d%H%M", utc=True)
    df["high_f"] = pd.to_numeric(df["high"], errors="coerce")
    df = df.dropna(subset=["high_f"])
    df = df[(df["climate_date"] >= start) & (df["climate_date"] <= end)]
    df = df.drop_duplicates(subset=["climate_date"], keep="last")

    df["location"] = row["location"]
    df["station_id"] = row["station_id"]
    return df[["location", "station_id", "climate_date", "high_f", "published_at"]]


def main():
    out = config.DATA_DIR / "obs_daily_high.parquet"
    cache = pd.read_parquet(out) if out.exists() and not config.FULL_REFRESH else None
    start = config.incremental_start(pd.to_datetime(cache["climate_date"]).max() if cache is not None else None)

    frames = []
    for _, row in config.load_locations().iterrows():
        print(f"Fetching observations for {row['location']} ({row['station_id']}) {start:%Y-%m-%d}..{config.END_DATE}")
        frames.append(fetch_obs(row, start))

    new = pd.concat(frames, ignore_index=True)
    df = config.merge_cache(cache, new, ["location", "climate_date"], new["climate_date"])
    df.to_parquet(out, index=False)

    print(f"fetched {len(new):,} rows, cache now {len(df):,} rows "
          f"({df['climate_date'].min()}..{df['climate_date'].max()}) -> {out}")
    print(new.groupby("location")["climate_date"].max().value_counts().rename("cities by last day"))


if __name__ == "__main__":
    main()
