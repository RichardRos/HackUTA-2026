from __future__ import annotations

import contextlib
import inspect
import io
import json
import math
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import ingest_service as service


class FakePopen:
    def __init__(self, output="", returncode=0, timeout_once=False):
        self.output = output
        self.returncode = returncode
        self.timeout_once = timeout_once
        self.calls = 0
        self.killed = False
        self.pid = 1234

    def communicate(self, timeout=None):
        self.calls += 1
        if self.timeout_once and self.calls == 1:
            raise subprocess.TimeoutExpired("cmd", timeout)
        return self.output, None

    def kill(self):
        self.killed = True
        self.returncode = -9


class DsnTests(unittest.TestCase):
    def test_child_env_pins_exact_dsn(self):
        with mock.patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgres://alias-a/db",
                "TIGER_URL": "postgres://alias-b/db",
                "NOSTOS_ENV": "C:/other/.env",
                "UNRELATED": "kept",
            },
            clear=False,
        ):
            env = service.child_env("postgres://exact/target")
        self.assertNotIn("DATABASE_URL", env)
        self.assertNotIn("TIGER_URL", env)
        self.assertNotIn("NOSTOS_ENV", env)
        self.assertEqual(env["TIMESCALE_SERVICE_URL"], "postgres://exact/target")
        self.assertEqual(env["UNRELATED"], "kept")


class CommandTests(unittest.TestCase):
    def test_success_captures_output_and_pid(self):
        proc = FakePopen("hello\n")
        pids = []
        result = service.run_command(
            ["cmd"], 5, on_start=pids.append, popen=lambda *a, **k: proc
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, "hello\n")
        self.assertFalse(result.timed_out)
        self.assertEqual(pids, [1234])

    def test_timeout_kills_and_communicates(self):
        proc = FakePopen("partial", returncode=None, timeout_once=True)
        result = service.run_command(["cmd"], 5, popen=lambda *a, **k: proc)
        self.assertTrue(result.timed_out)
        self.assertTrue(proc.killed)
        self.assertEqual(result.exit_code, -9)
        self.assertEqual(result.output, "partial")

    def test_classification(self):
        self.assertEqual(service.classify_result("news", 0, "ok", False), (True, None))
        self.assertEqual(
            service.classify_result("news", 2, "bad", False), (False, "exit code 2")
        )
        self.assertEqual(
            service.classify_result("news", 0, "ignored", True), (False, "timeout")
        )
        self.assertFalse(
            service.classify_result("markets", 0, "kalshi: fetch failed: boom", False)[0]
        )
        self.assertFalse(
            service.classify_result("markets", 0, "kalshi: fetched 2, upserted 0, failed 2", False)[0]
        )
        self.assertTrue(
            service.classify_result(
                "markets", 0, "kalshi: fetched 2, upserted 2, failed 0", False, rows=2
            )[0]
        )

    def test_market_zero_or_missing_rows_fails(self):
        self.assertFalse(
            service.classify_result(
                "markets", 0, "kalshi: fetched 2, upserted 0, failed 0", False, rows=0
            )[0]
        )
        self.assertFalse(
            service.classify_result(
                "markets", 0, "kalshi: fetched 2, upserted 0, failed 0", False, rows=None
            )[0]
        )

    def test_real_tiny_command(self):
        result = service.run_command([sys.executable, "-c", "print('tiny-ok')"], 10)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.timed_out)
        self.assertIn("tiny-ok", result.output)


class RowsTests(unittest.TestCase):
    def test_markets_sums_upserted(self):
        output = (
            "kalshi: fetched 2, upserted 2, failed 0\n"
            "polymarket: fetched 5, upserted 4, failed 1\n"
        )
        self.assertEqual(service.parse_rows("markets", output), 6)
        self.assertEqual(
            service.parse_rows("markets", "kalshi: fetched 2, upserted 0, failed 2"), 0
        )

    def test_missing_rows_are_not_invented(self):
        self.assertIsNone(service.parse_rows("markets", "kalshi: fetch failed: boom"))
        self.assertIsNone(service.parse_rows("news", "no articles today"))
        self.assertIsNone(service.parse_rows("noaa", "FAIL noaa: timeout"))
        self.assertIsNone(service.parse_rows("commodities", ""))

    def test_news_and_open_feed_rows(self):
        self.assertEqual(
            service.parse_rows("news", "8/10 feeds ok, 42 articles upserted, 2 failed"),
            42,
        )
        self.assertEqual(service.parse_rows("noaa", "noaa: 7 rows stored"), 7)
        self.assertEqual(
            service.parse_rows("shipping", "noaa_coops: 3 rows stored\nsse: 4 rows stored"),
            7,
        )

    def test_log_excerpt_limit(self):
        self.assertEqual(len(service._excerpt("x" * 20_000)), service.LOG_LIMIT)
        self.assertEqual(len(service._excerpt("x" * 20_000)), 12_000)


