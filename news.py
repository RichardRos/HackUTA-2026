from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

FEEDS = [
    ("wsj", "https://feeds.content.dowjones.io/public/rss/RSSWorldNews"),
    ("wsj", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"),
    ("wsj", "https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness"),
    ("wsj", "https://feeds.content.dowjones.io/public/rss/socialeconomyfeed"),
    ("ft", "https://www.ft.com/rss/home"),
    ("ft", "https://www.ft.com/rss/world"),
    ("ft", "https://www.ft.com/rss/markets"),
    ("ft", "https://www.ft.com/rss/commodities"),
    ("ft", "https://www.ft.com/rss/energy"),
    ("ft", "https://www.ft.com/rss/climate"),
]
HTTP_TIMEOUT = 25
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TRACKING_PARAMS = {"mod"}
TRACKING_PREFIXES = ("syn-",)

FEED_UPSERT = """
    INSERT INTO rss_feeds (source, feed_url, title, raw_xml, fetched_at)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (source, feed_url) DO UPDATE
       SET title = EXCLUDED.title, raw_xml = EXCLUDED.raw_xml,
           fetched_at = EXCLUDED.fetched_at
    RETURNING id
"""

ARTICLE_UPSERT = """
    INSERT INTO rss_articles (id, feed_id, guid, link, title, summary, author,
                              published_at, updated_at, raw, fetched_at, tags)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
    ON CONFLICT (id) DO UPDATE
       SET feed_id = EXCLUDED.feed_id, guid = EXCLUDED.guid, link = EXCLUDED.link,
           title = EXCLUDED.title, summary = EXCLUDED.summary,
           author = EXCLUDED.author, published_at = EXCLUDED.published_at,
           updated_at = EXCLUDED.updated_at, raw = EXCLUDED.raw,
           fetched_at = EXCLUDED.fetched_at,
           tags = ARRAY(SELECT DISTINCT unnest(rss_articles.tags || EXCLUDED.tags) ORDER BY 1)
"""

# Feed URL path segment -> section tag; unknown segments fall back to the segment itself.
SECTIONS = {
    "rssworldnews": "world", "rssmarketsmain": "markets", "wsjcomusbusiness": "business",
    "socialeconomyfeed": "economy", "home": "top-stories",
}

# Topic tag -> keyword pattern matched against title + summary.
TOPICS = {
    "oil": r"oil|crude|brent|wti|opec\+?|petroleum|refiner(y|ies)|gasoline|diesel",
    "natural-gas": r"natural gas|lng|henry hub",
    "power-grid": r"power grids?|electricity|power prices?|utilit(y|ies)|ercot|blackouts?|data cent(er|re)s?",
    "renewables": r"solar|wind (power|farms?|energy)|renewables?|battery storage",
    "nuclear": r"nuclear power|nuclear plants?|uranium|reactors?",
    "coal": r"coal",
    "weather": r"weather|heat ?waves?|hurricanes?|storms?|drought|freeze|el ni[nñ]o|la ni[nñ]a|temperatures?",
    "climate": r"climate|emissions|carbon|net zero",
    "agriculture": r"wheat|corn|soybeans?|grains?|crops?|farm(s|ers?|ing)?|cattle|coffee|cocoa|sugar",
    "metals": r"gold|silver|copper|alumini?um|iron ore|lithium|nickel",
    "shipping": r"shipping|freight|tankers?|container ships?|suez|panama canal|red sea|ports?",
    "rates": r"federal reserve|the fed|interest rates?|rate (cut|hike)s?|treasur(y|ies)|bond yields?|ecb|bank of england",
    "inflation": r"inflation|cpi|consumer prices",
    "stocks": r"stocks?|s&p 500|nasdaq|dow jones|equities|shares",
    "crypto": r"bitcoin|crypto\w*|ethereum|stablecoins?",
    "texas": r"texas|ercot|houston|dallas|austin",
    "geopolitics": r"tariffs?|sanctions?|iran|russia|ukraine|china|israel|gaza",
}
_TOPIC_RES = {tag: re.compile(rf"\b(?:{p})\b", re.IGNORECASE) for tag, p in TOPICS.items()}


def section_tag(url: str) -> str:
    segment = urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].lower()
    return SECTIONS.get(segment, segment)


