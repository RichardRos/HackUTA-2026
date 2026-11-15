"""Shared short-lived, read-only PostgreSQL queries."""

import json

from markets import _json_default, get_dsn


def read_query(sql: str, params: tuple = ()) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(
        get_dsn(),
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
    ) as conn:
        rows = conn.execute(sql, params).fetchall()
    return json.loads(json.dumps(rows, default=_json_default))
