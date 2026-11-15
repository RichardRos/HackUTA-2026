"""US tides from NOAA CO-OPS (Tides & Currents), keyless.

Three layers, each cached so the app never hammers NOAA:
  tide_stations()        every tidal water-level station (~240, Great Lakes excluded), daily
  tide_overview()        high/low predictions for all of them, yesterday through +2 days, every 6 h,
                         plus each station's state right now (level, rising/falling, next turn)
  tide_station(id)       one station: 15-min predicted curve, observed water level and the
                         highs/lows for the last 24 h and next 48 h, every 10 min

All times are UTC ISO strings, heights metres above MLLW (mean lower low water, the chart datum).
Between two predicted turns the level follows a half cosine, which is how NOAA's own tide
tables interpolate and matches the harmonic curve to a few centimetres.
"""

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

from world_data import TIMEOUT, keep_last, ttl_cache

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STATIONS = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
HEADERS = {"User-Agent": "nostos-dev research tool"}
DATUM = "MLLW"
_WORKERS = 8  # parallel requests to CO-OPS; ~240 stations finish in a few seconds


class NoaaError(httpx.HTTPError):
    """CO-OPS answered 200 with an error body. An HTTPError so callers treat it as upstream."""


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%d %H:%M")


def _iso(t: str) -> str:
    """CO-OPS 'YYYY-MM-DD HH:MM' (GMT) -> ISO UTC."""
    return t.replace(" ", "T") + ":00Z"


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _get(url: str, params: dict) -> dict:
    r = httpx.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if isinstance(body, dict) and "error" in body:
        raise NoaaError(str(body["error"].get("message", "CO-OPS error")).strip())
    return body


def _series(station_id: str, product: str, begin: datetime, end: datetime, interval: str | None = None) -> list[dict]:
    params = {"product": product, "station": station_id, "begin_date": _ts(begin), "end_date": _ts(end),
              "datum": DATUM, "units": "metric", "time_zone": "gmt", "format": "json", "application": "nostos"}
    if interval:
        params["interval"] = interval
    body = _get(API, params)
    rows = body.get("predictions") or body.get("data") or []
    out = []
    for row in rows:
        try:
            point = {"t": _iso(row["t"]), "v": round(float(row["v"]), 3)}
        except (KeyError, TypeError, ValueError):
            continue  # CO-OPS leaves "v" empty for missing observations
        if row.get("type") in ("H", "L"):
            point["type"] = row["type"]
        out.append(point)
    return out


@ttl_cache(24 * 3600)
@keep_last
def tide_stations() -> list[dict]:
    """Tidal water-level stations (Great Lakes have no tides), sorted by id."""
    body = _get(STATIONS, {"type": "waterlevels"})
    out = []
    for s in body.get("stations", []):
        if not s.get("tidal") or s.get("greatlakes") or s.get("lat") is None or s.get("lng") is None:
            continue
        out.append({"id": str(s["id"]), "name": s.get("name") or str(s["id"]), "state": s.get("state") or None,
                    "lat": float(s["lat"]), "lon": float(s["lng"]), "tide_type": s.get("tideType") or None})
    if not out:
        raise NoaaError("CO-OPS returned no tidal stations")
    return sorted(out, key=lambda s: s["id"])


def tide_now(events: list[dict], at: datetime) -> dict | None:
    """Level and direction at `at`, cosine-interpolated between the surrounding high and low."""
    for a, b in zip(events, events[1:]):
        ta, tb = _parse(a["t"]), _parse(b["t"])
        if not ta <= at <= tb or tb <= ta:
            continue
        f = (at - ta) / (tb - ta)
        level = a["v"] + (b["v"] - a["v"]) * (1 - math.cos(math.pi * f)) / 2
        hours = (tb - ta).total_seconds() / 3600
        rate = (b["v"] - a["v"]) * math.pi / 2 * math.sin(math.pi * f) / hours  # m per hour
        return {"level_m": round(level, 3), "rate_m_per_h": round(rate, 3), "rising": b["v"] > a["v"],
                "next": {"type": b.get("type"), "t": b["t"], "v": b["v"]}}
    return None


def _station_events(station: dict, begin: datetime, end: datetime) -> dict | None:
    try:
        events = _series(station["id"], "predictions", begin, end, "hilo")
    except (httpx.HTTPError, ValueError):
        return None  # one station down must not drop the layer
    return {**station, "events": events} if len(events) >= 2 else None


@ttl_cache(6 * 3600)
@keep_last
def _overview() -> dict:
    stations = tide_stations()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    begin, end = today - timedelta(days=1), today + timedelta(days=3) - timedelta(minutes=1)
    with ThreadPoolExecutor(_WORKERS) as pool:
        rows = [r for r in pool.map(lambda s: _station_events(s, begin, end), stations) if r]
    if not rows:
        raise NoaaError("CO-OPS returned no tide predictions")
    return {"source": "NOAA CO-OPS tide predictions", "datum": DATUM, "units": "m",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "window": {"start": begin.isoformat(), "end": end.isoformat()}, "stations": rows}


def tide_overview() -> dict:
    """High/low predictions for every US tidal station, each with its state at request time."""
    base = _overview()
    now = datetime.now(timezone.utc)
    return {**base, "as_of": now.isoformat(),
            "stations": [{**s, "now": tide_now(s["events"], now)} for s in base["stations"]]}


@ttl_cache(600)
@keep_last
def tide_station(station_id: str) -> dict:
    """Predicted curve (15 min), observed level and high/low turns from 24 h ago to 48 h ahead."""
    station = next((s for s in tide_stations() if s["id"] == station_id), None)
    if station is None:
        raise ValueError(f"unknown tidal station {station_id}")
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    begin, end = now - timedelta(hours=24), now + timedelta(hours=48)
    predicted = _series(station_id, "predictions", begin, end, "15")
    events = _series(station_id, "predictions", begin - timedelta(hours=13), end + timedelta(hours=13), "hilo")
    try:
        observed, observed_error = _series(station_id, "water_level", begin, now), None
    except (httpx.HTTPError, ValueError) as exc:  # gauges go offline; predictions still stand
        observed, observed_error = [], str(exc)
    return {"source": "NOAA CO-OPS", "datum": DATUM, "units": "m", "station": station,
            "fetched_at": now.isoformat(), "now": tide_now(events, now),
            "predicted": predicted, "observed": observed, "observed_error": observed_error,
            "events": [e for e in events if begin <= _parse(e["t"]) <= end]}
