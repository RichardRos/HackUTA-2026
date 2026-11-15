"""History load: national EIA-930 demand + NOAA weather. Safe to re-run.

    python ingest/backfill.py                                  # everything, 2024-02-01 -> now
    python ingest/backfill.py --start 2025-06-01 --only eia
    python ingest/backfill.py --only forecasts --nbm-products nbs,nbh --nbm-cycles 0,12

Parts (--only / --skip take a comma list):
  eia        D + DF for every EIA-930 respondent -> grid_hourly
             available_at = eia_backfill_available_at(series, period, BA tz)
  subba      sub-BA zone demand -> grid_subba_hourly (available_at: next day 10:00 ET)
  forecasts  NOAA NBM station bulletins from the AWS archive -> weather_forecasts
             (model nbm_nbs / nbm_nbh, issued_at = NOAA's publish time)
  actuals    NOAA NCEI GHCNh observations -> weather_actuals (source ghcnh)

Size: NBM files are ~30 MB each (all ~9,500 stations), so forecasts cost
~30 MB x products x cycles per day; the default (NBS, 12z) is ~1 GB per month.
GHCNh is ~1.5 MB per station-year. The NWS gridpoint forecast has no archive,
so 'nws_ndfd' only builds up from ingest/update.py.

Run db/06_national_noaa.sql and db/99_clear_fake.sql first (the script checks).
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import psycopg

import common as c
import eia
import noaa_ghcnh
import noaa_nbm
import weather

PARTS = ("eia", "subba", "forecasts", "actuals")


def chunks(start: datetime, end: datetime, days: int):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt


def run_pool(tasks, workers, label):
    """Run (desc, fn) tasks in threads; yield (desc, result) as they finish, log failures."""
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fn): desc for desc, fn in tasks}
        for f in as_completed(futs):
            try:
                yield futs[f], f.result()
            except Exception as e:                       # one bad chunk shouldn't stop the backfill
                failed += 1
                c.log(f"  {label} {futs[f]} FAILED: {e}")
    if failed:
        c.log(f"{label}: {failed} task(s) failed; re-run the same command to retry them")


def refresh_aggregates(conn: psycopg.Connection) -> None:
    conn.commit()
    conn.autocommit = True                               # CALL can't run in a transaction
    try:
        for view in ("grid_daily", "grid_subba_daily"):
            try:
                conn.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")
                c.log(f"{view} refreshed")
            except psycopg.Error as e:
                c.log(f"warning: {view} refresh failed: {str(e).strip()}")
    finally:
        conn.autocommit = False


def backfill_eia(conn, start, end, series, chunk_days, workers):
    c.log(f"EIA region-data {'/'.join(series)}, all respondents: {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    tasks = [(f"{a:%Y-%m-%d}", lambda a=a, b=b: eia.fetch_region(a, b - timedelta(hours=1), series))
             for a, b in chunks(start, end, chunk_days)]
    total = 0
    for desc, rows in run_pool(tasks, workers, "EIA"):
        n = eia.upsert_region(conn, rows, available_at=None)
        total += n
        c.log(f"  EIA {desc}: fetched {len(rows):6d}, upserted {n:6d}")
    c.log(f"EIA done: {total} rows inserted/revised")


def backfill_subba(conn, start, end, chunk_days, workers):
    c.log(f"EIA sub-BA demand: {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    tasks = [(f"{a:%Y-%m-%d}", lambda a=a, b=b: eia.fetch_subba(a, b - timedelta(hours=1)))
             for a, b in chunks(start, end, chunk_days)]
    total = 0
    for desc, (rows, names) in run_pool(tasks, workers, "sub-BA"):
        n = eia.upsert_subba(conn, rows, names, available_at=None)
        total += n
        c.log(f"  sub-BA {desc}: fetched {len(rows):6d}, upserted {n:6d}")
    c.log(f"sub-BA done: {total} rows inserted/revised")


def backfill_forecasts(conn, points, start, end, products, cycles, workers):
    stations = weather.by_station(points)
    c.log(f"NBM {products} cycles {cycles}Z for {len(stations)} stations: {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    now = c.utcnow()

    def job(cycle, product):
        files = noaa_nbm.cycle_files(cycle)
        return noaa_nbm.fetch_cycle(cycle, product, stations, files)

    tasks = []
    day = start.replace(hour=0)
    while day < end:
        for h in cycles:
            cyc = day + timedelta(hours=h)
            if start <= cyc < end and cyc + timedelta(hours=2) < now:
                for p in products:
                    tasks.append((f"{p} {cyc:%Y-%m-%d %H}Z", lambda cyc=cyc, p=p: job(cyc, p)))
        day += timedelta(days=1)

    total = 0
    for desc, (issued, data) in run_pool(tasks, workers, "NBM"):
        if issued is None:
            c.log(f"  NBM {desc}: no file in the NOAA archive")
            continue
        model = noaa_nbm.MODELS[desc.split()[0]]
        tuples = []
        for icao, rows in data.items():
            tuples += weather.forecast_tuples(rows, stations[icao], issued, model, "nbm_text")
        n = weather.upsert_forecasts(conn, tuples)
        total += n
        c.log(f"  NBM {desc}: {len(data):3d} stations, {n:6d} new rows")
    c.log(f"NBM done: {total} rows")


def backfill_actuals(conn, points, start, end, workers):
    stations = weather.by_station(points, key="ghcn_id")
    c.log(f"GHCNh observations for {len(stations)} stations: {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    tasks = [(gid, lambda gid=gid: noaa_ghcnh.fetch_range(gid, start, end)) for gid in stations]
    total = 0
    for gid, rows in run_pool(tasks, workers, "GHCNh"):
        n = weather.upsert_actuals(conn, weather.actual_tuples(rows, stations[gid], "ghcnh"))
        total += n
        c.log(f"  GHCNh {gid} ({','.join(stations[gid])}): {n:6d} rows")
    c.log(f"GHCNh done: {total} rows")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default="2024-02-01", help="UTC start (default 2024-02-01)")
    p.add_argument("--end", default=None, help="UTC end, exclusive (default: now)")
    p.add_argument("--only", default=",".join(PARTS), help=f"parts to run: {','.join(PARTS)}")
    p.add_argument("--skip", default="", help="parts to skip")
    p.add_argument("--eia-series", default="D,DF", help="EIA-930 types: D,DF,NG,TI (default D,DF)")
    p.add_argument("--nbm-products", default="nbs", help="nbs (3-hourly, 72 h) and/or nbh (hourly, 25 h)")
    p.add_argument("--nbm-cycles", default="12", help="UTC cycle hours per day (default 12)")
    p.add_argument("--eia-chunk-days", type=int, default=7)
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()

    parts = [x for x in args.only.split(",") if x and x not in args.skip.split(",")]
    if bad := set(parts) - set(PARTS):
        sys.exit(f"unknown part(s): {bad}")
    products = [x.strip() for x in args.nbm_products.split(",") if x.strip()]
    if bad := set(products) - set(noaa_nbm.MODELS):
        sys.exit(f"unknown NBM product(s): {bad}")
    cycles = sorted({int(x) for x in args.nbm_cycles.split(",") if x.strip()})
    series = [x.strip() for x in args.eia_series.split(",") if x.strip()]

    c.load_env()
    start = c.parse_utc(args.start)
    end = c.floor_hour(c.parse_utc(args.end) if args.end else c.utcnow())
    if start >= end:
        sys.exit("--start must be before --end")

    with c.connect() as conn:
        c.require_schema_v3(conn)
        c.guard_fake(conn)
        c.log(f"synced {eia.sync_respondents(conn)} EIA respondents")
        c.log(f"synced {weather.sync_points(conn)} weather points")
        points = weather.load_points(conn)

        if "eia" in parts:
            backfill_eia(conn, start, end, series, args.eia_chunk_days, args.workers)
        if "subba" in parts:
            backfill_subba(conn, start, end, args.eia_chunk_days * 2, args.workers)
        if "eia" in parts or "subba" in parts:
            refresh_aggregates(conn)
        if "forecasts" in parts:
            backfill_forecasts(conn, points, start, end, products, cycles, args.workers)
        if "actuals" in parts:
            backfill_actuals(conn, points, start, end, args.workers)


if __name__ == "__main__":
    main()
