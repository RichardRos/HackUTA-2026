import pandas as pd 
import config

def naive_baseline(obs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for lead in config.LEADS:
        shifted = obs[["location", "climate_date", "high_f"]].copy()
        shifted["climate_date"] = shifted["climate_date"] + pd.Timedelta(days=lead + 1)
        shifted["lead"] = lead
        shifted["model"] = "naive_persistence"
        rows.append(shifted)
    return pd.concat(rows, ignore_index=True)


def main():
    fc = pd.read_parquet(config.DATA_DIR / "forecasts_daily_high.parquet")
    obs = pd.read_parquet(config.DATA_DIR / "obs_daily_high.parquet")

    fc["climate_date"] = pd.to_datetime(fc["climate_date"])
    obs["climate_date"] = pd.to_datetime(obs["climate_date"])

    forcasts = pd.concat([fc[["location", "model", "lead", "climate_date", "high_f"]], naive_baseline(obs)], ignore_index=True,)
    scored = forcasts.merge(obs[["location", "climate_date", "high_f"]].rename(columns={"high_f": "observed_f"}),
        on=["location", "climate_date"],)
    scored["error"] = scored["high_f"] - scored["observed_f"]


    board = (
        scored.groupby(["lead", "model"])
        .agg(
            days=("error", "size"),
            mae_f=("error", lambda e: e.abs().mean()),
            bias_f=("error", "mean"),
            within_2f_pct=("error", lambda e: (e.abs() <= 2).mean() * 100),
        )
        .round(2)
        .reset_index()
        .sort_values(["lead", "mae_f"])
    )

    out = config.DATA_DIR / "scores.csv"
    board.to_csv(out, index=False)

    for lead, table in board.groupby("lead"):
        label = "night before" if lead == 1 else f"{lead} days ahead"
        print(f"\n=== Lead {lead} ({label}) ===")
        print(table.drop(columns="lead").to_string(index=False))
    print(f"\nsaved -> {out}")

if __name__ == "__main__":
    main()


    
    
    
