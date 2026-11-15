"""Leakage tests: no bias/mae may use an official high not published at issued_at."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import blend  # noqa: E402
import config  # noqa: E402

OFFSET_H = -6


def synthetic(n_days: int = 90, seed: int = 0):
    """One city, two models, leads 1-3; CLI published ~1h after the local day ends,
    plus traps: a few reports published days late and some published before the day ended."""
    rng = np.random.default_rng(seed)
    days = pd.date_range("2025-03-01", periods=n_days, freq="D")
    truth = 70 + 10 * np.sin(np.arange(n_days) / 9) + rng.normal(0, 3, n_days)
    day_end = days.tz_localize("UTC") + pd.Timedelta(days=1) - pd.Timedelta(hours=OFFSET_H)
    published = day_end + pd.Timedelta(hours=1)
    published = published.where(np.arange(n_days) % 17 != 5, day_end + pd.Timedelta(hours=60))  # late
    published = published.where(np.arange(n_days) % 13 != 3, day_end - pd.Timedelta(hours=7))   # early
    obs = pd.DataFrame({"location": "testville", "climate_date": days, "high_f": truth,
                        "published_at": published})
    rows = []
    for model, bias in [("ecmwf_ifs025", -2.0), ("gfs_seamless", 1.0)]:
        for lead in config.LEADS:
            day_start = days.tz_localize("UTC") - pd.Timedelta(hours=OFFSET_H)
            rows.append(pd.DataFrame({
                "location": "testville", "model": model, "lead": lead, "climate_date": days,
                "issued_at": day_start - pd.Timedelta(days=lead - 1) - pd.Timedelta(hours=1),
                "high_f": truth + bias + rng.normal(0, 1 + lead, n_days),
            }))
    return pd.concat(rows, ignore_index=True), obs


def assert_no_leak(fc, obs, targets, **kwargs):
    """For each target, corrupt every obs NOT available at its issued_at (day after
    D-(lead+1), or published after issued_at). Its bias and mae must not change."""
    base = blend.window_stats(fc, obs, **kwargs).set_index(blend.KEYS)
    for t in targets.itertuples():
        cutoff_day = t.climate_date - pd.Timedelta(days=t.lead + 1)
        unavailable = (obs["location"] == t.location) & (
            (obs["climate_date"] > cutoff_day) | (obs["published_at"] > t.issued_at))
        corrupted = obs.copy()
        corrupted.loc[unavailable, "high_f"] += 500.0
        fc_loc = fc[fc["location"] == t.location]
        again = blend.window_stats(fc_loc, corrupted[corrupted["location"] == t.location],
                                   **kwargs).set_index(blend.KEYS)
        key = (t.location, t.model, t.lead, t.climate_date)
        for col in ["bias", "mae"]:
            a, b = base.at[key, col], again.at[key, col]
            assert (pd.isna(a) and pd.isna(b)) or np.isclose(a, b), (
                f"{col} for {key} (issued {t.issued_at}) changed {a} -> {b}: "
                "it used an official high not yet published")


def test_synthetic_no_leak():
    fc, obs = synthetic()
    assert_no_leak(fc, obs, fc[fc["climate_date"] >= "2025-04-10"])


def test_leak_test_catches_a_leak():
    """Using D-lead (whose CLI can appear at ~5pm, before issued_at) must be caught."""
    fc, obs = synthetic()
    with pytest.raises(AssertionError, match="not yet published"):
        assert_no_leak(fc, obs, fc[fc["climate_date"] >= "2025-04-10"], gap_days=0)
    with pytest.raises(AssertionError, match="not yet published"):
        assert_no_leak(fc, obs, fc[fc["climate_date"] >= "2025-04-10"], respect_published=False)


REAL = (config.DATA_DIR / "forecasts_daily_high.parquet").exists() and \
       (config.DATA_DIR / "obs_daily_high.parquet").exists()


@pytest.fixture(scope="module")
def real():
    if not REAL:
        pytest.skip("pipeline data not present")
    fc, obs = blend.load()
    return blend.input_forecasts(fc), obs


def test_real_every_bias_uses_only_published_days(real):
    fc, obs = real
    stats = blend.window_stats(fc, obs).dropna(subset=["bias"])
    assert len(stats) > 0.9 * len(fc)
    cutoff = stats["climate_date"] - pd.to_timedelta(stats["lead"] + 1, unit="D")
    assert (stats["last_used_day"] <= cutoff).all()
    assert (stats["max_used_published_at"] <= stats["issued_at"]).all()


def test_real_perturbation_no_leak(real):
    fc, obs = real
    targets = fc.sample(25, random_state=1)
    assert_no_leak(fc, obs, targets)


def test_benchmark_never_an_input(real):
    fc, _ = real
    assert set(fc["model"]) == set(config.MODELS)


def test_nbm_corrected_no_leak():
    """The fairness competitor uses the same window_stats, so it must obey the same rule."""
    if not REAL:
        pytest.skip("pipeline data not present")
    fc, obs = blend.load()
    nbm = fc[fc["model"].isin(config.BENCHMARK_MODELS)]
    stats = blend.window_stats(nbm, obs).dropna(subset=["bias"])
    cutoff = stats["climate_date"] - pd.to_timedelta(stats["lead"] + 1, unit="D")
    assert (stats["last_used_day"] <= cutoff).all()
    assert (stats["max_used_published_at"] <= stats["issued_at"]).all()
    assert_no_leak(nbm, obs, nbm.sample(10, random_state=2))
