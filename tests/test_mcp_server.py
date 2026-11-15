import unittest
from unittest.mock import patch

from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcp_server import mcp


class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_discovery(self):
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        self.assertEqual(len(tools), 16)
        self.assertTrue(tools["get_latest_forecast"].annotations.read_only_hint)
        self.assertIn("start", tools["get_grid_hourly"].input_schema["required"])

    @patch("mcp_server._query", return_value=[])
    async def test_all_tools_accept_documented_arguments(self, query):
        window = {
            "ba_code": "ERCO",
            "start": "2026-11-12T00:00:00Z",
            "end": "2026-11-13T00:00:00Z",
        }
        calls = {
            "get_latest_forecast": {},
            "get_scoreboard_summary": {},
            "get_scoreboard_daily": {},
            "get_scoreboard_by_lead": {},
            "get_backtest_monthly": {},
            "get_backtest_by_hour": {},
            "get_grid_hourly": {**window, "series": "D"},
            "get_fuel_mix": window,
            "get_weather_forecast": {**window, "model": "gfs_seamless"},
            "get_weather_actuals": {**window, "source": "era5"},
            "search_prediction_markets": {"q": "100%_temperature"},
            "get_news": {"q": "natural gas"},
            "get_data_integrity": {},
        }
        async with Client(mcp) as client:
            for name, arguments in calls.items():
                with self.subTest(tool=name):
                    result = await client.call_tool(name, arguments)
                    self.assertIsNotNone(result.data)
        self.assertEqual(query.call_count, len(calls))

    @patch("mcp_server._query", return_value=[])
    async def test_search_data_combines_structured_filters(self, query):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "search_data",
                {"filters": {
                    "region": "ERCO",
                    "instrument": "kalshi:TEX-HEAT",
                    "query": "temperature",
                    "sources": ["grid", "markets", "news"],
                    "series": ["D", "DF"],
                    "start": "2026-11-12T00:00:00Z",
                    "end": "2026-11-13T00:00:00Z",
                    "market_platform": "kalshi",
                    "news_source": "wsj",
                }},
            )
        self.assertEqual(set(result.data["results"]), {"grid", "markets", "news"})
        grid_sql, grid_params = query.call_args_list[0].args
        self.assertIn("series = ANY(%s)", grid_sql)
        self.assertEqual(grid_params[3], ["D", "DF"])
        market_sql, market_params = query.call_args_list[1].args
        self.assertIn("platform || ':' || market_id", market_sql)
        self.assertIn("%kalshi:TEX-HEAT%", market_params)

    @patch("mcp_server._query", return_value=[])
    async def test_instrument_search_defaults_to_markets_and_news(self, query):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "search_data", {"filters": {"instrument": "natural gas"}}
            )
        self.assertEqual(set(result.data["results"]), {"markets", "news"})

    async def test_semantic_search_validates_interpreted_filters(self):
        interpreted = {
            "region": "ERCO",
            "sources": ["grid"],
            "series": ["D"],
            "start": "2026-11-12T00:00:00Z",
            "end": "2026-11-13T00:00:00Z",
        }
        with (patch("mcp_server.gemini_json", return_value=interpreted) as gemini,
              patch("mcp_server._query", return_value=[])):
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "semantic_search", {"request": "ERCOT demand yesterday", "limit": 5}
                )
        self.assertEqual(result.data["interpretation"], "gemini")
        self.assertEqual(result.data["filters"]["region"], "ERCO")
        self.assertEqual(result.data["filters"]["limit"], 5)
        self.assertEqual(set(result.data["results"]), {"grid"})
        self.assertEqual(gemini.call_args.args[2], "response_json_schema")

    async def test_semantic_search_has_transparent_keyword_fallback(self):
        with (patch("mcp_server.gemini_json", side_effect=RuntimeError("no key")),
              patch("mcp_server._query", return_value=[])):
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "semantic_search", {"request": "natural gas news"}
                )
        self.assertEqual(result.data["interpretation"], "keyword_fallback")
        self.assertEqual(result.data["filters"]["query"], "natural gas news")
        self.assertEqual(set(result.data["results"]), {"markets", "news"})

    @patch("mcp_server._query")
    async def test_region_context_bundles_sources_and_selects_weather_model(self, query):
        def rows(sql, params=()):
            if "SELECT f.model FROM weather_forecasts" in sql:
                return [{"model": "gfs_seamless"}]
            if "normalized_prediction_markets" in sql:
                return [{"market_id": "weather-2026", "outcomes": [{"price": 0.65}]}]
            return []

        query.side_effect = rows
        async with Client(mcp) as client:
            result = await client.call_tool(
                "get_region_context", {"region": "ERCO", "query": "Texas"}
            )
        self.assertEqual(
            set(result.data),
            {"region", "query", "generated_at", "lookback_hours", "forecast",
             "model_accuracy", "grid", "fuel_mix", "weather", "market_prices", "news"},
        )
        self.assertEqual(result.data["weather"]["model"], "gfs_seamless")
        self.assertEqual(result.data["market_prices"][0]["outcomes"][0]["price"], 0.65)

    @patch("mcp_server._query", return_value=[])
    async def test_region_context_isolates_source_failures(self, query):
        def fail_markets(sql, params=()):
            if "normalized_prediction_markets" in sql:
                raise RuntimeError("market source unavailable")
            return []

        query.side_effect = fail_markets
        async with Client(mcp) as client:
            result = await client.call_tool("get_region_context", {})
        self.assertIn("error", result.data["market_prices"])
        self.assertEqual(result.data["grid"], [])
        self.assertEqual(result.data["weather"], {"model": None, "hours": []})

    @patch("mcp_server._query", return_value=[{"demand_mwh": 123.0}])
    async def test_latest_forecast_returns_rows_and_parameters(self, query):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "get_latest_forecast", {"ba_code": "ERCO", "model_version": "v1"}
            )
        self.assertEqual(result.data, [{"demand_mwh": 123.0}])
        sql, params = query.call_args.args
        self.assertIn("latest_forecast", sql)
        self.assertEqual(params, ("ERCO", "v1", 1000))

    @patch("mcp_server._query", return_value=[])
    async def test_as_of_is_parameterized(self, query):
        async with Client(mcp) as client:
            await client.call_tool(
                "get_grid_hourly",
                {
                    "ba_code": "ERCO",
                    "series": "D",
                    "start": "2026-11-12T00:00:00Z",
                    "end": "2026-11-13T00:00:00Z",
                    "as_of": "2026-11-12T12:00:00Z",
                },
            )
        sql, params = query.call_args.args
        self.assertIn("available_at <= coalesce(%s, now())", sql)
        self.assertEqual(params[0:2], ("ERCO", "D"))
        self.assertEqual(params[4].isoformat(), "2026-11-12T12:00:00+00:00")

    async def test_rejects_naive_time_range(self):
        async with Client(mcp) as client:
            with self.assertRaises(ToolError):
                await client.call_tool(
                    "get_grid_hourly",
                    {
                        "ba_code": "ERCO",
                        "series": "D",
                        "start": "2026-11-12T00:00:00",
                        "end": "2026-11-13T00:00:00Z",
                    },
                )


if __name__ == "__main__":
    unittest.main()
