"""NOAA NCEI Global Historical Climatology Network - hourly (GHCNh): quality-
controlled station observations, the successor to ISD (which stopped in Aug 2025).
One Parquet file per station per year (~1.5 MB), updated daily with a ~2-3 day lag.

  -> weather_actuals source 'ghcnh'

Each hour (valid_time) takes, per variable, the report nearest to it,
preferring routine METARs (FM-15). A :53 METAR counts for the next hour,
matching hour-ending EIA periods. Values that failed NCEI QC are dropped.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

import common as c

BASE = "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly"
STATION_LIST = f"{BASE}/doc/ghcnh-station-list.csv"

_REPORT_RANK = {"FM15": 0, "FM-15": 0, "FM16": 1, "FM-16": 1}

# GHCNh variable -> (output key, converter); keys not in weather_actuals go to `extra`
VARS = {
    "temperature": ("temp_f", c.c_to_f),
    "dew_point_temperature": ("dewpoint_f", c.c_to_f),
    "relative_humidity": ("rh_pct", None),
    "wind_speed": ("wind_10m_mph", c.ms_to_mph),
    "wind_direction": ("wind_dir_deg", None),
    "wind_gust": ("wind_gust_mph", c.ms_to_mph),
    "precipitation": ("precip_in", c.mm_to_in),
    "visibility": ("visibility_mi", c.km_to_mi),
    "ceiling_height": ("ceiling_ft", c.m_to_ft),
    "sea_level_pressure": ("sea_level_pressure_hpa", None),
    "station_level_pressure": ("station_pressure_hpa", None),
    "wet_bulb_temperature": ("wet_bulb_f", c.c_to_f),
    "altimeter": ("altimeter_hpa", None),
    "pressure_3hr_change": ("pressure_3hr_change_hpa", None),
    "snow_depth": ("snow_depth_in", c.mm_to_in),
    "precipitation_3_hour": ("precip_3h_in", c.mm_to_in),
    "precipitation_6_hour": ("precip_6h_in", c.mm_to_in),
    "precipitation_12_hour": ("precip_12h_in", c.mm_to_in),
    "precipitation_24_hour": ("precip_24h_in", c.mm_to_in),
}
TYPED_KEYS = {"temp_f", "dewpoint_f", "rh_pct", "wind_10m_mph", "wind_dir_deg", "wind_gust_mph",
              "precip_in", "visibility_mi", "ceiling_ft", "sea_level_pressure_hpa",
              "station_pressure_hpa"}
SKY = [f"sky_cover_summation_{i}" for i in range(1, 5)]
WX = [f"pres_wx_AU{i}" for i in range(1, 4)] + [f"pres_wx_MW{i}" for i in range(1, 4)]


def station_index() -> dict[str, tuple[str, float, float, str]]:
    """{icao: (ghcn_id, lat, lon, name)}, preferring US first-order (USW) ids."""
    from stations import GHCN_ID_OVERRIDES

    text = c.get_bytes(STATION_LIST).decode("utf-8", "replace")
    out, by_id = {}, {}
    for r in csv.DictReader(io.StringIO(text)):
        gid = r["GHCN_ID"].strip()
        rec = (gid, float(r["LATITUDE"]), float(r["LONGITUDE"]), r["NAME"].strip())
        by_id[gid] = rec
        icao = (r.get("ICAO") or "").strip()
        if icao and (icao not in out or (gid.startswith("USW") and not out[icao][0].startswith("USW"))):
            out[icao] = rec
    for icao, gid in GHCN_ID_OVERRIDES.items():
        if gid in by_id:
            out[icao] = by_id[gid]
    return out


def _qc_ok(q) -> bool:
    if q is None or q == "":
        return True
    return q.isdigit() and q not in "2367"           # letters = failed a GHCNh QC check


def fetch_year(ghcn_id: str, year: int, start: Optional[datetime] = None,
               end: Optional[datetime] = None) -> list[dict]:
    """Hourly weather_actuals row dicts for one station-year ([] if NCEI has no file),
    optionally limited to valid_time in [start, end)."""
    import pyarrow.compute as pc                       # only the backfill needs pyarrow
    import pyarrow.parquet as pq

    url = f"{BASE}/access/by-year/{year}/parquet/GHCNh_{ghcn_id}_{year}.parquet"
    try:
        buf = c.get_bytes(url, timeout=300)
    except FileNotFoundError:
        return []
    pf = pq.ParquetFile(io.BytesIO(buf))
    names = set(pf.schema_arrow.names)
    want = ["DATE"]
    for v in list(VARS) + SKY + WX:
        if v in names:
            want.append(v)
            for suf in ("_Quality_Code", "_Report_Type"):
                if v + suf in names:
                    want.append(v + suf)
    table = pf.read(columns=want)
    # DATE is an ISO string, so string comparison filters by time (30 min slack for hour rounding)
    if start is not None:
        table = table.filter(pc.greater_equal(table["DATE"], f"{start - timedelta(minutes=30):%Y-%m-%dT%H:%M:%S}"))
    if end is not None:
        table = table.filter(pc.less(table["DATE"], f"{end:%Y-%m-%dT%H:%M:%S}"))
    cols = {name: table[name].to_pylist() for name in table.column_names}
    variables = [v for v in list(VARS) + SKY + WX if v in cols]
    empty = [None] * table.num_rows

    best: dict[datetime, dict[str, tuple]] = {}
    for i, date in enumerate(cols["DATE"]):
        ts = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
        vt = c.round_hour(ts)
        off = abs((ts - vt).total_seconds())
        slot = best.setdefault(vt, {})
        for v in variables:
            val = cols[v][i]
            if val is None or val == "" or not _qc_ok(cols.get(v + "_Quality_Code", empty)[i]):
                continue
            rank = (_REPORT_RANK.get(cols.get(v + "_Report_Type", empty)[i] or "", 2), off)
            if v not in slot or rank < slot[v][0]:
                slot[v] = (rank, val, ts)

    rows = []
    for vt in sorted(best):
        slot = best[vt]
        if not slot:
            continue
        row: dict = {"valid_time": vt, "extra": {}}
        last_obs = max(t for _, _, t in slot.values())
        for v, (key, conv) in VARS.items():
            if v in slot:
                x = c.num(slot[v][1])
                if x is not None and conv:
                    x = conv(x)
                if key in TYPED_KEYS:
                    row[key] = x
                elif x is not None:
                    row["extra"][key] = x
        oktas = []
        for s in SKY:
            if s in slot:
                part = str(slot[s][1]).split(":")
                if len(part) > 1 and part[1][:2].isdigit() and int(part[1][:2]) <= 8:
                    oktas.append(int(part[1][:2]))
        row["cloud_cover_pct"] = max(oktas) / 8 * 100 if oktas else None
        wx = [str(slot[w][1]).split(":")[0] for w in WX[:3] if w in slot]
        row["present_weather"] = " ".join(wx) or None
        t, td = row.get("temp_f"), row.get("dewpoint_f")
        if row.get("rh_pct") is None:
            row["rh_pct"] = c.rh_from(t, td)
        row["apparent_temp_f"] = c.apparent_f(t, row["rh_pct"], row.get("wind_10m_mph"))
        row["available_at"] = max(vt, last_obs + timedelta(minutes=15))
        rows.append(row)
    return rows


def fetch_range(ghcn_id: str, start: datetime, end: datetime) -> list[dict]:
    rows = []
    for year in range(start.year, end.year + 1):
        rows += [r for r in fetch_year(ghcn_id, year, start, end) if start <= r["valid_time"] < end]
    return rows
