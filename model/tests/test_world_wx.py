"""Global Nostos (world_wx): parsing, blend, day boundaries, no leakage. No network: HTTP is mocked."""
import re
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


def test_past_runs_batch_keep_order_and_cache_per_point():
    calls = []

    def fake(url, params, timeout):
        calls.append(params["latitude"].split(","))
        resp = MagicMock(status_code=200)
        blocks = [{"lat": float(x)} for x in params["latitude"].split(",")]
        resp.json.return_value = blocks if len(blocks) > 1 else blocks[0]
        return resp

    pts = [(float(i), 0.0) for i in range(30)]
    cols = {w.TEMP: [1]}
    with patch.object(w.requests, "get", side_effect=fake):
        assert [b["lat"] for b in w.fetch_past_runs(pts, NOW, cols)] == [float(i) for i in range(30)]
        assert sorted(len(c) for c in calls) == [6, 12, 12]          # batches of BATCH_POINTS
        cost = w.Cost()
        out = w.fetch_past_runs([(3.0, 0.0), (50.0, 1.0), (3.0, 0.0)], NOW, cols, cost)  # one new point
    assert calls[-1] == ["50.0"] and [b["lat"] for b in out] == [3.0, 50.0, 3.0]
    assert cost.calls[0]["points_fetched"] == 1 and cost.calls[0]["points_cached"] == 1


def test_call_weight_matches_open_meteo_examples():
    assert w.call_weight(15, 14) == pytest.approx(1.5)   # "2 weeks with 15 variables = 1.5"
    assert w.call_weight(15, 28) == pytest.approx(3.0)   # "4 weeks = 3.0"
    assert w.call_weight(5, 3) == 1.0


def test_snap_quarter_degree():
    assert w.snap([(32.78, -96.80), (31.55, -97.15), (30.12, -97.88)], 0.25) == \
        [(32.75, -96.75), (31.5, -97.25), (30.0, -98.0)]


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
    assert p["models"] == "era5" and p["end_date"] == "2026-11-10" and p["start_date"] == "2026-10-09"
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

def requested_hours(params):
    start = pd.Timestamp(params["start_date"], tz="UTC")
    return times(start, 24 * ((pd.Timestamp(params["end_date"], tz="UTC") - start).days + 1))


def wire(values: dict, ts) -> dict:
    """{column: pd.Series indexed by time} -> Open-Meteo hourly block over ts (missing -> null)."""
    h = {"time": iso(ts)}
    for col, ser in values.items():
        h[col] = [None if pd.isna(v) else float(v) for v in ser.reindex(ts)]
    return {"hourly": h}


def serve(url, params, forecast_for, truth_for):
    """Mocked Open-Meteo that honours hourly=, models=, start/end_date and multiple coordinates.
    forecast_for(lat, lon) -> long fc frame; truth_for(lat, lon) -> long truth frame."""
    lats = [float(x) for x in params["latitude"].split(",")]
    lons = [float(x) for x in params["longitude"].split(",")]
    blocks = []
    for lat, lon in zip(lats, lons):
        if url == w.CURRENT_URL:
            blocks.append({"current": {"time": "2026-11-15T09:00", "interval": 900, "temperature_2m": 9.1,
                                       "wind_speed_10m": 3.4, "wind_gusts_10m": 7.5, "precipitation": 0.0,
                                       "weather_code": 3}})
            continue
        ts = requested_hours(params)
        if url == w.PREVIOUS_RUNS_URL:
            fc = forecast_for(lat, lon).set_index(["var", "model", "lead"]).sort_index()
            cols = {}
            for col in params["hourly"].split(","):
                var, _, prev = col.partition("_previous_day")
                for m in params["models"].split(","):
                    key = (var, m, int(prev) if prev else 0)
                    cols[f"{col}_{m}"] = (fc.loc[key].set_index("time")["value"] if key in fc.index
                                          else pd.Series(dtype=float))
            blocks.append(wire(cols, ts))
        else:
            tr = truth_for(lat, lon)
            blocks.append(wire({v: tr[tr["var"] == v].set_index("time")["value"]
                                for v in params["hourly"].split(",")}, ts))
    resp = MagicMock(status_code=200)
    resp.json.return_value = blocks if len(blocks) > 1 else blocks[0]
    return resp


