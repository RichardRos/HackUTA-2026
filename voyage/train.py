"""Train the voyage speed model and publish it to Vultr Object Storage.

    python -m voyage.train                  # AIS (if the database has it) + physics prior, upload
    python -m voyage.train --no-ais         # physics prior only
    python -m voyage.train --days 14 --max-fixes 20000 --n-jobs 8

Meant to run as a one-shot job on a Vultr compute instance (CPU-optimised is plenty; no GPU), e.g.
`docker compose --profile train run --rm voyage-train` from cron, weekly. The API picks the new
model up within an hour, no redeploy.

Real rows: ship_positions fixes under way on engine (cargo, tanker, passenger), each joined to the
hindcast waves, wind and current at its 1° cell and hour (Open-Meteo, which keeps ~3 months of
past data). Target = speed over ground / that vessel's 95th-percentile speed under way.
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np

from voyage import storage
from voyage.model import VESSELS, Vessel, features, fit_speed_model, kwon_factor, synthetic

log = logging.getLogger("voyage.train")
CHUNK = 50


def ais_fixes(days: int, limit: int) -> list[dict]:
    """Recent AIS fixes under way, with the vessel's static data. Empty without a database."""
    dsn = os.getenv("TIMESCALE_SERVICE_URL")
    if not dsn:
        log.warning("TIMESCALE_SERVICE_URL not set: no AIS rows")
        return []
    import psycopg
    from psycopg.rows import dict_row

    sql = """
        SELECT p.mmsi, p.time, p.lat, p.lon, p.speed_kn, p.course, v.category, v.length_m
        FROM ship_positions p JOIN vessels v USING (mmsi)
        WHERE p.time > now() - make_interval(days => %s)
          AND p.status = 'under way using engine'
          AND p.speed_kn BETWEEN 2 AND 35 AND p.course IS NOT NULL
          AND v.category IN ('cargo', 'tanker', 'passenger')
        ORDER BY random() LIMIT %s"""
    try:
        with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10,
                             options="-c default_transaction_read_only=on") as conn:
            return conn.execute(sql, (days, limit)).fetchall()
    except psycopg.Error as exc:
        log.warning("AIS query failed (%s): no AIS rows", exc)
        return []


def _hindcast(cells: list[tuple[float, float]], start: str, end: str) -> dict[tuple[float, float], dict]:
    """Hourly past waves/currents and wind per 1° cell between two dates (inclusive)."""
    key = os.getenv("OPEN_METEO_API_KEY")
    marine = "https://customer-marine-api.open-meteo.com/v1/marine" if key else "https://marine-api.open-meteo.com/v1/marine"
    wind = "https://customer-api.open-meteo.com/v1/forecast" if key else "https://api.open-meteo.com/v1/forecast"
    extra = {"apikey": key} if key else {}
    out: dict[tuple[float, float], dict] = {}
    for i in range(0, len(cells), CHUNK):
        chunk = cells[i:i + CHUNK]
        common = {"latitude": ",".join(f"{c[0]:g}" for c in chunk), "longitude": ",".join(f"{c[1]:g}" for c in chunk),
                  "start_date": start, "end_date": end, "timezone": "GMT", **extra}
        m = httpx.get(marine, params={**common, "hourly": "wave_height,wave_direction,wave_period,"
                                      "ocean_current_velocity,ocean_current_direction"}, timeout=60).json()
        w = httpx.get(wind, params={**common, "hourly": "wind_speed_10m,wind_direction_10m",
                                    "wind_speed_unit": "ms"}, timeout=60).json()
        m = m if isinstance(m, list) else [m]
        w = w if isinstance(w, list) else [w]
        for cell, mm, ww in zip(chunk, m, w):
            out[cell] = {**(mm.get("hourly") or {}), **{k: v for k, v in (ww.get("hourly") or {}).items() if k != "time"}}
    return out


def profile(category: str, length_m: float | None, ref_kn: float) -> Vessel:
    if category == "tanker":
        base = VESSELS["tanker"]
    elif category == "passenger":
        base = VESSELS["ferry"]
    else:
        base = VESSELS["container"] if (length_m or 0) >= 260 or ref_kn >= 17 else VESSELS["bulk"]
    return Vessel(**{**base.__dict__, "service_kn": ref_kn, "length_m": float(length_m or base.length_m)})


