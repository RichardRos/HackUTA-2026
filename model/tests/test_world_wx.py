"""Global Nostos (world_wx): parsing, blend, day boundaries, no leakage. No network: HTTP is mocked."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
import world_wx as w  # noqa: E402

NOW = pd.Timestamp("2026-11-15T09:10Z")


@pytest.fixture(autouse=True)
def clear_cache():
    w._cache.clear()


def times(start, hours):
    return pd.date_range(start, periods=hours, freq="h", tz="UTC")


def iso(ts):
    return [t.strftime("%Y-%m-%dT%H:%M") for t in ts]


# ---------------------------------------------------------------- parsing

def test_parse_multiple_models_and_leads():
    block = {"hourly": {
        "time": ["2026-11-15T00:00", "2026-11-15T01:00"],
        "temperature_2m_gfs_seamless": [10.0, 11.0],
        "temperature_2m_previous_day2_icon_seamless": [9.0, None],
        "wind_gusts_10m_previous_day1_gem_seamless": [12.5, 13.0],
        "wind_speed_10m_ecmwf_ifs025": [3.0, 4.0],
    }}
    fc = w.parse_forecasts(block)
    got = set(map(tuple, fc[["var", "model", "lead"]].drop_duplicates().values.tolist()))
    assert got == {("temperature_2m", "gfs_seamless", 0), ("temperature_2m", "icon_seamless", 2),
                   ("wind_gusts_10m", "gem_seamless", 1), ("wind_speed_10m", "ecmwf_ifs025", 0)}
    assert len(fc) == 7  # the null is dropped
    assert str(fc["time"].dt.tz) == "UTC"


def test_parse_rejects_unknown_columns():
    with pytest.raises(ValueError):
        w.parse_forecasts({"hourly": {"time": ["2026-11-15T00:00"], "temperature_2m_some_model": [1.0]}})
    with pytest.raises(ValueError):
        w.parse_forecasts({"hourly": {"time": ["2026-11-15T00:00"], "dew_point_2m_gfs_seamless": [1.0]}})


def test_single_point_response_becomes_list_and_is_cached():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"current": {"time": "2026-11-15T09:00"}}
    with patch.object(w.requests, "get", return_value=resp) as get:
        assert w.fetch_current([(48.85, 2.35)]) == [{"current": {"time": "2026-11-15T09:00"}}]
        w.fetch_current([(48.85, 2.35)])
    assert get.call_count == 1
    params = get.call_args.kwargs["params"]
    assert params["timezone"] == "GMT" and params["wind_speed_unit"] == "ms"
    assert get.call_args.kwargs["timeout"] == 15


def test_truth_pins_era5_and_lags_five_days():
    resp = MagicMock(status_code=200)
    resp.json.return_value = [{"hourly": {}}, {"hourly": {}}]
    with patch.object(w.requests, "get", return_value=resp) as get:
        w.fetch_truth([(1.0, 2.0), (3.0, 4.0)], NOW)
    p = get.call_args.kwargs["params"]
    assert p["models"] == "era5" and p["end_date"] == "2026-11-10"
    assert p["latitude"] == "1.0,3.0" and p["longitude"] == "2.0,4.0"


def test_http_error_is_readable_runtime_error():
    resp = MagicMock(status_code=400, text='{"error":true,"reason":"Cannot initialize"}')
    with patch.object(w.requests, "get", return_value=resp):
        with pytest.raises(RuntimeError, match="Open-Meteo error 400"):
            w.fetch_current([(0.0, 0.0)])


# ---------------------------------------------------------------- standard-time days

@pytest.mark.parametrize("lon, offset", [(-97.0, -6), (2.35, 0), (139.7, 9), (-180, -12), (180, 12), (-7.4, 0)])
def test_std_offset(lon, offset):
    assert w.std_offset_hours(lon) == offset


def test_local_day_boundaries_utc_minus_6():
    # 48 hours from 06:00Z: exactly two local days (00:00-23:00 at UTC-6), one value each.
    ts = times("2026-11-01T06:00", 48)
    vals = np.r_[np.full(24, 1.0), np.full(24, 2.0)]
    vals[23] = 9.0   # 05:00Z on Nov 2 = 23:00 local Nov 1 -> belongs to Nov 1
    vals[24] = 5.0   # 06:00Z on Nov 2 = 00:00 local Nov 2
    df = pd.DataFrame({"time": ts, "value": vals})
    d = w.local_daily_max(df, -6, [])
    assert d["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-11-01", "2026-11-02"]
    assert d["high"].tolist() == [9.0, 5.0]
    # Shift by one hour: both local days are now incomplete and dropped.
    assert w.local_daily_max(df.iloc[1:], -6, []).shape[0] == 1


# ---------------------------------------------------------------- the blend

OFFSET = 0
TRUTH_START = pd.Timestamp("2026-10-09T00:00Z")


def synthetic(seed=0):
    """Truth = smooth diurnal cycle + daily noise. Two models, leads 1-3:
    ecmwf = truth + 3 °C (biased but steady), gfs = truth + N(0, 2.5) per day (unbiased but noisy).
    Truth is known through Nov 9 (ERA5 lag); the live run covers Nov 15 .. Nov 19."""
    rng = np.random.default_rng(seed)
    ts = times(TRUTH_START, 24 * 41)  # Oct 9 .. Nov 18
    day = (ts - TRUTH_START).days
    daily_noise = rng.normal(0, 2, day.max() + 1)
    temp = 12 + 5 * np.sin(np.arange(len(ts)) / 24 * 2 * np.pi) + daily_noise[day]
    truth_all = pd.DataFrame({"time": ts, "var": w.TEMP, "value": temp})
    rows = []
    gfs_noise = rng.normal(0, 2.5, day.max() + 1)
    for model, err in [("ecmwf_ifs025", 3.0 + rng.normal(0, 0.3, day.max() + 1)), ("gfs_seamless", gfs_noise)]:
        for lead in [0, *config.LEADS]:
            rows.append(pd.DataFrame({"time": ts, "var": w.TEMP, "model": model, "lead": lead,
                                      "value": temp + err[day]}))
    fc = pd.concat(rows, ignore_index=True)
    truth = truth_all[truth_all["time"] < pd.Timestamp("2026-11-10T00:00Z")]
    return fc, truth, truth_all


def test_blend_beats_biased_and_noisy_models():
    fc, truth, truth_all = synthetic()
    stats = w.daily_stats(fc, truth, OFFSET, NOW)
    s1 = stats[stats["lead"] == 1].set_index("model")
    assert s1.loc["ecmwf_ifs025", "bias_f"] == pytest.approx(3.0 * 1.8, abs=0.3)
    assert s1.loc["ecmwf_ifs025", "mae_f"] < s1.loc["gfs_seamless", "mae_f"]
    assert (stats.dropna(subset=["bias_f"])["n_days"] == 30).all()

    daily = w.daily_forecast(fc, stats, OFFSET, NOW)
    obs = w.local_daily_max(truth_all, OFFSET, []).set_index("date")["high"].pipe(w.c_to_f)
    daily["obs_f"] = daily["climate_date"].map(obs)
    nostos_mae = (daily.groupby("lead")[["nostos_f", "obs_f"]].first().diff(axis=1)["obs_f"].abs()).mean()
    raw_mae = (daily["high_f"] - daily["obs_f"]).abs().groupby(daily["model"]).mean()
    assert sorted(daily["lead"].unique()) == [1, 2, 3]
    assert nostos_mae < raw_mae["ecmwf_ifs025"] and nostos_mae < raw_mae["gfs_seamless"]
    w1 = daily[daily["lead"] == 1].set_index("model")["weight"]
    assert w1["ecmwf_ifs025"] > w1["gfs_seamless"] and w1.sum() == pytest.approx(1.0)


def test_no_truth_after_now_is_used():
    fc, _, truth_all = synthetic()
    now = pd.Timestamp("2026-11-05T12:00Z")  # pretend truth for later days leaked into the input
    stats = w.daily_stats(fc, truth_all, OFFSET, now)
    used = stats.dropna(subset=["bias_f"])
    assert (used["last_used_day"].map(lambda d: w.day_end_utc(d, OFFSET)) <= now).all()
    h = w.hourly_stats(fc, truth_all, now)
    assert (h.dropna(subset=["last_used_time"])["last_used_time"] < now).all()


def test_daily_forecast_targets_are_future_local_days():
    fc, truth, _ = synthetic()
    daily = w.daily_forecast(fc, w.daily_stats(fc, truth, OFFSET, NOW), OFFSET, NOW)
    starts = daily["climate_date"].map(lambda d: w.day_end_utc(d, OFFSET) - pd.Timedelta(days=1))
    assert (starts > NOW).all()


def test_gusts_use_only_gust_models_and_precip_is_not_bias_corrected():
    ts = times(TRUTH_START, 24 * 30)
    rows = [pd.DataFrame({"time": ts, "var": var, "model": m, "lead": 1, "value": 5.0})
            for var in (w.GUST, w.PRECIP) for m in config.MODELS]
    fc = pd.concat(rows, ignore_index=True)
    truth = pd.concat([pd.DataFrame({"time": ts, "var": v, "value": 4.0}) for v in (w.GUST, w.PRECIP)])
    h = w.hourly_stats(fc, truth, NOW)
    g = h[(h["var"] == w.GUST) & (h["lead"] == 1)]
    assert set(g["model"]) == set(w.GUST_MODELS)
    assert g["bias"].tolist() == pytest.approx([1.0] * 3)
    p = h[(h["var"] == w.PRECIP) & (h["lead"] == 1)]
    assert p["bias"].isna().all() and p["mae"].tolist() == pytest.approx([1.0] * 5)


# ---------------------------------------------------------------- end to end with mocked HTTP

def fake_get(url, params, timeout):
    """Mocked Open-Meteo: synthetic() served in the wire format of each API."""
    fc, truth, _ = synthetic()
    resp = MagicMock(status_code=200)
    if url == w.CURRENT_URL:
        resp.json.return_value = {"current": {"time": "2026-11-15T09:00", "interval": 900, "temperature_2m": 9.1,
                                              "wind_speed_10m": 3.4, "wind_gusts_10m": 7.5, "precipitation": 0.0,
                                              "weather_code": 3}}
    elif url == w.PREVIOUS_RUNS_URL:
        h = {"time": iso(fc["time"].drop_duplicates())}
        for (model, lead), g in fc.groupby(["model", "lead"]):
            h[f"temperature_2m{'' if lead == 0 else f'_previous_day{lead}'}_{model}"] = g["value"].tolist()
        resp.json.return_value = {"hourly": h}
    else:
        resp.json.return_value = {"hourly": {"time": iso(truth["time"]), "temperature_2m": truth["value"].tolist()}}
    return resp


def test_point_end_to_end():
    with patch.object(w.requests, "get", side_effect=fake_get):
        out = w.point(48.85, 2.35, now=NOW)
    assert [d["lead_days"] for d in out["daily"]] == [1, 2, 3]
    assert out["daily"][0]["date"] == "2026-11-16" and out["issued_at"] == "2026-11-15T09:10:00Z"
    assert out["rankings"][0]["model"] == "ecmwf_ifs025" and out["rankings"][0]["truth"] == "ERA5"
    assert out["hourly_rankings"]["temperature_2m"]["truth"] == "ERA5"
    assert out["nearest_us_station"] is None and out["current"]["time"] == "2026-11-15T09:00Z"


def test_point_near_trained_city_flags_station():
    with patch.object(w.requests, "get", side_effect=fake_get):
        out = w.point(30.27, -97.74, now=NOW)  # downtown Austin, ~10 km from KAUS
    assert out["nearest_us_station"]["station_id"] == "KAUS"


def test_router_errors():
    pytest.importorskip("fastapi", reason="API deps not in the model venv")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from api import wx_world

    app = FastAPI()
    app.include_router(wx_world.make_router(lambda: "user"))
    client = TestClient(app)
    resp = MagicMock(status_code=503, text="maintenance")
    with patch.object(w.requests, "get", return_value=resp):
        r = client.get("/api/wx/world/point", params={"lat": 1, "lon": 2})
    assert r.status_code == 502 and "Open-Meteo error 503" in r.json()["detail"]
    assert client.get("/api/wx/world/point", params={"lat": 95, "lon": 2}).status_code == 422
    with patch.object(w.requests, "get", side_effect=fake_get):
        assert client.get("/api/wx/world/point", params={"lat": 48.85, "lon": 2.35}).status_code == 200
