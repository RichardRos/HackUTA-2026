"""AIS ship snapshot for the hourly job: listen to aisstream.io for a window, then store every
vessel's static data (vessels) and its latest fix (ship_positions). Needs AISSTREAM_API_KEY and
db/14_ships.sql. Parsing lives in the repo-root ships.py, shared with the live API.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import common as c

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ships  # noqa: E402

VESSEL_COLS = [("mmsi", "bigint"), ("name", "text"), ("imo", "bigint"), ("callsign", "text"), ("flag", "text"),
               ("type_code", "smallint"), ("category", "text"), ("hazard", "text"), ("destination", "text"),
               ("eta", "text"), ("length_m", "smallint"), ("beam_m", "smallint"), ("draught_m", "real"),
               ("seen", "timestamptz")]
POSITION_COLS = [("time", "timestamptz"), ("mmsi", "bigint"), ("lat", "double precision"),
                 ("lon", "double precision"), ("speed_kn", "real"), ("course", "real"),
                 ("heading", "smallint"), ("status", "text")]


def has_schema(conn) -> bool:
    return conn.execute("SELECT to_regclass('ship_positions') IS NOT NULL").fetchone()[0]


def listen(seconds: int) -> ships.Tracker:
    """Run a fresh tracker for `seconds` and return what it heard."""
    tracker = ships.Tracker()

    async def run() -> None:
        if not tracker.start():
            raise RuntimeError("AISSTREAM_API_KEY is not set")
        await asyncio.sleep(seconds)
        await tracker.stop()

    asyncio.run(run())
    return tracker


def store(conn, tracker: ships.Tracker) -> tuple[int, int]:
    """Upsert vessels (static fields only overwrite when heard) and insert one fix per vessel."""
    small = lambda v: int(v) if v is not None and 0 < v < 32767 else None  # noqa: E731
    vessels, positions = [], []
    for s in tracker.ships.values():
        seen = datetime.fromtimestamp(s["seen"], timezone.utc)
        vessels.append((s["mmsi"], s.get("name"), s.get("imo") or None, s.get("callsign"), s.get("flag"),
                        small(s.get("type_code")), s.get("category"), s.get("hazard"), s.get("destination"),
                        s.get("eta"), small(s.get("length_m")), small(s.get("beam_m")), s.get("draught_m"), seen))
        if s.get("lat") is not None and -90 <= s["lat"] <= 90 and -180 <= s["lon"] <= 180:
            positions.append((seen, s["mmsi"], s["lat"], s["lon"], s.get("speed_kn"), s.get("course"),
                              small(s.get("heading")), s.get("status")))
    n_vessels = c.stage_and_merge(conn, "stage_vessels", VESSEL_COLS, vessels, """
        INSERT INTO vessels AS v (mmsi, name, imo, callsign, flag, type_code, category, hazard, destination,
                                  eta, length_m, beam_m, draught_m, first_seen, last_seen)
        SELECT mmsi, name, imo, callsign, flag, type_code, category, hazard, destination,
               eta, length_m, beam_m, draught_m, seen, seen FROM stage_vessels
        ON CONFLICT (mmsi) DO UPDATE SET
            name = coalesce(EXCLUDED.name, v.name), imo = coalesce(EXCLUDED.imo, v.imo),
            callsign = coalesce(EXCLUDED.callsign, v.callsign), flag = EXCLUDED.flag,
            type_code = coalesce(EXCLUDED.type_code, v.type_code),
            category = CASE WHEN EXCLUDED.type_code IS NULL THEN v.category ELSE EXCLUDED.category END,
            hazard = CASE WHEN EXCLUDED.type_code IS NULL THEN v.hazard ELSE EXCLUDED.hazard END,
            destination = coalesce(EXCLUDED.destination, v.destination), eta = coalesce(EXCLUDED.eta, v.eta),
            length_m = coalesce(EXCLUDED.length_m, v.length_m), beam_m = coalesce(EXCLUDED.beam_m, v.beam_m),
            draught_m = coalesce(EXCLUDED.draught_m, v.draught_m),
            last_seen = greatest(v.last_seen, EXCLUDED.last_seen)""")
    n_positions = c.stage_and_merge(conn, "stage_ship_positions", POSITION_COLS, positions, """
        INSERT INTO ship_positions (time, mmsi, lat, lon, speed_kn, course, heading, status)
        SELECT time, mmsi, lat, lon, speed_kn, course, heading, status FROM stage_ship_positions
        ON CONFLICT (mmsi, time) DO NOTHING""")
    return n_vessels, n_positions


def update(conn, seconds: int) -> None:
    if not has_schema(conn):
        raise RuntimeError("run db/14_ships.sql first (or --skip ships)")
    if not os.getenv("AISSTREAM_API_KEY", "").strip():
        raise RuntimeError("AISSTREAM_API_KEY is not set (or --skip ships)")
    tracker = listen(seconds)
    v, p = store(conn, tracker)
    c.log(f"ships: {tracker.messages} AIS messages in {seconds}s, {v} vessels upserted, {p} rows stored")


if __name__ == "__main__":  # the ingest container's hourly "ships" job
    c.load_env(quiet=True)
    with c.connect() as conn:
        update(conn, int(os.getenv("SHIPS_SECONDS", "90")))
