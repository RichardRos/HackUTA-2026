from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

KALSHI_EVENTS_URL = "https://api.elections.kalshi.com/trade-api/v2/events"
KALSHI_SERIES_URL = "https://api.elections.kalshi.com/trade-api/v2/series"
KALSHI_COMMODITY_CATEGORY = "Commodities"
POLYMARKET_MARKETS_URL = "https://gamma-api.polymarket.com/markets/keyset"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL_DEFAULT = "gemini-3.5-flash-lite"  # 2.5 models are closed to new API keys
HTTP_TIMEOUT = 30

# "no" and "not" are deliberately absent: dropping them flips market meaning.
STOP_WORDS = frozenset("""
a an the will would shall should can could may might must is are was were be been being
to of in on at for by from with into about during
and or but if then than that this these those it its do does did has have had
as again further once here there when where why how
all any both each few more most other some such only own same so too very
just also i you he she we they them his her our your their
what which who whom whose whether s t
""".split())


def regex_normalize(text: object) -> str:
    if text is None:
        return ""
    value = unicodedata.normalize("NFKD", str(text))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    value = (
        value.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    # 70,000 -> 70000 (leave 0.12 and stray commas alone)
    value = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", value)
    value = value.replace("_", " ")
    value = re.sub(r"[^\w.\s]", " ", value, flags=re.UNICODE)
    value = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", value)
    tokens = re.findall(r"\d+(?:\.\d+)?|[^\W\d_]+", value, flags=re.UNICODE)
    return " ".join(token for token in tokens if token not in STOP_WORDS)


def clean_lower(text: object) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).strip().lower())


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().with_name(".env"))
    except ImportError:
        pass


def gemini_json(prompt: str, schema: dict, schema_key: str = "response_schema") -> dict:
    _load_env()
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Gemini fallback required but GEMINI_API_KEY is not set")
    model = os.getenv("GEMINI_MODEL", GEMINI_MODEL_DEFAULT).strip() or GEMINI_MODEL_DEFAULT
    data = _http_json(
        GEMINI_URL.format(model=model),
        method="POST",
        payload={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                schema_key: schema,
            },
        },
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
    )
    candidates = data.get("candidates") if isinstance(data, dict) else None
    parts = []
    if candidates and isinstance(candidates[0], dict):
        parts = (candidates[0].get("content") or {}).get("parts") or []
    raw = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
    if not raw:
        raise RuntimeError("Gemini fallback returned no text")
    try:
        obj = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise RuntimeError("Gemini fallback returned invalid JSON") from None
    if not isinstance(obj, dict):
        raise RuntimeError("Gemini fallback returned invalid JSON")
    return obj


def gemini_normalize(text: object) -> str:
    prompt = (
        "Normalize this prediction-market question into a concise lowercase keyword string. "
        "Drop English stop words and punctuation, keep numbers and meaning, and return strict JSON "
        '{"question": "<normalized question>"}.\n\nQuestion: ' + str(text)
    )
    obj = gemini_json(prompt, {
        "type": "OBJECT",
        "properties": {"question": {"type": "STRING"}},
        "required": ["question"],
    })
    question = obj.get("question")
    if not isinstance(question, str) or not question.strip():
        raise RuntimeError("Gemini fallback returned no question")
    normalized = regex_normalize(question) or clean_lower(question)
    if not normalized:
        raise RuntimeError("Gemini fallback returned an unusable question")
    return normalized


def normalize_market_question(text: object) -> tuple[str, str]:
    normalized = regex_normalize(text)
    if len(normalized.split()) >= 2:
        return normalized, "regex"
    return gemini_normalize(text), "gemini"


def normalize_outcome_label(text: object) -> str:
    return regex_normalize(text) or clean_lower(text)


def normalize_market(market: dict) -> dict:
    question, method = normalize_market_question(market.get("question_raw") or "")
    market["question_normalized"] = question
    market["normalization_method"] = method
    market["outcomes"] = [
        {
            "raw": outcome.get("raw", ""),
            "normalized": normalize_outcome_label(outcome.get("raw", "")),
            "price": outcome.get("price"),
        }
        for outcome in market.get("outcomes") or []
    ]
    return market


