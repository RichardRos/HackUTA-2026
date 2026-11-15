"""Shared read-only PostgreSQL queries over one pooled set of connections."""

import json
import threading

from markets import _json_default, get_dsn

_pool = None
_lock = threading.Lock()


def _get_pool():
    # A fresh TLS handshake to the cloud DB per query timed out under bursts (a country rundown fans out
    # several queries at once), so connections are pooled and reused.
    global _pool
    with _lock:
        if _pool is None:
            from psycopg_pool import ConnectionPool

            _pool = ConnectionPool(
                get_dsn(), min_size=1, max_size=8, open=True, timeout=20,
                kwargs={"autocommit": True, "connect_timeout": 10,
                        "options": "-c default_transaction_read_only=on -c statement_timeout=10000"},
                check=ConnectionPool.check_connection,  # cloud DBs drop idle connections
            )
        return _pool


def read_query(sql: str, params: tuple = ()) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row

    for attempt in (1, 2):
        try:
            with _get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(sql, params).fetchall()
            return json.loads(json.dumps(rows, default=_json_default))
        except psycopg.OperationalError:
            if attempt == 2:  # one retry: a pooled connection the server dropped mid-query
                raise
    return []
