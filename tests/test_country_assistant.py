import asyncio
import os
import unittest
from unittest.mock import patch

import httpx

import world_data
from api import assistant
from api.assistant import Message

CURVE = {"as_of": "2026-11-13", "curve": [{"tenor": "10 Yr", "years": 10, "yield": 5.15}]}


class FakeGemini:
    """Scripted stand-in for httpx.AsyncClient: one tool call, then a text answer."""

    def __init__(self, *a, **kw):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.requests.append(json)
        if len(self.requests) == 1:
            parts = [{"functionCall": {"name": "get_treasury_curve", "args": {}}, "thoughtSignature": "sig"}]
        else:
            tool_reply = json["contents"][-1]["parts"][0]["functionResponse"]["response"]["result"]
            assert "5.15" in tool_reply, tool_reply
            assert json["contents"][-2]["parts"][0]["thoughtSignature"] == "sig"  # model turn sent back verbatim
            parts = [{"text": "The 10-year is at 5.15%."}]
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": parts}}]})


class AssistantLoopTests(unittest.TestCase):
    def test_runs_mcp_tool_then_answers(self):
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "k"}),
            patch.object(assistant.httpx, "AsyncClient", FakeGemini),
            patch.object(world_data, "treasury_curve", return_value=CURVE),
        ):
            out = asyncio.run(assistant.answer([Message(role="user", text="Where is the 10-year?")]))
        self.assertEqual(out, {"reply": "The 10-year is at 5.15%.", "tools_used": ["get_treasury_curve"]})

    def test_rate_limit(self):
        assistant._calls.clear()
        for _ in range(assistant.PER_MINUTE):
            assistant._rate_limit("u1")
        with self.assertRaises(Exception) as ctx:
            assistant._rate_limit("u1")
        self.assertEqual(ctx.exception.status_code, 429)
        assistant._rate_limit("u2")  # other users are unaffected


class TtlCacheTests(unittest.TestCase):
    def test_caches_per_argument(self):
        calls = []

        @world_data.ttl_cache(60)
        def f(x):
            calls.append(x)
            return x * 2

        self.assertEqual([f(1), f(1), f(2)], [2, 2, 4])
        self.assertEqual(calls, [1, 2])


class EdgarTests(unittest.TestCase):
    def test_unsupported_country_skips_the_request(self):
        with patch.object(world_data.httpx, "get") as get:
            out = world_data.edgar_filings("Atlantis")
        get.assert_not_called()
        self.assertFalse(out["supported"])

    def test_one_row_per_company_day_form_and_links(self):
        def hit(adsh, name, doc):
            return {"_id": f"{adsh}:{doc}", "_source": {"ciks": ["0000001"], "display_names": [f"{name}  (X)"],
                                                         "file_date": "2026-11-13", "form": "6-K"}}
        payload = {"hits": {"total": {"value": 3}, "hits": [hit("0001-26-1", "Mizuho", "a.htm"),
                                                            hit("0001-26-2", "Mizuho", "b.htm"),
                                                            hit("0001-26-3", "Sony", "c.htm")]}}
        world_data.edgar_filings.cache_clear()
        with patch.object(world_data.httpx, "get", return_value=httpx.Response(200, json=payload, request=httpx.Request("GET", "https://x"))):
            out = world_data.edgar_filings("Japan")
        self.assertEqual([f["company"] for f in out["filings"]], ["Mizuho", "Sony"])
        self.assertEqual(out["filings"][0]["url"], "https://www.sec.gov/Archives/edgar/data/1/0001261/a.htm")


if __name__ == "__main__":
    unittest.main()
