"""Tides worldwide: NOAA CO-OPS (Tides & Currents) for US waters, a global tide model elsewhere. Keyless.

Three kinds of station, each tagged with `kind`:
  gauge     NOAA water-level station: harmonic predictions plus a live observed level (~240)
  harmonic  NOAA prediction-only station, added only where no gauge is within GAP_KM (~120, mostly
            Alaska and Pacific islands)
  model     ~135 coastal points worldwide from Open-Meteo Marine's hourly `sea_level_height_msl`
            (a global tide model plus surge); highs and lows are read off the hourly curve

Entry points, each cached so the app never hammers either source:
  tide_stations()        NOAA gauges + gap-filling harmonic stations, daily
  tide_overview()        high/low turns for every station, yesterday through +2 days, every 6 h,
                         plus each station's state right now (level, rising/falling, next turn)
  tide_station(id)       one station: predicted curve, observed level (gauges only) and the
                         highs/lows from 24 h ago to 48 h ahead, every 10 min

All times are UTC ISO strings, heights metres. NOAA heights are above MLLW (mean lower low water,
the chart datum); model heights are relative to mean sea level, so each station carries `datum`.
Between two turns the level follows a half cosine, which is how NOAA's own tide tables interpolate.
"""

import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

from world_data import TIMEOUT, stored

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STATIONS = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
MARINE = "https://marine-api.open-meteo.com/v1/marine"
HEADERS = {"User-Agent": "nostos-dev research tool"}
DATUM = "MLLW"
GAP_KM = 60  # a harmonic or model point joins only if nothing already shown is this close
_WORKERS = 12  # parallel requests to CO-OPS; ~360 stations finish in well under a minute
_MARINE_CHUNK = 50  # points per Open-Meteo request. Each point counts as a call on the free tier
                    # (10k/day, shared with world_data's grids): 135 points x 4 refreshes = 540/day.
MIN_SWING_M = 0.03  # model turns closer than this are wiggles (surge noise), not tides
STATION_ID = r"^(\d{7}|om-[a-z0-9-]{1,48})$"