class RetryTests(unittest.TestCase):
    def test_three_attempts_then_dead(self):
        self.assertEqual(service.failure_plan(None, 0), (1, "pending", 60))
        self.assertEqual(service.failure_plan("pending", 1), (2, "pending", 300))
        self.assertEqual(service.failure_plan("pending", 2), (3, "dead", None))
        self.assertEqual(service.failure_plan("dead", 3), (4, "dead", None))

    def test_resolved_resets_attempts(self):
        self.assertEqual(service.failure_plan("resolved", 2), (1, "pending", 60))


class CheckTests(unittest.TestCase):
    def test_count_thresholds(self):
        self.assertEqual(service.classify_value(0, {"fail_above": 0}), ("pass", 0.0))
        self.assertEqual(service.classify_value(1, {"fail_above": 0})[0], "fail")
        self.assertEqual(service.classify_value(1, {"warn_above": 0})[0], "warn")
        self.assertEqual(service.classify_value(1, {"fail_below": 1})[0], "pass")
        self.assertEqual(service.classify_value(0, {"fail_below": 1})[0], "fail")

    def test_age_thresholds_are_strict(self):
        spec = {"warn_above": 600, "fail_above": 1800, "missing_inf": True}
        self.assertEqual(service.classify_value(600, spec)[0], "pass")
        self.assertEqual(service.classify_value(601, spec)[0], "warn")
        self.assertEqual(service.classify_value(1800, spec)[0], "warn")
        self.assertEqual(service.classify_value(1801, spec)[0], "fail")

    def test_missing_age_is_infinity_and_fails(self):
        status, value = service.classify_value(
            None, {"warn_above": 600, "fail_above": 1800, "missing_inf": True}
        )
        self.assertEqual(status, "fail")
        self.assertTrue(math.isinf(value))

    def test_serialization_is_utc_iso(self):
        value = datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=2)))
        text = json.dumps(service._clean_json({"at": value}), default=service._json_default)
        self.assertIn("2026-01-01T10:00:00+00:00", text)
        self.assertEqual(service._clean_json(float("inf")), "Infinity")
        self.assertEqual(service._clean_json(float("nan")), "NaN")


class ValidationTests(unittest.TestCase):
    def test_unsupported_once_job_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                service.main(["once", "--job", "bogus"])
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                service.main(["replay", "bogus"])

    def test_supported_jobs_parse(self):
        self.assertEqual(
            service.build_parser().parse_args(["once", "--job", "markets"]).job, "markets"
        )
        self.assertEqual(
            service.build_parser().parse_args(["replay", "noaa"]).job, "noaa"
        )

    def test_limit_validation(self):
        self.assertEqual(service._parse_limit(""), 20)
        self.assertEqual(service._parse_limit("limit=100"), 100)
        for value in ("0", "101", "abc"):
            with self.assertRaises(ValueError):
                service._parse_limit(f"limit={value}")


class CheckRecoveryTests(unittest.TestCase):
    def test_failed_check_then_success_records_recovery_pass(self):
        class FakeDb:
            def __init__(self):
                self.results = [RuntimeError("missing table"), {"value": 0}]

            def one(self, sql, params=()):
                result = self.results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

        instance = object.__new__(service.IngestService)
        instance.db = FakeDb()
        spec = {
            "source": "markets",
            "name": "recovery_probe",
            "sql": "SELECT 0 AS value",
            "fail_above": 0,
        }
        latest = {}
        for rows in (instance._check_rows([spec]), instance._check_rows([spec])):
            for row in rows:
                latest[(row[0], row[1])] = row[2]
        self.assertEqual(latest[("service", "check_error_recovery_probe")], "pass")
        self.assertEqual(latest[("markets", "recovery_probe")], "pass")


