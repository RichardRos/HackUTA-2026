from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from markets import get_dsn

ROOT = Path(__file__).resolve().parent
LOG_LIMIT = 12_000
RUN_RETENTION_DAYS = 30
RETENTION_INTERVAL = 86_400
LEGACY_CHECK_INTERVAL = 21_600
HEARTBEAT_SECONDS = 5
HEARTBEAT_STALE = 30
LOCK_CHECK_SECONDS = 10
TICK_SECONDS = 1
TICK_STALE = 15
HTTP_STATEMENT_TIMEOUT_MS = 5000
ADVISORY_LOCK_KEY = 728_193_041

JOBS = {
    "markets": {
        "command": [sys.executable, "markets.py", "scrape", "--platform", "both", "--pages", "1"],
        "interval": 300,
        "timeout": 900,
    },
    "news": {
        "command": [sys.executable, "news.py", "--once"],
        "interval": 900,
        "timeout": 600,
    },
    "noaa": {
        "command": [sys.executable, "open_feeds.py", "scrape", "--source", "noaa"],
        "interval": 3600,
        "timeout": 900,
    },
    "commodities": {
        "command": [sys.executable, "open_feeds.py", "scrape", "--source", "commodities"],
        "interval": 21_600,
        "timeout": 1800,
    },
    "shipping": {
        "command": [sys.executable, "open_feeds.py", "scrape", "--source", "shipping"],
        "interval": 3600,
        "timeout": 900,
    },
    "grid_weather": {  # EIA-930 demand, NOAA NBM/NWS/GFS weather (ingest/update.py); was a GitHub Action
        "command": [sys.executable, "ingest/update.py", "--skip", "ships"],
        "interval": 3600,
        "timeout": 1800,
    },
    "ships": {  # AIS snapshot (aisstream.io): one fix per vessel per hour, the voyage model's training rows
        "command": [sys.executable, "ingest/ships_ais.py"],
        "interval": 3600,
        "timeout": 600,
    },
}


def child_env(dsn: str) -> dict:
    env = os.environ.copy()
    for name in ("DATABASE_URL", "TIGER_URL", "NOSTOS_ENV"):
        env.pop(name, None)
    env["TIMESCALE_SERVICE_URL"] = dsn
    return env


def connect(dsn: str | None = None, readonly: bool = False, **kwargs):
    import psycopg
    from psycopg.rows import dict_row

    if readonly:
        kwargs["options"] = (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={HTTP_STATEMENT_TIMEOUT_MS}"
        )
    return psycopg.connect(
        dsn or get_dsn(), autocommit=True, row_factory=dict_row, **kwargs
    )


def _age_check(source: str, name: str, warn: int, fail: int) -> dict:
    return {
        "source": "external",
        "name": name,
        "sql": "SELECT extract(epoch FROM now() - max(fetched_at))::float8 AS value "
        "FROM external_observations WHERE source = %s",
        "params": (source,),
        "warn_above": warn,
        "fail_above": fail,
        "missing_inf": True,
    }


def _freshness_check(job: str) -> dict:
    interval = JOBS[job]["interval"]
    return {
        "source": job,
        "name": f"{job}_last_success_age_seconds",
        "sql": "SELECT extract(epoch FROM now() - max(started_at))::float8 AS value "
        "FROM ingest_runs WHERE job_name = %s AND status = 'succeeded'",
        "params": (job,),
        "warn_above": interval * 2,
        "fail_above": interval * 4,
        "missing_inf": True,
    }