# (name, country, lat, lon): just offshore of each port, so the marine model has a sea cell there.
GLOBAL_POINTS = [
    ("Halifax", "Canada", 44.60, -63.50), ("Saint John (Bay of Fundy)", "Canada", 45.24, -66.06),
    ("St. John's", "Canada", 47.57, -52.68), ("Vancouver", "Canada", 49.28, -123.20),
    ("Prince Rupert", "Canada", 54.30, -130.40), ("Churchill", "Canada", 58.80, -94.15),
    ("Iqaluit", "Canada", 63.70, -68.55), ("Victoria", "Canada", 48.40, -123.40),
    ("Ensenada", "Mexico", 31.84, -116.66), ("Mazatlán", "Mexico", 23.18, -106.45),
    ("Acapulco", "Mexico", 16.83, -99.92), ("Veracruz", "Mexico", 19.22, -96.10),
    ("Cancún", "Mexico", 21.17, -86.75), ("Panama City", "Panama", 8.90, -79.50),
    ("Colón", "Panama", 9.40, -79.92), ("Cartagena", "Colombia", 10.38, -75.58),
    ("Buenaventura", "Colombia", 3.85, -77.15), ("Guayaquil (Puná)", "Ecuador", -2.75, -80.20),
    ("Callao", "Peru", -12.05, -77.20), ("Valparaíso", "Chile", -33.00, -71.65),
    ("Puerto Montt", "Chile", -41.55, -72.95), ("Punta Arenas", "Chile", -53.15, -70.85),
    ("Beagle Channel (Ushuaia)", "Argentina", -55.05, -66.80), ("Río Gallegos", "Argentina", -51.60, -68.90),
    ("Mar del Plata", "Argentina", -38.05, -57.50), ("Montevideo", "Uruguay", -34.95, -56.20),
    ("Santos", "Brazil", -24.05, -46.30), ("Rio de Janeiro", "Brazil", -22.95, -43.15),
    ("Salvador", "Brazil", -12.98, -38.55), ("Recife", "Brazil", -8.05, -34.85),
    ("São Luís", "Brazil", -2.45, -44.30), ("Belém (Amazon mouth)", "Brazil", -0.60, -48.20),
    ("Georgetown", "Guyana", 6.90, -58.20), ("Port of Spain", "Trinidad and Tobago", 10.65, -61.55),
    ("Kingston", "Jamaica", 17.90, -76.80), ("Havana", "Cuba", 23.17, -82.35),
    ("Nassau", "Bahamas", 25.10, -77.35), ("Reykjavík", "Iceland", 64.17, -22.00),
    ("Nuuk", "Greenland", 64.15, -51.80), ("Bergen", "Norway", 60.40, 5.25),
    ("Tromsø", "Norway", 70.00, 18.00), ("Murmansk (Kola Bay)", "Russia", 69.45, 33.70),
    ("Arkhangelsk", "Russia", 64.75, 40.30), ("Aberdeen", "United Kingdom", 57.15, -2.02),
    ("Liverpool", "United Kingdom", 53.45, -3.10), ("Bristol Channel", "United Kingdom", 51.40, -3.30),
    ("Thames Estuary", "United Kingdom", 51.50, 1.20), ("Dover", "United Kingdom", 51.10, 1.35),
    ("Dublin", "Ireland", 53.35, -6.10), ("Galway", "Ireland", 53.25, -9.10),
    ("Saint-Malo", "France", 48.68, -2.05), ("Brest", "France", 48.35, -4.55),
    ("Le Havre", "France", 49.50, 0.05), ("Bordeaux (Gironde)", "France", 45.60, -1.20),
    ("Rotterdam", "Netherlands", 51.98, 4.00), ("Hamburg (Elbe)", "Germany", 53.90, 8.60),
    ("Esbjerg", "Denmark", 55.45, 8.35), ("Lisbon", "Portugal", 38.65, -9.30),
    ("Cádiz", "Spain", 36.55, -6.35), ("Gibraltar", "Gibraltar", 36.12, -5.33),
    ("Venice", "Italy", 45.38, 12.45), ("Casablanca", "Morocco", 33.62, -7.65),
    ("Dakar", "Senegal", 14.65, -17.48), ("Conakry", "Guinea", 9.45, -13.80),
    ("Abidjan", "Côte d'Ivoire", 5.20, -4.00), ("Lagos", "Nigeria", 6.35, 3.40),
    ("Luanda", "Angola", -8.75, 13.20), ("Walvis Bay", "Namibia", -22.95, 14.45),
    ("Cape Town", "South Africa", -33.88, 18.40), ("Durban", "South Africa", -29.88, 31.10),
    ("Maputo", "Mozambique", -26.00, 32.80), ("Beira", "Mozambique", -19.90, 34.95),
    ("Toamasina", "Madagascar", -18.15, 49.45), ("Port Louis", "Mauritius", -20.15, 57.45),
    ("Dar es Salaam", "Tanzania", -6.80, 39.35), ("Mombasa", "Kenya", -4.10, 39.75),
    ("Djibouti", "Djibouti", 11.65, 43.10), ("Jeddah", "Saudi Arabia", 21.50, 39.05),
    ("Suez", "Egypt", 29.85, 32.55), ("Muscat", "Oman", 23.70, 58.65),
    ("Dubai", "United Arab Emirates", 25.30, 55.20), ("Kuwait", "Kuwait", 29.40, 48.10),
    ("Karachi", "Pakistan", 24.75, 66.90), ("Mumbai", "India", 18.90, 72.75),
    ("Gulf of Khambhat", "India", 21.40, 72.40), ("Kochi", "India", 9.95, 76.15),
    ("Chennai", "India", 13.10, 80.35), ("Kolkata (Sagar)", "India", 21.55, 88.05),
    ("Chittagong", "Bangladesh", 22.20, 91.75), ("Colombo", "Sri Lanka", 6.95, 79.80),
    ("Yangon (Elephant Point)", "Myanmar", 16.40, 96.30), ("Phuket", "Thailand", 7.85, 98.30),
    ("Bangkok (Gulf)", "Thailand", 13.30, 100.70), ("Singapore Strait", "Singapore", 1.22, 103.85),
    ("Port Klang", "Malaysia", 2.95, 101.25), ("Vung Tau", "Vietnam", 10.30, 107.10),
    ("Haiphong", "Vietnam", 20.65, 106.90), ("Manila", "Philippines", 14.55, 120.85),
    ("Jakarta", "Indonesia", -6.05, 106.85), ("Surabaya", "Indonesia", -7.10, 112.75),
    ("Hong Kong", "China", 22.25, 114.20), ("Shanghai (Yangtze)", "China", 31.15, 122.20),
    ("Qingdao", "China", 36.00, 120.40), ("Tianjin", "China", 38.95, 117.95),
    ("Incheon", "South Korea", 37.40, 126.45), ("Busan", "South Korea", 35.05, 129.10),
    ("Tokyo Bay", "Japan", 35.45, 139.80), ("Osaka Bay", "Japan", 34.55, 135.25),
    ("Kagoshima", "Japan", 31.55, 130.60), ("Hakodate", "Japan", 41.75, 140.70),
    ("Vladivostok", "Russia", 43.05, 131.95), ("Petropavlovsk-Kamchatsky", "Russia", 52.95, 158.70),
    ("Magadan", "Russia", 59.50, 150.80), ("Keelung", "Taiwan", 25.20, 121.75),
    ("Darwin", "Australia", -12.40, 130.80), ("Broome", "Australia", -18.00, 122.15),
    ("Derby (King Sound)", "Australia", -17.10, 123.55), ("Fremantle", "Australia", -32.05, 115.65),
    ("Adelaide", "Australia", -34.80, 138.40), ("Port Phillip", "Australia", -38.30, 144.65),
    ("Hobart", "Australia", -42.95, 147.40), ("Sydney", "Australia", -33.85, 151.30),
    ("Moreton Bay", "Australia", -27.30, 153.25), ("Mackay", "Australia", -21.10, 149.30),
    ("Cairns", "Australia", -16.85, 145.80), ("Port Moresby", "Papua New Guinea", -9.50, 147.10),
    ("Auckland", "New Zealand", -36.80, 174.85), ("Wellington", "New Zealand", -41.35, 174.80),
    ("Lyttelton", "New Zealand", -43.60, 172.80), ("Nouméa", "New Caledonia", -22.30, 166.40),
    ("Suva", "Fiji", -18.15, 178.42), ("Papeete", "French Polynesia", -17.50, -149.60),
    ("Apia", "Samoa", -13.80, -171.75), ("Tarawa", "Kiribati", 1.40, 172.95),
]


