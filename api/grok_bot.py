"""POST a visitor's question to the Grok Bot routine's webhook.

The webhook only *accepts* the job (200/202). The routine answers later by POSTing
{"request_id", "answer"} back to POST /api/ask/answer (see api/ask.py).

Env:
    GROK_BOT_WEBHOOK_URL   from the routine's Webhook trigger
    GROK_BOT_WEBHOOK_KEY   from the same screen (server-side only, never the frontend)
    GROK_BOT_AUTH          'bearer' (default) or 'signed'

End-to-end test (the API and its public tunnel must be running so the reply can land):
    python -m api.grok_bot "What is tomorrow's ERCOT peak?"
It inserts a pending row, sends the webhook, then waits up to 5 minutes for the answer.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import httpx

TIMEOUT_SECONDS = 15


class GrokBotError(Exception):
    """The webhook is not configured, unreachable, or did not accept the job."""


def bearer_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def signed_headers(key: str, body: bytes, *, msg_id: str | None = None,
                   timestamp: int | None = None) -> dict[str, str]:
    """Standard Webhooks signature: v1,base64(HMAC-SHA256(key, "{id}.{ts}.{body}")).

    `body` must be the exact bytes that get sent. A key in the Standard Webhooks
    form "whsec_<base64>" is base64-decoded first; any other key is used as UTF-8.
    """
    msg_id = msg_id or str(uuid.uuid4())
    ts = str(int(time.time()) if timestamp is None else timestamp)
    secret = base64.b64decode(key[len("whsec_"):]) if key.startswith("whsec_") else key.encode()
    mac = hmac.new(secret, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": ts,
        "webhook-signature": "v1," + base64.b64encode(mac).decode(),
    }


def auth_mode() -> str:
    """GROK_BOT_AUTH, defaulting to 'bearer' when unset *or blank* (as in .env.example)."""
    return (os.getenv("GROK_BOT_AUTH") or "").strip().lower() or "bearer"


def encode_payload(request_id: str, question: str) -> bytes:
    return json.dumps({"request_id": request_id, "question": question},
                      separators=(",", ":"), ensure_ascii=False).encode()


async def send_question(request_id: str, question: str, *,
                        client: httpx.AsyncClient | None = None) -> httpx.Response:
    """POST {request_id, question} to the webhook. Raises GrokBotError on any failure."""
    url = os.getenv("GROK_BOT_WEBHOOK_URL", "").strip()
    key = os.getenv("GROK_BOT_WEBHOOK_KEY", "").strip()
    mode = auth_mode()
    if not url or not key:
        raise GrokBotError("GROK_BOT_WEBHOOK_URL / GROK_BOT_WEBHOOK_KEY are not set")

    body = encode_payload(request_id, question)
    if mode == "bearer":
        auth = bearer_headers(key)
    elif mode == "signed":
        auth = signed_headers(key, body)
    else:
        raise GrokBotError(f"GROK_BOT_AUTH must be 'bearer' or 'signed', not {mode!r}")
    headers = {"Content-Type": "application/json", **auth}

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as own_client:
                resp = await own_client.post(url, content=body, headers=headers)
        else:
            resp = await client.post(url, content=body, headers=headers)
    except httpx.HTTPError as e:                       # DNS, connect, timeout, ...
        raise GrokBotError(f"webhook request failed: {type(e).__name__}: {e}") from e

    if not resp.is_success:
        raise GrokBotError(f"webhook returned HTTP {resp.status_code}: {resp.text[:200]}")
    return resp


def main(argv: list[str]) -> None:
    from api import assistant_store as store
    from api.config import load_env
    load_env()
    question = " ".join(argv) or "test question"
    request_id = uuid.uuid4()
    store.create_request(request_id, question, "cli")     # so the routine's reply is accepted
    print(f"POST webhook ({auth_mode()})  request_id={request_id}")
    try:
        resp = asyncio.run(send_question(str(request_id), question))
    except GrokBotError as e:
        store.mark_failed(request_id, str(e)[:500])
        sys.exit(f"FAILED: {e}")
    print(f"HTTP {resp.status_code}  {resp.text[:300]}")
    print("Waiting for the routine to POST its answer to /api/ask/answer (Ctrl+C to stop)...")

    deadline = time.monotonic() + store.PENDING_TIMEOUT.total_seconds()
    try:
        while time.monotonic() < deadline:
            row = store.get_request(request_id)
            if row["status"] != "pending":
                print(f"\n{row['status'].upper()}:\n{row['answer'] or row['error']}")
                return
            time.sleep(3)
        print("No answer after 5 minutes. Check the routine's run in Grok Bot, and that the "
              "public URL it posts to reaches this machine.")
    finally:
        store.close_pool()


if __name__ == "__main__":
    main(sys.argv[1:])
