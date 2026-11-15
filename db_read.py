"""Shared PostgreSQL access over pooled connections: read-only queries, plus one small writer pool."""

import json
import threading

from markets import _json_default, get_dsn

_pools: dict[bool, object] = {}
_lock = threading.Lock()


def _get_pool(read_only: bool = True):
    # A fresh TLS handshake to the cloud DB per query timed out under bursts (a country rundown fans out
    # several queries at once), so connections are pooled and reused.
    with _lock:
        if read_only not in _pools:
            from psycopg_pool import ConnectionPool

            options = "-c statement_timeout=10000" + (" -c default_transaction_read_only=on" if read_only else "")
            _pools[read_only] = ConnectionPool(
                get_dsn(), min_size=1, max_size=8 if read_only else 2, open=True, timeout=20,
                kwargs={"autocommit": True, "connect_timeout": 10, "options": options},
                check=ConnectionPool.check_connection,  # cloud DBs drop idle connections
            )
        return _pools[read_only]


def _run(sql: str, params: tuple, read_only: bool) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row

    for attempt in (1, 2):
        try:
            with _get_pool(read_only).connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall() if cur.description else []
            return json.loads(json.dumps(rows, default=_json_default))
        except psycopg.OperationalError:
            if attempt == 2:  # one retry: a pooled connection the server dropped mid-query
                raise
    return []


def read_query(sql: str, params: tuple = ()) -> list[dict]:
    return _run(sql, params, read_only=True)


def write_query(sql: str, params: tuple = ()) -> list[dict]:
    return _run(sql, params, read_only=False)
