"""Weather points + writes to weather_forecasts / weather_actuals.

Sources return rows per station (ICAO). A station can represent several BAs
(e.g. KPDX for BPAT, PACW and PGE), so rows are fanned out to every point_id
that uses that station.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Iterable

import psycopg

import common as c
import noaa_ghcnh
import noaa_nws
from global_points import global_points
from stations import all_points

FC_VALUES = ["temp_f", "dewpoint_f", "apparent_temp_f", "rh_pct", "cloud_cover_pct",
             "wind_10m_mph", "wind_100m_mph", "shortwave_wm2", "wind_dir_deg", "wind_gust_mph",
             "precip_prob_pct", "precip_in", "snow_in", "thunder_prob_pct", "visibility_mi",
             "ceiling_ft", "wet_bulb_globe_f"]
AC_VALUES = ["temp_f", "dewpoint_f", "apparent_temp_f", "rh_pct", "cloud_cover_pct",
             "wind_10m_mph", "wind_100m_mph", "shortwave_wm2", "wind_dir_deg", "wind_gust_mph",
             "precip_in", "visibility_mi", "ceiling_ft", "sea_level_pressure_hpa",
             "station_pressure_hpa"]


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------
def sync_points(conn: psycopg.Connection) -> int:
    """Upsert every configured station point (ingest/stations.py) into weather_points.

    Coordinates and GHCNh ids come from NCEI's station list (NWS as fallback).
    The NWS grid cell is cleared when coordinates change, so it is re-resolved.
    """
    index = noaa_ghcnh.station_index()
    rows = []
    for pid, ba, icao, weight, purpose in all_points():
        if icao in index:
            gid, lat, lon, name = index[icao]
        else:
            c.log(f"  {icao}: not in GHCNh station list, using NWS coordinates (no history)")
            lat, lon, name = noaa_nws.station_coords(icao)
            gid = None
        rows.append((pid, ba, f"{name} ({icao})", lat, lon, weight, purpose, icao, gid))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO weather_points (point_id, ba_code, name, latitude, longitude, weight, purpose, icao, ghcn_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (point_id) DO UPDATE SET
                ba_code = EXCLUDED.ba_code, name = EXCLUDED.name,
                weight = EXCLUDED.weight, purpose = EXCLUDED.purpose,
                icao = EXCLUDED.icao, ghcn_id = EXCLUDED.ghcn_id,
                nws_grid_id = CASE WHEN (weather_points.latitude, weather_points.longitude)
                                        = (EXCLUDED.latitude, EXCLUDED.longitude)
                                   THEN weather_points.nws_grid_id END,
                latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude
            """, rows)
    conn.commit()
    return len(rows)