CHECKS = [
    *[_freshness_check(job) for job in JOBS],
    {
        "source": "markets",
        "name": "markets_rows",
        "sql": "SELECT count(*)::float8 AS value FROM prediction_markets",
        "fail_below": 1,
    },
    {
        "source": "markets",
        "name": "markets_age_seconds",
        "sql": "SELECT extract(epoch FROM now() - max(scraped_at))::float8 AS value FROM prediction_markets",
        "warn_above": 600,
        "fail_above": 1800,
        "missing_inf": True,
    },
    {
        "source": "markets",
        "name": "markets_blank_questions",
        "sql": (
            "SELECT count(*)::float8 AS value FROM prediction_markets "
            "WHERE btrim(question_raw) = '' OR btrim(question_normalized) = ''"
        ),
        "fail_above": 0,
    },
    {
        "source": "markets",
        "name": "markets_without_outcomes",
        "sql": (
            "SELECT count(*)::float8 AS value FROM prediction_markets m "
            "WHERE NOT EXISTS (SELECT 1 FROM prediction_market_outcomes o "
            "WHERE o.platform = m.platform AND o.market_id = m.market_id)"
        ),
        "warn_above": 0,
    },
    {
        "source": "news",
        "name": "news_articles",
        "sql": "SELECT count(*)::float8 AS value FROM rss_articles",
        "fail_below": 1,
    },
    {
        "source": "news",
        "name": "news_age_seconds",
        "sql": "SELECT extract(epoch FROM now() - max(fetched_at))::float8 AS value FROM rss_feeds",
        "warn_above": 1800,
        "fail_above": 3600,
        "missing_inf": True,
    },
    {
        "source": "news",
        "name": "news_blank_or_tracking",
        "sql": (
            "SELECT count(*)::float8 AS value FROM rss_articles "
            "WHERE (title IS NOT NULL AND btrim(title) = '') "
            "OR link ~ '[?&]mod=' OR link ~ '[?&]syn-'"
        ),
        "warn_above": 0,
    },
    _age_check("noaa_nws", "noaa_nws_age_seconds", 5400, 7200),
    _age_check("fred", "fred_age_seconds", 25_200, 28_800),
    _age_check("cftc", "cftc_age_seconds", 25_200, 28_800),
    _age_check("noaa_coops", "noaa_coops_age_seconds", 5400, 7200),
    _age_check("sse", "sse_age_seconds", 5400, 7200),
    {
        "source": "external",
        "name": "external_nonfinite_values",
        "sql": (
            "SELECT count(*)::float8 AS value FROM external_observations "
            "WHERE value IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)"
        ),
        "fail_above": 0,
    },
    {
        "source": "external",
        "name": "external_available_after_fetch",
        "sql": (
            "SELECT count(*)::float8 AS value FROM external_observations "
            "WHERE available_at > fetched_at + interval '5 minutes'"
        ),
        "fail_above": 0,
    },
]

LEGACY_CHECK = {
    "source": "legacy_informational",
    "name": "data_integrity_violations",
    "sql": "SELECT coalesce(sum(violations), 0)::float8 AS value FROM data_integrity_checks",
    "warn_above": 0,
}

CHECK_MAP = {spec["name"]: spec for spec in CHECKS}
JOB_CHECKS = {
    "grid_weather": ["grid_weather_last_success_age_seconds"],
    "ships": ["ships_last_success_age_seconds"],
    "markets": [
        "markets_last_success_age_seconds",
        "markets_rows",
        "markets_age_seconds",
        "markets_blank_questions",
        "markets_without_outcomes",
    ],
    "news": [
        "news_last_success_age_seconds",
        "news_articles",
        "news_age_seconds",
        "news_blank_or_tracking",
    ],
    "noaa": [
        "noaa_last_success_age_seconds",
        "noaa_nws_age_seconds",
        "external_nonfinite_values",
        "external_available_after_fetch",
    ],
    "commodities": [
        "commodities_last_success_age_seconds",
        "fred_age_seconds",
        "cftc_age_seconds",
        "external_nonfinite_values",
        "external_available_after_fetch",
    ],
    "shipping": [
        "shipping_last_success_age_seconds",
        "noaa_coops_age_seconds",
        "sse_age_seconds",
        "external_nonfinite_values",
        "external_available_after_fetch",
    ],
}


class Db:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.conn = None

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def _with(self, fn):
        import psycopg

        last = None
        for _ in range(2):
            try:
                if self.conn is None or self.conn.closed:
                    self.conn = connect(self.dsn)
                return fn(self.conn)
            except psycopg.OperationalError as exc:
                last = exc
                self.close()
        raise last

    def one(self, sql: str, params=()):
        return self._with(lambda conn: conn.execute(sql, params).fetchone())

    def all(self, sql: str, params=()):
        return self._with(lambda conn: conn.execute(sql, params).fetchall())

    def exec(self, sql: str, params=()):
        return self._with(lambda conn: conn.execute(sql, params))

    def executemany(self, sql: str, params) -> None:
        self._with(lambda conn: conn.cursor().executemany(sql, params))


