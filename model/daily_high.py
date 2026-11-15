import pandas as pd 
import config

def main():
    fc = pd.read_parquet(config.DATA_DIR / "forecasts.parquet")
    locations = config.load_locations()[["location", "std_utc_offset_hours"]]
    fc = fc.merge(locations, on="location")

    offset = pd.to_timedelta(fc["std_utc_offset_hours"], unit="h")
    fc["climate_date"] = (fc["target_time"] + offset).dt.date

    daily = (
        fc.groupby(["location", "model", "lead", "climate_date", "std_utc_offset_hours"], as_index=False)
        .agg(high_f=("temp_f", "max"), hours=("temp_f", "count"))
    )

    daily = daily[daily["hours"] == 24].copy()

    day_start = pd.to_datetime(daily["climate_date"]).dt.tz_localize("UTC") - pd.to_timedelta(daily["std_utc_offset_hours"], unit="h")
    # issued at 23:00 local standard time on day D-lead: start of climate day - (lead-1) days - 1 hour
    daily["issued_at"] = day_start - pd.to_timedelta(daily["lead"] - 1, unit="D") - pd.Timedelta(hours=1)
    daily = daily[["location", "model", "lead", "climate_date", "issued_at", "high_f"]]
    out = config.DATA_DIR / "forecasts_daily_high.parquet"
    daily.to_parquet(out, index=False)

    print(f"saved {len(daily):,} rows -> {out}")
    print(daily.groupby(["model", "lead"]).size().unstack()) 
    print(daily.head())


if __name__ == "__main__":
    main()
    

    