def _http_json(url: str, params: dict | None = None, method: str = "GET",
               payload: dict | None = None, headers: dict | None = None):
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"User-Agent": "hackuta-markets/1.0", "Accept": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    host = urllib.parse.urlparse(url).netloc
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {host}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HTTP request to {host} failed: {exc.reason}") from None
    except TimeoutError:
        raise RuntimeError(f"HTTP request to {host} timed out") from None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(f"invalid JSON from {host}") from None


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


def _mid(bid: object, ask: object) -> float | None:
    low, high = _num(bid), _num(ask)
    return (low + high) / 2 if low is not None and high is not None else None


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_kalshi_market(event: dict, market: dict) -> dict | None:
    if not isinstance(event, dict) or not isinstance(market, dict):
        return None
    market_id = _clean_text(market.get("ticker"))
    if not market_id:
        return None
    event_id = _clean_text(market.get("event_ticker") or event.get("event_ticker"))
    series = _clean_text(market.get("series_ticker") or event.get("series_ticker"))
    url = f"https://kalshi.com/markets/{series.lower()}" if series else None
    title = _clean_text(market.get("title")) or _clean_text(event.get("title")) or ""
    subtitle = _clean_text(market.get("yes_sub_title")) or ""
    question_raw = " ".join(part for part in (title, subtitle) if part)
    rules = _clean_text(market.get("rules_primary")) or ""
    secondary = _clean_text(market.get("rules_secondary"))
    if secondary:
        rules = f"{rules} {secondary}".strip()

    last_price = _num(market.get("last_price_dollars"))
    yes_price = _mid(market.get("yes_bid_dollars"), market.get("yes_ask_dollars"))
    if yes_price is None:
        yes_price = last_price
    no_price = _mid(market.get("no_bid_dollars"), market.get("no_ask_dollars"))
    if no_price is None and last_price is not None:
        no_price = 1 - last_price

    return {
        "platform": "kalshi",
        "market_id": market_id,
        "event_id": event_id,
        "slug": None,
        "url": url,
        "category": _clean_text(event.get("category")),
        "question_raw": question_raw,
        "status": _clean_text(market.get("status")),
        "close_time": _clean_text(market.get("close_time")),
        "rules": rules or None,
        "raw_data": market,
        "outcomes": [
            {"raw": "Yes", "price": yes_price},
            {"raw": "No", "price": no_price},
        ],
    }


def parse_polymarket_market(market: dict) -> dict | None:
    if not isinstance(market, dict):
        return None
    market_id = _clean_text(market.get("conditionId")) or _clean_text(market.get("id"))
    if not market_id:
        return None
    events = _as_list(market.get("events"))
    event = events[0] if events and isinstance(events[0], dict) else {}
    event_id = _clean_text(event.get("id")) or _clean_text(market.get("eventId"))
    slug = _clean_text(event.get("slug")) or _clean_text(market.get("slug"))
    url = f"https://polymarket.com/event/{slug}" if slug else None

    if _truthy(market.get("closed")):
        status = "closed"
    elif _truthy(market.get("archived")):
        status = "archived"
    elif _truthy(market.get("active")):
        status = "active"
    else:
        status = "inactive"

    labels = _as_list(market.get("outcomes"))
    prices = _as_list(market.get("outcomePrices"))
    outcomes = [
        {
            "raw": "" if label is None else str(label),
            "price": _num(prices[index]) if index < len(prices) else None,
        }
        for index, label in enumerate(labels)
    ]

    return {
        "platform": "polymarket",
        "market_id": market_id,
        "event_id": event_id,
        "slug": slug,
        "url": url,
        "category": None,
        "question_raw": _clean_text(market.get("question")) or "",
        "status": status,
        "close_time": _clean_text(market.get("endDate")),
        "rules": _clean_text(market.get("description")),
        "raw_data": market,
        "outcomes": outcomes,
    }


