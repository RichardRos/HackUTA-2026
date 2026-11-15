"""Nostos daily-high temperature model: scores vs NWS official highs (Tiger schema nostos_wx).

All temperatures are °F; dates are NWS climate days; lead 1 = forecast issued the night before.
Roles: nostos (our model), benchmark (NBM), input (raw NWP models), baseline (persistence).
The default window is the model team's held-out test period.
"""
from __future__ import annotations

import re
from datetime import date

from db_read import read_query

TEST_START, TEST_END = date(2026, 9, 14), date(2026, 11, 12)
NBM_MODELS = ("nbm_corrected", "ncep_nbm_conus")


def _window(start: date | None, end: date | None) -> tuple[date, date]:
    start, end = start or TEST_START, end or TEST_END
    if start > end:
        raise ValueError("start must be on or before end")
    return start, end


def _lead(lead_days: int | None) -> None:
    if lead_days is not None and lead_days not in (1, 2, 3):
        raise ValueError("lead_days must be 1, 2 or 3")


def leaderboard(start: date | None = None, end: date | None = None,
                lead_days: int | None = None) -> list[dict]:
    """MAE / bias / % within 2 °F per model and lead ("The Bow"; all leads = "The Voyage")."""
    start, end = _window(start, end)
    _lead(lead_days)
    return read_query(
        "SELECT lead_days, model, role, sum(n)::int AS days, "
        "round((sum(sum_abs_err_f) / sum(n))::numeric, 2)::float8 AS mae_f, "
        "round((sum(sum_err_f) / sum(n))::numeric, 2)::float8 AS bias_f, "
        "round((100.0 * sum(n_within_2f) / sum(n))::numeric, 1)::float8 AS within_2f_pct "
        "FROM nostos_wx.leaderboard_daily WHERE day BETWEEN %s AND %s "
        "AND (%s::int IS NULL OR lead_days = %s) "
        "GROUP BY lead_days, model, role ORDER BY lead_days, mae_f",
        (start, end, lead_days, lead_days),
    )


def weekly_trend(start: date | None = None, end: date | None = None, lead_days: int = 1,
                 models: list[str] | None = None) -> list[dict]:
    """Weekly MAE per model (time_bucket weeks start Monday)."""
    start, end = _window(start, end)
    _lead(lead_days)
    return read_query(
        "SELECT time_bucket('1 week', day) AS week, model, role, sum(n)::int AS days, "
        "round((sum(sum_abs_err_f) / sum(n))::numeric, 2)::float8 AS mae_f "
        "FROM nostos_wx.leaderboard_daily WHERE day BETWEEN %s AND %s AND lead_days = %s "
        "AND (%s::text[] IS NULL OR model = ANY(%s)) "
        "GROUP BY week, model, role ORDER BY week, mae_f",
        (start, end, lead_days, models, models),
    )


def city_series(location: str, start: date | None = None, end: date | None = None,
                lead_days: int = 1) -> list[dict]:
    """One city ("Ithaca"): observed high vs Nostos vs NBM per climate day, one row per day."""
    start, end = _window(start, end)
    _lead(lead_days)
    return read_query(
        "SELECT climate_date, max(observed_f) AS observed_f, "
        "max(high_f) FILTER (WHERE model = 'nostos') AS nostos_f, "
        "max(high_f) FILTER (WHERE model = %s) AS nbm_corrected_f, "
        "max(high_f) FILTER (WHERE model = %s) AS nbm_f "
        "FROM nostos_wx.daily_high_forecasts "
        "WHERE location = %s AND lead_days = %s AND climate_date BETWEEN %s AND %s "
        "AND model IN ('nostos', %s, %s) "
        "GROUP BY climate_date ORDER BY climate_date",
        (*NBM_MODELS, location.lower(), lead_days, start, end, *NBM_MODELS),
    )


def city_map(start: date | None = None, end: date | None = None, lead_days: int = 1,
             model: str = "nostos") -> list[dict]:
    """The 20 cities with location metadata and one model's MAE (for the map)."""
    start, end = _window(start, end)
    _lead(lead_days)
    return read_query(
        "SELECT l.location, l.station_id, l.latitude, l.longitude, l.kalshi_series, "
        "count(f.error_f)::int AS days, round(avg(abs(f.error_f))::numeric, 2)::float8 AS mae_f, "
        "round(avg(f.error_f)::numeric, 2)::float8 AS bias_f "
        "FROM nostos_wx.locations l LEFT JOIN nostos_wx.daily_high_forecasts f "
        "ON f.location = l.location AND f.model = %s AND f.lead_days = %s "
        "AND f.climate_date BETWEEN %s AND %s AND f.error_f IS NOT NULL "
        "GROUP BY l.location ORDER BY l.location",
        (model, lead_days, start, end),
    )


# Thin readers over the model team's published views (fixed test period, lead 1 for cities).
def _location(location: str) -> str:
    if not re.fullmatch(r"[a-z_]{1,40}", location):
        raise ValueError("location must be a lowercase key like 'austin'")
    return location


def leaderboard_test(lead_days: int | None = None) -> list[dict]:
    _lead(lead_days)
    return read_query(
        "SELECT lead_days, model, role, days, mae_f, bias_f, within_2f_pct "
        "FROM nostos_wx.leaderboard_test WHERE (%s::int IS NULL OR lead_days = %s) "
        "ORDER BY lead_days, mae_f",
        (lead_days, lead_days),
    )


def city_accuracy() -> list[dict]:
    return read_query(
        "SELECT location, station_id, latitude, longitude, days, nostos_mae_f, "
        "nbm_corrected_mae_f, nbm_mae_f FROM nostos_wx.city_accuracy ORDER BY location"
    )


def city_timeline(location: str) -> list[dict]:
    return read_query(
        "SELECT climate_date, observed_f, nostos_f, nbm_corrected_f, nbm_f "
        "FROM nostos_wx.city_timeline WHERE location = %s ORDER BY climate_date",
        (_location(location),),
    )
