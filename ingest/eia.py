"""EIA API v2, Form EIA-930 (Hourly Electric Grid Monitor), every respondent.

  region-data          D (demand) and DF (day-ahead demand forecast) for every
                       balancing authority, the 13 EIA regions and US48
                       -> grid_hourly
  region-sub-ba-data   demand for sub-BA zones (ERCOT weather zones, PJM zones,
                       NYISO zones, CAISO utilities, SPP members, ...)
                       -> grid_subba_hourly

Periods are UTC, hour-ending. EIA returns `value` as a string in MWh.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterable, Optional

import psycopg

import common as c
from stations import BA_TIMEZONES, DEFAULT_TZ

BASE = "https://api.eia.gov/v2/electricity/rto"
PAGE = 5000                     # EIA v2 max rows per request
DEFAULT_SERIES = ("D", "DF")
REGIONS = {"CAL", "CAR", "CENT", "FLA", "MIDA", "MIDW", "NE", "NW", "NY", "SE", "SW", "TEN", "TEX"}

_warned = False


def api_key() -> str:
    global _warned
    key = os.getenv("EIA_API_KEY")
    if not key:
        if not _warned:
            c.log("warning: EIA_API_KEY not set, using EIA's rate-limited DEMO_KEY")
            _warned = True
        key = "DEMO_KEY"
    return key


def _hour(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def _paged(route: str, params: list[tuple[str, object]]) -> list[dict]:
    out, offset = [], 0
    while True:
        resp = c.get_json(f"{BASE}/{route}/data/",
                          [("api_key", api_key())] + params
                          + [("offset", offset), ("length", PAGE)])["response"]
        data = resp.get("data") or []
        out.extend(data)
        offset += len(data)
        if not data or offset >= int(resp.get("total") or 0):
            return out


def _window(start: datetime, end: datetime) -> list[tuple[str, object]]:
    return [
        ("frequency", "hourly"),
        ("data[0]", "value"),
        ("start", _hour(start)),
        ("end", _hour(end)),                     # inclusive
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
    ]


def respondents() -> list[tuple[str, str]]:
    """(code, name) for every EIA-930 respondent."""
    resp = c.get_json(f"{BASE}/region-data/facet/respondent", [("api_key", api_key())])
    return [(f["id"], f["name"]) for f in resp["response"]["facets"]]


def fetch_region(start: datetime, end: datetime, series: Iterable[str] = DEFAULT_SERIES,
                 bas: Optional[Iterable[str]] = None) -> list[tuple[datetime, str, str, float]]:
    """(period, ba_code, series, value_mwh) for start..end inclusive; null values skipped."""
    params = _window(start, end) + [("facets[type][]", s) for s in series]
    params += [("facets[respondent][]", b) for b in (bas or [])]
    rows = []
    for r in _paged("region-data", params):
        v = c.num(r.get("value"))
        if v is not None:
            rows.append((c.parse_utc(r["period"]), r["respondent"], r["type"], v))
    return rows


def fetch_subba(start: datetime, end: datetime, bas: Optional[Iterable[str]] = None):
    """Returns (rows, names): rows = (period, parent, subba, value_mwh); names = {(parent, subba): name}."""
    params = _window(start, end) + [("facets[parent][]", b) for b in (bas or [])]
    rows, names = [], {}
    for r in _paged("region-sub-ba-data", params):
        names[(r["parent"], r["subba"])] = r.get("subba-name")
        v = c.num(r.get("value"))
        if v is not None:
            rows.append((c.parse_utc(r["period"]), r["parent"], r["subba"], v))
    return rows, names


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
def sync_respondents(conn: psycopg.Connection) -> int:
    """Add every EIA-930 respondent to balancing_authorities (timezone + kind)."""
    rows = []
    for code, name in respondents():
        kind = "total" if code == "US48" else "region" if code in REGIONS else "ba"
        rows.append((code, name, BA_TIMEZONES.get(code, DEFAULT_TZ), kind))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO balancing_authorities (ba_code, name, timezone, kind) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (ba_code) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind",
            rows)
    conn.commit()
    return len(rows)


def upsert_region(conn: psycopg.Connection, rows, available_at: Optional[datetime]) -> int:
    """Upsert into grid_hourly with the schema's rule (available_at kept from first sight).

    available_at=None -> backfill: eia_backfill_available_at(series, period, BA tz).
    """
    return c.stage_and_merge(
        conn, "_grid_stage",
        [("period", "timestamptz"), ("ba_code", "text"), ("series", "text"), ("value_mwh", "float8")],
        rows,
        f"""
        INSERT INTO grid_hourly (period, ba_code, series, value_mwh, available_at)
        SELECT DISTINCT ON (s.ba_code, s.series, s.period)
               s.period, s.ba_code, s.series, s.value_mwh,
               COALESCE(%(avail)s::timestamptz,
                        eia_backfill_available_at(s.series, s.period,
                                                  COALESCE(b.timezone, '{DEFAULT_TZ}')))
        FROM _grid_stage s
        LEFT JOIN balancing_authorities b ON b.ba_code = s.ba_code
        ORDER BY s.ba_code, s.series, s.period
        ON CONFLICT (ba_code, series, period) DO UPDATE
           SET value_mwh = EXCLUDED.value_mwh, updated_at = now()
           WHERE grid_hourly.value_mwh IS DISTINCT FROM EXCLUDED.value_mwh
        """,
        {"avail": available_at})


def upsert_subba(conn: psycopg.Connection, rows, names: dict, available_at: Optional[datetime]) -> int:
    if names:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO eia_subbas (ba_code, subba, name) VALUES (%s, %s, %s) "
                "ON CONFLICT (ba_code, subba) DO UPDATE SET name = EXCLUDED.name",
                [(p, s, n) for (p, s), n in names.items()])
        conn.commit()
    return c.stage_and_merge(
        conn, "_subba_stage",
        [("period", "timestamptz"), ("ba_code", "text"), ("subba", "text"), ("value_mwh", "float8")],
        rows,
        f"""
        INSERT INTO grid_subba_hourly (period, ba_code, subba, value_mwh, available_at)
        SELECT DISTINCT ON (s.ba_code, s.subba, s.period)
               s.period, s.ba_code, s.subba, s.value_mwh,
               COALESCE(%(avail)s::timestamptz,
                        eia_backfill_available_at('NG', s.period,
                                                  COALESCE(b.timezone, '{DEFAULT_TZ}')))
        FROM _subba_stage s
        LEFT JOIN balancing_authorities b ON b.ba_code = s.ba_code
        ORDER BY s.ba_code, s.subba, s.period
        ON CONFLICT (ba_code, subba, period) DO UPDATE
           SET value_mwh = EXCLUDED.value_mwh, updated_at = now()
           WHERE grid_subba_hourly.value_mwh IS DISTINCT FROM EXCLUDED.value_mwh
        """,
        {"avail": available_at})
