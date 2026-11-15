"""Website assistant endpoints.

    POST /api/ask                {"question"} -> 202 {"request_id", "status": "pending"}
    GET  /api/ask/{request_id}   poll until status is 'done' or 'failed'
    WS   /ws/ask/{request_id}    or wait here: one JSON message (same shape as GET), then close
    POST /api/ask/answer         the Grok Bot routine replies here:
                                 {"request_id", "answer"} or {"request_id", "error"}
                                 with  Authorization: Bearer <NOSTOS_ANSWER_SECRET>

Include in any FastAPI app with:  app.include_router(api.ask.router)
Open sockets live in this process's memory, so run ONE uvicorn worker (see main.py).
"""
import asyncio
import hmac
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints, model_validator

from api import assistant_store as store
from api import grok_bot

log = logging.getLogger(__name__)
router = APIRouter(tags=["assistant"])

# Shown to visitors. The real cause stays in assistant_requests.error for debugging.
PUBLIC_ERROR = "The assistant is unavailable right now. Please try again."


class AskBody(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class BotReply(BaseModel):
    request_id: uuid.UUID
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                             max_length=store.MAX_ANSWER_CHARS)] | None = None
    error: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                            max_length=2000)] | None = None

    @model_validator(mode="after")
    def answer_or_error(self):
        if self.answer is None and self.error is None:
            raise ValueError("send 'answer' (or 'error' when you can't answer)")
        return self


class RateLimiter:
    """Sliding window, in memory. Per process: with N uvicorn workers each keeps its own count."""

    def __init__(self, limit: int = 5, window_seconds: float = 60.0):
        self.limit, self.window = limit, window_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


limiter = RateLimiter()

# request_id -> the browser socket waiting for that answer (single process only).
sockets: dict[str, WebSocket] = {}


def client_ip(request: Request) -> str:
    # Behind a reverse proxy or tunnel, run uvicorn with --proxy-headers so this is the visitor's IP.
    return request.client.host if request.client else "unknown"


def status_payload(row: dict) -> dict:
    status, error = row["status"], None
    if row["timed_out"]:
        status, error = "failed", "timed out"
    elif status == "failed":
        error = PUBLIC_ERROR
    return {
        "request_id": str(row["request_id"]),
        "status": status,
        "answer": row["answer"],
        "error": error,
        "created_at": row["created_at"],
        "answered_at": row["answered_at"],
    }


# ---- ask + poll ----------------------------------------------------------------

@router.post("/api/ask", status_code=202)
async def ask(body: AskBody, request: Request):
    ip = client_ip(request)
    if not limiter.allow(ip):
        raise HTTPException(429, "Too many questions. Wait a minute and try again.",
                            headers={"Retry-After": "60"})

    request_id = uuid.uuid4()
    # Insert first: the routine may reply before the webhook call even returns.
    await asyncio.to_thread(store.create_request, request_id, body.question, ip)

    try:
        await grok_bot.send_question(str(request_id), body.question)
    except grok_bot.GrokBotError as e:
        log.warning("Grok Bot webhook failed for %s: %s", request_id, e)
        await asyncio.to_thread(store.mark_failed, request_id, str(e)[:500])
        return JSONResponse(status_code=502, content={
            "request_id": str(request_id), "status": "failed", "error": PUBLIC_ERROR,
        })

    return {"request_id": str(request_id), "status": "pending"}


# Declared before /api/ask/{request_id} so "answer" is never parsed as an id.
@router.post("/api/ask/answer")
async def bot_reply(body: BotReply, authorization: Annotated[str | None, Header()] = None):
    """The Grok Bot routine POSTs its answer (or why it couldn't answer) here."""
    secret = os.getenv("NOSTOS_ANSWER_SECRET", "").strip()
    if not secret:
        raise HTTPException(404, "Not enabled")      # off unless a secret is configured
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), secret.encode()):
        raise HTTPException(401, "Bad or missing bearer token")

    if body.answer is not None:                      # if both are sent, the answer wins
        saved = await asyncio.to_thread(store.save_answer, str(body.request_id), body.answer)
        ok = saved == store.SAVED
    else:
        ok = await asyncio.to_thread(store.mark_failed, body.request_id, f"routine: {body.error}")
    if not ok:
        raise HTTPException(409, store.UNKNOWN)

    await push_to_socket(body.request_id)
    return {"request_id": str(body.request_id), "result": "saved"}


@router.get("/api/ask/{request_id}")
async def ask_status(request_id: uuid.UUID):
    row = await asyncio.to_thread(store.get_request, request_id)
    if row is None:
        raise HTTPException(404, "Unknown request_id")
    return status_payload(row)


# ---- WebSocket -----------------------------------------------------------------

def _take(key: str, ws: WebSocket) -> bool:
    """Unregister ws if it is still the socket for key. Whoever takes it sends the result."""
    if sockets.get(key) is ws:
        del sockets[key]
        return True
    return False


async def _send_and_close(ws: WebSocket, row: dict | None) -> None:
    try:
        if row is None:
            await ws.close(code=4404, reason="Unknown request_id")
        else:
            await ws.send_json(jsonable_encoder(status_payload(row)))
            await ws.close()
    except Exception as e:                           # the browser may already be gone
        log.info("socket send failed: %s", e)


async def push_to_socket(request_id: uuid.UUID) -> None:
    """Send the finished row to the socket waiting on it, if any. The DB copy stays for GET."""
    ws = sockets.pop(str(request_id), None)
    if ws is not None:
        await _send_and_close(ws, await asyncio.to_thread(store.get_request, request_id))


async def _until_closed(ws: WebSocket) -> None:
    while (await ws.receive())["type"] != "websocket.disconnect":
        pass


@router.websocket("/ws/ask/{request_id}")
async def ask_socket(websocket: WebSocket, request_id: uuid.UUID):
    await websocket.accept()
    key = str(request_id)
    sockets[key] = websocket          # register before reading the DB so a reply arriving meanwhile isn't missed
    try:
        row = await asyncio.to_thread(store.get_request, request_id)
        finished = row is None or row["status"] != "pending" or row["timed_out"]
        if finished and _take(key, websocket):
            await _send_and_close(websocket, row)
            return
        await asyncio.wait_for(_until_closed(websocket), store.PENDING_TIMEOUT.total_seconds())
    except asyncio.TimeoutError:
        if _take(key, websocket):
            await _send_and_close(websocket, await asyncio.to_thread(store.get_request, request_id))
    finally:
        _take(key, websocket)