class NoaaError(httpx.HTTPError):
    """An upstream answered 200 with an error body. An HTTPError so callers treat it as upstream."""


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%d %H:%M")


def _iso(t: str) -> str:
    """CO-OPS 'YYYY-MM-DD HH:MM' or Open-Meteo 'YYYY-MM-DDTHH:MM' (both GMT) -> ISO UTC."""
    return t.replace(" ", "T")[:16] + ":00Z"


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2, dl = math.radians(a[0]), math.radians(b[0]), math.radians(b[1] - a[1])
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return 6371 * math.acos(max(-1.0, min(1.0, c)))


def _far_from(point: tuple[float, float], kept: list[tuple[float, float]], km: float = GAP_KM) -> bool:
    return all(_km(point, q) >= km for q in kept)


def _slug(name: str) -> str:
    return "om-" + re.sub(r"[^a-z0-9]+", "-", name.lower().encode("ascii", "ignore").decode()).strip("-")[:48]


def _get(url: str, params: dict) -> dict | list:
    r = httpx.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if isinstance(body, dict) and "error" in body:
        message = body["error"].get("message", "upstream error") if isinstance(body["error"], dict) else body.get("reason")
        raise NoaaError(str(message).strip())
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


def _noaa_station(s: dict, kind: str) -> dict:
    return {"id": str(s["id"]), "name": s.get("name") or str(s["id"]), "state": s.get("state") or None,
            "lat": float(s["lat"]), "lon": float(s["lng"]), "tide_type": s.get("tideType") or None,
            "kind": kind, "datum": DATUM, "source": "NOAA CO-OPS"}