@dataclass
class Execution:
    ok: bool
    rows: int | None
    error: str | None
    exit_code: int | None
    timed_out: bool
    output: str


def parse_rows(job: str, output: str) -> int | None:
    if job == "markets":
        counts = [int(value) for value in re.findall(r"upserted (\d+)", output)]
        return sum(counts) if counts else None
    if job == "news":
        counts = re.findall(r"(\d+) articles upserted", output)
        return int(counts[-1]) if counts else None
    counts = [int(value) for value in re.findall(r"(\d+) rows stored", output)]
    return sum(counts) if counts else None


def classify_result(
    job: str, exit_code: int | None, output: str, timed_out: bool, rows: int | None = None
):
    if timed_out:
        return False, "timeout"
    if exit_code != 0:
        return False, f"exit code {exit_code}"
    if job == "markets":
        if "fetch failed" in output:
            return False, "fetch failed"
        match = re.search(r"failed ([1-9]\d*)", output)
        if match:
            return False, f"failed {match.group(1)}"
        if not rows:
            return False, "no markets upserted"
    return True, None


def run_command(
    command,
    timeout: int,
    on_start=None,
    on_proc=None,
    env=None,
    popen=subprocess.Popen,
):
    proc = popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if on_proc is not None:
        on_proc(proc)
    timed_out = False
    try:
        if on_start is not None:
            on_start(proc.pid)
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        output, _ = proc.communicate()
        timed_out = True
    except BaseException:
        proc.kill()
        proc.communicate()
        raise
    return Execution(
        ok=False,
        rows=None,
        error=None,
        exit_code=proc.returncode,
        timed_out=timed_out,
        output=output or "",
    )


def failure_plan(status: str | None, attempts: int):
    next_attempt = 1 if status in (None, "resolved") else attempts + 1
    if next_attempt >= 3:  # keep trying hourly: a rate limit or outage clears, and dead used to mean stopped for good
        return next_attempt, "pending", 3600
    return next_attempt, "pending", 60 if next_attempt == 1 else 300


def classify_value(value, spec: dict):
    if value is None:
        if spec.get("missing_inf"):
            value = float("inf")
        else:
            return "fail", float("nan")
    value = float(value)
    if spec.get("fail_below") is not None and value < spec["fail_below"]:
        return "fail", value
    if spec.get("fail_above") is not None and value > spec["fail_above"]:
        return "fail", value
    if spec.get("warn_above") is not None and value > spec["warn_above"]:
        return "warn", value
    return "pass", value


def _excerpt(output: str) -> str | None:
    return output[-LOG_LIMIT:] if output else None