def sync_global_points(conn: psycopg.Connection) -> int:
    """Upsert the global city points (ingest/global_points.py): purpose 'global', no BA."""
    rows = [(pid, name, lat, lon, cc) for pid, name, cc, lat, lon in global_points()]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO weather_points (point_id, ba_code, name, latitude, longitude, weight, purpose, country)
            VALUES (%s, NULL, %s, %s, %s, 0, 'global', %s)
            ON CONFLICT (point_id) DO UPDATE SET
                name = EXCLUDED.name, latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude, country = EXCLUDED.country
            """, rows)
    conn.commit()
    return len(rows)


def has_global_schema(conn: psycopg.Connection) -> bool:
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'weather_points' AND column_name = 'country')").fetchone()[0]


def points_missing(conn: psycopg.Connection) -> bool:
    have = {r[0] for r in conn.execute("SELECT point_id FROM weather_points WHERE icao IS NOT NULL")}
    return any(p[0] not in have for p in all_points())


def global_points_missing(conn: psycopg.Connection) -> bool:
    have = {r[0] for r in conn.execute("SELECT point_id FROM weather_points WHERE purpose = 'global'")}
    return any(p[0] not in have for p in global_points())


def load_all_points(conn: psycopg.Connection) -> list[tuple[str, float, float]]:
    """(point_id, lat, lon) for every point, US stations and global cities (for GFS)."""
    return [tuple(r) for r in conn.execute(
        "SELECT point_id, latitude, longitude FROM weather_points ORDER BY point_id")]


def load_points(conn: psycopg.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT point_id, ba_code, icao, ghcn_id, latitude, longitude, nws_grid_id, nws_grid_x, nws_grid_y "
        "FROM weather_points WHERE icao IS NOT NULL ORDER BY ba_code, point_id")
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def by_station(points: Iterable[dict], key: str = "icao") -> dict[str, list[str]]:
    out = defaultdict(list)
    for p in points:
        if p.get(key):
            out[p[key]].append(p["point_id"])
    return dict(out)


def save_grid(conn: psycopg.Connection, point_ids: list[str], grid: tuple[str, int, int]) -> None:
    conn.execute("UPDATE weather_points SET nws_grid_id = %s, nws_grid_x = %s, nws_grid_y = %s "
                 "WHERE point_id = ANY(%s)", (*grid, point_ids))
    conn.commit()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def _clean(v):
    return None if isinstance(v, float) and v != v else v


def forecast_tuples(rows: list[dict], point_ids: list[str], issued_at, model: str,
                    source_api: str) -> list[tuple]:
    out = []
    for r in rows:
        lead = r.get("lead_hours")
        extra = json.dumps(r.get("extra") or {}, separators=(",", ":"))
        vals = [_clean(r.get(k)) for k in FC_VALUES]
        for pid in point_ids:
            out.append((r["valid_time"], issued_at, pid, model, source_api, lead,
                        None if lead is None else lead // 24, *vals, extra))
    return out


def actual_tuples(rows: list[dict], point_ids: list[str], source: str) -> list[tuple]:
    out = []
    for r in rows:
        extra = json.dumps(r.get("extra") or {}, separators=(",", ":"))
        vals = [_clean(r.get(k)) for k in AC_VALUES]
        for pid in point_ids:
            out.append((r["valid_time"], pid, source, r["available_at"], *vals,
                        r.get("present_weather"), extra))
    return out


def upsert_forecasts(conn: psycopg.Connection, tuples: list[tuple]) -> int:
    """Insert forecast rows. A (point, model, valid_time, issued_at) run never changes, so
    rows already stored are left alone; re-runs are cheap no-ops."""
    cols = [("valid_time", "timestamptz"), ("issued_at", "timestamptz"), ("point_id", "text"),
            ("model", "text"), ("source_api", "text"), ("lead_hours", "smallint"),
            ("lead_days", "smallint")] + [(v, "float8") for v in FC_VALUES] + [("extra", "jsonb")]
    names = ", ".join(n for n, _ in cols)
    return c.stage_and_merge(
        conn, "_fc_stage", cols, tuples,
        f"""
        INSERT INTO weather_forecasts ({names})
        SELECT DISTINCT ON (point_id, model, valid_time, issued_at) {names}
        FROM _fc_stage
        ORDER BY point_id, model, valid_time, issued_at
        ON CONFLICT (point_id, model, valid_time, issued_at) DO NOTHING
        """)


def upsert_actuals(conn: psycopg.Connection, tuples: list[tuple]) -> int:
    """Insert or refresh observed weather (late METAR corrections replace earlier values)."""
    cols = [("valid_time", "timestamptz"), ("point_id", "text"), ("source", "text"),
            ("available_at", "timestamptz")] + [(v, "float8") for v in AC_VALUES] \
        + [("present_weather", "text"), ("extra", "jsonb")]
    names = ", ".join(n for n, _ in cols)
    updates = ", ".join(f"{v} = EXCLUDED.{v}" for v in AC_VALUES + ["present_weather", "extra"])
    return c.stage_and_merge(
        conn, "_ac_stage", cols, tuples,
        f"""
        INSERT INTO weather_actuals ({names})
        SELECT DISTINCT ON (point_id, source, valid_time) {names}
        FROM _ac_stage
        ORDER BY point_id, source, valid_time
        ON CONFLICT (point_id, source, valid_time) DO UPDATE SET {updates},
            available_at = LEAST(weather_actuals.available_at, EXCLUDED.available_at),
            ingested_at = now()
        """)


def store_gfs_run(conn: psycopg.Connection, cycle, leads: list[int], points, which: str = "core",
                  workers: int = 16, files=None):
    """Fetch one GFS run at every point and insert it. Returns (issued_at, cells, rows inserted),
    or None if NOAA hasn't posted all requested steps yet."""
    import noaa_gfs                                       # numpy/eccodes only needed for GFS

    res = noaa_gfs.fetch_run(cycle, leads, points, which=which, workers=workers, files=files)
    if res is None:
        return None
    issued, cells, rows = res
    tuples = []
    for cell, pids in cells.items():
        tuples += forecast_tuples(rows[cell], pids, issued, noaa_gfs.MODEL, noaa_gfs.SOURCE_API)
    return issued, len(cells), upsert_forecasts(conn, tuples)


def forecast_exists(conn: psycopg.Connection, model: str, issued_at) -> bool:
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM weather_forecasts WHERE model = %s AND issued_at = %s "
        "AND valid_time BETWEEN %s AND %s + INTERVAL '4 days')",      # lets Timescale skip old chunks
        (model, issued_at, issued_at, issued_at)).fetchone()[0]
