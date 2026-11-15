"""Forecast sea conditions along a route: waves, wind and ocean currents, hourly, ~2 weeks.

Points come from a 2° lattice snapped from the route corridor, so nearby requests share them, and
each point's forecast is cached for 3 hours (per process). Sources (keyless; set OPEN_METEO_API_KEY
for the commercial endpoints in production):
  marine   wave height / direction / period, ocean current speed / direction (~9 days of forecast)
  forecast 10 m wind speed and direction (14 days)
Past the marine horizon the last forecast value is held (persistence) and flagged, so the router
can still cost long voyages but reports how much of the route was beyond the forecast.

Directions are converted to vectors: waves and wind "from" (meteorological) and currents "toward"
(oceanographic), each as eastward/northward components.
"""
from __future__ import annotations

import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np

LATTICE = 2.0
TTL_S = 3 * 3600
MARINE_DAYS = 10  # the marine forecast runs ~9 days; asking for more only adds empty hours
WIND_DAYS = 14  # Open-Meteo bills a request longer than 2 weeks as two
CHUNK = 80  # points per request (URL length)
HEADERS = {"User-Agent": "nostos-dev research tool"}
MARINE_VARS = ["wave_height", "wave_direction", "wave_period", "ocean_current_velocity", "ocean_current_direction"]
WIND_VARS = ["wind_speed_10m", "wind_direction_10m"]
_cache: dict[tuple[float, float], tuple[float, dict]] = {}
_lock = threading.Lock()


def _hosts() -> tuple[str, str, dict]:
    key = os.getenv("OPEN_METEO_API_KEY")
    if key:
        return ("https://customer-marine-api.open-meteo.com/v1/marine",
                "https://customer-api.open-meteo.com/v1/forecast", {"apikey": key})
    return "https://marine-api.open-meteo.com/v1/marine", "https://api.open-meteo.com/v1/forecast", {}


def lattice_point(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat / LATTICE) * LATTICE, ((round(lon / LATTICE) * LATTICE + 180) % 360) - 180)


class RateLimited(RuntimeError):
    """The forecast provider throttled us (free tier: 600 points/min, 10k/day)."""


def _fetch(url: str, chunk: list[tuple[float, float]], fields: list[str], days: int, extra: dict) -> list[dict]:
    r = httpx.get(url, params={"latitude": ",".join(f"{p[0]:g}" for p in chunk),
                               "longitude": ",".join(f"{p[1]:g}" for p in chunk),
                               "hourly": ",".join(fields), "forecast_days": days, "timezone": "GMT", **extra},
                  headers=HEADERS, timeout=httpx.Timeout(30.0))
    if r.status_code == 429:
        raise RateLimited("the forecast provider is rate-limiting this server; try again in a minute"
                          " (or set OPEN_METEO_API_KEY)")
    r.raise_for_status()
    body = r.json()
    return body if isinstance(body, list) else [body]


def _load(points: list[tuple[float, float]]) -> None:
    """Fetch every point not in the cache (all chunks of both sources in parallel) and store it."""
    now = time.monotonic()
    with _lock:
        missing = [p for p in points if p not in _cache or now - _cache[p][0] > TTL_S]
    if not missing:
        return
    marine_url, wind_url, key = _hosts()
    chunks = [missing[i:i + CHUNK] for i in range(0, len(missing), CHUNK)]
    # A long route is several chunks; one after another they took ~3 s each, so fetch them all at once.
    with ThreadPoolExecutor(min(16, 2 * len(chunks))) as pool:
        marine_f = [pool.submit(_fetch, marine_url, c, MARINE_VARS, MARINE_DAYS, key) for c in chunks]
        wind_f = [pool.submit(_fetch, wind_url, c, WIND_VARS, WIND_DAYS, {"wind_speed_unit": "ms", **key}) for c in chunks]
        marine = [m for f in marine_f for m in f.result()]
        wind = [w for f in wind_f for w in f.result()]
    with _lock:
        for p, m, w in zip(missing, marine, wind):
            hm, hw = m.get("hourly") or {}, w.get("hourly") or {}
            _cache[p] = (now, {"time": hw.get("time") or hm.get("time") or [],
                               **{f: hm.get(f) or [] for f in MARINE_VARS}, **{f: hw.get(f) or [] for f in WIND_VARS}})


def _filled(values: list, n: int) -> tuple[np.ndarray, int]:
    """Float array of length n with gaps held from the last value; also the last forecast index."""
    a = np.array([np.nan if v is None else v for v in values[:n]] + [np.nan] * max(0, n - len(values)), dtype=float)
    valid = np.flatnonzero(~np.isnan(a))
    if not len(valid):
        return a, -1
    a[:valid[0]] = a[valid[0]]
    for k in range(valid[0] + 1, n):
        if np.isnan(a[k]):
            a[k] = a[k - 1]
    return a, int(valid[-1])


