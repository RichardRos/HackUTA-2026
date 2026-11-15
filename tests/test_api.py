import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api import main as api
from fastmcp.server.auth.auth import AccessToken


def _token(**claims):
    return AccessToken(token="t", client_id="c", scopes=[], claims=claims)


class APIAuthTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)
        self.verifier = AsyncMock()
        patcher = patch.object(api, "_verifier", self.verifier)
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, token="good"):
        return self.client.get("/api/markets?q=rain", headers={"Authorization": f"Bearer {token}"})

    def test_missing_token_is_rejected(self):
        self.assertEqual(self.client.get("/api/markets").status_code, 401)
        self.verifier.verify_token.assert_not_called()

    def test_invalid_token_is_rejected(self):
        self.verifier.verify_token.return_value = None
        self.assertEqual(self.get().status_code, 401)

    def test_token_from_other_origin_is_rejected(self):
        self.verifier.verify_token.return_value = _token(sub="u1", azp="https://evil.example")
        self.assertEqual(self.get().status_code, 401)

    def test_valid_token_reaches_tool(self):
        self.verifier.verify_token.return_value = _token(sub="u1", azp="http://localhost:5173")
        with patch.object(api.tools, "search_prediction_markets", return_value=[{"id": 1}]) as tool:
            response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{"id": 1}])
        tool.assert_called_once_with(q="rain", limit=50)

    def test_text_search_defaults_to_sources_the_page_renders(self):
        self.verifier.verify_token.return_value = _token(sub="u1")
        with patch.object(api.tools, "semantic_search", return_value={"results": {}}) as tool:
            response = self.client.get(
                "/api/search?q=Argentina%20rainfall", headers={"Authorization": "Bearer t"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tool.call_args.args[2], {"sources": ["markets", "news"]})

    def test_text_search_includes_summary_contract(self):
        self.verifier.verify_token.return_value = _token(sub="u1")
        payload = {"interpretation": "gemini", "filters": {"query": "gas"},
                   "results": {"news": [{"title": "Gas prices rise"}], "markets": []}}
        summary = {"text": "Gas demand rose.", "sources": [
            {"id": 1, "kind": "news", "title": "Gas prices rise",
             "url": "https://example.com/gas", "source": "ft"}]}
        with (patch.object(api.tools, "semantic_search", return_value=payload),
              patch.object(api.tools, "summarize_search_results", return_value=summary) as helper):
            response = self.client.get(
                "/api/search?q=gas", headers={"Authorization": "Bearer t"}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"], summary)
        self.assertEqual(body["interpretation"], "gemini")
        self.assertEqual(body["results"]["news"], payload["results"]["news"])
        helper.assert_called_once_with(payload["results"], "gas")

    def test_filter_only_search_includes_summary_contract(self):
        self.verifier.verify_token.return_value = _token(sub="u1")
        payload = {"filters": {"news_tag": "oil"}, "results": {"news": []}}
        with (patch.object(api.tools, "search_data", return_value=payload),
              patch.object(api.tools, "summarize_search_results", return_value=None) as helper):
            response = self.client.get(
                "/api/search?tag=oil", headers={"Authorization": "Bearer t"}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["interpretation"], "filters")
        self.assertIsNone(body["summary"])
        helper.assert_called_once_with(payload["results"], "oil")

    def test_filter_only_search_without_text_passes_no_request(self):
        self.verifier.verify_token.return_value = _token(sub="u1")
        payload = {"filters": {"market_platform": "kalshi"}, "results": {"markets": []}}
        with (patch.object(api.tools, "search_data", return_value=payload),
              patch.object(api.tools, "summarize_search_results", return_value=None) as helper):
            response = self.client.get(
                "/api/search?market_platform=kalshi", headers={"Authorization": "Bearer t"}
            )
        self.assertEqual(response.status_code, 200)
        helper.assert_called_once_with(payload["results"], None)

    def test_summary_error_does_not_fail_search(self):
        self.verifier.verify_token.return_value = _token(sub="u1")
        payload = {"interpretation": "gemini", "filters": {}, "results": {"news": []}}
        error = {"error": "AI summary is unavailable right now."}
        with (patch.object(api.tools, "semantic_search", return_value=payload),
              patch.object(api.tools, "summarize_search_results", return_value=error)):
            response = self.client.get(
                "/api/search?q=gas", headers={"Authorization": "Bearer t"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"], error)
        self.assertEqual(response.json()["interpretation"], "gemini")

    def test_query_bounds_are_enforced(self):
        self.verifier.verify_token.return_value = _token(sub="u1")
        response = self.client.get("/api/regions/ERCO?hours=999", headers={"Authorization": "Bearer t"})
        self.assertEqual(response.status_code, 422)

    def test_signals_are_cached(self):
        self.verifier.verify_token.return_value = _token(sub="u1")
        api._signals_cache.clear()
        with patch.object(api.tools, "get_region_signals", return_value=[{"ba_code": "ERCO"}]) as tool:
            for _ in range(2):
                response = self.client.get("/api/signals", headers={"Authorization": "Bearer t"})
                self.assertEqual(response.json(), [{"ba_code": "ERCO"}])
        tool.assert_called_once_with(12)

    def test_commodity_editorial_route_passes_commodity_and_country(self):
        self.verifier.verify_token.return_value = _token(sub="u1")
        expected = {"commodity": "oil", "country": "Saudi Arabia", "headline": "h", "summary": "s",
                    "watch": [], "model": "gemini"}
        with patch.object(api.tools, "get_commodity_editorial", return_value=expected) as tool:
            response = self.client.get(
                "/api/commodities/oil/editorial?country=Saudi%20Arabia",
                headers={"Authorization": "Bearer t"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        tool.assert_called_once_with("oil", "Saudi Arabia")

    def test_commodity_editorial_requires_country(self):
        self.verifier.verify_token.return_value = _token(sub="u1")
        response = self.client.get(
            "/api/commodities/oil/editorial", headers={"Authorization": "Bearer t"}
        )
        self.assertEqual(response.status_code, 422)

    def test_unconfigured_auth_fails_closed(self):
        with patch.object(api, "_verifier", None):
            self.assertEqual(self.get().status_code, 503)


if __name__ == "__main__":
    unittest.main()
