"""NOAA National Weather Service API (api.weather.gov). Live only: NWS keeps
no forecast archive and only ~7 days of observations.

  /points/{lat},{lon}            -> forecast grid cell for a station (cached in weather_points)
  /gridpoints/{wfo}/{x},{y}      -> forecaster grid (NDFD), every numeric layer, hourly to 7 days
                                    -> weather_forecasts model 'nws_ndfd'
  /stations/{icao}/observations  -> METAR observations -> weather_actuals source 'nws_obs'
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

import common as c

API = "https://api.weather.gov"
HEADERS = {"Accept": "application/geo+json"}
MODEL = "nws_ndfd"

# gridpoint layer -> typed weather_forecasts column
TYPED = {
    "temperature": "temp_f",
    "dewpoint": "dewpoint_f",
    "apparentTemperature": "apparent_temp_f",
    "relativeHumidity": "rh_pct",
    "skyCover": "cloud_cover_pct",
    "windSpeed": "wind_10m_mph",
    "windDirection": "wind_dir_deg",
    "windGust": "wind_gust_mph",
    "probabilityOfPrecipitation": "precip_prob_pct",
    "quantitativePrecipitation": "precip_in",
    "snowfallAmount": "snow_in",
    "probabilityOfThunder": "thunder_prob_pct",
    "visibility": "visibility_mi",
    "ceilingHeight": "ceiling_ft",
    "wetBulbGlobeTemperature": "wet_bulb_globe_f",
}
ACCUMULATIONS = {"quantitativePrecipitation", "snowfallAmount", "iceAccumulation"}

_DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def _hours(duration: str) -> int:
    m = _DUR.match(duration)
    if not m:
        return 1
    d, h, mi = (int(x or 0) for x in m.groups())
    return max(1, d * 24 + h + (1 if mi else 0))


def _convert(uom: Optional[str], v):
    """(value in nostos units, key suffix) for an NWS unit code."""
    if v is None:
        return None, ""
    u = (uom or "").split(":")[-1]
    if u == "degC":
        return c.c_to_f(v), "_f"
    if u == "km_h-1":
        return c.kmh_to_mph(v), "_mph"
    if u == "m_s-1":
        return c.ms_to_mph(v), "_mph"
    if u == "mm":
        return c.mm_to_in(v), "_in"
    if u == "m":
        return c.m_to_ft(v), "_ft"
    if u == "Pa":
        return v / 100, "_hpa"
    if u == "percent":
        return v, "_pct"
    if u == "degree_(angle)":
        return v, "_deg"
    return v, ""


# ---------------------------------------------------------------------------
# Grid lookup
# ---------------------------------------------------------------------------
def resolve_grid(lat: float, lon: float) -> tuple[str, int, int]:
    p = c.get_json(f"{API}/points/{lat:.4f},{lon:.4f}", headers=HEADERS)["properties"]
    return p["gridId"], int(p["gridX"]), int(p["gridY"])


def station_coords(icao: str) -> tuple[float, float, str]:
    f = c.get_json(f"{API}/stations/{icao}", headers=HEADERS)
    lon, lat = f["geometry"]["coordinates"][:2]
    return lat, lon, f["properties"].get("name") or icao


# ---------------------------------------------------------------------------
# Gridpoint forecast
# ---------------------------------------------------------------------------
def _weather_text(v) -> Optional[str]:
    parts = []
    for w in v or []:
        words = [w.get("coverage"), w.get("intensity"), w.get("weather")]
        words = [x for x in words if x]
        if words:
            parts.append(" ".join(words))
    return "; ".join(parts) or None


def gridpoint_forecast(grid_id: str, x: int, y: int, now: datetime) -> tuple[datetime, list[dict]]:
    """(issued_at = grid updateTime, hourly row dicts with valid_time >= issued_at)."""
    props = c.get_json(f"{API}/gridpoints/{grid_id}/{x},{y}", headers=HEADERS)["properties"]
    issued = c.parse_utc(props["updateTime"])
    start = max(c.floor_hour(now), issued)
    hours: dict[datetime, dict] = {}

    def put(t0: datetime, n: int, col: str, val, typed: bool):
        for k in range(n):
            vt = t0 + timedelta(hours=k)
            if vt < start:
                continue
            row = hours.setdefault(vt, {"extra": {}})
            if typed:
                row[col] = val
            else:
                row["extra"][col] = val

    for name, layer in props.items():
        if not isinstance(layer, dict) or "values" not in layer:
            continue
        uom = layer.get("uom")
        for item in layer["values"]:
            t_str, dur = item["validTime"].split("/")
            t0, n = c.floor_hour(c.parse_utc(t_str)), _hours(dur)
            v = item["value"]
            if name == "weather":
                put(t0, n, "weather", _weather_text(v), False)
                continue
            if name == "hazards":
                put(t0, n, "hazards", ",".join(f"{h.get('phenomenon')}.{h.get('significance')}"
                                               for h in v or []) or None, False)
                continue
            if not isinstance(v, (int, float)) and v is not None:
                continue
            if v is not None and name in ACCUMULATIONS:
                v = v / n                                    # spread period total over its hours
            if name in TYPED:
                col = TYPED[name]
                if name == "visibility":
                    val = c.m_to_mi(v)
                elif name == "ceilingHeight":
                    val = None if v is None or v < 0 else c.m_to_ft(v)
                else:
                    val, _ = _convert(uom, v)
                put(t0, n, col, val, True)
            else:
                val, suffix = _convert(uom, v)
                put(t0, n, name + suffix, val, False)

    rows = []
    for vt in sorted(hours):
        r = hours[vt]
        r["extra"] = {k: v for k, v in r["extra"].items() if v is not None}
        r["valid_time"] = vt
        r["lead_hours"] = int((vt - issued).total_seconds() // 3600)
        rows.append(r)
    return issued, rows


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------
_OKTA = {"SKC": 0, "CLR": 0, "NCD": 0, "FEW": 2, "SCT": 4, "BKN": 7, "OVC": 8, "VV": 8}
_BAD_QC = {"X", "Q"}


def station_observations(icao: str, start: datetime) -> list[dict]:
    """Hourly weather_actuals row dicts from METARs since `start`.

    Each hour (valid_time) takes the observation nearest to it, preferring the
    routine :51-:59 METAR, matching how ghcnh rows are built.
    """
    feats = c.get_json(f"{API}/stations/{icao}/observations",
                       [("start", start.strftime("%Y-%m-%dT%H:%M:%SZ"))], headers=HEADERS)["features"]
    best: dict[datetime, tuple] = {}
    for f in feats:
        p = f["properties"]
        ts = c.parse_utc(p["timestamp"])
        vt = c.round_hour(ts)
        rank = (0 if 50 <= ts.minute <= 59 else 1, abs((ts - vt).total_seconds()))
        if vt not in best or rank < best[vt][0]:
            best[vt] = (rank, ts, p)

    rows = []
    for vt, (_, ts, p) in sorted(best.items()):
        def q(name):
            o = p.get(name) or {}
            if o.get("qualityControl") in _BAD_QC:
                return None
            return o.get("value")

        temp, dew = c.c_to_f(q("temperature")), c.c_to_f(q("dewpoint"))
        wind = c.kmh_to_mph(q("windSpeed"))
        rh = q("relativeHumidity")
        rh = rh if rh is not None else c.rh_from(temp, dew)
        layers = p.get("cloudLayers") or []
        oktas = [_OKTA.get(l.get("amount")) for l in layers if _OKTA.get(l.get("amount")) is not None]
        ceil = [l["base"]["value"] for l in layers
                if l.get("amount") in ("BKN", "OVC", "VV") and (l.get("base") or {}).get("value") is not None]
        extra = {}
        for k, o in p.items():
            if isinstance(o, dict) and "unitCode" in o and o.get("value") is not None and k not in (
                    "temperature", "dewpoint", "windSpeed", "windDirection", "windGust",
                    "relativeHumidity", "visibility", "seaLevelPressure", "precipitationLastHour",
                    "elevation"):
                val, suffix = _convert(o["unitCode"], o["value"])
                extra[k + suffix] = val
        if p.get("textDescription"):
            extra["textDescription"] = p["textDescription"]
        slp = q("seaLevelPressure")
        rows.append({
            "valid_time": vt,
            "available_at": max(vt, ts + timedelta(minutes=15)),
            "temp_f": temp,
            "dewpoint_f": dew,
            "rh_pct": rh,
            "apparent_temp_f": c.apparent_f(temp, rh, wind),
            "cloud_cover_pct": max(oktas) / 8 * 100 if oktas else None,
            "wind_10m_mph": wind,
            "wind_dir_deg": q("windDirection"),
            "wind_gust_mph": c.kmh_to_mph(q("windGust")),
            "precip_in": c.mm_to_in(q("precipitationLastHour")),
            "visibility_mi": c.m_to_mi(q("visibility")),
            "ceiling_ft": c.m_to_ft(min(ceil)) if ceil else None,
            "sea_level_pressure_hpa": None if slp is None else slp / 100,
            "present_weather": " ".join(w.get("rawString") or "" for w in p.get("presentWeather") or []).strip() or None,
            "extra": extra,
        })
    return rows
