"""Live aircraft positions.

  aircraft_near()  adsb.lol (free, no key, ODbL: https://api.adsb.lol): one circle <= 250 nm, ~1 s fresh
  aircraft_global() OpenSky Network (https://opensky-network.org): the whole world in one call, ~5-10 s
                    fresh. With OPENSKY_CLIENT_ID/SECRET (free account, 4000 credits/day, 4 per global
                    call) the snapshot refreshes every 90 s; anonymously (400/day) every 15 min.

Cargo flights are flagged by the ICAO airline prefix of the callsign (FDX123 -> FedEx);
ADS-B itself carries no passenger/cargo field.

    python flights.py 32.9 -97.04 50          # aircraft within 50 nm of DFW
    python flights.py global                  # worldwide snapshot summary
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.adsb.lol/v2"
USER_AGENT = "nostos-flights/1.0"
TIMEOUT = 15
CACHE_SECONDS = 10  # adsb.lol rate-limits by load; positions refresh about once a second upstream

# ICAO airline designator -> cargo operator. shortcut: hand-kept list of major freight airlines;
# mixed passenger/cargo airlines (Cathay, Emirates...) are left out. Extend as needed.
CARGO_OPERATORS = {
    "FDX": "FedEx Express", "UPS": "UPS Airlines", "GTI": "Atlas Air", "ABX": "ABX Air",
    "ATN": "Air Transport International", "CKS": "Kalitta Air", "PAC": "Polar Air Cargo",
    "WGN": "Western Global", "NCR": "National Airlines", "SOO": "Southern Air", "CLX": "Cargolux",
    "GEC": "Lufthansa Cargo", "BOX": "AeroLogic", "DHK": "DHL Air UK", "DHX": "DHL Aero Expreso",
    "BCS": "European Air Transport (DHL)", "DAE": "DHL Aero Expreso", "ABW": "AirBridgeCargo",
    "CAO": "Air China Cargo", "CKK": "China Cargo Airlines", "CSS": "SF Airlines",
    "YZR": "Suparna Airlines",
    "NCA": "Nippon Cargo Airlines", "CLU": "Cargologic Air", "MPH": "Martinair Cargo",
    "SQC": "Singapore Airlines Cargo", "CKT": "Caribbean Air Cargo",
    "AJT": "Amerijet", "KYE": "Sky Lease Cargo", "TPA": "Avianca Cargo", "LCO": "LATAM Cargo Chile",
    "LTG": "LATAM Cargo Brasil", "ICV": "Cargolux Italia", "TAY": "ASL Airlines Belgium",
    "ABR": "ASL Airlines Ireland", "SWN": "West Atlantic", "RCF": "Aeroflot Cargo",
    "ADB": "Antonov Airlines", "ATG": "Aerotranscargo", "QAJ": "Quick Airways",
    "CMB": "US Transcom", "AMZ": "Amazon Air (Prime)", "WIG": "Wiggins Airways", "MRA": "Martinaire",
    "CFS": "Empire Airlines (FedEx feeder)", "MTN": "Mountain Air Cargo", "JUS": "USA Jet Airlines", "BDA": "Blue Dart Aviation",
}

_cache: dict[tuple, tuple[float, list[dict]]] = {}


def cargo_operator(callsign: str | None) -> str | None:
    prefix = (callsign or "").strip().upper()[:3]
    return CARGO_OPERATORS.get(prefix) if prefix.isalpha() else None


def normalize(ac: dict) -> dict | None:
    if ac.get("lat") is None or ac.get("lon") is None:
        return None
    callsign = (ac.get("flight") or "").strip() or None
    alt = ac.get("alt_baro")
    return {
        "hex": ac.get("hex"),
        "callsign": callsign,
        "registration": ac.get("r"),
        "aircraft_type": ac.get("t"),
        "lat": ac["lat"],
        "lon": ac["lon"],
        "altitude_ft": None if alt == "ground" else alt,
        "on_ground": alt == "ground",
        "ground_speed_kt": ac.get("gs"),
        "track_deg": ac.get("track"),
        "vertical_rate_fpm": ac.get("baro_rate"),
        "squawk": ac.get("squawk"),
        "emergency": ac.get("emergency") not in (None, "none"),
        "cargo_operator": cargo_operator(callsign),
        "seen_seconds_ago": ac.get("seen"),
    }


def _get(path: str) -> dict:
    req = urllib.request.Request(f"{API}/{path}", headers={"User-Agent": USER_AGENT,
                                                            "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def aircraft_near(lat: float, lon: float, radius_nm: int = 100, cargo_only: bool = False) -> list[dict]:
    """Aircraft within radius_nm (1-250) of a point, newest data, sorted by callsign."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat must be -90..90 and lon -180..180")
    if not 1 <= radius_nm <= 250:
        raise ValueError("radius_nm must be 1..250")
    key = (round(lat, 2), round(lon, 2), radius_nm)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        rows = hit[1]
    else:
        try:
            data = _get(f"point/{key[0]}/{key[1]}/{radius_nm}")
        except OSError as exc:  # URLError, timeouts, HTTP errors
            raise RuntimeError(f"adsb.lol request failed: {exc}") from None
        rows = sorted(filter(None, map(normalize, data.get("ac") or [])),
                      key=lambda r: (r["callsign"] or "~", r["hex"] or ""))
        _cache[key] = (time.monotonic(), rows)
    return [r for r in rows if r["cargo_operator"]] if cargo_only else rows


