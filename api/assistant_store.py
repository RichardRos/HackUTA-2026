"""Tiger (Postgres) access for the assistant_requests table (db/07_assistant.sql).

Synchronous psycopg on purpose: async psycopg does not run on Windows' default
ProactorEventLoop. The FastAPI router calls these through asyncio.to_thread.

save_answer() is called by POST /api/ask/answer, where the Grok Bot routine sends
its reply. If the routine ever goes back to MCP tools, wrap the same function:

    @mcp.tool()
    def save_answer(request_id: str, answer: str) -> str:
        return assistant_store.save_answer(request_id, answer)
"""
import os
import threading
import uuid
from datetime import timedelta

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

PENDING_TIMEOUT = timedelta(minutes=5)
MAX_ANSWER_CHARS = 8000

SAVED = "saved"
UNKNOWN = "unknown or already answered"
TOO_LONG = f"answer too long (max {MAX_ANSWER_CHARS} chars)"
EMPTY = "answer is empty"

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def pool() -> ConnectionPool:
    """Lazily open one shared pool (DSN: TIMESCALE_SERVICE_URL)."""
    global _pool
    with _pool_lock:
        if _pool is None:
            url = os.getenv("TIMESCALE_SERVICE_URL")
            if not url:
                raise RuntimeError("TIMESCALE_SERVICE_URL is not set")
            _pool = ConnectionPool(
                url, min_size=1, max_size=5, open=True,
                kwargs={"autocommit": True},
                check=ConnectionPool.check_connection,   # cloud DBs drop idle connections
            )
        return _pool


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def create_request(request_id: uuid.UUID, question: str, client_ip: str | None) -> None:
    with pool().connection() as conn:
        conn.execute(
            "INSERT INTO assistant_requests (request_id, question, client_ip) VALUES (%s, %s, %s)",
            (request_id, question, client_ip),
        )


def mark_failed(request_id: uuid.UUID, error: str) -> bool:
    """Pending -> failed. False if the row is unknown or already finished."""
    with pool().connection() as conn:
        cur = conn.execute(
            "UPDATE assistant_requests SET status = 'failed', error = %s "
            "WHERE request_id = %s AND status = 'pending'",
            (error, request_id),
        )
        return cur.rowcount == 1


def get_request(request_id: uuid.UUID) -> dict | None:
    """Row as a dict, plus `timed_out` (pending longer than PENDING_TIMEOUT), or None."""
    with pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT request_id, status, answer, error, created_at, answered_at, "
            "       (status = 'pending' AND created_at < now() - %s) AS timed_out "
            "FROM assistant_requests WHERE request_id = %s",
            (PENDING_TIMEOUT, request_id),
        )
        return cur.fetchone()


def save_answer(request_id: str, answer: str) -> str:
    """Mark a pending request done. Returns SAVED, UNKNOWN, TOO_LONG or EMPTY."""
    answer = (answer or "").strip()
    if not answer:
        return EMPTY
    if len(answer) > MAX_ANSWER_CHARS:
        return TOO_LONG
    try:
        rid = uuid.UUID(str(request_id))
    except ValueError:
        return UNKNOWN
    with pool().connection() as conn:
        cur = conn.execute(
            "UPDATE assistant_requests SET answer = %s, status = 'done', answered_at = now() "
            "WHERE request_id = %s AND status = 'pending'",
            (answer, rid),
        )
        return SAVED if cur.rowcount == 1 else UNKNOWN
