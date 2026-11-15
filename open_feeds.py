from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HTTP_TIMEOUT = 30
NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
CFTC_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
COOPS_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
SSE_URL = "https://en.sse.net.cn/currentIndex"

NWS_ANCHORS = [
    ("cushing_ok", 35.9851, -96.7676),
    ("henry_hub_la", 29.9469, -92.0691),
    ("iowa_corn_belt", 42.0308, -93.6319),
    ("permian_tx", 31.9973, -102.0779),
    ("haynesville_la", 32.5252, -93.7502),
]

FRED_SERIES = [
    ("DCOILWTICO", "USD/bbl"),
    ("DCOILBRENTEU", "USD/bbl"),
    ("DHHNGSP", "USD/MMBtu"),
    ("PCOPPUSDM", "USD/metric-ton"),
    ("PWHEAMTUSDM", "USD/metric-ton"),
    ("PMAIZMTUSDM", "USD/metric-ton"),
]

CFTC_CONTRACTS = [
    "CRUDE OIL, LIGHT SWEET-WTI",
    "HENRY HUB",
    "CORN",
    "WHEAT",
    "WHEAT-SRW",
    "WHEAT-HRW",
    "WHEAT-HRSpring",
    "SOYBEANS",
    "GOLD",
    "COPPER- #1",
]
CFTC_FIELDS = [
    "open_interest_all",
    "m_money_positions_long_all",
    "m_money_positions_short_all",
]

COOPS_STATIONS = [("9414290", "San Francisco"), ("8638610", "Sewells Point")]
COOPS_PRODUCTS = [
    ("water_level", {"datum": "MLLW", "units": "english"}, {"v": "feet"}),
    ("wind", {"units": "english"}, {"s": "knots", "g": "knots", "d": "degrees"}),
]

SSE_INDEXES = ["scfi", "ccfi"]

OBS_UPSERT = """
    INSERT INTO external_observations
        (source, dataset, series_id, observed_at, available_at, value, unit,
         dimensions, raw_data, fetched_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (source, dataset, series_id, observed_at) DO UPDATE
       SET value = EXCLUDED.value, unit = EXCLUDED.unit,
           dimensions = EXCLUDED.dimensions, raw_data = EXCLUDED.raw_data,
           fetched_at = EXCLUDED.fetched_at,
           available_at = CASE
               WHEN external_observations.value IS DISTINCT FROM EXCLUDED.value
               THEN EXCLUDED.available_at
               ELSE LEAST(external_observations.available_at, EXCLUDED.available_at)
           END
"""

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().with_name(".env"))
    except ImportError:
        pass


def _user_agent() -> str:
    return os.getenv("NOAA_USER_AGENT", "").strip() or "nostos-open-feeds/1.0"


def _db_url() -> str:
    db_dir = str(Path(__file__).resolve().parent / "db")
    sys.path.insert(0, db_dir)
    try:
        from run_sql import get_url
    finally:
        sys.path.remove(db_dir)
    return get_url()


def _connect():
    try:
        import psycopg
    except ImportError:
        raise SystemExit("psycopg is required: pip install 'psycopg[binary]'")
    return psycopg.connect(_db_url())


