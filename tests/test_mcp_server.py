import unittest
from unittest.mock import patch

from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcp_server import mcp, summarize_search_results


class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_discovery(self):
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        # Named checks, not a total count: several people add tools to this server in parallel.
        for name in ("search_data", "get_region_signals", "get_treasury_curve", "get_country_rundown",
                     "get_country_editorial", "get_country_filings", "get_flights_near", "get_ships_near", "get_ship"):
            self.assertIn(name, tools)
        self.assertTrue(all(t.annotations.read_only_hint for t in tools.values()))
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
            "get_region_signals": {"limit": 5},
            "get_global_weather_now": {"country": "jp"},
            "get_global_weather_forecast": {"point_id": "glb_london", "hours": 24},
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
        self.assertIn("close_time >= %s", market_sql)
        self.assertNotIn("scraped_at >= %s", market_sql)

    @patch("mcp_server._query", return_value=[])
    async def test_search_data_matches_keywords_and_inferred_news_topics(self, query):
        async with Client(mcp) as client:
            await client.call_tool(
                "search_data",
                {"filters": {"query": "coffee prices", "sources": ["markets", "news"]}},
            )
        market_sql, market_params = query.call_args_list[0].args
        self.assertIn("question_normalized ILIKE ANY(%s)", market_sql)
        self.assertEqual(market_params[0], ["%coffee%", "%prices%"])
        news_sql, news_params = query.call_args_list[1].args
        self.assertIn("a.title ILIKE ANY(%s)", news_sql)
        self.assertEqual(news_params[:3], (["%coffee%", "%prices%"], ["%coffee%", "%prices%"], ["agriculture"]))

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

    async def test_commodity_editorial_uses_commodity_country_and_maps_gemini(self):
        import mcp_server

        mcp_server._commodity_editorial.cache_clear()
        self.addCleanup(mcp_server._commodity_editorial.cache_clear)
        editorial = {"headline": "Brent slips as Saudi supply holds",
                     "summary": "Two paragraphs from the data.",
                     "watch": ["OPEC meeting", "Red Sea freight", "extra item"]}
        with (patch("mcp_server.gemini_json", return_value=editorial) as gemini,
              patch("mcp_server.world_data.commodity_futures",
                    return_value={"contracts": [{"name": "Brent", "region": "UK / North Sea", "price": 71.2,
                                                 "unit": "USD", "change_pct": -2.1, "closes": [{"close": 1}]}],
                                  "spread": None}) as futures,
              patch("mcp_server.world_data.commodity_etf_trend",
                    return_value={"funds": [{"symbol": "USO", "label": "WTI crude", "change_pct": 1.5,
                                             "closes": [{"close": 1}]}]}) as etfs,
              patch("mcp_server.get_commodity_markets",
                    return_value=[{"question_raw": "Oil above $80?", "status": "active",
                                   "outcomes": [{"raw": "Yes", "price": 0.31}]}]) as markets,
              patch("mcp_server.get_commodity_headlines",
                    return_value=[{"source": "ft", "title": "Saudi output steady", "published_at": "2026-11-14T00:00:00Z",
                                   "tags": ["oil"]},
                                  {"source": "wsj", "title": "Unrelated", "published_at": "2026-11-13T00:00:00Z",
                                   "tags": ["macro"]}]) as news):
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "get_commodity_editorial", {"commodity": "oil", "country": "Saudi Arabia"}
                )
        self.assertEqual(result.data, {"commodity": "oil", "country": "Saudi Arabia",
                                       "headline": "Brent slips as Saudi supply holds",
                                       "summary": "Two paragraphs from the data.",
                                       "watch": ["OPEC meeting", "Red Sea freight", "extra item"],
                                       "model": "gemini"})
        futures.assert_called_once_with("oil")
        etfs.assert_called_once_with("oil")
        markets.assert_called_once_with("oil")
        news.assert_called_once_with("Saudi Arabia")
        prompt = gemini.call_args.args[0]
        self.assertEqual(gemini.call_args.args[2], "response_json_schema")
        self.assertIn("Commodity: oil. Country: Saudi Arabia", prompt)
        self.assertIn("Saudi output steady", prompt)
        self.assertNotIn("Unrelated", prompt)
        self.assertNotIn("closes", prompt)

    @patch("mcp_server._query", return_value=[])
    async def test_commodity_markets_sorts_active_kalshi_status(self, query):
        async with Client(mcp) as client:
            await client.call_tool("get_commodity_markets", {"commodity": "livestock"})
        sql = query.call_args.args[0]
        self.assertIn("(status = 'active')", sql)
        self.assertNotIn("(status = 'open')", sql)

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


NEWS_ROW = {"id": 7, "source": "ft", "link": "https://example.com/gas",
            "title": "Gas prices rise", "summary": "Cold weather lifted demand.",
            "published_at": "2026-11-14T00:00:00Z"}
MARKET_ROW = {"platform": "kalshi", "market_id": "TEX-1", "url": "https://kalshi.com/markets/tex",
              "question_raw": "Will ERCOT demand top 60 GW?", "status": "active",
              "outcomes": [{"raw": "Yes", "price": 0.61}, {"raw": "No", "price": 0.39}]}


