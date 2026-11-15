"""Offline tests: fake in-memory store + mocked webhook (no Tiger, no Grok Bot).

    python -m pytest tests -q
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api import ask, assistant_store, grok_bot

SECRET = "s3cret"
AUTH = {"Authorization": f"Bearer {SECRET}"}


class FakeStore:
    def __init__(self):
        self.rows: dict = {}

    def create_request(self, request_id, question, client_ip):
        self.rows[request_id] = {"request_id": request_id, "question": question, "status": "pending",
                                 "answer": None, "error": None, "client_ip": client_ip,
                                 "created_at": datetime.now(timezone.utc), "answered_at": None}

    def mark_failed(self, request_id, error):
        row = self.rows.get(request_id)
        if row is None or row["status"] != "pending":
            return False
        row.update(status="failed", error=error)
        return True

    def get_request(self, request_id):
        row = self.rows.get(request_id)
        if row is None:
            return None
        age = datetime.now(timezone.utc) - row["created_at"]
        return {**row, "timed_out": row["status"] == "pending" and age > assistant_store.PENDING_TIMEOUT}

    def save_answer(self, request_id, answer):
        row = next((r for r in self.rows.values() if str(r["request_id"]) == request_id), None)
        if row is None or row["status"] != "pending":
            return assistant_store.UNKNOWN
        row.update(status="done", answer=answer, answered_at=datetime.now(timezone.utc))
        return assistant_store.SAVED


@pytest.fixture
def fake(monkeypatch):
    store = FakeStore()
    for name in ("create_request", "mark_failed", "get_request", "save_answer"):
        monkeypatch.setattr(assistant_store, name, getattr(store, name))
    monkeypatch.setattr(ask, "limiter", ask.RateLimiter())
    monkeypatch.setattr(ask, "sockets", {})
    monkeypatch.setenv("NOSTOS_ANSWER_SECRET", SECRET)
    store.sent = []

    async def ok_send(request_id, question):
        store.sent.append((request_id, question))
    monkeypatch.setattr(grok_bot, "send_question", ok_send)
    return store


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ask.router)
    return TestClient(app)


def new_ask(client, question="q"):
    return client.post("/api/ask", json={"question": question}).json()["request_id"]


# ---- POST /api/ask -----------------------------------------------------------

def test_ask_returns_202_and_pending_row(fake, client):
    r = client.post("/api/ask", json={"question": "  peak tomorrow?  "})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending"
    (row,) = fake.rows.values()
    assert str(row["request_id"]) == body["request_id"]
    assert row["status"] == "pending" and row["question"] == "peak tomorrow?"
    assert fake.sent == [(body["request_id"], "peak tomorrow?")]


def test_webhook_failure_gives_502_and_failed_row(fake, client, monkeypatch):
    async def bad_send(request_id, question):
        raise grok_bot.GrokBotError("webhook returned HTTP 401: bad key")
    monkeypatch.setattr(grok_bot, "send_question", bad_send)

    r = client.post("/api/ask", json={"question": "test"})
    assert r.status_code == 502
    assert r.json()["status"] == "failed"
    assert "401" not in r.json()["error"]                 # details stay server-side
    (row,) = fake.rows.values()
    assert row["status"] == "failed" and "401" in row["error"]
    polled = client.get(f"/api/ask/{r.json()['request_id']}").json()
    assert polled["status"] == "failed" and polled["error"] == ask.PUBLIC_ERROR


def test_sixth_ask_in_a_minute_gets_429(fake, client):
    codes = [client.post("/api/ask", json={"question": f"q{i}"}).status_code for i in range(6)]
    assert codes == [202] * 5 + [429]
    assert len(fake.rows) == 5


@pytest.mark.parametrize("question", ["", "   ", "x" * 1001])
def test_question_validation(fake, client, question):
    assert client.post("/api/ask", json={"question": question}).status_code == 422
    assert not fake.rows


# ---- GET /api/ask/{id} -------------------------------------------------------

def test_poll_pending_then_done(fake, client):
    rid = new_ask(client)
    assert client.get(f"/api/ask/{rid}").json()["status"] == "pending"
    assert client.post("/api/ask/answer", json={"request_id": rid, "answer": "42 GW"},
                       headers=AUTH).status_code == 200
    body = client.get(f"/api/ask/{rid}").json()
    assert body["status"] == "done" and body["answer"] == "42 GW" and body["answered_at"]


def test_poll_timed_out(fake, client, monkeypatch):
    rid = new_ask(client)
    monkeypatch.setattr(assistant_store, "PENDING_TIMEOUT", timedelta(0))
    body = client.get(f"/api/ask/{rid}").json()
    assert body["status"] == "failed" and body["error"] == "timed out"


def test_poll_unknown_is_404(fake, client):
    assert client.get("/api/ask/00000000-0000-4000-8000-000000000000").status_code == 404


# ---- POST /api/ask/answer (the routine's reply) --------------------------------

def test_reply_answer_then_duplicate_is_409(fake, client):
    rid = new_ask(client)
    r = client.post("/api/ask/answer", json={"request_id": rid, "answer": "a"}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"request_id": rid, "result": "saved"}
    r = client.post("/api/ask/answer", json={"request_id": rid, "answer": "b"}, headers=AUTH)
    assert r.status_code == 409 and r.json()["detail"] == "unknown or already answered"


def test_reply_error_marks_failed(fake, client):
    rid = new_ask(client)
    r = client.post("/api/ask/answer", json={"request_id": rid, "error": "no data"}, headers=AUTH)
    assert r.status_code == 200
    (row,) = fake.rows.values()
    assert row["status"] == "failed" and row["error"] == "routine: no data"
    assert client.get(f"/api/ask/{rid}").json()["status"] == "failed"


def test_reply_unknown_id_is_409(fake, client):
    r = client.post("/api/ask/answer", headers=AUTH,
                    json={"request_id": "00000000-0000-4000-8000-000000000000", "answer": "a"})
    assert r.status_code == 409


@pytest.mark.parametrize("headers, code", [
    ({}, 401),
    ({"Authorization": "Bearer nope"}, 401),
    ({"Authorization": SECRET}, 401),                   # missing "Bearer "
    ({"X-Nostos-Secret": SECRET}, 401),
    ({"Authorization": f"bearer {SECRET}"}, 200),       # scheme is case-insensitive
])
def test_reply_auth(fake, client, headers, code):
    rid = new_ask(client)
    assert client.post("/api/ask/answer", json={"request_id": rid, "answer": "a"},
                       headers=headers).status_code == code


@pytest.mark.parametrize("payload", [
    {"answer": "a"},                                     # no request_id
    {"request_id": "not-a-uuid", "answer": "a"},
    {"request_id": "00000000-0000-4000-8000-000000000000"},              # neither field
    {"request_id": "00000000-0000-4000-8000-000000000000", "answer": "  "},
    {"request_id": "00000000-0000-4000-8000-000000000000", "answer": "x" * 8001},
])
def test_reply_validation(fake, client, payload):
    assert client.post("/api/ask/answer", json=payload, headers=AUTH).status_code == 422


def test_reply_disabled_without_secret(fake, client, monkeypatch):
    rid = new_ask(client)
    monkeypatch.delenv("NOSTOS_ANSWER_SECRET")
    assert client.post("/api/ask/answer", json={"request_id": rid, "answer": "a"},
                       headers=AUTH).status_code == 404


# ---- WS /ws/ask/{id} -----------------------------------------------------------

def test_socket_gets_answer_pushed(fake, client):
    rid = new_ask(client)
    with client.websocket_connect(f"/ws/ask/{rid}") as ws:
        client.post("/api/ask/answer", json={"request_id": rid, "answer": "52 GW"}, headers=AUTH)
        msg = ws.receive_json()
        assert msg["status"] == "done" and msg["answer"] == "52 GW" and msg["request_id"] == rid
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()                            # server closed it
    assert ask.sockets == {}


def test_socket_after_answer_gets_it_immediately(fake, client):
    rid = new_ask(client)
    client.post("/api/ask/answer", json={"request_id": rid, "answer": "early"}, headers=AUTH)
    with client.websocket_connect(f"/ws/ask/{rid}") as ws:
        assert ws.receive_json()["answer"] == "early"


def test_socket_gets_routine_error_as_failed(fake, client):
    rid = new_ask(client)
    with client.websocket_connect(f"/ws/ask/{rid}") as ws:
        client.post("/api/ask/answer", json={"request_id": rid, "error": "boom"}, headers=AUTH)
        msg = ws.receive_json()
        assert msg["status"] == "failed" and msg["error"] == ask.PUBLIC_ERROR


def test_socket_times_out(fake, client, monkeypatch):
    rid = new_ask(client)
    monkeypatch.setattr(assistant_store, "PENDING_TIMEOUT", timedelta(seconds=0.2))
    with client.websocket_connect(f"/ws/ask/{rid}") as ws:
        msg = ws.receive_json()
        assert msg["status"] == "failed" and msg["error"] == "timed out"
    assert ask.sockets == {}


def test_socket_unknown_id_closes_4404(fake, client):
    with client.websocket_connect("/ws/ask/00000000-0000-4000-8000-000000000000") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4404


def test_socket_browser_leaves_early(fake, client):
    rid = new_ask(client)
    with client.websocket_connect(f"/ws/ask/{rid}"):
        pass                                             # browser closes before the answer
    r = client.post("/api/ask/answer", json={"request_id": rid, "answer": "a"}, headers=AUTH)
    assert r.status_code == 200                          # still saved, GET has it
    assert client.get(f"/api/ask/{rid}").json()["answer"] == "a"


# ---- grok_bot webhook client -------------------------------------------------

def _send_with(handler, monkeypatch, auth="bearer", key="k3y"):
    monkeypatch.setenv("GROK_BOT_WEBHOOK_URL", "https://grok.example/hook")
    monkeypatch.setenv("GROK_BOT_WEBHOOK_KEY", key)
    monkeypatch.setenv("GROK_BOT_AUTH", auth)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await grok_bot.send_question("rid-1", "héllo?", client=c)
    return asyncio.run(go())


def test_bearer_post(monkeypatch):
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(202)
    assert _send_with(handler, monkeypatch).status_code == 202
    assert seen == {"auth": "Bearer k3y", "body": {"request_id": "rid-1", "question": "héllo?"}}


def test_blank_auth_mode_defaults_to_bearer(monkeypatch):
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(202)
    _send_with(handler, monkeypatch, auth="")            # GROK_BOT_AUTH= (blank, as in .env.example)
    assert seen["auth"] == "Bearer k3y"


def test_signed_post_signs_exact_body(monkeypatch):
    seen = {}

    def handler(req):
        seen.update(req.headers, raw=req.content)
        return httpx.Response(200)
    _send_with(handler, monkeypatch, auth="signed")
    expected = grok_bot.signed_headers("k3y", seen["raw"], msg_id=seen["webhook-id"],
                                       timestamp=int(seen["webhook-timestamp"]))
    assert seen["webhook-signature"] == expected["webhook-signature"]
    assert "authorization" not in seen


def test_signature_matches_standard_webhooks_vector():
    # Reference vector from the Standard Webhooks / Svix docs.
    h = grok_bot.signed_headers("whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw", b'{"test": 2432232314}',
                                msg_id="msg_p5jXN8AQM9LWM0D4loKWxJek", timestamp=1614265330)
    assert h["webhook-signature"] == "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="


@pytest.mark.parametrize("handler", [
    lambda req: httpx.Response(401, text="bad key"),
    lambda req: (_ for _ in ()).throw(httpx.ConnectError("no route")),
])
def test_webhook_errors_raise_grokboterror(monkeypatch, handler):
    with pytest.raises(grok_bot.GrokBotError):
        _send_with(handler, monkeypatch)


def test_missing_config_raises(monkeypatch):
    monkeypatch.delenv("GROK_BOT_WEBHOOK_URL", raising=False)
    with pytest.raises(grok_bot.GrokBotError, match="not set"):
        asyncio.run(grok_bot.send_question("rid", "q"))