def ais_rows(fixes: list[dict], days: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Feature rows, targets and vessel groups from AIS fixes (rows whose cell has no sea data drop)."""
    if not fixes:
        return np.empty((0, 12)), np.empty(0), np.empty(0)
    by_vessel: dict[int, list[float]] = {}
    for f in fixes:
        by_vessel.setdefault(f["mmsi"], []).append(f["speed_kn"])
    ref = {m: float(np.percentile(s, 95)) for m, s in by_vessel.items() if len(s) >= 6 and np.percentile(s, 95) >= 6}
    fixes = [f for f in fixes if f["mmsi"] in ref]
    cells = sorted({(round(f["lat"]), round(f["lon"])) for f in fixes})
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    log.info("%d fixes from %d vessels over %d cells: fetching hindcast", len(fixes), len(ref), len(cells))
    hind = _hindcast(cells, start.isoformat(), end.isoformat())
    X, y, groups = [], [], []
    for f in fixes:
        h = hind.get((round(f["lat"]), round(f["lon"])))
        if not h or not h.get("time"):
            continue
        t = f["time"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")
        try:
            k = h["time"].index(t)
        except ValueError:
            continue
        vals = [h.get(n, [None] * (k + 1))[k] for n in ("wave_height", "wave_period", "wave_direction", "wind_speed_10m",
                                                        "wind_direction_10m", "ocean_current_velocity", "ocean_current_direction")]
        if any(v is None for v in vals):
            continue  # land cell in the marine model
        hs, tp, wdir, wind, wind_dir, cur, cur_dir = vals
        cur_ms, toward = cur / 3.6, np.radians(cur_dir)
        vessel = profile(f["category"], f["length_m"], ref[f["mmsi"]])
        X.append(features(vessel, f["course"], hs, tp, wdir, wind, wind_dir,
                          cur_ms * np.sin(toward), cur_ms * np.cos(toward))[0])
        y.append(min(1.3, f["speed_kn"] / ref[f["mmsi"]]))
        groups.append(f["mmsi"])
    return np.array(X).reshape(-1, 12), np.array(y), np.array(groups)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=30, help="AIS look-back (max ~90: Open-Meteo hindcast)")
    parser.add_argument("--max-fixes", type=int, default=15000)
    parser.add_argument("--prior-rows", type=int, default=8000, help="physics-prior rows per vessel profile")
    parser.add_argument("--no-ais", action="store_true")
    parser.add_argument("--no-upload", action="store_true", help="keep the artifact on local disk only")
    parser.add_argument("--n-jobs", type=int, default=int(os.getenv("VOYAGE_N_JOBS", "-1")))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    Xp, yp, gp = synthetic(rows_per_vessel=args.prior_rows)
    Xa, ya, ga = (np.empty((0, 12)), np.empty(0), np.empty(0)) if args.no_ais else ais_rows(
        ais_fixes(args.days, args.max_fixes), args.days)
    X, y, groups = np.vstack([Xa, Xp]), np.concatenate([ya, yp]), np.concatenate([ga, gp])
    log.info("training on %d AIS rows + %d prior rows", len(ya), len(yp))
    model = fit_speed_model(X, y, groups, n_jobs=args.n_jobs, n_real=len(ya))
    if len(ya):
        prior = np.concatenate([kwon_factor(profile_for_row(r), r[None, :]) for r in Xa])
        model.meta["ais_rmse_physics_prior"] = round(float(np.sqrt(np.mean((prior - ya) ** 2))), 4)
        model.meta["ais_rmse_superlearner_in_sample"] = round(float(np.sqrt(np.mean((model.factor(Xa) - ya) ** 2))), 4)
    key = storage.save(model, remote=not args.no_upload)
    sl = model.meta["cv"]["superlearner"]
    log.info("saved %s: CV R² %.3f, RMSE %.4f, conformal q90 %.4f, weights %s",
             key, sl["r2"], sl["rmse"], model.q90, model.meta["weights"])
    return 0


def profile_for_row(row: np.ndarray) -> Vessel:
    """The vessel a feature row describes (service speed, length, Cb and sail flag are features)."""
    service, length, cb, sail = row[8], row[9], row[10], row[11]
    base = min(VESSELS.values(), key=lambda v: abs(v.block_coeff - cb) + abs(float(v.sail) - sail) * 10)
    return Vessel(**{**base.__dict__, "service_kn": float(service), "length_m": float(length)})


if __name__ == "__main__":
    raise SystemExit(main())