class OnceLockTests(unittest.TestCase):
    def test_once_holds_lock_and_loads_dlq(self):
        events = []

        class FakeLock:
            def execute(self, sql, params=()):
                events.append("unlock")
                return self

            def close(self):
                events.append("lock_close")

        class FakeDb:
            def close(self):
                events.append("db_close")

        class FakeService:
            def __init__(self):
                self.db = FakeDb()

            def _acquire_lock(self):
                events.append("acquire")
                return FakeLock()

            def _load_dlq(self):
                events.append("load_dlq")

            def _attempt(self, job, trigger):
                events.append(f"attempt:{job}:{trigger}")
                return True

        with mock.patch.object(service, "IngestService", FakeService):
            self.assertEqual(service.run_once("markets"), 0)
        self.assertEqual(
            events,
            [
                "acquire",
                "load_dlq",
                "attempt:markets:manual",
                "unlock",
                "lock_close",
                "db_close",
            ],
        )

    def test_once_lock_busy_fails_before_running(self):
        events = []

        class FakeDb:
            def close(self):
                events.append("db_close")

        class FakeService:
            def __init__(self):
                self.db = FakeDb()

            def _acquire_lock(self):
                events.append("acquire")
                raise RuntimeError("another ingest_service instance holds the advisory lock")

            def _load_dlq(self):
                events.append("load_dlq")

            def _attempt(self, job, trigger):
                events.append("attempt")
                return True

        with mock.patch.object(service, "IngestService", FakeService):
            with self.assertRaises(RuntimeError):
                service.run_once("markets")
        self.assertEqual(events, ["acquire", "db_close"])


class StaticInvariantTests(unittest.TestCase):
    def test_jobs_are_fixed_whitelist(self):
        self.assertEqual(
            list(service.JOBS), ["markets", "news", "noaa", "commodities", "shipping", "ships"]
        )
        expected = {
            "markets": (300, 900),
            "news": (900, 600),
            "noaa": (3600, 900),
            "commodities": (21_600, 1800),
            "shipping": (3600, 900),
            "ships": (3600, 600),
        }
        for name, (interval, timeout) in expected.items():
            self.assertEqual(service.JOBS[name]["interval"], interval)
            self.assertEqual(service.JOBS[name]["timeout"], timeout)
            self.assertEqual(service.JOBS[name]["command"][0], sys.executable)

    def test_check_sql_is_static_select_only(self):
        for spec in service.CHECKS:
            sql = spec["sql"]
            self.assertTrue(sql.lstrip().upper().startswith("SELECT"))
            self.assertNotIn(";", sql)
            self.assertNotIn("{", sql)
            self.assertEqual(sql.count("%s"), len(spec.get("params", ())))

    def test_checks_are_scoped_per_job(self):
        for job, names in service.JOB_CHECKS.items():
            self.assertIn(f"{job}_last_success_age_seconds", names)
            for name in names:
                self.assertIn(name, service.CHECK_MAP)
        self.assertIn("markets_rows", service.JOB_CHECKS["markets"])
        self.assertNotIn("news_articles", service.JOB_CHECKS["markets"])
        self.assertNotIn("markets_rows", service.JOB_CHECKS["news"])

    def test_legacy_check_is_informational_and_warn_only(self):
        self.assertEqual(service.LEGACY_CHECK["source"], "legacy_informational")
        self.assertEqual(service.LEGACY_CHECK["warn_above"], 0)
        self.assertNotIn("fail_above", service.LEGACY_CHECK)
        self.assertIn(
            "legacy_informational",
            inspect.getsource(service.IngestHTTPHandler._health),
        )

    def test_replay_queues_without_running(self):
        source = inspect.getsource(service.replay)
        self.assertNotIn("_attempt", source)
        self.assertIn('JOBS[job]["command"]', source)
        self.assertIn("status = 'pending'", source)

    def test_retention_keeps_dead_letters(self):
        self.assertEqual(service.RUN_RETENTION_DAYS, 30)
        source = inspect.getsource(service.IngestService._maybe_retention)
        self.assertIn("interval '30 days'", source)
        self.assertNotIn("ingest_dead_letters", source)


if __name__ == "__main__":
    unittest.main()
