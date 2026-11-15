"""Live aircraft positions from adsb.lol (free, no key, ODbL open data: https://api.adsb.lol).

Cargo flights are flagged by the ICAO airline prefix of the callsign (FDX123 -> FedEx);
ADS-B itself carries no passenger/cargo field.

    python flights.py 32.9 -97.04 50          # aircraft within 50 nm of DFW
"""
from __future__ import annotations

import json
import sys
import time
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


if __name__ == "__main__":
    lat, lon, radius = (float(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 \
        else (32.9, -97.04, 50)
    rows = aircraft_near(lat, lon, radius)
    cargo = [r for r in rows if r["cargo_operator"]]
    print(f"{len(rows)} aircraft, {len(cargo)} cargo")
    for r in cargo:
        print(f"  {r['callsign']:8} {r['aircraft_type'] or '?':5} {r['cargo_operator']}")
