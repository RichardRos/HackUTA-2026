"""NOAA Global Forecast System (GFS), 0.25° global grid, from NOAA's open-data
bucket on AWS (noaa-gfs-bdp-pds). Same files for history (back to 2021) and live.

How it reads efficiently
  Each forecast step is one ~500 MB GRIB2 file holding ~740 fields, plus a
  small text index (.idx) giving every field's byte offset. We read the index,
  then download only the fields we need with HTTP Range requests (~0.5-1 MB
  each), decode them with ECMWF's eccodes into numpy, and pull the values at
  all points in one indexing step. A run with 72 hourly steps x 16 fields is
  ~1 GB of downloads instead of ~36 GB of whole files.

What's stored
  Point values in weather_forecasts (model 'gfs_0p25', source_api 'gfs_grib')
  for every weather point, US and global. Each point uses its nearest land
  grid cell.

Runs: 00/06/12/18Z. Hourly steps to 120 h, 3-hourly to 384 h. Averages
(DSWRF) reset every 6 h and precipitation is also given as a run total; both
are turned into the mean / total over the period since the previous stored step.
issued_at = when NOAA posted the last step used (S3 upload time).
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

import common as c

BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
MODEL = "gfs_0p25"
SOURCE_API = "gfs_grib"
_S3NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

# key, GRIB short name, level, kind, core
#   kind: inst = value at valid time; ave = mean over a window (resets every 6 h);
#         total = accumulated since the run started
FIELDS = [
    ("t2", "TMP", "2 m above ground", "inst", True),
    ("td2", "DPT", "2 m above ground", "inst", True),
    ("rh2", "RH", "2 m above ground", "inst", True),
    ("at2", "APTMP", "2 m above ground", "inst", True),
    ("u10", "UGRD", "10 m above ground", "inst", True),
    ("v10", "VGRD", "10 m above ground", "inst", True),
    ("u100", "UGRD", "100 m above ground", "inst", True),
    ("v100", "VGRD", "100 m above ground", "inst", True),
    ("gust", "GUST", "surface", "inst", True),
    ("tcc", "TCDC", "entire atmosphere", "inst", True),
    ("dswrf", "DSWRF", "surface", "ave", True),
    ("apcp", "APCP", "surface", "total", True),
    ("vis", "VIS", "surface", "inst", True),
    ("ceil", "HGT", "cloud ceiling", "inst", True),
    ("prmsl", "PRMSL", "mean sea level", "inst", True),
    ("cape", "CAPE", "surface", "inst", True),
    # --gfs-fields all adds these
    ("psfc", "PRES", "surface", "inst", False),
    ("cpofp", "CPOFP", "surface", "inst", False),
    ("csnow", "CSNOW", "surface", "inst", False),
    ("crain", "CRAIN", "surface", "inst", False),
    ("cfrzr", "CFRZR", "surface", "inst", False),
    ("pwat", "PWAT", "entire atmosphere (considered as a single layer)", "inst", False),
    ("hpbl", "HPBL", "surface", "inst", False),
    ("snod", "SNOD", "surface", "inst", False),
    ("dlwrf", "DLWRF", "surface", "ave", False),
    ("u80", "UGRD", "80 m above ground", "inst", False),
    ("v80", "VGRD", "80 m above ground", "inst", False),
]


def fields(which: str = "core") -> list[tuple]:
    return [f for f in FIELDS if which == "all" or f[4]]


def parse_leads(spec: str) -> list[int]:
    """'1-72' -> 1..72; '12-48/3' -> 12,15,..,48; '6,12,24' -> those. Comma-joinable."""
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)(?:/(\d+))?", part)
        if m:
            a, b, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
            out.update(range(a, b + 1, s))
        else:
            out.add(int(part))
    # GFS has hourly steps to 120 h, then every 3 h to 384 h
    return sorted(h for h in out if 0 <= h <= 120 or (120 < h <= 384 and h % 3 == 0))


# ---------------------------------------------------------------------------
# Files and index
# ---------------------------------------------------------------------------
def _base(cycle: datetime) -> str:
    return f"{BUCKET}/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/gfs.t{cycle:%H}z.pgrb2.0p25.f"


def run_files(cycle: datetime) -> dict[int, datetime]:
    """{forecast hour: S3 upload time} for the 0.25° GRIB files of one run."""
    prefix = f"gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/gfs.t{cycle:%H}z.pgrb2.0p25.f"
    out, token = {}, None
    while True:
        params = [("list-type", 2), ("prefix", prefix)] + ([("continuation-token", token)] if token else [])
        root = ET.fromstring(c.get_bytes(BUCKET + "/", params))
        for item in root.iter(f"{_S3NS}Contents"):
            m = re.search(r"\.f(\d{3})$", item.findtext(f"{_S3NS}Key") or "")
            if m:
                out[int(m.group(1))] = c.parse_utc(item.findtext(f"{_S3NS}LastModified")[:19])
        token = root.findtext(f"{_S3NS}NextContinuationToken")
        if not token:
            return out


def issued_at(cycle: datetime, uploads: list[datetime]) -> datetime:
    """Latest upload among the steps used; cycle + 5 h if that looks like a later re-upload."""
    last = max(uploads)
    return last if last <= cycle + timedelta(hours=12) else cycle + timedelta(hours=5)


def _index(url: str) -> list[tuple[int, Optional[int], str, str, str]]:
    """(start, end or None, var, level, label) per field, from the .idx file."""
    lines = [l.split(":") for l in c.get_bytes(url + ".idx").decode().splitlines() if l]
    out = []
    for i, l in enumerate(lines):
        end = int(lines[i + 1][1]) - 1 if i + 1 < len(lines) else None
        out.append((int(l[1]), end, l[3], l[4], l[5]))
    return out


_WINDOW = re.compile(r"^(\d+)-(\d+) (hour|day)")


def _window_start(label: str) -> Optional[int]:
    m = _WINDOW.match(label)
    if not m:
        return None
    return int(m.group(1)) * (24 if m.group(3) == "day" else 1)


def _pick(index, var: str, level: str, kind: str):
    for start, end, v, lev, label in index:
        if v != var or lev != level:
            continue
        if kind == "inst" and not any(w in label for w in ("ave", "acc", "max", "min")):
            return start, end, label
        if kind == "ave" and "ave" in label:
            return start, end, label
        if kind == "total" and "acc" in label and label.startswith("0-"):
            return start, end, label
    return None


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------
def _decode(blob: bytes) -> tuple[np.ndarray, dict]:
    import eccodes

    h = eccodes.codes_new_from_message(blob)
    try:
        eccodes.codes_set(h, "missingValue", 1e20)
        grid = {k: eccodes.codes_get(h, k) for k in (
            "Ni", "Nj", "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
            "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees", "jScansPositively")}
        vals = eccodes.codes_get_values(h).astype(np.float64)
    finally:
        eccodes.codes_release(h)
    vals[vals >= 1e19] = np.nan
    return vals, grid


def _fetch_field(url: str, start: int, end: Optional[int]) -> tuple[np.ndarray, dict]:
    rng = f"bytes={start}-{'' if end is None else end}"
    return _decode(c.get_bytes(url, headers={"Range": rng}, timeout=120))


def _cell_index(grid: dict, lat: float, lon: float) -> tuple[int, int]:
    lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
    dj = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
    i = int(round((lat - lat0) / dj))
    j = int(round(((lon - lon0) % 360) / grid["iDirectionIncrementInDegrees"])) % grid["Ni"]
    return max(0, min(grid["Nj"] - 1, i)), j


def snap_to_land(points: list[tuple[str, float, float]], land: np.ndarray, grid: dict) -> dict[int, list[str]]:
    """{flat cell index: [point_ids]}: nearest grid cell, moved to the nearest land
    cell within 2 cells (~50 km) when the nearest one is water (coastal cities)."""
    ni, nj = grid["Ni"], grid["Nj"]
    land2d = land.reshape(nj, ni)
    cells: dict[int, list[str]] = {}
    for pid, lat, lon in points:
        i, j = _cell_index(grid, lat, lon)
        if not land2d[i, j] >= 0.5:
            best = None
            for di in range(-2, 3):
                for dj in range(-2, 3):
                    ii, jj = i + di, (j + dj) % ni
                    if 0 <= ii < nj and land2d[ii, jj] >= 0.5:
                        d = di * di + dj * dj
                        if best is None or d < best[0]:
                            best = (d, ii, jj)
            if best:
                i, j = best[1], best[2]
        cells.setdefault(i * ni + j, []).append(pid)
    return cells


# ---------------------------------------------------------------------------
# Run -> rows
# ---------------------------------------------------------------------------
def _k_to_f(k):
    return (k - 273.15) * 9 / 5 + 32


def _speed_dir(u, v):
    spd = math.hypot(u, v)
    return spd * 2.236936, (math.degrees(math.atan2(-u, -v)) % 360)


def fetch_run(cycle: datetime, leads: list[int], points: list[tuple[str, float, float]],
              which: str = "core", workers: int = 16,
              files: Optional[dict[int, datetime]] = None):
    """Returns (issued_at, {flat cell: [point_ids]}, {flat cell: [row dicts]}), or None
    when NOAA hasn't posted every requested step of this run yet."""
    files = files if files is not None else run_files(cycle)
    if not leads or any(h not in files for h in leads):
        return None
    issued = issued_at(cycle, [files[h] for h in leads])
    flds = fields(which)
    period = {k for k, _, _, kind, _ in flds if kind in ("ave", "total")}

    # Steps to read: the requested ones, plus the step before the first one for
    # averaged/accumulated fields (so the first period is exact too).
    prior = max([h for h in files if h < leads[0]], default=None)
    steps = ([prior] if prior is not None else []) + leads
    base = _base(cycle)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        indexes = dict(zip(steps, pool.map(lambda h: _index(f"{base}{h:03d}"), steps)))

        # Land mask -> grid cells for every point (once per run; ~30 KB)
        land_entry = _pick(indexes[leads[0]], "LAND", "surface", "inst")
        land, grid = _fetch_field(f"{base}{leads[0]:03d}", land_entry[0], land_entry[1])
        cells = snap_to_land(points, land, grid)
        flat = np.array(sorted(cells))

        tasks = {}
        for h in steps:
            for key, var, level, kind, _ in flds:
                if h == prior and key not in period:
                    continue
                entry = _pick(indexes[h], var, level, kind)
                if entry:
                    tasks[(h, key)] = (entry[2], pool.submit(
                        lambda e=entry, h=h: _fetch_field(f"{base}{h:03d}", e[0], e[1])[0][flat]))
        values = {hk: (label, fut.result()) for hk, (label, fut) in tasks.items()}

    rows: dict[int, list[dict]] = {cell: [] for cell in flat.tolist()}
    prev_h = prior
    for h in leads:
        vt = cycle + timedelta(hours=h)
        if vt < issued:
            prev_h = h
            continue
        get = lambda k, hh=h: values.get((hh, k), (None, None))[1]

        # period fields: mean / total over (prev_h, h]
        dswrf = None
        if (h, "dswrf") in values:
            label, cur = values[(h, "dswrf")]
            a = _window_start(label)
            if prev_h is not None and a is not None and prev_h > a and (prev_h, "dswrf") in values:
                pv = values[(prev_h, "dswrf")][1]
                dswrf = (cur * (h - a) - pv * (prev_h - a)) / (h - prev_h)
            else:
                dswrf = cur
            dswrf = np.clip(dswrf, 0, None)
        dlwrf = None
        if (h, "dlwrf") in values:
            label, cur = values[(h, "dlwrf")]
            a = _window_start(label)
            if prev_h is not None and a is not None and prev_h > a and (prev_h, "dlwrf") in values:
                dlwrf = (cur * (h - a) - values[(prev_h, "dlwrf")][1] * (prev_h - a)) / (h - prev_h)
            else:
                dlwrf = cur
        apcp = None
        if (h, "apcp") in values:
            cur = values[(h, "apcp")][1]
            before = values[(prev_h, "apcp")][1] if prev_h is not None and (prev_h, "apcp") in values else 0
            apcp = np.clip(cur - before, 0, None)

        for n, cell in enumerate(flat.tolist()):
            def v(key):
                arr = get(key)
                if arr is None:
                    return None
                x = float(arr[n])
                return None if math.isnan(x) else x

            t, td, at = v("t2"), v("td2"), v("at2")
            u10, v10, u100, v100 = v("u10"), v("v10"), v("u100"), v("v100")
            spd10, dir10 = _speed_dir(u10, v10) if u10 is not None and v10 is not None else (None, None)
            spd100 = _speed_dir(u100, v100)[0] if u100 is not None and v100 is not None else None
            gust, vis, ceil = v("gust"), v("vis"), v("ceil")
            extra = {}
            for key, label, conv in (("prmsl", "prmsl_hpa", lambda x: x / 100),
                                     ("cape", "cape_jkg", None), ("psfc", "psfc_hpa", lambda x: x / 100),
                                     ("cpofp", "frozen_precip_pct", lambda x: None if x < 0 else x),
                                     ("csnow", "snow_flag", None), ("crain", "rain_flag", None),
                                     ("cfrzr", "freezing_rain_flag", None), ("pwat", "pwat_mm", None),
                                     ("hpbl", "pbl_height_m", None), ("snod", "snow_depth_in", lambda x: x * 39.3701)):
                x = v(key)
                if x is not None:
                    x = conv(x) if conv else x
                    if x is not None:
                        extra[label] = round(x, 3)
            if dlwrf is not None and not math.isnan(dlwrf[n]):
                extra["longwave_down_wm2"] = round(float(dlwrf[n]), 1)
            if v("u80") is not None and v("v80") is not None:
                extra["wind_80m_mph"] = round(_speed_dir(v("u80"), v("v80"))[0], 2)
            rows[cell].append({
                "valid_time": vt,
                "lead_hours": h,
                "temp_f": None if t is None else _k_to_f(t),
                "dewpoint_f": None if td is None else _k_to_f(td),
                "apparent_temp_f": None if at is None else _k_to_f(at),
                "rh_pct": v("rh2"),
                "cloud_cover_pct": v("tcc"),
                "wind_10m_mph": spd10,
                "wind_dir_deg": dir10,
                "wind_100m_mph": spd100,
                "wind_gust_mph": None if gust is None else gust * 2.236936,
                "shortwave_wm2": None if dswrf is None or math.isnan(dswrf[n]) else float(dswrf[n]),
                "precip_in": None if apcp is None or math.isnan(apcp[n]) else float(apcp[n]) / 25.4,
                "visibility_mi": None if vis is None else vis / 1609.344,
                # GFS reports "no ceiling" as ~20,000 m; real ceilings never get near 15 km
                "ceiling_ft": None if ceil is None or ceil <= 0 or ceil >= 15000 else ceil * 3.28084,
                "extra": extra,
            })
        prev_h = h
    return issued, cells, rows
