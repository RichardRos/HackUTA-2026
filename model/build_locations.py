"""Build locations from Kalshi daily-high settlement stations + IEM station metadata.

Kalshi's market rules name the NWS climate report each daily-high market settles on,
e.g. "maximum temperature recorded at Chicago (CLIMDW)" -> station KMDW. IEM's NWSCLI
network gives that station's coordinates and time zone. We compare against
data/locations.csv and flag every difference. Output: data/locations_built.csv
(locations.csv is never overwritten; review the diff and copy it over by hand).

Run:  python build_locations.py
"""
import math
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import config

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
IEM_CLI = "https://mesonet.agron.iastate.edu/geojson/network/NWSCLI.geojson"
HEADERS = {"User-Agent": "weather-scorer"}
EXCLUDE = {"KXHIGHUS", "KXHIGHNYD"}      # national high; hourly NYC
CLI_CODE = re.compile(r"\(CLI([A-Z]{3})\)")
NEAR_KM = 75                             # same city if settlement station is this close
COORD_TOL_KM = 5


def get(url: str, **params) -> dict:
    for attempt in range(4):
        r = requests.get(url, params=params, headers=HEADERS, timeout=60)
        if r.status_code == 200:
            return r.json()
        time.sleep(2 * 2 ** attempt)
    raise RuntimeError(f"GET {url} failed: {r.status_code}")


def kalshi_stations() -> pd.DataFrame:
    series = get(f"{KALSHI}/series", category="Climate and Weather")["series"]
    rows = []
    for s in series:
        if not s["ticker"].startswith("KXHIGH") or s["ticker"] in EXCLUDE:
            continue
        sources = " ".join(x.get("url", "") for x in s.get("settlement_sources") or [])
        markets = get(f"{KALSHI}/markets", series_ticker=s["ticker"], limit=1).get("markets") or []
        rules = markets[0].get("rules_primary", "") if markets else ""
        time.sleep(0.2)
        m = CLI_CODE.search(rules) or re.search(r"issuedby=([A-Z]{3})", sources)
        if m:
            rows.append({"kalshi_series": s["ticker"], "title": s["title"].strip(),
                         "station_id": "K" + m.group(1), "has_markets": bool(markets)})
    return pd.DataFrame(rows)


def std_offset_hours(tzname: str) -> int:
    jan = datetime(2026, 1, 15, 12, tzinfo=ZoneInfo(tzname))
    return int(jan.utcoffset().total_seconds() // 3600)


def iem_stations() -> pd.DataFrame:
    feats = get(IEM_CLI)["features"]
    return pd.DataFrame([{
        "station_id": f["properties"]["sid"], "iem_name": f["properties"]["sname"],
        "iem_lat": f["geometry"]["coordinates"][1], "iem_lon": f["geometry"]["coordinates"][0],
        "iem_offset": std_offset_hours(f["properties"]["tzname"]),
    } for f in feats if f["properties"].get("tzname")])


def km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def main():
    current = config.load_locations()
    iem = iem_stations()
    kal = kalshi_stations().merge(iem, on="station_id", how="inner")   # US CLI stations only
    kal = (kal.sort_values("has_markets", ascending=False)
              .groupby("station_id", as_index=False)
              .agg(kalshi_series=("kalshi_series", ";".join), title=("title", "first"),
                   iem_name=("iem_name", "first"), iem_lat=("iem_lat", "first"),
                   iem_lon=("iem_lon", "first"), iem_offset=("iem_offset", "first")))

    flags, built, used = [], [], set()
    for row in current.itertuples():
        dist = kal.apply(lambda k: km(row.lat, row.lon, k.iem_lat, k.iem_lon), axis=1)
        same = kal[kal["station_id"] == row.station_id]
        near = kal[dist <= NEAR_KM].assign(km=dist[dist <= NEAR_KM]).sort_values("km")
        out = row._asdict()
        out.pop("Index")
        if not same.empty:
            k = same.iloc[0]
            status = "verified"
        elif not near.empty:
            k = near.iloc[0]
            status = f"DIFFERS: Kalshi settles on {k.station_id} ({k.iem_name}), csv has {row.station_id}"
        else:
            k = None
            status = "UNVERIFIED: no Kalshi daily-high market for this city"
        if k is not None:
            used.add(k.station_id)
            moved = km(row.lat, row.lon, k.iem_lat, k.iem_lon)
            if status == "verified" and moved > COORD_TOL_KM:
                status += f"; coords off by {moved:.0f} km"
            if k.iem_offset != row.std_utc_offset_hours:
                status += f"; std offset {row.std_utc_offset_hours} vs IEM {k.iem_offset}"
            if pd.isna(row.kalshi_series) or row.kalshi_series != k.kalshi_series.split(";")[0]:
                status += f"; kalshi_series -> {k.kalshi_series}"
            out.update(station_id=k.station_id, lat=round(k.iem_lat, 2), lon=round(k.iem_lon, 2),
                       std_utc_offset_hours=k.iem_offset, kalshi_series=k.kalshi_series)
        flags.append((row.location, row.station_id, status))
        built.append(out)

    extra = kal[~kal["station_id"].isin(used)]
    built = pd.DataFrame(built)[list(current.columns)]
    out_path = config.DATA_DIR / "locations_built.csv"
    built.to_csv(out_path, index=False)

    print(f"{'location':15} {'csv':6} status")
    for loc, sid, status in flags:
        print(f"{loc:15} {sid:6} {status}")
    if not extra.empty:
        print("\nKalshi US daily-high stations not in locations.csv:")
        print(extra[["station_id", "iem_name", "kalshi_series"]].to_string(index=False))
    n_station = sum(not s.startswith("verified") or "coords" in s or "offset" in s for _, _, s in flags)
    n_series = sum("kalshi_series ->" in s for _, _, s in flags)
    print(f"\n{n_station} of {len(flags)} locations differ in station/coords/offset; "
          f"{n_series} only need kalshi_series filled; proposed file -> {out_path}")


if __name__ == "__main__":
    main()
