"""NOAA National Blend of Models (NBM) station text bulletins, from NOAA's
open-data bucket on AWS (noaa-nbm-grib2-pds). Same files for history and live.

  NBH  hourly guidance, 1-25 h ahead          -> model 'nbm_nbh'
  NBS  short-range guidance, 3-hourly 6-72 h  -> model 'nbm_nbs'

Both are issued every hour, ~1 h after the cycle time, and cover ~9,500
stations (~4,300 US ICAO sites). Each file is ~30 MB of fixed-width text: a
block per station, one row per element, 3 characters per column.

issued_at = the S3 upload time (when NOAA actually published the bulletin);
cycle + 90 min if that timestamp looks like a later re-upload.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import common as c

BUCKET = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
MODELS = {"nbh": "nbm_nbh", "nbs": "nbm_nbs"}

_HEADER = re.compile(r"^\s*(\S+)\s+NBM\s+(?:V[\d.]+\s+)?(NBH|NBS)\s+GUIDANCE\s+"
                     r"(\d\d?)/(\d\d?)/(\d{4})\s+(\d\d)(\d\d)\s+UTC")   # older files: ' 3/01/2024'
_S3NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

# Accumulation / probability elements by product (period ending at valid_time)
_PERIOD = {
    "nbh": {"prob": "P01", "qpf": "Q01", "snow": "S01", "thunder": "T01"},
    "nbs": {"prob": "P06", "qpf": "Q06", "snow": "S06", "thunder": "T03"},
}


def cycle_files(cycle: datetime) -> dict[str, tuple[str, Optional[datetime]]]:
    """{'nbh': (url, last_modified), 'nbs': ...} for the text files of one cycle."""
    prefix = f"blend.{cycle:%Y%m%d}/{cycle:%H}/text/"
    root = ET.fromstring(c.get_bytes(BUCKET + "/", [("list-type", 2), ("prefix", prefix)]))
    out = {}
    for item in root.iter(f"{_S3NS}Contents"):
        key = item.findtext(f"{_S3NS}Key")
        m = re.search(r"blend_(nb[hs])tx\.t\d\dz$", key or "")
        if m:
            lm = item.findtext(f"{_S3NS}LastModified")
            out[m.group(1)] = (f"{BUCKET}/{key}", c.parse_utc(lm[:19]) if lm else None)
    return out


def issued_at(cycle: datetime, last_modified: Optional[datetime]) -> datetime:
    if last_modified and cycle <= last_modified <= cycle + timedelta(hours=6):
        return last_modified
    return cycle + timedelta(minutes=90)


def _cols(line: str) -> list[Optional[int]]:
    vals = []
    for i in range(5, len(line), 3):
        tok = line[i:i + 3].strip()
        try:
            vals.append(int(tok) if tok else None)
        except ValueError:
            vals.append(None)
    return vals


def _rows_from_block(product: str, cycle: datetime, lines: list[str]) -> list[dict]:
    table = {}
    for ln in lines:
        label = ln[1:4].strip()
        if label and label not in table and label not in ("DT", "FHR"):
            table[label] = _cols(ln)
    hours = table.pop("UTC", None)
    if not hours:
        return []
    # Valid times: step forward from the cycle until the clock hour matches
    valid, t = [], cycle
    for h in hours:
        if h is None:
            valid.append(None)
            continue
        t += timedelta(hours=1)
        while t.hour != h:
            t += timedelta(hours=1)
        valid.append(t)

    p = _PERIOD[product]
    rows = []
    for i, vt in enumerate(valid):
        if vt is None:
            continue
        raw = {k: v[i] for k, v in table.items() if i < len(v) and v[i] is not None}

        def g(k):
            v = raw.get(k)
            return None if v is None or v <= -88 else float(v)   # -88 = none/unlimited, -99 = missing

        temp, dew, wsp = g("TMP"), g("DPT"), c.kt_to_mph(g("WSP"))
        rh = c.rh_from(temp, dew)
        cig = g("CIG")
        rows.append({
            "valid_time": vt,
            "lead_hours": int((vt - cycle).total_seconds() // 3600),
            "temp_f": temp,
            "dewpoint_f": dew,
            "rh_pct": rh,
            "apparent_temp_f": c.apparent_f(temp, rh, wsp),
            "cloud_cover_pct": g("SKY"),
            "wind_10m_mph": wsp,
            "wind_dir_deg": None if g("WDR") is None else g("WDR") * 10,
            "wind_gust_mph": c.kt_to_mph(g("GST")),
            "shortwave_wm2": None if g("SOL") is None else g("SOL") * 10,
            "precip_prob_pct": g(p["prob"]),
            "precip_in": None if g(p["qpf"]) is None else g(p["qpf"]) / 100,
            "snow_in": None if g(p["snow"]) is None else g(p["snow"]) / 10,
            "thunder_prob_pct": g(p["thunder"]),
            "visibility_mi": None if g("VIS") is None else g("VIS") / 10,
            "ceiling_ft": None if cig is None else cig * 100,
            "wet_bulb_globe_f": g("WBG"),
            "extra": raw,                                  # every element, NBM native units
        })
    return rows


def fetch_bulletin(url: str, product: str, wanted: Iterable[str]) -> dict[str, list[dict]]:
    """Stream one NBH/NBS file and return {icao: [hourly row dicts]} for the wanted stations."""
    wanted = set(wanted)
    out: dict[str, list[dict]] = {}
    station, cycle, block = None, None, []

    def flush():
        if station and block:
            out[station] = _rows_from_block(product, cycle, block)

    for line in c.iter_lines(url, timeout=300):
        if "GUIDANCE" in line:
            flush()
            m = _HEADER.match(line)
            station, block = None, []
            if m and m.group(1) in wanted:
                mo, dd, yy, hh = int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6))
                station, cycle = m.group(1), datetime(yy, mo, dd, hh, tzinfo=timezone.utc)
        elif station is not None:
            if line.strip():
                block.append(line)
            else:
                flush()
                station, block = None, []
    flush()
    return out


def fetch_cycle(cycle: datetime, product: str, wanted: Iterable[str],
                files: Optional[dict] = None) -> tuple[Optional[datetime], dict[str, list[dict]]]:
    """(issued_at, {icao: rows}) for one product/cycle; (None, {}) if NOAA has no file.

    Rows that start before issued_at (the hour already under way) are dropped.
    """
    files = files if files is not None else cycle_files(cycle)
    if product not in files:
        return None, {}
    url, lm = files[product]
    issued = issued_at(cycle, lm)
    data = fetch_bulletin(url, product, wanted)
    for icao in data:
        data[icao] = [r for r in data[icao] if r["valid_time"] >= issued]
    return issued, data
