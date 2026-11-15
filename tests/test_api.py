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

    def test_query_bounds_are_enforced(self):
        self.verifier.verify_token.return_value = _token(sub="u1")
        response = self.client.get("/api/regions/ERCO?hours=999", headers={"Authorization": "Bearer t"})
        self.assertEqual(response.status_code, 422)

    def test_unconfigured_auth_fails_closed(self):
        with patch.object(api, "_verifier", None):
            self.assertEqual(self.get().status_code, 503)


if __name__ == "__main__":
    unittest.main()
