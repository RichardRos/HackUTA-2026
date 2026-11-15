"""Shared helpers for the nostos ingest scripts: env, HTTP, time, units, DB writes.

Environment
  TIMESCALE_SERVICE_URL  Tiger Cloud connection string (required)
  EIA_API_KEY            free key from https://www.eia.gov/opendata/register.php
                         (falls back to EIA's DEMO_KEY, which is too rate-limited
                         for a national backfill)
  NWS_USER_AGENT         optional; api.weather.gov asks for an app name plus a
                         contact, e.g. "nostos-ingest (you@example.com)"
Read from the environment first, then from the .env file named by NOSTOS_ENV
(same default as db/run_sql.py), then from a repo-root .env.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

import psycopg
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ENV = REPO.parent / "Research" / "tiger-cloud-db-nostos_weather-credentials.env"
USER_AGENT = "nostos-ingest/2.0 (HackUTA 2026)"


# ---------------------------------------------------------------------------
# Environment / logging / time
# ---------------------------------------------------------------------------
ENV_KEYS = ("TIMESCALE_SERVICE_URL", "EIA_API_KEY", "NWS_USER_AGENT")
PASSWORD_KEYS = ("PGPASSWORD", "TIMESCALE_PASSWORD", "TIMESCALE_SERVICE_PASSWORD")


def env_files() -> list[Path]:
    """.env files to read, highest priority first: $NOSTOS_ENV (or the Research default), repo .env."""
    return [Path(os.getenv("NOSTOS_ENV", DEFAULT_ENV)), REPO / ".env"]


def load_env(quiet: bool = False) -> None:
    """Fill missing keys from the .env files. Real environment variables win; an exported
    but empty variable counts as missing (so it can't hide the .env value)."""
    for k in ENV_KEYS + PASSWORD_KEYS:
        if k in os.environ and not os.environ[k].strip():
            del os.environ[k]
    loaded = [p for p in env_files() if p.is_file() and load_dotenv(p)]
    # Tiger Cloud's credentials file keeps the password out of the URL, on its own line.
    # libpq reads PGPASSWORD; accept the other names too.
    if not os.getenv("PGPASSWORD"):
        for k in PASSWORD_KEYS[1:]:
            if os.getenv(k):
                os.environ["PGPASSWORD"] = os.environ[k]
                break
    if not quiet:
        found = [k for k in ENV_KEYS if os.getenv(k)]
        missing = [k for k in ENV_KEYS if not os.getenv(k)]
        log(f"env: read {', '.join(str(p) for p in loaded) or 'no .env file'}; "
            f"set: {', '.join(found) or 'none'}" + (f"; missing: {', '.join(missing)}" if missing else "")
            + f"; db password: {_password_source()}")


def _password_source() -> str:
    url = os.getenv("TIMESCALE_SERVICE_URL") or ""
    if urllib.parse.urlsplit(url).password:
        return "in URL"
    return "PGPASSWORD" if os.getenv("PGPASSWORD") else "none"


def db_url() -> str:
    url = os.getenv("TIMESCALE_SERVICE_URL")
    if not url:
        sys.exit("TIMESCALE_SERVICE_URL is not set. Add a line TIMESCALE_SERVICE_URL=postgres://... to "
                 f"{REPO / '.env'} (or export it, or point NOSTOS_ENV at another .env file).")
    if _password_source() == "none":
        sys.exit("TIMESCALE_SERVICE_URL has no password and PGPASSWORD is not set.\n"
                 "  Locally: add PGPASSWORD=... (from the Tiger Cloud credentials file) to .env.\n"
                 "  GitHub Actions: add a repo secret TIMESCALE_PASSWORD with that value.")
    return url


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%SZ}  {msg}", flush=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def round_hour(dt: datetime) -> datetime:
    """Nearest hour; :30 and later round up (a :53 METAR belongs to the next hour)."""
    return floor_hour(dt + timedelta(minutes=30))


