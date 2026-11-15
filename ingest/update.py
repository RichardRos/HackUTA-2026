"""Scheduled job: newest national EIA-930 data + NOAA weather. Run hourly.

    python ingest/update.py
    python ingest/update.py --skip nws_forecast --lookback-hours 168

Parts (--only / --skip take a comma list):
  eia           D + DF, every respondent, now - lookback .. now + 48 h
  subba         sub-BA zone demand, now - lookback .. now
                (EIA rows: available_at = this run's time on first sight; revisions
                 update value_mwh/updated_at only)
  nbm           NBM NBH + NBS bulletins for every cycle in the last --nbm-hours
                that isn't stored yet (issued_at = NOAA publish time)
  nws_forecast  NWS gridpoint forecast for every station (issued_at = grid updateTime)
  nws_obs       NWS station observations for the last --obs-hours
  gfs           NOAA GFS 0.25° global model (runs 00/06/12/18Z, ~3.5-5 h to post):
                each fully posted run from the last --gfs-hours that isn't stored,
                leads --gfs-leads, at every point (US stations + global cities)
  ships         AIS (aisstream.io): listen --ships-seconds, upsert vessels and store one
                position per vessel (needs AISSTREAM_API_KEY and db/14_ships.sql)

Each part runs independently; exit code 1 if any part failed.
Scheduled hourly by the ingest container (ingest_service.py job grid_weather; see ingest/README.md).
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

import common as c
import eia
import noaa_nbm
import noaa_nws
import ships_ais
import weather

PARTS = ("eia", "subba", "nbm", "nws_forecast", "nws_obs", "gfs", "ships")


def threaded(items, fn, workers):
    """Yield (item, result-or-exception) for fn(item) over a thread pool."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fn, it): it for it in items}
        for f in as_completed(futs):
            try:
                yield futs[f], f.result()
            except Exception as e:
                yield futs[f], e


def update_eia(conn, now, args):
    start = c.floor_hour(now) - timedelta(hours=args.lookback_hours)
    rows = eia.fetch_region(start, c.floor_hour(now) + timedelta(hours=48))
    n = eia.upsert_region(conn, rows, available_at=now)
    bas = {r[1] for r in rows}
    c.log(f"EIA: {len(rows)} rows for {len(bas)} respondents, {n} new/revised")


def update_subba(conn, now, args):
    start = c.floor_hour(now) - timedelta(hours=args.lookback_hours)
    rows, names = eia.fetch_subba(start, c.floor_hour(now))
    n = eia.upsert_subba(conn, rows, names, available_at=now)
    c.log(f"sub-BA: {len(rows)} rows for {len(names)} zones, {n} new/revised")


def update_nbm(conn, now, args, points):
    stations = weather.by_station(points)
    newest = c.floor_hour(now)
    for back in range(args.nbm_hours):
        cycle = newest - timedelta(hours=back)
        files = noaa_nbm.cycle_files(cycle)
        for product in ("nbh", "nbs"):
            if product not in files:
                continue
            model = noaa_nbm.MODELS[product]
            issued = noaa_nbm.issued_at(cycle, files[product][1])
            if weather.forecast_exists(conn, model, issued):
                continue
            _, data = noaa_nbm.fetch_cycle(cycle, product, stations, files)
            tuples = []
            for icao, rows in data.items():
                tuples += weather.forecast_tuples(rows, stations[icao], issued, model, "nbm_text")
            n = weather.upsert_forecasts(conn, tuples)
            c.log(f"NBM {product} {cycle:%Y-%m-%d %H}Z: {len(data)} stations, {n} rows")


def update_nws_forecast(conn, now, args, points):
    # Resolve and cache any missing grid cells (one /points call per new station)
    for p in points:
        if not p["nws_grid_id"]:
            try:
                grid = noaa_nws.resolve_grid(p["latitude"], p["longitude"])
                same = [q["point_id"] for q in points if q["icao"] == p["icao"]]
                weather.save_grid(conn, same, grid)
                for q in points:
                    if q["icao"] == p["icao"]:
                        q["nws_grid_id"], q["nws_grid_x"], q["nws_grid_y"] = grid
            except Exception as e:
                c.log(f"  NWS grid lookup {p['icao']} failed: {e}")
    grids: dict[tuple, list[str]] = {}
    for p in points:
        if p["nws_grid_id"]:
            grids.setdefault((p["nws_grid_id"], p["nws_grid_x"], p["nws_grid_y"]), []).append(p["point_id"])

    total, failed = 0, 0
    for grid, res in threaded(list(grids), lambda g: noaa_nws.gridpoint_forecast(*g, now), args.workers):
        if isinstance(res, Exception):
            failed += 1
            c.log(f"  NWS gridpoint {grid} failed: {str(res)[:200]}")
            continue
        issued, rows = res
        total += weather.upsert_forecasts(
            conn, weather.forecast_tuples(rows, grids[grid], issued, noaa_nws.MODEL, "nws_api"))
    c.log(f"NWS forecast: {len(grids) - failed}/{len(grids)} grid cells, {total} new rows")
    if failed == len(grids) and grids:
        raise RuntimeError("every NWS gridpoint request failed")