def _json_default(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _clean_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity" if value < 0 else "NaN"
    if isinstance(value, dict):
        return {key: _clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(item) for item in value]
    return value


class IngestService:
    def __init__(self) -> None:
        self.dsn = get_dsn()
        self.db = Db(self.dsn)
        self.instance_id = str(uuid.uuid4())
        self.started_at = datetime.now(timezone.utc)
        self.stop = threading.Event()
        self.fatal = None
        self.http_server = None
        self.lock_conn = None
        self.current_proc = None
        self.dlq: dict[str, dict] = {}
        self.due: dict[str, datetime | None] = {}
        self.retry_jobs: set[str] = set()
        self.startup_jobs: set[str] = set(JOBS)
        self.next_retention = 0.0
        self.next_legacy_check = 0.0

    def start(self, host: str = "127.0.0.1", port: int = 8001) -> int:
        self.lock_conn = self._acquire_lock()
        self._install_signals()
        server = None
        heartbeat = None
        lock_monitor = None
        try:
            self._recover()
            self._write_heartbeat()
            heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
            heartbeat.start()
            lock_monitor = threading.Thread(target=self._lock_monitor_loop, daemon=True)
            lock_monitor.start()
            server = IngestHTTPServer((host, port), IngestHTTPHandler)
            server.dsn = self.dsn
            self.http_server = server
            threading.Thread(target=server.serve_forever, daemon=True).start()
            print(f"ingest service listening on http://{host}:{port}", flush=True)
            self._schedule_loop()
            if self.fatal is not None:
                raise RuntimeError(str(self.fatal))
        finally:
            self.stop.set()
            if self.http_server is not None:
                self.http_server.shutdown()
                self.http_server.server_close()
            if heartbeat is not None:
                heartbeat.join(timeout=6)
            if lock_monitor is not None:
                lock_monitor.join(timeout=6)
            self.db.close()
            try:
                self.lock_conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
            except Exception:
                pass
            finally:
                self.lock_conn.close()
        return 0

    def _install_signals(self) -> None:
        for name in ("SIGINT", "SIGBREAK", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is not None:
                signal.signal(sig, self._on_signal)

    def _on_signal(self, signum, frame) -> None:
        self._terminate_current()
        self.stop.set()
        if self.http_server is not None:
            threading.Thread(target=self.http_server.shutdown, daemon=True).start()

    def _terminate_current(self) -> None:
        proc = self.current_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def _acquire_lock(self):
        conn = connect(
            self.dsn,
            connect_timeout=5,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
        )
        try:
            row = conn.execute(
                "SELECT pg_try_advisory_lock(%s) AS locked", (ADVISORY_LOCK_KEY,)
            ).fetchone()
            if not row["locked"]:
                raise RuntimeError("another ingest_service instance holds the advisory lock")
            return conn
        except Exception:
            conn.close()
            raise

    def _lock_monitor_loop(self) -> None:
        while not self.stop.wait(LOCK_CHECK_SECONDS):
            try:
                self.lock_conn.execute("SELECT 1").fetchone()
                row = self.lock_conn.execute(
                    "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' "
                    "AND pid = pg_backend_pid() AND granted "
                    "AND classid = 0 AND objid = %s::oid",
                    (ADVISORY_LOCK_KEY,),
                ).fetchone()
            except Exception as exc:
                self._lock_failed(exc)
                return
            if not row or row["n"] < 1:
                self._lock_failed(RuntimeError("advisory lock is no longer held"))
                return

    def _lock_failed(self, exc: Exception) -> None:
        self.fatal = exc
        print(f"ingest service lock lost: {exc}", file=sys.stderr)
        self._terminate_current()
        self.stop.set()

    def _recover(self) -> None:
        self.db.exec(
            "UPDATE ingest_runs SET status = 'failed', finished_at = now(), "
            "error = 'service_restarted' WHERE status = 'running'"
        )
        self._load_dlq()
        now = self._db_now()
        for job in JOBS:
            row = self.dlq.get(job)
            if row and row["status"] == "dead":
                self.due[job] = None
            elif row and row["status"] == "pending" and row["retry_at"] and row["retry_at"] > now:
                self.due[job] = row["retry_at"]
                self.retry_jobs.add(job)
            else:
                self.due[job] = now
                if row and row["status"] == "pending":
                    self.retry_jobs.add(job)

    def _load_dlq(self) -> None:
        rows = self.db.all("SELECT * FROM ingest_dead_letters")
        self.dlq = {row["job_name"]: row for row in rows}

    def _schedule_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                print(f"scheduler tick failed: {exc}", file=sys.stderr)
            self.stop.wait(TICK_SECONDS)

    def _tick(self) -> None:
        self._maybe_retention()
        now = self._db_now()
        self._refresh_dlq(now)
        self._write_scheduler_tick()
        for job in JOBS:
            due = self.due.get(job)
            if due is None or due > now:
                continue
            if job in self.retry_jobs:
                trigger = "retry"
            elif job in self.startup_jobs:
                trigger = "startup"
            else:
                trigger = "scheduled"
            try:
                self._attempt(job, trigger)
            except Exception as exc:
                print(f"{job} attempt failed: {exc}", file=sys.stderr)
                self.due[job] = now + timedelta(seconds=60)
            return

    def _refresh_dlq(self, now: datetime) -> None:
        rows = self.db.all("SELECT * FROM ingest_dead_letters")
        for row in rows:
            job = row["job_name"]
            if job not in JOBS:
                continue
            self.dlq[job] = row
            if row["status"] == "dead":
                self.due[job] = None
                self.retry_jobs.discard(job)
            elif row["status"] == "pending":
                retry_at = row["retry_at"] or now
                current = self.due.get(job)
                if job not in self.retry_jobs or current is None or retry_at < current:
                    self.due[job] = retry_at
                self.retry_jobs.add(job)
            else:
                self.retry_jobs.discard(job)
                if self.due.get(job) is None:
                    self.due[job] = now

    def _write_scheduler_tick(self) -> None:
        try:
            self.db.exec("UPDATE ingest_service_state SET scheduler_tick_at = now() WHERE id")
        except Exception as exc:
            print(f"scheduler tick update failed: {exc}", file=sys.stderr)

    def _db_now(self) -> datetime:
        return self.db.one("SELECT now() AS now")["now"]

    def _attempt(self, job: str, trigger: str) -> bool:
        spec = JOBS[job]
        previous = self.dlq.get(job)
        attempt = (
            1
            if not previous or previous["status"] == "resolved"
            else int(previous["attempts"]) + 1
        )
        run = self.db.one(
            "INSERT INTO ingest_runs (job_name, status, attempt, trigger_source) "
            "VALUES (%s, 'running', %s, %s) RETURNING id",
            (job, attempt, trigger),
        )
        run_id = int(run["id"])

        def record_pid(pid: int) -> None:
            try:
                self.db.exec("UPDATE ingest_runs SET pid = %s WHERE id = %s", (pid, run_id))
            except Exception as exc:
                print(f"recording pid failed: {exc}", file=sys.stderr)

        try:
            result = run_command(
                spec["command"],
                spec["timeout"],
                on_start=record_pid,
                on_proc=lambda proc: setattr(self, "current_proc", proc),
                env=child_env(self.dsn),
            )
            rows = parse_rows(job, result.output)
            ok, error = classify_result(
                job, result.exit_code, result.output, result.timed_out, rows
            )
            if result.timed_out:
                error = f"timeout after {spec['timeout']}s"
        except OSError as exc:
            result = Execution(False, None, f"spawn failed: {exc}", None, False, "")
            ok, error, rows = False, f"spawn failed: {exc}", None
        finally:
            self.current_proc = None

        self.db.exec(
            "UPDATE ingest_runs SET status = %s, finished_at = now(), exit_code = %s, "
            "rows_affected = %s, log_excerpt = %s, error = %s WHERE id = %s",
            (
                "succeeded" if ok else "failed",
                result.exit_code,
                rows,
                _excerpt(result.output),
                error[:2000] if error else None,
                run_id,
            ),
        )

        if ok:
            self._resolve_dlq(job)
            self.retry_jobs.discard(job)
            self.due[job] = self._db_now() + timedelta(seconds=spec["interval"])
        else:
            _, status, retry_at = self._record_failure(job, error or "failed", result.output)
            if status == "dead":
                self.due[job] = None
                self.retry_jobs.discard(job)
            else:
                self.due[job] = retry_at
                self.retry_jobs.add(job)

        self.startup_jobs.discard(job)
        self._run_checks(job)
        print(
            f"{job}: {'succeeded' if ok else 'failed'} attempt={attempt} "
            f"exit={result.exit_code} rows={rows}",
            flush=True,
        )
        return ok

    def _resolve_dlq(self, job: str) -> None:
        self.db.exec(
            "UPDATE ingest_dead_letters SET status = 'resolved', resolved_at = now(), "
            "retry_at = NULL, updated_at = now() WHERE job_name = %s",
            (job,),
        )
        row = self.dlq.get(job)
        if row is not None:
            row["status"] = "resolved"
            row["retry_at"] = None

    def _record_failure(self, job: str, error: str, output: str):
        from psycopg.types.json import Jsonb

        previous = self.dlq.get(job) or {}
        attempts, status, delay = failure_plan(
            previous.get("status"), int(previous.get("attempts") or 0)
        )
        retry_at = self._db_now() + timedelta(seconds=delay) if delay else None
        self.db.exec(
            """
            INSERT INTO ingest_dead_letters (
                job_name, command, attempts, status, last_error, last_log,
                retry_at, first_failed_at, updated_at, resolved_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now(), NULL)
            ON CONFLICT (job_name) DO UPDATE SET
                command = EXCLUDED.command,
                attempts = EXCLUDED.attempts,
                status = EXCLUDED.status,
                last_error = EXCLUDED.last_error,
                last_log = EXCLUDED.last_log,
                retry_at = EXCLUDED.retry_at,
                updated_at = now(),
                first_failed_at = CASE
                    WHEN ingest_dead_letters.status = 'resolved' THEN now()
                    ELSE ingest_dead_letters.first_failed_at
                END,
                resolved_at = NULL
            """,
            (
                job,
                Jsonb(JOBS[job]["command"]),
                attempts,
                status,
                error[:2000],
                _excerpt(output),
                retry_at,
            ),
        )
        row = {
            "job_name": job,
            "attempts": attempts,
            "status": status,
            "retry_at": retry_at,
            "last_error": error[:2000],
            "last_log": _excerpt(output),
        }
        self.dlq[job] = row
        return attempts, status, retry_at

    def _legacy_due(self) -> bool:
        if time.time() < self.next_legacy_check:
            return False
        try:
            row = self.db.one(
                "SELECT extract(epoch FROM now() - max(checked_at))::float8 AS age "
                "FROM ingest_check_results WHERE check_name = %s",
                (LEGACY_CHECK["name"],),
            )
        except Exception as exc:
            print(f"legacy check scheduling failed: {exc}", file=sys.stderr)
            return False
        if row and row["age"] is not None and row["age"] < LEGACY_CHECK_INTERVAL:
            self.next_legacy_check = time.time() + LEGACY_CHECK_INTERVAL
            return False
        return True

    def _run_checks(self, job: str) -> None:
        specs = [CHECK_MAP[name] for name in JOB_CHECKS[job]]
        if self._legacy_due():
            specs.append(LEGACY_CHECK)
            self.next_legacy_check = time.time() + LEGACY_CHECK_INTERVAL
        rows = self._check_rows(specs)
        if not rows:
            return
        try:
            self.db.executemany(
                "INSERT INTO ingest_check_results "
                "(source_name, check_name, status, value, details) "
                "VALUES (%s, %s, %s, %s, %s)",
                rows,
            )
        except Exception as exc:
            print(f"storing check results failed: {exc}", file=sys.stderr)

    def _check_rows(self, specs) -> list:
        from psycopg.types.json import Jsonb

        rows = []
        for spec in specs:
            try:
                result = self.db.one(spec["sql"], spec.get("params", ()))
                value = result["value"] if result else None
            except Exception as exc:
                print(f"check {spec['name']} failed: {exc}", file=sys.stderr)
                rows.append(
                    (
                        "service",
                        f"check_error_{spec['name']}",
                        "fail",
                        0.0,
                        Jsonb({"error": str(exc)[:500]}),
                    )
                )
                continue
            status, value = classify_value(value, spec)
            details = {
                key: spec[key]
                for key in ("warn_above", "fail_above", "fail_below", "missing_inf")
                if key in spec
            }
            rows.append((spec["source"], spec["name"], status, value, Jsonb(details)))
            rows.append(
                (
                    "service",
                    f"check_error_{spec['name']}",
                    "pass",
                    0.0,
                    Jsonb({"recovered": True}),
                )
            )
        return rows

    def _maybe_retention(self) -> None:
        if time.time() < self.next_retention:
            return
        self.db.exec(
            "DELETE FROM ingest_runs WHERE started_at < now() - interval '30 days'"
        )
        self.db.exec(
            "DELETE FROM ingest_check_results WHERE checked_at < now() - interval '30 days'"
        )
        self.next_retention = time.time() + RETENTION_INTERVAL

    def _heartbeat_loop(self) -> None:
        while not self.stop.wait(HEARTBEAT_SECONDS):
            try:
                self._write_heartbeat()
            except Exception as exc:
                print(f"heartbeat failed: {exc}", file=sys.stderr)

    def _write_heartbeat(self) -> None:
        from psycopg.types.json import Jsonb

        jobs = {
            name: {
                "interval": spec["interval"],
                "timeout": spec["timeout"],
                "command": spec["command"],
            }
            for name, spec in JOBS.items()
        }
        conn = connect(self.dsn)
        try:
            conn.execute(
                """
                INSERT INTO ingest_service_state
                    (id, instance_id, hostname, pid, started_at, heartbeat_at,
                     scheduler_tick_at, jobs)
                VALUES (TRUE, %s, %s, %s, %s, now(), now(), %s)
                ON CONFLICT (id) DO UPDATE SET
                    instance_id = EXCLUDED.instance_id,
                    hostname = EXCLUDED.hostname,
                    pid = EXCLUDED.pid,
                    started_at = EXCLUDED.started_at,
                    heartbeat_at = now(),
                    jobs = EXCLUDED.jobs
                """,
                (
                    self.instance_id,
                    socket.gethostname(),
                    os.getpid(),
                    self.started_at,
                    Jsonb(jobs),
                ),
            )
        finally:
            conn.close()


def run_once(job: str) -> int:
    service = IngestService()
    lock_conn = None
    try:
        lock_conn = service._acquire_lock()
        service._load_dlq()
        jobs = list(JOBS) if job == "all" else [job]
        results = [service._attempt(name, "manual") for name in jobs]
        return 0 if all(results) else 1
    finally:
        if lock_conn is not None:
            try:
                lock_conn.execute(
                    "SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,)
                )
            except Exception:
                pass
            finally:
                lock_conn.close()
        service.db.close()


def _supervisor_live(conn) -> bool:
    state = conn.execute(
        "SELECT extract(epoch FROM now() - heartbeat_at)::float8 AS heartbeat_age, "
        "extract(epoch FROM now() - scheduler_tick_at)::float8 AS tick_age "
        "FROM ingest_service_state WHERE id"
    ).fetchone()
    lock = conn.execute(
        "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' "
        "AND granted AND classid = 0 AND objid = %s::oid",
        (ADVISORY_LOCK_KEY,),
    ).fetchone()["n"]
    if state is None or state["heartbeat_age"] > HEARTBEAT_STALE or lock < 1:
        return False
    if state["tick_age"] is not None and state["tick_age"] <= TICK_STALE:
        return True
    running = conn.execute(
        "SELECT job_name, extract(epoch FROM now() - started_at)::float8 AS age "
        "FROM ingest_runs WHERE status = 'running'"
    ).fetchall()
    return any(
        row["job_name"] in JOBS
        and row["age"] <= JOBS[row["job_name"]]["timeout"] + 30
        for row in running
    )


def replay(job: str) -> int:
    from psycopg.types.json import Jsonb

    dsn = get_dsn()
    conn = connect(dsn)
    try:
        if not _supervisor_live(conn):
            print(
                "replay rejected: ingest supervisor is not running or healthy",
                file=sys.stderr,
            )
            return 1
        conn.execute(
            """
            INSERT INTO ingest_dead_letters (
                job_name, command, attempts, status, retry_at,
                first_failed_at, updated_at, resolved_at
            ) VALUES (%s, %s, 0, 'pending', now(), now(), now(), NULL)
            ON CONFLICT (job_name) DO UPDATE SET
                command = EXCLUDED.command,
                attempts = 0,
                status = 'pending',
                retry_at = now(),
                updated_at = now(),
                resolved_at = NULL
            """,
            (job, Jsonb(JOBS[job]["command"])),
        )
    finally:
        conn.close()
    print(f"queued replay for {job}", flush=True)
    return 0


def _parse_limit(query: str) -> int:
    values = parse_qs(query).get("limit")
    if not values:
        return 20
    try:
        limit = int(values[-1])
    except ValueError:
        raise ValueError("limit must be an integer between 1 and 100") from None
    if not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    return limit


class IngestHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    dsn = ""


class IngestHTTPHandler(BaseHTTPRequestHandler):
    server_version = "IngestService/1.0"

    def log_message(self, fmt, *args) -> None:
        pass

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(_clean_json(payload), default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        routes = {"/health", "/jobs", "/runs", "/checks", "/dead-letters"}
        if path not in routes:
            self._send_json(404, {"error": "not found"})
            return
        try:
            limit = _parse_limit(parsed.query) if path == "/runs" else None
            conn = connect(self.server.dsn, readonly=True)
            try:
                payload, status = self._route(conn, path, limit)
            finally:
                conn.close()
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception as exc:
            print(f"http {path} failed: {exc}", file=sys.stderr)
            self._send_json(503, {"error": "database unavailable"})
            return
        self._send_json(status, payload)

    def _health(self, conn):
        state = conn.execute(
            "SELECT instance_id, hostname, pid, started_at, heartbeat_at, "
            "scheduler_tick_at, "
            "extract(epoch FROM now() - heartbeat_at)::float8 AS heartbeat_age, "
            "extract(epoch FROM now() - scheduler_tick_at)::float8 AS scheduler_tick_age "
            "FROM ingest_service_state WHERE id"
        ).fetchone()
        lock = conn.execute(
            "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' "
            "AND granted AND classid = 0 AND objid = %s::oid",
            (ADVISORY_LOCK_KEY,),
        ).fetchone()["n"]
        failed = conn.execute(
            "SELECT count(*) AS n FROM ingest_latest_checks "
            "WHERE status = 'fail' AND source_name <> 'legacy_informational'"
        ).fetchone()["n"]
        dead = conn.execute(
            "SELECT count(*) AS n FROM ingest_dead_letters WHERE status = 'dead'"
        ).fetchone()["n"]
        running = conn.execute(
            "SELECT job_name, extract(epoch FROM now() - started_at)::float8 AS age "
            "FROM ingest_runs WHERE status = 'running'"
        ).fetchall()
        busy = any(
            row["job_name"] in JOBS
            and row["age"] <= JOBS[row["job_name"]]["timeout"] + 30
            for row in running
        )
        heartbeat_ok = state is not None and state["heartbeat_age"] <= HEARTBEAT_STALE
        tick_ok = (
            state is not None
            and state["scheduler_tick_age"] is not None
            and state["scheduler_tick_age"] <= TICK_STALE
        )
        healthy = lock > 0 and heartbeat_ok and (tick_ok or busy)
        if not healthy:
            status = "degraded"
            code = 503
        elif failed or dead:
            status = "degraded"
            code = 200
        elif busy:
            status = "busy"
            code = 200
        else:
            status = "ok"
            code = 200
        return (
            {
                "status": status,
                "instance_id": state["instance_id"] if state else None,
                "hostname": state["hostname"] if state else None,
                "pid": state["pid"] if state else None,
                "started_at": state["started_at"] if state else None,
                "heartbeat_at": state["heartbeat_at"] if state else None,
                "scheduler_tick_at": state["scheduler_tick_at"] if state else None,
                "heartbeat_age_seconds": state["heartbeat_age"] if state else None,
                "scheduler_tick_age_seconds": state["scheduler_tick_age"] if state else None,
                "lock_held": lock > 0,
                "busy": busy,
                "failed_checks": failed,
                "dead_letters": dead,
            },
            code,
        )

    def _route(self, conn, path: str, limit: int | None):
        if path == "/health":
            return self._health(conn)
        if path == "/jobs":
            latest = {
                row["job_name"]: row
                for row in conn.execute("SELECT * FROM ingest_latest_runs").fetchall()
            }
            dead = {
                row["job_name"]: row
                for row in conn.execute("SELECT * FROM ingest_dead_letters").fetchall()
            }
            return (
                {
                    "jobs": [
                        {
                            "name": name,
                            "interval": spec["interval"],
                            "timeout": spec["timeout"],
                            "command": spec["command"],
                            "latest_run": latest.get(name),
                            "dead_letter": dead.get(name),
                        }
                        for name, spec in JOBS.items()
                    ]
                },
                200,
            )
        if path == "/runs":
            rows = conn.execute(
                "SELECT * FROM ingest_runs ORDER BY started_at DESC, id DESC LIMIT %s",
                (limit,),
            ).fetchall()
            return {"runs": rows}, 200
        if path == "/checks":
            rows = conn.execute(
                "SELECT * FROM ingest_latest_checks ORDER BY source_name, check_name"
            ).fetchall()
            return {"checks": rows}, 200
        rows = conn.execute(
            "SELECT * FROM ingest_dead_letters WHERE status IN ('pending', 'dead') "
            "ORDER BY updated_at DESC"
        ).fetchall()
        return {"dead_letters": rows}, 200


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervised scheduler for the ingest CLIs.")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="run the scheduler and read-only HTTP server")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8001)

    once = sub.add_parser("once", help="run jobs now, then exit")
    once.add_argument("--job", choices=[*JOBS, "all"], default="all")

    replay_parser = sub.add_parser(
        "replay", help="queue a dead letter for the live supervisor"
    )
    replay_parser.add_argument("job", choices=list(JOBS))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "once":
            return run_once(args.job)
        if args.command == "replay":
            return replay(args.job)
        return IngestService().start(args.host, args.port)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"ingest_service: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