def fake_get(url, params, timeout):
    """Mocked Open-Meteo: synthetic() served in the wire format of each API."""
    fc, truth, _ = synthetic()
    return serve(url, params, lambda *_: fc, lambda *_: truth)


def test_point_end_to_end():
    with patch.object(w.requests, "get", side_effect=fake_get):
        out = w.point(48.85, 2.35, now=NOW)
    assert [d["lead_days"] for d in out["daily"]] == [1, 2, 3]
    assert out["daily"][0]["date"] == "2026-11-16" and out["issued_at"] == "2026-11-15T09:10:00Z"
    assert out["rankings"][0]["model"] == "ecmwf_ifs025" and out["rankings"][0]["truth"] == "ERA5"
    assert out["hourly_rankings"]["temperature_2m"]["truth"] == "ERA5"
    assert out["nearest_us_station"] is None and out["current"]["time"] == "2026-11-15T09:00Z"
    assert out["openmeteo_cost"]["weighted_calls_est"] > 0


def test_point_requests_only_the_columns_it_uses():
    seen = []

    def spy(url, params, timeout):
        seen.append((url, params))
        return fake_get(url, params, timeout)

    with patch.object(w.requests, "get", side_effect=spy):
        w.point(48.85, 2.35, now=NOW)
    runs = {p["hourly"]: p for u, p in seen if u == w.PREVIOUS_RUNS_URL}
    assert len(seen) == 4 and len(runs) == 2   # current, past runs, latest run, ERA5
    past = next(p for h, p in runs.items() if "previous_day" in h)
    assert set(past["hourly"].split(",")) == {
        "temperature_2m_previous_day1", "temperature_2m_previous_day2", "temperature_2m_previous_day3",
        "wind_speed_10m_previous_day1", "wind_gusts_10m_previous_day1", "precipitation_previous_day1"}
    assert past["end_date"] == "2026-11-10"
    latest = runs["temperature_2m"]
    assert (latest["start_date"], latest["end_date"]) == ("2026-11-15", "2026-11-19")


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


# ---------------------------------------------------------------- Odyssey routes