@dataclass
class Field:
    """Hourly conditions at lattice points. Arrays are (points, hours) from `t0`."""
    t0: datetime
    points: np.ndarray  # (P, 2) lat, lon
    hs: np.ndarray  # significant wave height, m
    tp: np.ndarray  # wave period, s
    wave_from: np.ndarray  # degrees, direction waves come FROM
    wind: np.ndarray  # m/s
    wind_from: np.ndarray  # degrees
    cur_u: np.ndarray  # current toward east, m/s
    cur_v: np.ndarray  # current toward north, m/s
    is_sea: np.ndarray  # (P,) the marine model has data here
    horizon_h: int  # last hour with a real marine forecast (beyond: persistence)

    @property
    def hours(self) -> int:
        return self.hs.shape[1]

    def nearest(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        """Index of the nearest lattice point that is sea, for each coordinate."""
        sea = np.flatnonzero(self.is_sea)
        pts = self.points[sea]
        dlat = lat[:, None] - pts[None, :, 0]
        dlon = (lon[:, None] - pts[None, :, 1] + 180) % 360 - 180
        dlon *= np.cos(np.radians(lat))[:, None]
        return sea[np.argmin(dlat ** 2 + dlon ** 2, axis=1)]


def corridor(path_latlon: list[tuple[float, float]], radius_deg: float = 6.0, max_points: int = 350) -> list[tuple[float, float]]:
    """Lattice points within `radius_deg` of a path, thinned to at most `max_points`."""
    step = LATTICE
    while True:
        cells: set[tuple[float, float]] = set()
        reach = int(math.ceil(radius_deg / step))
        for lat, lon in path_latlon[::2] + path_latlon[-1:]:
            la = round(lat / step) * step
            lo = round(lon / step) * step
            for di in range(-reach, reach + 1):
                for dj in range(-reach, reach + 1):
                    plat = la + di * step
                    if -78 <= plat <= 78:
                        cells.add((plat, ((lo + dj * step + 180) % 360) - 180))
        if len(cells) <= max_points or step >= 8:
            return sorted(cells)
        step *= 2  # long ocean crossings: a coarser lattice keeps the request bounded


def field(points: list[tuple[float, float]], t0: datetime | None = None) -> Field:
    """Conditions at `points` (lattice coordinates) from the hour of `t0` onward."""
    _load(points)
    with _lock:
        rows = [_cache[p][1] for p in points]
    first = next((r["time"] for r in rows if r["time"]), None)
    if not first:
        raise ValueError("no forecast available for this route")
    base = datetime.fromisoformat(first[0]).replace(tzinfo=timezone.utc)
    t0 = (t0 or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    skip = max(0, int((t0 - base).total_seconds() // 3600))
    stale = len(first) - skip < 24
    if stale:  # a late leg of a long voyage: hold the last forecast day (persistence), flagged below
        skip = max(0, len(first) - 24)
    full = len(first)
    n = full - skip
    arrays: dict[str, list[np.ndarray]] = {k: [] for k in MARINE_VARS + WIND_VARS}
    horizon = []
    is_sea = []
    for r in rows:
        last_marine = -1
        for k in arrays:
            # Fill over the whole series first, so persistence carries the last marine forecast
            # (which ends ~9 days out) into hours where only wind is forecast.
            a, last = _filled(r.get(k) or [], full)
            arrays[k].append(a[skip:])
            if k == "wave_height":
                last_marine = last
        is_sea.append(last_marine >= 0)
        if last_marine >= 0:
            horizon.append(last_marine - skip)
    stack = {k: np.nan_to_num(np.vstack(v), nan=0.0) for k, v in arrays.items()}
    speed = stack["ocean_current_velocity"] / 3.6  # km/h -> m/s
    toward = np.radians(stack["ocean_current_direction"])
    return Field(t0=t0, points=np.array(points, dtype=float),
                 hs=stack["wave_height"], tp=np.where(stack["wave_period"] > 0, stack["wave_period"], 6.0),
                 wave_from=stack["wave_direction"], wind=stack["wind_speed_10m"], wind_from=stack["wind_direction_10m"],
                 cur_u=speed * np.sin(toward), cur_v=speed * np.cos(toward),
                 is_sea=np.array(is_sea, dtype=bool),
                 horizon_h=-1 if stale else int(np.median(horizon)) if horizon else 0)


def calm(points: list[tuple[float, float]], t0: datetime | None = None, hours: int = 24 * 14) -> Field:
    """Neutral sea (no waves, wind or current) when the forecast can't be fetched: the route still plans on
    distance and the vessel's service speed, and every hour counts as beyond the forecast."""
    z = np.zeros((len(points), hours))
    return Field(t0=(t0 or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0),
                 points=np.array(points, dtype=float), hs=z, tp=z + 6.0, wave_from=z, wind=z, wind_from=z,
                 cur_u=z, cur_v=z, is_sea=np.ones(len(points), dtype=bool), horizon_h=-1)


def depart_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if not now - timedelta(hours=1) <= t <= now + timedelta(days=7):
        raise ValueError("departure must be between now and 7 days ahead (the forecast window)")
    return t
