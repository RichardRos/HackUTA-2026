"""Backfill ship_positions from the Danish Maritime Authority's public AIS archive (aisdata.ais.dk):
one CSV per day of every AIS message around Denmark, published ~3 days late, ~500 MB zipped.

    python ingest/ais_dk.py                          # the 14 newest days
    python ingest/ais_dk.py --start 2026-10-01 --end 2026-10-31

Kept: Class A cargo ships, tankers and passenger ships under way on engine at 2-35 kn, thinned to one
fix per vessel per hour. These are the voyage model's training rows (voyage/train.py), which aisstream
can't backfill. ship_positions keeps 90 days, so older days are dropped by its retention policy.
Safe to re-run (positions ON CONFLICT DO NOTHING; vessels only fill in missing fields).
"""
from __future__ import annotations

import argparse
import csv
import io
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import common as c

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ships  # noqa: E402
from ships_ais import POSITION_COLS, VESSEL_COLS  # noqa: E402

URL = "http://aisdata.ais.dk/aisdk-{day}.zip"
LAG_DAYS = 3
CATEGORY = {"Cargo": "cargo", "Tanker": "tanker", "Passenger": "passenger"}


def _num(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def parse(path) -> tuple[list[tuple], list[tuple]]:
    """(vessel rows, position rows) in VESSEL_COLS / POSITION_COLS order from one day's zip."""
    vessels: dict[int, tuple] = {}
    positions: dict[tuple[int, str], tuple] = {}
    with zipfile.ZipFile(path) as zf, zf.open(zf.namelist()[0]) as raw:
        rows = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline=""))
        col = {name.lstrip("# "): i for i, name in enumerate(next(rows))}
        ts, mob, mmsi_i, lat_i, lon_i, st, sog_i, cog_i, hdg_i, typ = (col[k] for k in (
            "Timestamp", "Type of mobile", "MMSI", "Latitude", "Longitude", "Navigational status", "SOG", "COG",
            "Heading", "Ship type"))
        for r in rows:
            if (len(r) < len(col) or r[typ] not in CATEGORY or r[st] != "Under way using engine"
                    or r[mob] != "Class A"):
                continue
            sog, cog, lat, lon = _num(r[sog_i]), _num(r[cog_i]), _num(r[lat_i]), _num(r[lon_i])
            if sog is None or not 2 <= sog <= 35 or cog is None or not 0 <= cog < 360 or lat is None or lon is None \
                    or not (-90 <= lat <= 90 and -180 <= lon <= 180) or not r[mmsi_i].isdigit():
                continue
            mmsi = int(r[mmsi_i])
            key = (mmsi, r[ts][:13])  # "dd/mm/yyyy HH": first fix of each hour
            if key in positions:
                continue
            t = datetime.strptime(r[ts], "%d/%m/%Y %H:%M:%S").replace(tzinfo=timezone.utc)
            hdg = _num(r[hdg_i])
            positions[key] = (t, mmsi, lat, lon, sog, cog, int(hdg) if hdg is not None and hdg < 360 else None,
                              "under way using engine")
            if mmsi not in vessels:
                small = lambda k: int(v) if (v := _num(r[col[k]])) and 0 < v < 32767 else None  # noqa: E731
                text = lambda k: r[col[k]].strip() if r[col[k]].strip() not in ("", "Unknown") else None  # noqa: E731
                imo = text("IMO")
                vessels[mmsi] = (mmsi, text("Name"), int(imo) if imo and imo.isdigit() else None, text("Callsign"),
                                 ships.flag(mmsi), None, CATEGORY[r[typ]], None, text("Destination"), None,
                                 small("Length"), small("Width"), _num(r[col["Draught"]]), t)
    return list(vessels.values()), list(positions.values())


def store(conn, vessels: list[tuple], positions: list[tuple]) -> tuple[int, int]:
    """Vessels fill in only what aisstream hasn't already stored; positions are insert-only."""
    v = c.stage_and_merge(conn, "stage_vessels_dk", VESSEL_COLS, vessels, """
        INSERT INTO vessels AS v (mmsi, name, imo, callsign, flag, category, destination, length_m, beam_m,
                                  draught_m, first_seen, last_seen)
        SELECT mmsi, name, imo, callsign, flag, category, destination, length_m, beam_m, draught_m, seen, seen
        FROM stage_vessels_dk
        ON CONFLICT (mmsi) DO UPDATE SET
            name = coalesce(v.name, EXCLUDED.name), imo = coalesce(v.imo, EXCLUDED.imo),
            callsign = coalesce(v.callsign, EXCLUDED.callsign), category = coalesce(v.category, EXCLUDED.category),
            length_m = coalesce(v.length_m, EXCLUDED.length_m), beam_m = coalesce(v.beam_m, EXCLUDED.beam_m),
            draught_m = coalesce(v.draught_m, EXCLUDED.draught_m),
            first_seen = least(v.first_seen, EXCLUDED.first_seen)""")
    p = c.stage_and_merge(conn, "stage_ship_positions_dk", POSITION_COLS, positions, """
        INSERT INTO ship_positions (time, mmsi, lat, lon, speed_kn, course, heading, status)
        SELECT time, mmsi, lat, lon, speed_kn, course, heading, status FROM stage_ship_positions_dk
        ON CONFLICT (mmsi, time) DO NOTHING""")
    return v, p


def load_day(conn, day: date) -> int:
    with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
        try:
            with urllib.request.urlopen(URL.format(day=day.isoformat()), timeout=120) as resp:
                shutil.copyfileobj(resp, tmp, 1 << 20)
        except urllib.error.HTTPError as exc:
            c.log(f"ais_dk {day}: not published ({exc.code})")
            return 0
        tmp.seek(0)
        vessels, positions = parse(tmp)  # the open file: Windows can't reopen a NamedTemporaryFile by name
    v, p = store(conn, vessels, positions)
    c.log(f"ais_dk {day}: {len(vessels)} vessels ({v} upserted), {p} rows stored")
    return p


def main(argv: list[str] | None = None) -> int:
    newest = datetime.now(timezone.utc).date() - timedelta(days=LAG_DAYS)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=date.fromisoformat, default=newest - timedelta(days=13))
    ap.add_argument("--end", type=date.fromisoformat, default=newest)
    args = ap.parse_args(argv)
    c.load_env(quiet=True)
    total = 0
    with c.connect() as conn:
        day = args.start
        while day <= args.end:
            total += load_day(conn, day)
            day += timedelta(days=1)
    c.log(f"ais_dk: {total} rows stored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
