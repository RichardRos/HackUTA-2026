"""Run .sql files against Tiger Cloud one statement at a time (like `psql -f`).

Each statement autocommits. That matters because TimescaleDB continuous
aggregates cannot be created inside a transaction block.

Usage:
    python db/run_sql.py db/01_schema.sql [more.sql ...]
    python db/run_sql.py -c "SELECT count(*) FROM grid_hourly"

Connection: TIMESCALE_SERVICE_URL from the environment, else from the .env file
named by NOSTOS_ENV (default: ../Research/tiger-cloud-db-nostos_weather-credentials.env
relative to the repo root), else from the repo-root .env.
"""
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ENV = REPO.parent / "Research" / "tiger-cloud-db-nostos_weather-credentials.env"


def get_url() -> str:
    if not os.getenv("TIMESCALE_SERVICE_URL", "").strip():
        os.environ.pop("TIMESCALE_SERVICE_URL", None)
        load_dotenv(os.getenv("NOSTOS_ENV", DEFAULT_ENV))
        load_dotenv(REPO / ".env")
    url = os.getenv("TIMESCALE_SERVICE_URL")
    if not url:
        sys.exit(f"TIMESCALE_SERVICE_URL not set. Add it to {REPO / '.env'} "
                 "(or export it, or set NOSTOS_ENV to another .env file).")
    return url


def split_sql(text: str) -> list[str]:
    """Split on top-level semicolons, respecting quotes, $tag$ bodies and comments."""
    stmts, buf, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("--", i):                      # line comment
            j = text.find("\n", i)
            j = n if j == -1 else j
            buf.append(text[i:j]); i = j
        elif text.startswith("/*", i):                    # block comment
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            buf.append(text[i:j]); i = j
        elif c == "'":                                    # string literal
            j = i + 1
            while j < n:
                if text[j] == "'" and text[j + 1:j + 2] == "'":
                    j += 2
                elif text[j] == "'":
                    break
                else:
                    j += 1
            buf.append(text[i:j + 1]); i = j + 1
        elif c == "$":                                    # dollar-quoted body
            j = text.find("$", i + 1)
            tag = text[i:j + 1] if j != -1 else ""
            if tag and (tag == "$$" or tag[1:-1].replace("_", "").isalnum()):
                k = text.find(tag, j + 1)
                k = n if k == -1 else k + len(tag)
                buf.append(text[i:k]); i = k
            else:
                buf.append(c); i += 1
        elif c == ";":
            stmts.append("".join(buf)); buf = []; i += 1
        else:
            buf.append(c); i += 1
    stmts.append("".join(buf))

    def has_code(s: str) -> bool:
        return any(l.strip() and not l.strip().startswith("--") for l in s.splitlines())

    return [s.strip() for s in stmts if has_code(s)]


def first_line(stmt: str) -> str:
    for line in stmt.splitlines():
        if line.strip() and not line.strip().startswith("--"):
            return line.strip()[:90]
    return ""


def run(conn, sql: str, label: str) -> int:
    failures = 0
    for n, stmt in enumerate(split_sql(sql), 1):
        try:
            cur = conn.execute(stmt)
            msg = cur.statusmessage or ""
            print(f"  ok   [{label} #{n}] {first_line(stmt)}  -> {msg}")
            if cur.description:
                cols = [d.name for d in cur.description]
                print("       " + " | ".join(cols))
                for row in cur.fetchmany(50):
                    print("       " + " | ".join(str(v) for v in row))
        except psycopg.Error as e:
            failures += 1
            print(f"  FAIL [{label} #{n}] {first_line(stmt)}\n       {str(e).strip()}")
    return failures


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    with psycopg.connect(get_url(), autocommit=True) as conn:
        if argv[0] == "-c":
            fails = run(conn, " ".join(argv[1:]), "cmd")
        else:
            fails = 0
            for path in argv:
                print(f"== {path}")
                fails += run(conn, Path(path).read_text(encoding="utf-8"), Path(path).name)
    print(f"\n{'All statements succeeded.' if not fails else f'{fails} statement(s) failed.'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