def _http_text(url: str, headers: dict | None = None, attempts: int = 1) -> str:
    host = urllib.parse.urlsplit(url).netloc
    request_headers = {"User-Agent": _user_agent(), "Accept": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    last: RuntimeError | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            last = RuntimeError(f"HTTP {exc.code} from {host}")
            if exc.code != 429 and exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            last = RuntimeError(f"HTTP request to {host} failed: {getattr(exc, 'reason', exc)}")
        if attempt + 1 < attempts:
            time.sleep(attempt + 1)
    raise last or RuntimeError(f"HTTP request to {host} failed")


def _json(text: str, label: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"invalid JSON from {label}") from None


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
    return number if math.isfinite(number) else None


def _first_number(value: object) -> str | None:
    if value is None:
        return None
    match = _NUMBER_RE.search(str(value))
    return match.group(0) if match else None


def _ts(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _obs(*, source: str, dataset: str, series_id: str, observed_at: datetime,
         available_at: datetime, value: float, unit: str | None,
         dimensions: dict | None, raw_data: dict | None,
         fetched_at: datetime) -> dict:
    return {
        "source": source,
        "dataset": dataset,
        "series_id": series_id,
        "observed_at": observed_at,
        "available_at": available_at,
        "value": value,
        "unit": unit,
        "dimensions": dimensions or {},
        "raw_data": raw_data or {},
        "fetched_at": fetched_at,
    }


def _uniq(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for row in rows:
        key = (row["source"], row["dataset"], row["series_id"], row["observed_at"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def nws_rows(fetched_at: datetime | None = None) -> list[dict]:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    rows: list[dict] = []
    for anchor, lat, lon in NWS_ANCHORS:
        point = _json(
            _http_text(
                NWS_POINTS_URL.format(lat=lat, lon=lon),
                {"Accept": "application/geo+json"},
            ),
            f"NWS points {anchor}",
        ).get("properties") or {}
        hourly_url = point.get("forecastHourly")
        if not hourly_url:
            raise RuntimeError(f"NWS points {anchor} has no forecastHourly")
        forecast = _json(
            _http_text(f"{hourly_url}?units=si", {"Accept": "application/geo+json"}),
            f"NWS hourly {anchor}",
        )
        props = forecast.get("properties") or {}
        available = _ts(props.get("updateTime")) or fetched_at
        location = (point.get("relativeLocation") or {}).get("properties") or {}
        dimensions = {
            "anchor": anchor,
            "wfo": point.get("gridId"),
            "grid_x": point.get("gridX"),
            "grid_y": point.get("gridY"),
            "city": location.get("city"),
        }
        dimensions = {k: v for k, v in dimensions.items() if v is not None}
        for period in props.get("periods") or []:
            observed = _ts(period.get("startTime"))
            if observed is None:
                continue
            metrics = [
                ("temperature", _num(period.get("temperature")), "celsius"),
                ("probability_of_precipitation",
                 _num((period.get("probabilityOfPrecipitation") or {}).get("value")), "percent"),
                ("relative_humidity",
                 _num((period.get("relativeHumidity") or {}).get("value")), "percent"),
                ("dewpoint", _num((period.get("dewpoint") or {}).get("value")), "celsius"),
                ("wind_speed", _num(_first_number(period.get("windSpeed"))), "km/h"),
            ]
            for field, value, unit in metrics:
                if value is None:
                    continue
                rows.append(_obs(
                    source="noaa_nws", dataset="nws_hourly_forecast",
                    series_id=f"{anchor}.{field}", observed_at=observed,
                    available_at=available, value=value, unit=unit,
                    dimensions=dimensions, raw_data=period, fetched_at=fetched_at,
                ))
    return _uniq(rows)


def fred_rows(fetched_at: datetime | None = None) -> list[dict]:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    rows: list[dict] = []
    for series_id, unit in FRED_SERIES:
        url = FRED_URL + "?" + urllib.parse.urlencode({"id": series_id, "cosd": "2020-01-01"})
        text = _http_text(url, {"Accept": "text/csv"}, attempts=3)
        reader = csv.DictReader(io.StringIO(text))
        date_key = next(
            (key for key in reader.fieldnames or [] if key.strip().lower() in {"observation_date", "date"}),
            None,
        )
        if date_key is None:
            raise RuntimeError(f"FRED {series_id} returned no date column")
        for record in reader:
            observed = _ts(record.get(date_key))
            value = _num(record.get(series_id))
            if observed is None or value is None:
                continue
            rows.append(_obs(
                source="fred", dataset="commodity_price", series_id=series_id,
                observed_at=observed, available_at=fetched_at, value=value,
                unit=unit, dimensions={}, raw_data=record, fetched_at=fetched_at,
            ))
    return _uniq(rows)


def cftc_url() -> str:
    params = {
        "$select": ",".join(["report_date_as_yyyy_mm_dd", "contract_market_name", *CFTC_FIELDS]),
        "$where": "contract_market_name in("
                  + ",".join("'" + name + "'" for name in CFTC_CONTRACTS) + ")",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "500",
    }
    return CFTC_URL + "?" + urllib.parse.urlencode(params)


def cftc_rows(fetched_at: datetime | None = None) -> list[dict]:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    records = _json(_http_text(cftc_url()), "CFTC")
    if not isinstance(records, list):
        raise RuntimeError("CFTC response is not a list")
    rows: list[dict] = []
    for record in records:
        contract = record.get("contract_market_name")
        observed = _ts(record.get("report_date_as_yyyy_mm_dd"))
        if not contract or observed is None:
            continue
        for field in CFTC_FIELDS:
            value = _num(record.get(field))
            if value is None:
                continue
            rows.append(_obs(
                source="cftc", dataset="cot_disaggregated",
                series_id=f"{contract}.{field}", observed_at=observed,
                available_at=fetched_at, value=value, unit="contracts",
                dimensions={"contract": contract}, raw_data=record,
                fetched_at=fetched_at,
            ))
    return _uniq(rows)


def coops_rows(fetched_at: datetime | None = None) -> list[dict]:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    rows: list[dict] = []
    for station, station_name in COOPS_STATIONS:
        for product, extra, fields in COOPS_PRODUCTS:
            params = {
                "product": product, "application": "nostos", "time_zone": "gmt",
                "format": "json", "date": "latest", "station": station, **extra,
            }
            payload = _json(
                _http_text(COOPS_URL + "?" + urllib.parse.urlencode(params)),
                f"CO-OPS {station} {product}",
            )
            if payload.get("error"):
                message = payload["error"]
                if isinstance(message, dict):
                    message = message.get("message", message)
                print(f"  skip CO-OPS {station} {product}: {message}", file=sys.stderr)
                continue
            metadata = payload.get("metadata") or {}
            name = metadata.get("name") or station_name
            for point in payload.get("data") or []:
                observed = _ts(point.get("t"))
                if observed is None:
                    continue
                for field, unit in fields.items():
                    value = _num(point.get(field))
                    if value is None:
                        continue
                    rows.append(_obs(
                        source="noaa_coops", dataset=f"{station}.{product}",
                        series_id=f"{station}.{field}", observed_at=observed,
                        available_at=fetched_at, value=value, unit=unit,
                        dimensions={"station": station, "name": name},
                        raw_data=point, fetched_at=fetched_at,
                    ))
    return _uniq(rows)


def sse_rows(fetched_at: datetime | None = None) -> list[dict]:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    rows: list[dict] = []
    for index in SSE_INDEXES:
        payload = _json(
            _http_text(SSE_URL + "?" + urllib.parse.urlencode({"indexName": index})),
            f"SSE {index}",
        )
        data = payload.get("data") or {}
        dates = {"current": _ts(data.get("currentDate")), "previous": _ts(data.get("lastDate"))}
        for line in data.get("lineDataList") or []:
            props = line.get("properties") or {}
            lane = (props.get("lineName_EN") or "").strip()
            if not lane:
                continue
            for kind, field in (("current", "currentContent"), ("previous", "lastContent")):
                observed = dates[kind]
                value = _num(line.get(field))
                if observed is None or value is None:
                    continue
                dimensions = {
                    "index": index,
                    "lane": lane,
                    "data_item_type": kind,
                    "item_type": line.get("dataItemTypeName"),
                }
                rows.append(_obs(
                    source="sse", dataset="container_freight_index",
                    series_id=f"{index}.{lane}", observed_at=observed,
                    available_at=fetched_at, value=value, unit=None,
                    dimensions={k: v for k, v in dimensions.items() if v is not None},
                    raw_data=line, fetched_at=fetched_at,
                ))
    return _uniq(rows)


def commodity_rows(fetched_at: datetime | None = None) -> list[dict]:
    return fred_rows(fetched_at) + cftc_rows(fetched_at)


def shipping_rows(fetched_at: datetime | None = None) -> list[dict]:
    return coops_rows(fetched_at) + sse_rows(fetched_at)


FAMILIES = {
    "noaa": nws_rows,
    "commodities": commodity_rows,
    "shipping": shipping_rows,
}


def store(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    from psycopg.types.json import Jsonb

    params = [
        (row["source"], row["dataset"], row["series_id"], row["observed_at"],
         row["available_at"], row["value"], row["unit"],
         Jsonb(row["dimensions"]), Jsonb(row["raw_data"]), row["fetched_at"])
        for row in rows
    ]
    conn.cursor().executemany(OBS_UPSERT, params)
    return len(params)


def scrape(source: str, dry_run: bool) -> int:
    now = datetime.now(timezone.utc)
    names = list(FAMILIES) if source == "all" else [source]
    conn = None if dry_run else _connect()
    failures = 0
    try:
        for name in names:
            try:
                rows = FAMILIES[name](now)
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            if dry_run:
                print(f"{name}: {len(rows)} rows (dry run)")
            else:
                count = store(conn, rows)
                conn.commit()
                print(f"{name}: {count} rows stored")
    finally:
        if conn is not None:
            conn.close()
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape no-auth NOAA, commodity, and shipping feeds into external_observations."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("scrape")
    run.add_argument("--source", choices=["all", *FAMILIES], default="all")
    run.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    _load_env()
    return scrape(args.source, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
