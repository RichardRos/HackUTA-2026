-- ============================================================================
-- nostos: website assistant (Grok Bot) request queue.  Safe to re-run.
-- Flow: FastAPI inserts a 'pending' row and POSTs the Grok Bot webhook; Grok Bot
-- answers asynchronously and calls save_answer(), which flips the row to 'done';
-- the browser polls GET /api/ask/{request_id}.
-- Regular table, not a hypertable: it is small and always looked up by key.
-- ============================================================================

CREATE TABLE IF NOT EXISTS assistant_requests (
    request_id   UUID        PRIMARY KEY,
    question     TEXT        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'done', 'failed')),
    answer       TEXT,
    error        TEXT,
    client_ip    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at  TIMESTAMPTZ
);

-- Small partial index for "oldest pending request" (the Grok Bot fallback mode).
CREATE INDEX IF NOT EXISTS assistant_requests_pending
    ON assistant_requests (created_at) WHERE status = 'pending';