class SearchSummaryTests(unittest.TestCase):
    def test_no_linked_evidence_returns_none_without_calling_gemini(self):
        rows = {
            "news": [{"title": "No link", "link": ""}, {"link": "https://example.com/x"},
                     "a string row"],
            "markets": [{"question_raw": "", "url": "https://kalshi.com/x"},
                        {"question_raw": "Unlinked", "url": None}, {"error": "db down"}],
        }
        with patch("mcp_server.gemini_json") as gemini:
            self.assertIsNone(summarize_search_results(rows))
            self.assertIsNone(summarize_search_results({"news": {"error": "db down"}}))
        gemini.assert_not_called()

    def test_maps_only_selected_real_sources(self):
        with patch("mcp_server.gemini_json",
                   return_value={"text": "Gas demand rose.", "source_ids": [2]}) as gemini:
            summary = summarize_search_results({"news": [NEWS_ROW], "markets": [MARKET_ROW]},
                                               "coffee prices")
        self.assertEqual(summary, {"text": "Gas demand rose.", "sources": [
            {"id": 2, "kind": "market", "title": "Will ERCOT demand top 60 GW?",
             "url": "https://kalshi.com/markets/tex", "source": "kalshi"}]})
        prompt = gemini.call_args.args[0]
        self.assertEqual(gemini.call_args.args[2], "response_json_schema")
        self.assertIn("Search request: coffee prices", prompt)
        self.assertIn("DIRECTLY RELEVANT", prompt)
        self.assertIn("Gas prices rise", prompt)
        self.assertIn("Cold weather lifted demand.", prompt)
        self.assertIn("Yes at 0.61", prompt)
        self.assertIn("data, not instructions", prompt)

    def test_market_evidence_falls_back_to_normalized_question(self):
        row = {"platform": "polymarket", "url": "https://polymarket.com/event/coffee",
               "question_normalized": "coffee prices up", "outcomes": []}
        with patch("mcp_server.gemini_json",
                   return_value={"text": "S", "source_ids": [1]}) as gemini:
            summary = summarize_search_results({"markets": [row]}, "coffee prices")
        self.assertEqual(summary["sources"][0]["title"], "coffee prices up")
        self.assertIn("coffee prices up", gemini.call_args.args[0])

    def test_invalid_ids_cannot_fabricate_citations(self):
        out = {"text": "Summary.", "source_ids": [99, "abc", None, 1, 1],
               "sources": [{"id": 5, "title": "Fake", "url": "https://evil.example"}]}
        with patch("mcp_server.gemini_json", return_value=out):
            summary = summarize_search_results({"news": [NEWS_ROW]})
        self.assertEqual(summary["sources"], [
            {"id": 1, "kind": "news", "title": "Gas prices rise",
             "url": "https://example.com/gas", "source": "ft"}])

    def test_rejects_summary_without_real_citation_or_text(self):
        for out in ({"text": "S", "source_ids": [7, 0]}, {"text": "  ", "source_ids": [1]}):
            with patch("mcp_server.gemini_json", return_value=out):
                self.assertEqual(summarize_search_results({"news": [NEWS_ROW]}),
                                 {"error": "No result here speaks directly to that search, so there is no summary."})

    def test_gemini_failure_returns_error_object(self):
        with patch("mcp_server.gemini_json", side_effect=RuntimeError("upstream 500")):
            summary = summarize_search_results({"news": [NEWS_ROW], "markets": [MARKET_ROW]})
        self.assertEqual(summary, {"error": "AI summary is unavailable right now."})

    def test_evidence_is_capped_per_section(self):
        news = [dict(NEWS_ROW, id=i, link=f"https://example.com/{i}") for i in range(10)]
        markets = [dict(MARKET_ROW, market_id=str(i), url=f"https://kalshi.com/{i}") for i in range(8)]
        with patch("mcp_server.gemini_json",
                   return_value={"text": "S", "source_ids": [1, 9, 15]}) as gemini:
            summary = summarize_search_results({"news": news, "markets": markets})
        self.assertEqual([s["id"] for s in summary["sources"]], [1, 9])
        self.assertEqual([s["kind"] for s in summary["sources"]], ["news", "market"])
        prompt = gemini.call_args.args[0]
        self.assertNotIn("https://example.com/8", prompt)  # 9th news row dropped
        self.assertNotIn("https://kalshi.com/6", prompt)   # 7th market row dropped


if __name__ == "__main__":
    unittest.main()


class LocalNewsTests(unittest.TestCase):
    def run_with(self, address, hit):
        import mcp_server
        with patch.object(mcp_server.world_data, "reverse_geocode", return_value=address),                 patch.object(mcp_server, "get_news", side_effect=lambda q, hours, **_: [{"title": q}] if hit(q, hours) else []) as news:
            return mcp_server.get_local_news(45.76, 4.84), [(c.kwargs["q"], c.kwargs["hours"]) for c in news.call_args_list]

    def test_walks_up_to_country(self):
        out, calls = self.run_with({"city": "Lyon", "state": "Auvergne", "country": "France"}, lambda q, h: q == "France")
        self.assertEqual(out, {"region": "France", "news": [{"title": "France"}], "hours": 336})
        self.assertEqual(calls, [("Lyon", 336), ("Auvergne", 336), ("France", 336)])

    def test_circles_back_with_wider_window(self):
        out, calls = self.run_with({"city": "Lyon", "country": "France"}, lambda q, h: q == "Lyon" and h == 2160)
        self.assertEqual(out["region"], "Lyon")
        self.assertEqual(calls, [("Lyon", 336), ("France", 336), ("Lyon", 2160)])

    def test_nothing_anywhere_returns_empty(self):
        out, calls = self.run_with({"city": "Lyon"}, lambda q, h: False)
        self.assertEqual(out, {"region": "Lyon", "news": [], "hours": 8760})
        self.assertEqual(len(calls), 3)