def parse_utc(s: str) -> datetime:
    """'2026-11-14', '2026-11-14T05', '2026-11-14T05:00[:00][+00:00|Z]' -> aware UTC."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1]
    if len(s) > 19 and s[19] in "+-":                   # ISO offset
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError(f"unrecognised UTC time: {s!r}")


# ---------------------------------------------------------------------------
# HTTP (stdlib only; retries on 429 / 5xx / network errors)
# ---------------------------------------------------------------------------
def _open(url: str, params=None, headers=None, retries: int = 5, timeout: int = 120):
    full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    shown = full.split("api_key=")[0] + ("api_key=***" if "api_key=" in full else "")
    hdrs = {"User-Agent": os.getenv("NWS_USER_AGENT") or USER_AGENT, **(headers or {})}
    req = urllib.request.Request(full, headers=hdrs)
    for attempt in range(1, retries + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code == 404:
                raise FileNotFoundError(shown) from None
            if e.code != 429 and e.code < 500:
                raise RuntimeError(f"HTTP {e.code} from {shown}: {body}") from None
            err = f"HTTP {e.code}: {body}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = str(e)
        if attempt == retries:
            raise RuntimeError(f"giving up after {retries} tries on {shown}: {err}")
        wait = min(60, 2 ** attempt)
        log(f"  retry {attempt}/{retries} in {wait}s ({err[:120]})")
        time.sleep(wait)


_READ_ERRORS = (ConnectionError, TimeoutError, socket.timeout, http.client.HTTPException, ssl.SSLError)


def get_bytes(url: str, params=None, headers=None, retries: int = 5, timeout: int = 120) -> bytes:
    """GET the whole body, retrying when the connection drops mid-download too."""
    for attempt in range(1, retries + 1):
        try:
            with _open(url, params, headers, retries=retries, timeout=timeout) as resp:
                return resp.read()
        except _READ_ERRORS as e:
            if attempt == retries:
                raise RuntimeError(f"giving up after {retries} tries reading {url}: {e}") from None
            wait = min(60, 2 ** attempt)
            log(f"  retry {attempt}/{retries} in {wait}s (read failed: {str(e)[:120]})")
            time.sleep(wait)


def get_json(url: str, params=None, headers=None, **kw):
    return json.loads(get_bytes(url, params, headers, **kw))


def iter_lines(url: str, **kw) -> Iterator[str]:
    """Stream a large text file line by line."""
    with _open(url, **kw) as resp:
        for raw in resp:
            yield raw.decode("ascii", "replace").rstrip("\r\n")


# ---------------------------------------------------------------------------
# Units and derived weather
# ---------------------------------------------------------------------------
def c_to_f(c):
    return None if c is None else c * 9 / 5 + 32


def kt_to_mph(kt):
    return None if kt is None else kt * 1.150779


def kmh_to_mph(v):
    return None if v is None else v / 1.609344


def ms_to_mph(v):
    return None if v is None else v * 2.236936


def mm_to_in(v):
    return None if v is None else v / 25.4


def m_to_ft(v):
    return None if v is None else v * 3.28084


def m_to_mi(v):
    return None if v is None else v / 1609.344


def km_to_mi(v):
    return None if v is None else v / 1.609344


def rh_from(t_f, td_f):
    """Relative humidity (%) from temperature and dew point (Magnus formula)."""
    if t_f is None or td_f is None:
        return None
    t, td = (t_f - 32) * 5 / 9, (td_f - 32) * 5 / 9
    a, b = 17.625, 243.04
    return max(0.0, min(100.0, 100 * math.exp(a * td / (b + td) - a * t / (b + t))))


def heat_index_f(t_f, rh):
    """NWS heat index (Rothfusz with Steadman for mild temps)."""
    if t_f is None or rh is None:
        return None
    simple = 0.5 * (t_f + 61 + (t_f - 68) * 1.2 + rh * 0.094)
    if (simple + t_f) / 2 < 80:
        return simple
    hi = (-42.379 + 2.04901523 * t_f + 10.14333127 * rh - 0.22475541 * t_f * rh
          - 6.83783e-3 * t_f ** 2 - 5.481717e-2 * rh ** 2 + 1.22874e-3 * t_f ** 2 * rh
          + 8.5282e-4 * t_f * rh ** 2 - 1.99e-6 * t_f ** 2 * rh ** 2)
    if rh < 13 and 80 <= t_f <= 112:
        hi -= (13 - rh) / 4 * math.sqrt((17 - abs(t_f - 95)) / 17)
    elif rh > 85 and 80 <= t_f <= 87:
        hi += (rh - 85) / 10 * (87 - t_f) / 5
    return hi


def wind_chill_f(t_f, wind_mph):
    if t_f is None or wind_mph is None or t_f > 50 or wind_mph < 3:
        return None
    v = wind_mph ** 0.16
    return 35.74 + 0.6215 * t_f - 35.75 * v + 0.4275 * t_f * v


def apparent_f(t_f, rh, wind_mph):
    """NWS-style apparent temperature: heat index when warm, wind chill when cold."""
    if t_f is None:
        return None
    if t_f >= 80 and rh is not None:
        return heat_index_f(t_f, rh)
    wc = wind_chill_f(t_f, wind_mph)
    return wc if wc is not None else t_f


def num(v) -> Optional[float]:
    """Parse a number that may arrive as str/None/''."""
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def connect() -> psycopg.Connection:
    return psycopg.connect(db_url())


def stage_and_merge(conn: psycopg.Connection, stage: str, cols: list[tuple[str, str]],
                    rows: list[tuple], merge_sql: str, params=None) -> int:
    """COPY rows into a temp table, run merge_sql (INSERT ... SELECT FROM stage), commit."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE IF NOT EXISTS {stage} ("
                    + ", ".join(f"{n} {t}" for n, t in cols) + ") ON COMMIT DELETE ROWS")
        with cur.copy(f"COPY {stage} ({', '.join(n for n, _ in cols)}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row(r)
        cur.execute(merge_sql, params or {})
        n = cur.rowcount
    conn.commit()
    return n


def guard_fake(conn: psycopg.Connection) -> None:
    fake = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM demand_predictions WHERE model_version = 'fake-v0')"
    ).fetchone()[0]
    if fake:
        sys.exit("Fake seed data is still loaded. Run first:\n"
                 "    python db/run_sql.py db/99_clear_fake.sql\n"
                 "(it deletes by time window, so running it after the backfill would delete real rows)")


def require_schema_v3(conn: psycopg.Connection) -> None:
    ok = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'weather_forecasts' AND column_name = 'extra')"
    ).fetchone()[0]
    if not ok:
        sys.exit("Schema is missing the NOAA/national columns. Run first:\n"
                 "    python db/run_sql.py db/06_national_noaa.sql")


def require_schema_v4(conn: psycopg.Connection) -> None:
    require_schema_v3(conn)
    ok = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'weather_points' AND column_name = 'country')"
    ).fetchone()[0]
    if not ok:
        sys.exit("Schema is missing the global/GFS changes. Run first:\n"
                 "    python db/run_sql.py db/13_global_gfs.sql\n"
                 "(or skip GFS with --skip gfs)")


if __name__ == "__main__":
    # `python ingest/common.py` shows which .env files and keys the ingest scripts will see
    load_env()