OPENSKY_STATES = "https://opensky-network.org/api/states/all"
OPENSKY_TOKEN = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
GLOBAL_CACHE_AUTH = 90      # 4000 credits/day / 4 per call = 1000 calls/day -> one per ~86 s
GLOBAL_CACHE_ANON = 900     # 400 credits/day / 4 per call = 100 calls/day -> one per ~15 min
M_TO_FT, MS_TO_KT, MS_TO_FPM = 3.28084, 1.94384, 196.850

_global_lock = threading.Lock()   # one upstream pull at a time: concurrent misses share it
_global: dict = {"at": 0.0, "rows": [], "fetched_at": None, "credits_left": None}
_token: dict = {"value": None, "expires": 0.0}


def _opensky_token() -> str | None:
    """OAuth2 client-credentials token (cached until 60 s before expiry), or None when anonymous."""
    cid, secret = os.getenv("OPENSKY_CLIENT_ID", "").strip(), os.getenv("OPENSKY_CLIENT_SECRET", "").strip()
    if not (cid and secret):
        return None
    if _token["value"] and time.monotonic() < _token["expires"]:
        return _token["value"]
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid,
                                   "client_secret": secret}).encode()
    req = urllib.request.Request(OPENSKY_TOKEN, data=body, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OpenSky login failed (HTTP {exc.code}): check OPENSKY_CLIENT_ID/SECRET") from None
    _token.update(value=data["access_token"], expires=time.monotonic() + data.get("expires_in", 1800) - 60)
    return _token["value"]


def normalize_state(sv: list) -> dict | None:
    """OpenSky state vector -> the same row shape as normalize() (metric converted to ft/kt/fpm)."""
    if sv[5] is None or sv[6] is None:
        return None
    callsign = (sv[1] or "").strip() or None
    on_ground = bool(sv[8])
    return {
        "hex": sv[0],
        "callsign": callsign,
        "registration": None,
        "aircraft_type": None,
        "origin_country": sv[2],
        "lat": sv[6],
        "lon": sv[5],
        "altitude_ft": None if on_ground or sv[7] is None else round(sv[7] * M_TO_FT),
        "on_ground": on_ground,
        "ground_speed_kt": None if sv[9] is None else round(sv[9] * MS_TO_KT, 1),
        "track_deg": sv[10],
        "vertical_rate_fpm": None if sv[11] is None else round(sv[11] * MS_TO_FPM),
        "squawk": sv[14],
        "emergency": sv[14] in ("7500", "7600", "7700"),
        "cargo_operator": cargo_operator(callsign),
        "seen_seconds_ago": None if sv[4] is None else max(0, round(time.time() - sv[4])),
    }


def _refresh_global() -> None:
    token = _opensky_token()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(OPENSKY_STATES, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
            credits = resp.headers.get("X-Rate-Limit-Remaining")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            wait = exc.headers.get("X-Rate-Limit-Retry-After-Seconds")
            raise RuntimeError(f"OpenSky daily credits used up; retry in {wait or '?'} s") from None
        raise RuntimeError(f"OpenSky request failed: HTTP {exc.code}") from None
    except OSError as exc:
        raise RuntimeError(f"OpenSky request failed: {exc}") from None
    _global.update(
        at=time.monotonic(),
        rows=list(filter(None, map(normalize_state, data.get("states") or []))),
        fetched_at=data.get("time"),
        credits_left=None if credits is None else int(credits),
    )


def aircraft_global(cargo_only: bool = False) -> dict:
    """Worldwide snapshot from OpenSky, cached 90 s with credentials (15 min anonymous).
    A stale snapshot is served if a refresh fails, so the globe never goes blank."""
    ttl = GLOBAL_CACHE_AUTH if os.getenv("OPENSKY_CLIENT_ID", "").strip() else GLOBAL_CACHE_ANON
    with _global_lock:
        error = None
        if not _global["rows"] or time.monotonic() - _global["at"] >= ttl:
            try:
                _refresh_global()
            except RuntimeError as exc:
                if not _global["rows"]:
                    raise
                error = str(exc)
        rows = _global["rows"]
        meta = {"snapshot_time": _global["fetched_at"], "credits_left": _global["credits_left"],
                "refresh_seconds": ttl, "authenticated": bool(_token["value"]), "stale_error": error}
    if cargo_only:
        rows = [r for r in rows if r["cargo_operator"]]
    return {**meta, "count": len(rows), "aircraft": rows}


if __name__ == "__main__":
    if sys.argv[1:2] == ["global"]:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
        snap = aircraft_global()
        cargo = [r for r in snap["aircraft"] if r["cargo_operator"]]
        print(f"{snap['count']} aircraft worldwide, {len(cargo)} cargo; authenticated={snap['authenticated']}, "
              f"credits left={snap['credits_left']}")
        sys.exit(0)
    lat, lon, radius = (float(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 \
        else (32.9, -97.04, 50)
    rows = aircraft_near(lat, lon, radius)
    cargo = [r for r in rows if r["cargo_operator"]]
    print(f"{len(rows)} aircraft, {len(cargo)} cargo")
    for r in cargo:
        print(f"  {r['callsign']:8} {r['aircraft_type'] or '?':5} {r['cargo_operator']}")