@stored(ttl=24 * 3600, max_age=7 * 24 * 3600)
def tide_stations() -> list[dict]:
    """NOAA tidal gauges (Great Lakes have no tides), then harmonic prediction stations that fill gaps."""
    gauges = [_noaa_station(s, "gauge") for s in _get(STATIONS, {"type": "waterlevels"}).get("stations", [])
              if s.get("tidal") and not s.get("greatlakes") and s.get("lat") is not None and s.get("lng") is not None]
    if not gauges:
        raise NoaaError("CO-OPS returned no tidal stations")
    kept = [(g["lat"], g["lon"]) for g in gauges]
    extra = []
    try:
        listed = _get(STATIONS, {"type": "tidepredictions"}).get("stations", [])
    except (httpx.HTTPError, ValueError):
        listed = []  # the gauges alone still make a layer
    for s in sorted(listed, key=lambda s: str(s.get("id"))):
        if s.get("type") != "R" or s.get("lat") is None or s.get("lng") is None:
            continue  # subordinate stations are offsets of a reference station; skip them
        point = (float(s["lat"]), float(s["lng"]))
        if _far_from(point, kept):
            kept.append(point)
            extra.append(_noaa_station(s, "harmonic"))
    return sorted(gauges, key=lambda s: s["id"]) + extra


def turns(series: list[dict], min_swing: float = MIN_SWING_M) -> list[dict]:
    """High and low turns of an hourly curve: local extrema refined by a parabola through the three
    hours around each, forced to alternate, with swings under `min_swing` dropped as noise."""
    found: list[dict] = []
    for i in range(1, len(series) - 1):
        a, b, c = series[i - 1]["v"], series[i]["v"], series[i + 1]["v"]
        kind = "H" if b >= a and b > c else "L" if b <= a and b < c else None
        if kind is None:
            continue
        curve = a - 2 * b + c
        offset = 0.5 * (a - c) / curve if curve else 0.0  # hours from the sample to the vertex
        peak = b - 0.25 * (a - c) * offset
        t = _parse(series[i]["t"]) + timedelta(hours=offset)
        event = {"t": t.strftime("%Y-%m-%dT%H:%M:00Z"), "v": round(peak, 3), "type": kind}
        if found and found[-1]["type"] == kind:  # two highs in a row: keep the higher (lows: lower)
            if (kind == "H") == (peak > found[-1]["v"]):
                found[-1] = event
            continue
        found.append(event)
    changed = True
    while changed:  # drop tiny high/low pairs until every swing is a real tide
        changed = False
        for k in range(1, len(found)):
            if abs(found[k]["v"] - found[k - 1]["v"]) < min_swing:
                del found[k - 1:k + 1]
                changed = True
                break
        for k in range(1, len(found)):  # removing a pair can leave two of a kind side by side
            if found[k]["type"] == found[k - 1]["type"]:
                keep_later = (found[k]["type"] == "H") == (found[k]["v"] > found[k - 1]["v"])
                del found[k - 1 if keep_later else k]
                changed = True
                break
    return found


@stored(ttl=6 * 3600, max_age=24 * 3600)
def _model_tides() -> dict[str, dict]:
    """Hourly modelled sea level for GLOBAL_POINTS, yesterday through +2 days, keyed by station id."""
    out: dict[str, dict] = {}
    for i in range(0, len(GLOBAL_POINTS), _MARINE_CHUNK):
        chunk = GLOBAL_POINTS[i:i + _MARINE_CHUNK]
        body = _get(MARINE, {"latitude": ",".join(str(p[2]) for p in chunk),
                             "longitude": ",".join(str(p[3]) for p in chunk),
                             "hourly": "sea_level_height_msl", "past_days": 1, "forecast_days": 3, "timezone": "GMT"})
        for (name, country, lat, lon), loc in zip(chunk, body if isinstance(body, list) else [body]):
            hourly = loc.get("hourly") or {}
            series = [{"t": _iso(t), "v": round(v, 3)}
                      for t, v in zip(hourly.get("time", []), hourly.get("sea_level_height_msl", [])) if v is not None]
            events = turns(series)
            if len(events) < 2:
                continue  # a land cell, or no tide worth showing
            station = {"id": _slug(name), "name": name, "state": country, "lat": lat, "lon": lon, "tide_type": None,
                       "kind": "model", "datum": "MSL", "source": "Open-Meteo Marine (global tide model)"}
            out[station["id"]] = {"station": station, "series": series, "events": events}
    if not out:
        raise NoaaError("Open-Meteo returned no modelled tides")
    return out


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