def article_tags(url: str, title: str, summary: str) -> list[str]:
    text = f"{title} {summary}"
    return sorted({section_tag(url), *(t for t, rx in _TOPIC_RES.items() if rx.search(text))} - {""})


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _strip_ns(root: ET.Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = _local(el.tag)


def _find_text(el: ET.Element, *names: str) -> str | None:
    for child in el:
        if _local(child.tag) in names:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return None


_TAG_RE = re.compile(r"<[^>]+>")


def plain_text(value: str | None) -> str:
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_link(url: str | None) -> str | None:
    if not url:
        return url
    parts = urllib.parse.urlsplit(url)
    pairs = [
        (k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if k not in TRACKING_PARAMS and not k.startswith(TRACKING_PREFIXES)
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(pairs), parts.fragment)
    )


def article_id(source: str, guid: str | None, link: str | None) -> str:
    return hashlib.sha256(f"{source}\n{guid or link or ''}".encode("utf-8")).hexdigest()


def _atom_link(entry: ET.Element) -> str | None:
    fallback = None
    for child in entry:
        if _local(child.tag) != "link" or not child.get("href"):
            continue
        href = child.get("href").strip()
        if child.get("rel", "alternate") == "alternate":
            return canonical_link(href)
        fallback = fallback or href
    return canonical_link(fallback)


def _raw_json(entry: ET.Element) -> dict:
    raw = {}
    for child in entry:
        name = _local(child.tag)
        if not name:
            continue
        value = "".join(child.itertext()).strip()
        if not value and child.get("href"):
            value = child.get("href")
        raw[name] = value
    return raw


def parse_feed(xml_text: str, source: str) -> dict:
    root = ET.fromstring(xml_text)
    _strip_ns(root)
    kind = _local(root.tag)
    if kind == "rss":
        channel = next((c for c in root if _local(c.tag) == "channel"), None)
        if channel is None:
            raise ValueError("RSS document has no <channel>")
        feed_title = _find_text(channel, "title")
        entries = [c for c in channel if _local(c.tag) == "item"]
    elif kind == "feed":
        feed_title = _find_text(root, "title")
        entries = [c for c in root if _local(c.tag) == "entry"]
    else:
        raise ValueError(f"unsupported feed root <{kind}> (expected rss or feed)")

    articles: list[dict] = []
    seen_guids: set[str] = set()
    seen_links: set[str] = set()
    for entry in entries:
        if kind == "rss":
            guid = _find_text(entry, "guid")
            link = canonical_link(_find_text(entry, "link"))
            summary = plain_text(_find_text(entry, "description", "summary", "encoded"))
            published = parse_timestamp(_find_text(entry, "pubDate", "date", "published"))
            updated = parse_timestamp(_find_text(entry, "updated", "modified"))
        else:
            guid = _find_text(entry, "id")
            link = _atom_link(entry)
            summary = plain_text(_find_text(entry, "summary") or _find_text(entry, "content"))
            published = parse_timestamp(_find_text(entry, "published"))
            updated = parse_timestamp(_find_text(entry, "updated"))

        if not (guid or link):
            continue
        # WSJ repeats guids/links inside one feed; collapse so no row is hit twice.
        if (guid and guid in seen_guids) or (link and link in seen_links):
            continue
        seen_guids.update(filter(None, [guid]))
        seen_links.update(filter(None, [link]))

        articles.append({
            "id": article_id(source, guid, link),
            "guid": guid,
            "link": link,
            "title": plain_text(_find_text(entry, "title")),
            "summary": summary,
            "author": _find_text(entry, "author", "creator"),
            "published_at": published,
            "updated_at": updated,
            "raw": _raw_json(entry),
        })

    return {"title": plain_text(feed_title), "articles": articles}


def fetch(url: str, timeout: int = HTTP_TIMEOUT) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _connect():
    try:
        import psycopg
    except ImportError:
        raise SystemExit("psycopg is required: pip install 'psycopg[binary]'")

    db_dir = str(Path(__file__).resolve().parent / "db")
    sys.path.insert(0, db_dir)
    try:
        from run_sql import get_url
    except ImportError as exc:
        raise SystemExit(f"cannot load db/run_sql.py: {exc}")
    finally:
        sys.path.remove(db_dir)
    return psycopg.connect(get_url())


def ingest(conn, feeds, now: datetime | None = None) -> tuple[int, list[tuple[str, str]]]:
    now = now or datetime.now(timezone.utc)
    total, failures = 0, []
    for source, url in feeds:
        try:
            raw_xml = fetch(url)
            parsed = parse_feed(raw_xml, source)
        except Exception as exc:
            failures.append((url, f"{type(exc).__name__}: {exc}"))
            print(f"  FAIL {source:3} {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        feed_id = conn.execute(
            FEED_UPSERT, (source, url, parsed["title"], raw_xml, now)
        ).fetchone()[0]
        rows = {
            a["id"]: (a["id"], feed_id, a["guid"], a["link"], a["title"], a["summary"],
                      a["author"], a["published_at"], a["updated_at"],
                      json.dumps(a["raw"]), now,
                      article_tags(url, a["title"], a["summary"]))
            for a in parsed["articles"]
        }
        conn.cursor().executemany(ARTICLE_UPSERT, list(rows.values()))
        conn.commit()
        total += len(rows)
        print(f"  {source:3} {url} -> {len(rows)} articles")
    return total, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Save WSJ and FT RSS feeds and their normalized articles."
    )
    parser.add_argument("--once", action="store_true", help="fetch once and exit (default)")
    parser.add_argument("--source", choices=["wsj", "ft", "all"], default="all",
                        help="restrict to one publisher (default: all)")
    parser.add_argument("--feed", action="append", default=[], metavar="URL",
                        help="use this feed URL instead of the configured set (repeatable)")
    args = parser.parse_args(argv)

    if args.feed:
        source = args.source if args.source != "all" else None
        feeds = [(source or ("ft" if "ft.com" in url else "wsj"), url) for url in args.feed]
    else:
        feeds = [(s, u) for s, u in FEEDS if args.source in ("all", s)]

    conn = _connect()
    try:
        total, failures = ingest(conn, feeds)
    finally:
        conn.close()

    print(f"{len(feeds) - len(failures)}/{len(feeds)} feeds ok, "
          f"{total} articles upserted, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
