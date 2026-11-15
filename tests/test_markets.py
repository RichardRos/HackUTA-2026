import json
import os
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

import markets


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class NormalizationTests(unittest.TestCase):
    def test_default_gemini_model(self):
        self.assertEqual(markets.GEMINI_MODEL_DEFAULT, "gemini-3.5-flash-lite")

    def test_punctuation_stopwords_and_numbers(self):
        expected = "market close above 70000 friday"
        self.assertEqual(
            markets.regex_normalize("Will the market close above 70,000 by Friday?"),
            expected,
        )
        self.assertEqual(
            markets.regex_normalize("WILL the MARKET close above 70,000 by Friday!!!"),
            expected,
        )
        self.assertEqual(markets.regex_normalize("0.12"), "0.12")
        # "no" and "not" must survive stop-word removal.
        self.assertEqual(markets.regex_normalize("Will not be no?"), "not no")

    def test_before_after_are_preserved(self):
        before = markets.regex_normalize("Will the price be above 100 before 2027?")
        after = markets.regex_normalize("Will the price be above 100 after 2027?")
        self.assertEqual(before, "price above 100 before 2027")
        self.assertEqual(after, "price above 100 after 2027")
        self.assertNotEqual(before, after)

    def test_regex_success_skips_gemini(self):
        with mock.patch.object(
            markets, "gemini_normalize", side_effect=AssertionError("Gemini was called")
        ):
            normalized, method = markets.normalize_market_question(
                "Will the price be above 100?"
            )
        self.assertEqual(normalized, "price above 100")
        self.assertEqual(method, "regex")

    def test_gemini_fallback_with_mocked_http(self):
        payload = {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps({"question": "Will X happen?"})}]}}
            ]
        }
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
            with mock.patch.object(
                markets.urllib.request, "urlopen", return_value=_FakeResponse(payload)
            ) as fake_urlopen:
                normalized, method = markets.normalize_market_question("???")
        self.assertEqual(normalized, "x happen")
        self.assertEqual(method, "gemini")
        request = fake_urlopen.call_args.args[0]
        self.assertIn(":generateContent", request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")

    def test_gemini_json_supports_standard_json_schema(self):
        payload = {"candidates": [{"content": {"parts": [{"text": '{"region":"ERCO"}'}]}}]}
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
            with mock.patch.object(
                markets.urllib.request, "urlopen", return_value=_FakeResponse(payload)
            ) as fake_urlopen:
                result = markets.gemini_json(
                    "find ERCO", {"type": "object"}, "response_json_schema"
                )
        self.assertEqual(result, {"region": "ERCO"})
        request = fake_urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertIn("response_json_schema", body["generationConfig"])
        self.assertNotIn("response_schema", body["generationConfig"])

    def test_gemini_requires_key_when_fallback_is_needed(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                markets.normalize_market_question("???")


class ConnectionTests(unittest.TestCase):
    def test_timescale_service_url_alias(self):
        env = {
            "DATABASE_URL": "",
            "TIGER_URL": "",
            "TIMESCALE_SERVICE_URL": "postgres://example/db",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("dotenv.load_dotenv"):
                self.assertEqual(markets.get_dsn(), "postgres://example/db")

    def test_root_dotenv_loaded_even_with_shell_dsn(self):
        root_env = Path(markets.__file__).resolve().with_name(".env")
        env = {
            "DATABASE_URL": "",
            "TIGER_URL": "",
            "TIMESCALE_SERVICE_URL": "postgres://shell/db",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("dotenv.load_dotenv") as load_dotenv:
                self.assertEqual(markets.get_dsn(), "postgres://shell/db")
        load_dotenv.assert_called_once_with(root_env)


class AdapterTests(unittest.TestCase):
    def test_polymarket_decodes_string_arrays(self):
        market = {
            "conditionId": "0xabc",
            "question": "Will it rain on Friday?",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.7", "0.3"]',
            "events": [{"id": "evt-1", "slug": "rain-friday"}],
            "closed": False,
            "archived": False,
            "active": True,
            "endDate": "2026-01-01T00:00:00Z",
            "description": "Rain rules",
        }
        parsed = markets.parse_polymarket_market(market)

        self.assertEqual(parsed["market_id"], "0xabc")
        self.assertEqual(parsed["event_id"], "evt-1")
        self.assertEqual(parsed["slug"], "rain-friday")
        self.assertEqual(parsed["url"], "https://polymarket.com/event/rain-friday")
        self.assertEqual(parsed["status"], "active")
        self.assertEqual([o["raw"] for o in parsed["outcomes"]], ["Yes", "No"])
        self.assertEqual([o["price"] for o in parsed["outcomes"]], [0.7, 0.3])

    def test_polymarket_status_precedence(self):
        base = {"conditionId": "0x1", "question": "Q?", "closed": True,
                "archived": True, "active": True}
        self.assertEqual(markets.parse_polymarket_market(base)["status"], "closed")

        archived = {"conditionId": "0x1", "question": "Q?", "closed": False,
                    "archived": True, "active": True}
        self.assertEqual(markets.parse_polymarket_market(archived)["status"], "archived")

        inactive = {"conditionId": "0x1", "question": "Q?", "active": False}
        self.assertEqual(markets.parse_polymarket_market(inactive)["status"], "inactive")

    def test_kalshi_numeric_and_price_parsing(self):
        event = {
            "event_ticker": "EVT",
            "series_ticker": "KXRAIN",
            "category": "Weather",
            "title": "Rain event",
        }
        market = {
            "ticker": "T1",
            "yes_bid_dollars": "0.40",
            "yes_ask_dollars": "0.60",
            "no_bid_dollars": "0.35",
            "no_ask_dollars": "0.45",
            "last_price_dollars": "0.90",
            "status": "open",
            "close_time": "2026-01-01T00:00:00Z",
            "rules_primary": "Primary",
            "rules_secondary": "Secondary",
            "title": "Will it rain?",
            "yes_sub_title": "Yes",
        }
        parsed = markets.parse_kalshi_market(event, market)

        self.assertEqual(parsed["market_id"], "T1")
        self.assertEqual(parsed["event_id"], "EVT")
        self.assertEqual(parsed["url"], "https://kalshi.com/markets/kxrain")
        self.assertEqual(parsed["question_raw"], "Will it rain? Yes")
        self.assertEqual(parsed["rules"], "Primary Secondary")
        self.assertAlmostEqual(parsed["outcomes"][0]["price"], 0.5)
        self.assertAlmostEqual(parsed["outcomes"][1]["price"], 0.4)

    def test_kalshi_last_price_fallback_and_malformed_values(self):
        event = {"event_ticker": "EVT"}
        market = {"ticker": "T2", "last_price_dollars": "0.25"}
        parsed = markets.parse_kalshi_market(event, market)
        self.assertEqual(parsed["outcomes"][0]["price"], 0.25)
        self.assertEqual(parsed["outcomes"][1]["price"], 0.75)

        malformed = {
            "ticker": "T3",
            "yes_bid_dollars": "n/a",
            "yes_ask_dollars": "0.60",
            "last_price_dollars": "",
        }
        parsed = markets.parse_kalshi_market(event, malformed)
        self.assertIsNone(parsed["outcomes"][0]["price"])
        self.assertIsNone(parsed["outcomes"][1]["price"])


class KalshiCommodityIngestTests(unittest.TestCase):
    def test_commodity_series_discovery_uses_category_index(self):
        payload = {"series": [{"ticker": "KXCATTLE"}, {"ticker": "KXGOLD"}, {"ticker": "KXCATTLE"}, {}]}
        with mock.patch.object(
            markets.urllib.request, "urlopen", return_value=_FakeResponse(payload)
        ) as fake_urlopen:
            tickers = markets.kalshi_commodity_series()

        self.assertEqual(tickers, ["KXCATTLE", "KXGOLD"])
        request = fake_urlopen.call_args.args[0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        self.assertTrue(request.full_url.startswith(markets.KALSHI_SERIES_URL))
        self.assertEqual(query["category"], ["Commodities"])
        self.assertEqual(query["limit"], ["200"])

    def test_fetch_kalshi_adds_a_query_per_commodity_series(self):
        def respond(request, **_):
            if request.full_url.startswith(markets.KALSHI_SERIES_URL):
                return _FakeResponse({"series": [{"ticker": "KXCATTLE"}]})
            query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
            ticker = (query.get("series_ticker") or ["GENERIC"])[0]
            return _FakeResponse({"events": [{
                "event_ticker": f"EV-{ticker}",
                "series_ticker": ticker,
                "category": "Commodities" if ticker != "GENERIC" else "Weather",
                "markets": [{"ticker": f"T-{ticker}", "status": "active"}],
            }]})

        with mock.patch.object(
            markets.urllib.request, "urlopen", side_effect=respond
        ) as fake_urlopen:
            fetched = list(markets.fetch_kalshi())

        self.assertEqual([m["market_id"] for m in fetched], ["T-GENERIC", "T-KXCATTLE"])
        self.assertEqual(fetched[-1]["category"], "Commodities")
        event_queries = [
            urllib.parse.parse_qs(urllib.parse.urlparse(call.args[0].full_url).query)
            for call in fake_urlopen.call_args_list
            if call.args[0].full_url.startswith(markets.KALSHI_EVENTS_URL)
        ]
        self.assertEqual(
            [q.get("series_ticker", [None])[0] for q in event_queries], [None, "KXCATTLE"]
        )
        self.assertTrue(all(q["status"] == ["open"] for q in event_queries))


if __name__ == "__main__":
    unittest.main()