@stored(ttl=6 * 3600, max_age=24 * 3600, mark_stale=True)
def _overview() -> dict:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    begin, end = today - timedelta(days=1), today + timedelta(days=3) - timedelta(minutes=1)
    rows: list[dict] = []
    errors: list[str] = []
    try:
        stations = tide_stations()
        with ThreadPoolExecutor(_WORKERS) as pool:
            rows = [r for r in pool.map(lambda s: _station_events(s, begin, end), stations) if r]
    except (httpx.HTTPError, ValueError) as exc:
        errors.append(f"NOAA: {exc}")
    try:  # the rest of the world, skipping points a NOAA station already covers
        kept = [(r["lat"], r["lon"]) for r in rows]
        for m in _model_tides().values():
            point = (m["station"]["lat"], m["station"]["lon"])
            if _far_from(point, kept):
                kept.append(point)
                rows.append({**m["station"], "events": m["events"]})
    except (httpx.HTTPError, ValueError) as exc:
        errors.append(f"Open-Meteo: {exc}")
    if not rows:
        raise NoaaError("; ".join(errors) or "no tide predictions")
    return {"source": "NOAA CO-OPS (US) and Open-Meteo Marine global tide model", "units": "m",
            "fetched_at": datetime.now(timezone.utc).isoformat(), "partial_errors": errors,
            "window": {"start": begin.isoformat(), "end": end.isoformat()}, "stations": rows}


def tide_overview() -> dict:
    """High/low turns for every tide station worldwide, each with its state at request time."""
    base = _overview()
    now = datetime.now(timezone.utc)
    return {**base, "as_of": now.isoformat(),
            "stations": [{**s, "now": tide_now(s["events"], now)} for s in base["stations"]]}


@stored(ttl=600, max_age=3600, mark_stale=True)
def tide_station(station_id: str) -> dict:
    """Predicted curve, observed level (NOAA gauges only) and high/low turns, 24 h ago to 48 h ahead."""
    if not re.fullmatch(STATION_ID, station_id):
        raise ValueError(f"bad tide station id {station_id!r}")
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    begin, end = now - timedelta(hours=24), now + timedelta(hours=48)
    in_window = lambda rows: [r for r in rows if begin <= _parse(r["t"]) <= end]  # noqa: E731
    if station_id.startswith("om-"):
        model = _model_tides().get(station_id)
        if model is None:
            raise ValueError(f"unknown tide station {station_id}")
        return {"source": model["station"]["source"], "datum": "MSL", "units": "m", "station": model["station"],
                "fetched_at": now.isoformat(), "now": tide_now(model["events"], now),
                "predicted": in_window(model["series"]), "observed": [], "observed_error": None,
                "events": in_window(model["events"])}
    station = next((s for s in tide_stations() if s["id"] == station_id), None)
    if station is None:
        raise ValueError(f"unknown tide station {station_id}")
    predicted = _series(station_id, "predictions", begin, end, "15")
    events = _series(station_id, "predictions", begin - timedelta(hours=13), end + timedelta(hours=13), "hilo")
    observed, observed_error = [], None
    if station["kind"] == "gauge":
        try:
            observed = _series(station_id, "water_level", begin, now)
        except (httpx.HTTPError, ValueError) as exc:  # gauges go offline; predictions still stand
            observed_error = str(exc)
    return {"source": "NOAA CO-OPS", "datum": DATUM, "units": "m", "station": station,
            "fetched_at": now.isoformat(), "now": tide_now(events, now),
            "predicted": predicted, "observed": observed, "observed_error": observed_error,
            "events": [e for e in events if begin <= _parse(e["t"]) <= end]}
