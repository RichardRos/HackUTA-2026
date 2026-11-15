"""Nostos blend: bias-corrected, skill-weighted average of config.MODELS.

For every forecast of day D at lead L (issued 23:00 local standard time on D-L),
we look back at that same model/lead/location's errors over the 30 days ending
D-(L+1), keeping only official highs whose CLI report was published before
issued_at. From those errors we get:
  bias = mean error            -> subtract it to correct the forecast
  mae  = mean |error - bias|   -> how wrong the corrected model has recently been
Three blends:
  avg_raw            plain mean of the raw model forecasts
  avg_corrected      mean of the bias-corrected forecasts
  inv_mae_corrected  bias-corrected forecasts weighted by 1/mae
The variant is chosen on a 60-day validation window; the final 60 days are test.
ncep_nbm_conus (NBM) is only ever a competitor, never an input.
"""
import pandas as pd

import config
from score import naive_baseline

WINDOW_DAYS = 30          # look-back length for bias / mae
MIN_DAYS = 10             # need at least this many published days to trust the stats
MAE_FLOOR = 0.5           # stops one lucky model from getting near-infinite weight
VAL_DAYS = 60
TEST_DAYS = 60
VARIANTS = ["avg_raw", "avg_corrected", "inv_mae_corrected"]
KEYS = ["location", "model", "lead", "climate_date"]
DAY_KEYS = ["location", "lead", "climate_date"]


def load():
    fc = pd.read_parquet(config.DATA_DIR / "forecasts_daily_high.parquet")
    obs = pd.read_parquet(config.DATA_DIR / "obs_daily_high.parquet")
    fc["climate_date"] = pd.to_datetime(fc["climate_date"])
    obs["climate_date"] = pd.to_datetime(obs["climate_date"])
    return fc, obs


def input_forecasts(fc: pd.DataFrame) -> pd.DataFrame:
    inputs = fc[fc["model"].isin(config.MODELS)].copy()
    assert not set(inputs["model"]) & set(config.BENCHMARK_MODELS), "benchmark leaked into inputs"
    return inputs


def window_stats(fc: pd.DataFrame, obs: pd.DataFrame, *, gap_days: int = 1,
                 respect_published: bool = True) -> pd.DataFrame:
    """Per forecast row: bias/mae from days up to D-(lead+gap_days) published by issued_at.

    gap_days / respect_published exist only so the tests can build a deliberately
    leaky version and prove the leak test catches it. Production uses the defaults.
    """
    errors = fc.merge(obs[["location", "climate_date", "high_f", "published_at"]]
                      .rename(columns={"high_f": "obs_f"}), on=["location", "climate_date"])
    errors["error"] = errors["high_f"] - errors["obs_f"]
    errors = errors[KEYS + ["error", "published_at"]].rename(columns={"climate_date": "day"})

    offsets = pd.DataFrame({"k": range(WINDOW_DAYS)})
    out = []
    for _, targets in fc[KEYS + ["issued_at"]].groupby("location"):  # per city keeps memory small
        win = targets.merge(offsets, how="cross")
        win["day"] = win["climate_date"] - pd.to_timedelta(win["lead"] + gap_days + win["k"], unit="D")
        win = win.merge(errors, on=["location", "model", "lead", "day"])
        if respect_published:
            win = win[win["published_at"] <= win["issued_at"]]
        win["bias"] = win.groupby(KEYS)["error"].transform("mean")
        win["abs_dev"] = (win["error"] - win["bias"]).abs()
        out.append(win.groupby(KEYS).agg(
            n_days=("error", "size"),
            bias=("error", "mean"),
            mae=("abs_dev", "mean"),
            last_used_day=("day", "max"),
            max_used_published_at=("published_at", "max"),
        ).reset_index())
    stats = pd.concat(out, ignore_index=True)
    stats = stats[stats["n_days"] >= MIN_DAYS]
    return fc.merge(stats, on=KEYS, how="left")


def weights(fs: pd.DataFrame) -> pd.Series:
    """Unnormalised inverse-MAE weight per model row.

    A model without enough history gets the median weight of the others that day.
    """
    mae = fs["mae"].fillna(fs.groupby(DAY_KEYS)["mae"].transform("median")).fillna(1.0)
    return 1.0 / mae.clip(lower=MAE_FLOOR)


def blend(fs: pd.DataFrame) -> pd.DataFrame:
    """Combine per-model rows (with stats) into one row per location/lead/day per variant."""
    d = fs.copy()
    # A model without enough history: no correction, and the median weight of the others.
    d["corrected"] = d["high_f"] - d["bias"].fillna(0.0)
    d["w"] = weights(d)
    d["wc"] = d["w"] * d["corrected"]
    out = d.groupby(DAY_KEYS).agg(
        issued_at=("issued_at", "first"),
        n_models=("model", "size"),
        n_corrected=("bias", "count"),
        avg_raw=("high_f", "mean"),
        avg_corrected=("corrected", "mean"),
        w_sum=("w", "sum"),
        wc_sum=("wc", "sum"),
    ).reset_index()
    out["inv_mae_corrected"] = out["wc_sum"] / out["w_sum"]
    return out.drop(columns=["w_sum", "wc_sum"])