def update_nws_obs(conn, now, args, points):
    stations = weather.by_station(points)
    start = now - timedelta(hours=args.obs_hours)
    total, failed = 0, 0
    for icao, res in threaded(list(stations), lambda s: noaa_nws.station_observations(s, start), args.workers):
        if isinstance(res, Exception):
            failed += 1
            c.log(f"  NWS obs {icao} failed: {str(res)[:200]}")
            continue
        total += weather.upsert_actuals(conn, weather.actual_tuples(res, stations[icao], "nws_obs"))
    c.log(f"NWS obs: {len(stations) - failed}/{len(stations)} stations, {total} rows")
    if failed == len(stations) and stations:
        raise RuntimeError("every NWS observation request failed")


def update_gfs(conn, now, args):
    """Store every GFS run from the last --gfs-hours that is fully posted and not stored yet."""
    import noaa_gfs

    if not weather.has_global_schema(conn):
        raise RuntimeError("run db/13_global_gfs.sql first (or --skip gfs)")
    leads = noaa_gfs.parse_leads(args.gfs_leads)
    points = weather.load_all_points(conn)
    newest = c.floor_hour(now).replace(hour=now.hour // 6 * 6)
    stored = 0
    for back in range(0, args.gfs_hours + 1, 6):
        cycle = newest - timedelta(hours=back)
        files = noaa_gfs.run_files(cycle)
        if any(h not in files for h in leads):
            c.log(f"GFS {cycle:%Y-%m-%d %H}Z: not fully posted yet ({len(files)} steps so far)")
            continue
        if weather.forecast_exists(conn, noaa_gfs.MODEL, noaa_gfs.issued_at(cycle, [files[h] for h in leads])):
            continue
        issued, cells, n = weather.store_gfs_run(conn, cycle, leads, points, args.gfs_fields,
                                                 args.gfs_workers, files)
        stored += 1
        c.log(f"GFS {cycle:%Y-%m-%d %H}Z: {cells} grid cells for {len(points)} points, {n} rows "
              f"(issued {issued:%H:%M}Z)")
    if not stored:
        c.log("GFS: no new runs")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", default=",".join(PARTS), help=f"parts to run: {','.join(PARTS)}")
    p.add_argument("--skip", default="", help="parts to skip")
    p.add_argument("--lookback-hours", type=int, default=72,
                   help="re-pull this many past hours of EIA data to catch late values/revisions")
    p.add_argument("--nbm-hours", type=int, default=6, help="catch up NBM cycles from the last N hours")
    p.add_argument("--obs-hours", type=int, default=30, help="hours of NWS observations to (re)load")
    p.add_argument("--gfs-hours", type=int, default=18, help="catch up GFS runs from the last N hours")
    p.add_argument("--gfs-leads", default="1-72", help="GFS forecast hours to store (default 1-72, hourly)")
    p.add_argument("--gfs-fields", default="core", choices=("core", "all"))
    p.add_argument("--gfs-workers", type=int, default=16, help="parallel range reads per GFS run")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--ships-seconds", type=int, default=90, help="how long to listen to AIS per run")
    args = p.parse_args()

    parts = [x for x in args.only.split(",") if x and x not in args.skip.split(",")]
    if bad := set(parts) - set(PARTS):
        sys.exit(f"unknown part(s): {bad}")

    c.load_env()
    if "subba" in parts and not os.getenv("EIA_API_KEY"):
        parts.remove("subba")  # its ~30 pages trip DEMO_KEY's rate limit every run and failed the whole job
        c.log("subba skipped: set EIA_API_KEY (free, eia.gov/opendata/register.php)")
    now = c.utcnow()
    failed = False
    with c.connect() as conn:
        c.require_schema_v3(conn)
        if weather.points_missing(conn):
            c.log("new stations configured: syncing respondents and weather points")
            eia.sync_respondents(conn)
            weather.sync_points(conn)
        if weather.has_global_schema(conn) and weather.global_points_missing(conn):
            c.log(f"synced {weather.sync_global_points(conn)} global points")
        points = weather.load_points(conn)

        jobs = {
            "eia": lambda: update_eia(conn, now, args),
            "subba": lambda: update_subba(conn, now, args),
            "nbm": lambda: update_nbm(conn, now, args, points),
            "nws_forecast": lambda: update_nws_forecast(conn, now, args, points),
            "nws_obs": lambda: update_nws_obs(conn, now, args, points),
            "gfs": lambda: update_gfs(conn, now, args),
            "ships": lambda: ships_ais.update(conn, args.ships_seconds),
        }
        for name in PARTS:
            if name not in parts:
                continue
            try:
                jobs[name]()
            except Exception:
                conn.rollback()
                failed = True
                c.log(f"{name} FAILED:\n{traceback.format_exc()}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