def test_densify_spacing_cap_and_endpoints():
    wp = [(32.78, -96.80), (31.55, -97.15), (30.27, -97.74)]
    pts, km = w.densify(wp)
    total = w.haversine_km(wp[0], wp[1]) + w.haversine_km(wp[1], wp[2])
    assert pts[0] == pytest.approx(wp[0]) and pts[-1] == pytest.approx(wp[-1])
    assert km[-1] == pytest.approx(total) and np.diff(km).max() <= 75 + 1e-9
    assert len(pts) == int(np.ceil(total / 75)) + 1
    # Sampled points lie on the path: consecutive haversine ≈ the along-path spacing.
    steps = [w.haversine_km(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    assert steps == pytest.approx(np.diff(km), rel=0.01)
    mid_pts, mid_km = w.densify([(0.0, 0.0), (0.0, 5.4)])    # ~600 km: 8 points, spacing widens to ~86 km
    assert len(mid_pts) == 8 and 75 < np.diff(mid_km).max() <= 100
    long_pts, long_km = w.densify([(0.0, 0.0), (0.0, 40.0)])  # ~4450 km: still 8 points (coarse)
    assert len(long_pts) == 8 and long_km[-1] == pytest.approx(w.haversine_km((0, 0), (0, 40)))
    named, _ = w.densify([(32.78, -96.80, "Dallas"), (30.27, -97.74, "Austin")])
    assert named[0] == pytest.approx((32.78, -96.80))
    with pytest.raises(ValueError):
        w.densify([(1.0, 1.0)])


def test_eta_and_road_hours():
    eta, road = w.point_hours(np.array([0.0, 40.0, 120.0, 160.0]), 80)
    assert eta.tolist() == [0.0, 0.5, 1.5, 2.0]
    assert road.tolist() == [0.25, 0.75, 0.75, 0.25] and road.sum() == pytest.approx(eta[-1])


def test_hazard_formula():
    df = pd.DataFrame([
        {"temp_c": 0.5, "wind_ms": 10.0, "gust_ms": 19.0, "precip_mm": 2.0, "temp_spread_c": 1.0, "wind_spread_ms": 2.0},
        {"temp_c": 38.0, "wind_ms": 3.0, "gust_ms": 5.0, "precip_mm": 0.0, "temp_spread_c": 0.0, "wind_spread_ms": 0.0},
    ])
    p = w.hazard_parts(df)
    assert p.loc[0].to_dict() == {"rain": 6.0, "wind": 2.0, "gust": 6.0, "ice": 5.0, "heat": 0.0, "uncertainty": 1.5}
    assert p.loc[1].sum() == pytest.approx(1.5)  # heat only: (38 − 35) × 0.5


def test_hourly_blend_uses_lead_by_hours_ahead_and_clips():
    hours = times(NOW.floor("h"), 80)
    fc = pd.concat([pd.DataFrame({"time": hours, "var": v, "model": "gfs_seamless", "lead": 0, "value": val})
                    for v, val in [(w.TEMP, 10.0), (w.WIND, 0.5), (w.GUST, 6.0), (w.PRECIP, 0.0)]])
    stats = pd.DataFrame([(v, "gfs_seamless", l, b, 1.0) for v, b in [(w.TEMP, 1.0), (w.WIND, 1.0), (w.GUST, 0.0)]
                          for l, b in [(1, b), (2, 2 * b), (3, 3 * b)]] + [(w.PRECIP, "gfs_seamless", l, np.nan, 1.0)
                                                                          for l in config.LEADS],
                         columns=["var", "model", "lead", "bias", "w"])
    out = w.hourly_blend(fc, stats, NOW)
    assert out.loc[NOW.floor("h") + pd.Timedelta(hours=5), "temp_c"] == 9.0     # lead 1
    assert out.loc[NOW.floor("h") + pd.Timedelta(hours=30), "temp_c"] == 8.0    # lead 2
    assert out.loc[NOW.floor("h") + pd.Timedelta(hours=60), "temp_c"] == 7.0    # lead 3
    assert (out["wind_ms"] == 0).all()   # 0.5 − bias clipped at 0


RAIN_BLOB = (31.06, -98.18)   # over the western route (Lampasas)
DALLAS, AUSTIN = (32.78, -96.80), (30.27, -97.74)
I35 = [DALLAS, (31.55, -97.15), AUSTIN]
WEST = [DALLAS, (32.44, -97.79), (*RAIN_BLOB, "Lampasas"), AUSTIN]


def weather(lat, lon, ts, rain_hours=None):
    """Same weather for every model and for ERA5 (zero bias), except a rain blob near RAIN_BLOB."""
    h = np.arange(len(ts))
    temp = 15 + 5 * np.sin(h / 24 * 2 * np.pi)
    rain = np.zeros(len(ts))
    if w.haversine_km((lat, lon), RAIN_BLOB) < 60:
        future = ts >= NOW.floor("h")
        if rain_hours is not None:
            future &= ts < NOW.floor("h") + pd.Timedelta(hours=rain_hours)
        rain[future] = 6.0
    return {w.TEMP: temp, w.WIND: np.full(len(ts), 3.0), w.GUST: np.full(len(ts), 6.0), w.PRECIP: rain}


WX_HOURS = times(NOW.normalize() - pd.Timedelta(days=w.PAST_DAYS), 24 * (w.PAST_DAYS + w.FORECAST_DAYS))


def route_forecast(lat, lon, rain_hours=None):
    wx = weather(lat, lon, WX_HOURS, rain_hours)
    rows = []
    for m in config.MODELS:
        for lead in [0, *config.LEADS]:
            for v in w.VARS:
                if v == w.GUST and (m == "jma_seamless" or (m == "ecmwf_ifs025" and lead)):
                    continue  # the real gaps
                rows.append(pd.DataFrame({"time": WX_HOURS, "var": v, "model": m, "lead": lead, "value": wx[v]}))
    return pd.concat(rows, ignore_index=True)


def route_truth(lat, lon):
    ts = WX_HOURS[WX_HOURS < NOW.normalize() - pd.Timedelta(days=w.ERA5_LAG_DAYS)]
    wx = weather(lat, lon, ts)
    return pd.concat([pd.DataFrame({"time": ts, "var": v, "value": wx[v]}) for v in w.VARS], ignore_index=True)


def make_fake_api(rain_hours=None, calls=None):
    def get(url, params, timeout):
        if calls is not None:
            calls.append((url, params))
        return serve(url, params, lambda la, lo: route_forecast(la, lo, rain_hours), route_truth)
    return get


def run_routes(rain_hours=None, calls=None, **kw):
    req = [{"name": "I-35", "waypoints": I35}, {"name": "West", "waypoints": WEST}]
    with patch.object(w.requests, "get", side_effect=make_fake_api(rain_hours, calls)):
        return w.routes(req, now=NOW, **{"depart_at": pd.Timestamp("2026-11-15T15:00Z"), **kw})


def test_route_through_rain_blob_ranks_last_with_one_call_per_api():
    calls = []
    out = run_routes(calls=calls)
    assert [r["route"] for r in out["routes"]] == ["I-35", "West"]
    urls = [u for u, _ in calls]
    assert urls.count(w.PREVIOUS_RUNS_URL) == 2 and urls.count(w.ARCHIVE_URL) == 1  # past, latest, ERA5
    sent = [tuple(zip(map(float, p["latitude"].split(",")), map(float, p["longitude"].split(","))))
            for _, p in calls]
    assert all(s == sent[0] for s in sent) and len(set(sent[0])) == len(sent[0])  # deduped, same points
    assert all((la * 4).is_integer() and (lo * 4).is_integer() for la, lo in sent[0])  # 0.25° grid
    samples = [(p["lat"], p["lon"]) for r in out["routes"] for p in r["points"]]
    assert len(sent[0]) == len(set(w.snap(samples, 0.25))) < len(samples)  # Dallas/Austin shared
    assert out["openmeteo_cost"]["unique_points"] == len(sent[0]) and out["openmeteo_cost"]["max_lead_days"] == 1
    past = next(p for u, p in calls if u == w.PREVIOUS_RUNS_URL and "previous_day" in p["hourly"])
    assert "previous_day2" not in past["hourly"]  # a same-day trip only needs lead-1 stats
    assert out["routes"][0]["arrive_at"].endswith(":00Z")
    west = out["routes"][1]
    assert west["rain_mm"] > 0 and "rain" in west["worst_point"]["reason"]
    assert west["worst_point"]["near"] == "Lampasas"
    assert out["routes"][0]["rain_mm"] == 0
    assert "avoids" in out["why"] and "mm of rain near Lampasas" in out["why"]


def test_why_falls_back_to_coordinates_and_second_request_reuses_cache():
    out = run_routes()
    calls = []
    req = [{"name": "I-35", "waypoints": I35}, {"name": "West", "waypoints": [DALLAS, (32.44, -97.79), RAIN_BLOB, AUSTIN]}]
    with patch.object(w.requests, "get", side_effect=make_fake_api(None, calls)):
        again = w.routes(req, now=NOW, depart_at=pd.Timestamp("2026-11-15T15:00Z"))
    assert calls == [] and again["openmeteo_cost"]["weighted_calls_est"] == 0  # all cached
    assert re.search(r"near -?\d+\.\d\d, -?\d+\.\d\d vs", again["why"])  # no names: coordinates
    assert out["openmeteo_cost"]["weighted_calls_est"] > 0


def test_optimized_sweep_fetches_lead_2_when_arrivals_reach_day_2():
    calls = []
    out = run_routes(calls=calls, optimize_departure=True, window_hours=24)
    assert out["openmeteo_cost"]["max_lead_days"] == 2
    past = next(p for u, p in calls if u == w.PREVIOUS_RUNS_URL and "previous_day" in p["hourly"])
    assert "previous_day2" in past["hourly"] and "previous_day3" not in past["hourly"]
    assert out["best"] == {"route": "I-35", "depart_at": "2026-11-15T15:00:00Z"}
    for r in out["routes"]:
        etas = pd.to_datetime([p["eta"] for p in r["points"]])
        assert etas.is_monotonic_increasing and r["n_points"] <= 20
        assert set(r["points"][0]) >= {"eta", "temp_c", "wind_ms", "gust_ms", "precip_mm", "spread", "hazard"}


def test_departure_sweep_waits_out_the_rain():
    # Rain over the western route for the next 12 h only; force West to be the only option.
    req = [{"name": "West", "waypoints": WEST}]
    with patch.object(w.requests, "get", side_effect=make_fake_api(rain_hours=12)):
        out = w.routes(req, depart_at=pd.Timestamp("2026-11-15T10:00Z"), optimize_departure=True,
                       window_hours=24, now=NOW)
    r = out["routes"][0]
    scores = {d["depart_at"]: d["score"] for d in r["departure_scores"]}
    assert len(scores) == 25 and r["rain_mm"] == 0 and "leave at" in out["why"]
    assert scores[out["best"]["depart_at"]] < scores["2026-11-15T10:00:00Z"]
    # The earliest departure that reaches the blob after the rain stops (21:00Z) wins ties.
    best = pd.Timestamp(out["best"]["depart_at"])
    blob_eta = min((p for p in r["points"]), key=lambda p: w.haversine_km((p["lat"], p["lon"]), RAIN_BLOB))["eta"]
    assert pd.Timestamp(blob_eta).round("h") >= pd.Timestamp("2026-11-15T21:00Z")
    assert all(s > r["score"] for d, s in scores.items() if pd.Timestamp(d) < best)


def test_departures_stay_inside_96h():
    out = run_routes(depart_at=NOW.floor("h") + pd.Timedelta(hours=90), optimize_departure=True, window_hours=24)
    last_ok = NOW + pd.Timedelta(hours=96)
    for r in out["routes"]:
        assert all(pd.Timestamp(d["depart_at"]) + pd.Timedelta(hours=r["duration_h"]) <= last_ok
                   for d in r["departure_scores"])
        assert pd.Timestamp(r["points"][-1]["eta"]) <= last_ok
    with pytest.raises(ValueError, match="96 h"):
        run_routes(depart_at=NOW.floor("h") + pd.Timedelta(hours=95))
    with pytest.raises(ValueError, match="past"):
        run_routes(depart_at=NOW - pd.Timedelta(hours=3))


def test_router_route_endpoint():
    pytest.importorskip("fastapi", reason="API deps not in the model venv")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from api import wx_world

    app = FastAPI()
    app.include_router(wx_world.make_router(lambda: "user"))
    client = TestClient(app)
    body = {"routes": [{"name": "A", "waypoints": I35}, {"name": "B", "waypoints": WEST}],
            "depart_at": "2026-11-15T15:00Z", "speed_kmh": 80, "optimize_departure": True, "window_hours": 6}
    with patch.object(w.requests, "get", side_effect=make_fake_api()), \
         patch.object(w.pd.Timestamp, "now", return_value=NOW):
        r = client.post("/api/wx/world/route", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["best"]["route"] == "A"
    named = {**body, "routes": [{"name": "A", "waypoints": [[*DALLAS, "Dallas"], [*AUSTIN, "Austin"]]}]}
    with patch.object(w.requests, "get", side_effect=make_fake_api()), \
         patch.object(w.pd.Timestamp, "now", return_value=NOW):
        assert client.post("/api/wx/world/route", json=named).status_code == 200
    bad = {**body, "routes": [{"name": "A", "waypoints": [[95, 0], [0, 0]]}]}
    assert client.post("/api/wx/world/route", json=bad).status_code == 422