def benchmark_corrected(fc: pd.DataFrame, obs: pd.DataFrame) -> pd.DataFrame:
    """Competitor only: NBM minus its own rolling bias, same window and lag rules as Nostos."""
    nbm = fc[fc["model"].isin(config.BENCHMARK_MODELS)]
    stats = window_stats(nbm, obs)
    stats["high_f"] = stats["high_f"] - stats["bias"].fillna(0.0)
    stats["model"] = "nbm_corrected"
    return stats


def metrics(scored: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    return (scored.groupby(by).agg(
        days=("error", "size"),
        mae_f=("error", lambda e: e.abs().mean()),
        bias_f=("error", "mean"),
        within_2f_pct=("error", lambda e: (e.abs() <= 2).mean() * 100),
    ).round(2).reset_index())


def splits():
    """Fixed validation / test windows, anchored on config.TEST_END (not END_DATE)."""
    end = pd.Timestamp(config.TEST_END)
    test_start = end - pd.Timedelta(days=TEST_DAYS - 1)
    val_start = test_start - pd.Timedelta(days=VAL_DAYS)
    return val_start, test_start, end


def main():
    fc, obs = load()
    inputs = input_forecasts(fc)
    blended = blend(window_stats(inputs, obs))
    truth = obs[["location", "climate_date", "high_f"]].rename(columns={"high_f": "observed_f"})
    val_start, test_start, end = splits()
    print(f"validation {val_start.date()}..{(test_start - pd.Timedelta(days=1)).date()}, "
          f"test {test_start.date()}..{end.date()}")

    # --- choose the variant on validation only ---
    val = blended[(blended["climate_date"] >= val_start) & (blended["climate_date"] < test_start)]
    val = val.melt(id_vars=DAY_KEYS, value_vars=VARIANTS, var_name="model", value_name="high_f")
    val = val.merge(truth, on=["location", "climate_date"])
    val["error"] = val["high_f"] - val["observed_f"]
    val_board = pd.concat([metrics(val, ["lead", "model"]),
                           metrics(val, ["model"]).assign(lead="all")], ignore_index=True)
    val_board.to_csv(config.DATA_DIR / "val_scores.csv", index=False)
    print("\n=== Validation MAE (°F) by variant ===")
    print(val_board.pivot(index="model", columns="lead", values="mae_f").to_string())
    chosen = val_board[val_board["lead"] == "all"].sort_values("mae_f").iloc[0]["model"]
    print(f"\nchosen variant: {chosen}")

    blended["variant"] = chosen
    blended["high_f"] = blended[chosen]
    blended["climate_date"] = blended["climate_date"].dt.date
    blended.to_parquet(config.DATA_DIR / "nostos_daily_high.parquet", index=False)
    blended["climate_date"] = pd.to_datetime(blended["climate_date"])

    # --- fairness competitor: NBM with the identical rolling bias correction ---
    nbm_corrected = benchmark_corrected(fc, obs)

    # --- test leaderboard, scored once, on days every competitor has ---
    competitors = pd.concat([
        blended[DAY_KEYS + ["high_f"]].assign(model="nostos"),
        nbm_corrected[DAY_KEYS + ["model", "high_f"]],
        fc[DAY_KEYS + ["model", "high_f"]],
        naive_baseline(obs),
    ], ignore_index=True)
    competitors = competitors[(competitors["climate_date"] >= test_start) & (competitors["climate_date"] <= end)]
    competitors = competitors.merge(truth, on=["location", "climate_date"]).dropna(subset=["high_f"])
    n_needed = competitors["model"].nunique()
    common = competitors.groupby(DAY_KEYS)["model"].transform("nunique") == n_needed
    test = competitors[common].copy()
    test["error"] = test["high_f"] - test["observed_f"]

    board = metrics(test, ["lead", "model"]).sort_values(["lead", "mae_f"])
    nbm = board[board["model"] == "ncep_nbm_conus"].set_index("lead")["mae_f"]
    board["vs_nbm_f"] = (board["mae_f"] - board["lead"].map(nbm)).round(2)
    board.to_csv(config.DATA_DIR / "test_scores.csv", index=False)
    for lead, table in board.groupby("lead"):
        print(f"\n=== TEST lead {lead} ===")
        print(table.drop(columns="lead").to_string(index=False))
    print(f"\nsaved -> {config.DATA_DIR / 'nostos_daily_high.parquet'}, test_scores.csv, val_scores.csv")


if __name__ == "__main__":
    main()