def _kalshi_markets(pages: int = 1, **filters):
    """Yield parsed markets from one cursor-paginated Kalshi /events query."""
    cursor = None
    for _ in range(max(1, pages)):
        params = {"status": "open", "with_nested_markets": "true", "limit": 200, **filters}
        if cursor:
            params["cursor"] = cursor
        data = _http_json(KALSHI_EVENTS_URL, params=params)
        if not isinstance(data, dict):
            raise RuntimeError("Kalshi returned an unexpected response")
        for event in data.get("events") or []:
            if not isinstance(event, dict):
                continue
            for market in event.get("markets") or []:
                parsed = parse_kalshi_market(event, market)
                if parsed:
                    yield parsed
        next_cursor = data.get("cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor


def kalshi_commodity_series() -> list[str]:
    """Series tickers under Kalshi's Commodities category, from the category index."""
    data = _http_json(
        KALSHI_SERIES_URL, params={"category": KALSHI_COMMODITY_CATEGORY, "limit": 200}
    )
    if not isinstance(data, dict):
        raise RuntimeError("Kalshi returned an unexpected response")
    tickers: list[str] = []
    for series in data.get("series") or []:
        ticker = _clean_text(series.get("ticker")) if isinstance(series, dict) else None
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    return tickers


def fetch_kalshi(pages: int = 1):
    yield from _kalshi_markets(pages)
    # The generic /events feed omits most commodity series, so query each one explicitly.
    for ticker in kalshi_commodity_series():
        yield from _kalshi_markets(pages, series_ticker=ticker)


def fetch_polymarket(pages: int = 1):
    after = None
    for _ in range(max(1, pages)):
        params = {"closed": "false", "limit": 100}
        if after:
            params["after_cursor"] = after
        data = _http_json(POLYMARKET_MARKETS_URL, params=params)
        if not isinstance(data, dict):
            raise RuntimeError("Polymarket returned an unexpected response")
        for market in data.get("markets") or []:
            parsed = parse_polymarket_market(market)
            if parsed:
                yield parsed
        next_cursor = data.get("next_cursor")
        if not next_cursor or next_cursor == after:
            break
        after = next_cursor


MARKET_UPSERT_SQL = """
INSERT INTO prediction_markets (
    platform, market_id, event_id, slug, url, category, question_raw,
    question_normalized, normalization_method, status, close_time, rules,
    raw_data, scraped_at, normalized_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
ON CONFLICT (platform, market_id) DO UPDATE SET
    event_id = EXCLUDED.event_id,
    slug = EXCLUDED.slug,
    url = EXCLUDED.url,
    category = EXCLUDED.category,
    question_raw = EXCLUDED.question_raw,
    question_normalized = EXCLUDED.question_normalized,
    normalization_method = EXCLUDED.normalization_method,
    status = EXCLUDED.status,
    close_time = EXCLUDED.close_time,
    rules = EXCLUDED.rules,
    raw_data = EXCLUDED.raw_data,
    scraped_at = now(),
    normalized_at = now()
"""

OUTCOME_INSERT_SQL = """
INSERT INTO prediction_market_outcomes (
    platform, market_id, outcome_index, outcome_raw, outcome_normalized, price, scraped_at
) VALUES (%s, %s, %s, %s, %s, %s, now())
"""


def _env_dsn() -> str | None:
    for name in ("DATABASE_URL", "TIGER_URL", "TIMESCALE_SERVICE_URL"):
        value = os.getenv(name)
        if value:
            return value
    return None


def get_dsn() -> str:
    _load_env()
    dsn = _env_dsn()
    if dsn:
        return dsn
    try:
        from db.run_sql import get_url

        return get_url()
    except (ImportError, RuntimeError, SystemExit):
        pass
    raise RuntimeError(
        "set DATABASE_URL, TIGER_URL, or TIMESCALE_SERVICE_URL in the environment or root .env"
    )


def upsert_markets(conn, markets: list[dict]) -> None:
    from psycopg.types.json import Jsonb

    if not markets:
        return
    keys = [(m["platform"], m["market_id"]) for m in markets]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(MARKET_UPSERT_SQL, [
            (
                m["platform"], m["market_id"], m.get("event_id"), m.get("slug"), m.get("url"),
                m.get("category"), m.get("question_raw"), m.get("question_normalized"),
                m.get("normalization_method"), m.get("status"), m.get("close_time"),
                m.get("rules"), Jsonb(m.get("raw_data") or {}),
            )
            for m in markets
        ])
        cur.executemany(
            "DELETE FROM prediction_market_outcomes WHERE platform = %s AND market_id = %s", keys
        )
        cur.executemany(OUTCOME_INSERT_SQL, [
            (m["platform"], m["market_id"], i, o.get("raw"), o.get("normalized"), o.get("price"))
            for m in markets
            for i, o in enumerate(m.get("outcomes") or [])
        ])


def scrape(platform: str = "both", pages: int = 1) -> None:
    import psycopg

    dsn = get_dsn()
    sources = []
    if platform in ("both", "kalshi"):
        sources.append(("kalshi", fetch_kalshi))
    if platform in ("both", "polymarket"):
        sources.append(("polymarket", fetch_polymarket))

    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except psycopg.Error as exc:
        raise RuntimeError(f"database connection failed: {exc}") from None

    with conn:
        for name, fetcher in sources:
            fetched = failed = 0
            batch: dict[tuple, dict] = {}
            try:
                for market in fetcher(pages):
                    fetched += 1
                    try:
                        normalize_market(market)
                        batch[(market["platform"], market["market_id"])] = market
                    except Exception as exc:
                        failed += 1
                        print(f"{name} {market.get('market_id')}: {exc}", file=sys.stderr)
            except Exception as exc:
                print(f"{name}: fetch failed: {exc}", file=sys.stderr)
            try:
                # One batched transaction per platform; per-market round trips took ~13 min.
                upsert_markets(conn, list(batch.values()))
                upserted = len(batch)
            except Exception as exc:
                upserted, failed = 0, fetched
                print(f"{name}: database write failed: {exc}", file=sys.stderr)
            print(f"{name}: fetched {fetched}, upserted {upserted}, failed {failed}")


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


class MarketsHTTPHandler(BaseHTTPRequestHandler):
    dsn = ""
    server_version = "HackUTA-Markets/1.0"

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/health":
                self._send_json(200, {"status": "ok"})
            elif parsed.path == "/markets":
                self._serve_markets(urllib.parse.parse_qs(parsed.query))
            else:
                self._send_json(404, {"error": "not found"})
        except Exception as exc:
            print(f"request failed: {exc}", file=sys.stderr)
            self._send_json(500, {"error": "internal server error"})

    def _serve_markets(self, query: dict) -> None:
        platform = (query.get("platform") or [None])[0]
        if platform not in (None, "", "kalshi", "polymarket"):
            self._send_json(400, {"error": "platform must be kalshi or polymarket"})
            return

        raw_limit = (query.get("limit") or ["100"])[0]
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            self._send_json(400, {"error": "limit must be an integer"})
            return
        limit = min(1000, max(1, limit))

        q = (query.get("q") or [None])[0]
        clauses: list[str] = []
        args: list[object] = []
        if platform:
            clauses.append("platform = %s")
            args.append(platform)
        if q:
            clauses.append("question_normalized ILIKE %s ESCAPE '\\'")
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.append(f"%{escaped}%")

        sql = "SELECT * FROM normalized_prediction_markets"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY scraped_at DESC NULLS LAST, market_id LIMIT %s"
        args.append(limit)

        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.dsn) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, args)
                rows = cur.fetchall()
        self._send_json(200, {"markets": rows, "count": len(rows)})


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    dsn = get_dsn()
    handler = type("BoundMarketsHTTPHandler", (MarketsHTTPHandler,), {"dsn": dsn})
    with ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"Serving markets on http://{host}:{port}")
        httpd.serve_forever()


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Scrape, normalize, store, and serve Kalshi/Polymarket prediction markets."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scrape_parser = sub.add_parser(
        "scrape",
        description="Fetch open markets from public APIs and upsert normalized rows.",
    )
    scrape_parser.add_argument(
        "--platform",
        choices=["both", "kalshi", "polymarket"],
        default="both",
        help="Which platform to scrape (default: both).",
    )
    scrape_parser.add_argument(
        "--pages",
        type=_positive_int,
        default=1,
        help="Maximum API pages per platform (default: 1).",
    )

    serve_parser = sub.add_parser(
        "serve",
        description="Serve GET /health and GET /markets as JSON.",
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    serve_parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000).")

    args = parser.parse_args(argv)
    try:
        if args.command == "scrape":
            scrape(args.platform, args.pages)
        else:
            serve(args.host, args.port)
    except RuntimeError as exc:
        raise SystemExit(f"error: {exc}") from None


if __name__ == "__main__":
    main()
