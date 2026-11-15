-- Ingest monitoring: run history, dead letters, data checks, service heartbeat.
-- Run with db/run_sql.py; safe to re-run.

CREATE TABLE IF NOT EXISTS ingest_runs (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_name      TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    attempt       INTEGER NOT NULL CHECK (attempt >= 1),
    trigger_source TEXT NOT NULL DEFAULT 'scheduled'
                  CHECK (trigger_source IN ('scheduled', 'startup', 'retry', 'manual')),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    pid           BIGINT,
    exit_code     INTEGER,
    rows_affected BIGINT,
    log_excerpt   TEXT,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS ingest_runs_job_started_idx
    ON ingest_runs (job_name, started_at DESC);

CREATE TABLE IF NOT EXISTS ingest_dead_letters (
    job_name       TEXT PRIMARY KEY,
    command        JSONB NOT NULL,
    attempts       INTEGER NOT NULL CHECK (attempts >= 0),
    status         TEXT NOT NULL CHECK (status IN ('pending', 'dead', 'resolved')),
    last_error     TEXT,
    last_log       TEXT,
    retry_at       TIMESTAMPTZ,
    first_failed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ingest_check_results (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_name TEXT NOT NULL,
    check_name  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('pass', 'warn', 'fail')),
    value       DOUBLE PRECISION NOT NULL,
    details     JSONB NOT NULL DEFAULT '{}',
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ingest_check_results_check_checked_idx
    ON ingest_check_results (source_name, check_name, checked_at DESC);

CREATE TABLE IF NOT EXISTS ingest_service_state (
    id           BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    instance_id  UUID NOT NULL,
    hostname     TEXT NOT NULL,
    pid          BIGINT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    scheduler_tick_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    jobs         JSONB NOT NULL DEFAULT '{}'
);

CREATE OR REPLACE VIEW ingest_latest_runs AS
SELECT DISTINCT ON (job_name)
    id, job_name, status, attempt, trigger_source, started_at, finished_at,
    pid, exit_code, rows_affected, log_excerpt, error
FROM ingest_runs
ORDER BY job_name, started_at DESC, id DESC;

CREATE OR REPLACE VIEW ingest_latest_checks AS
SELECT DISTINCT ON (source_name, check_name)
    id, source_name, check_name, status, value, details, checked_at
FROM ingest_check_results
ORDER BY source_name, check_name, checked_at DESC, id DESC;

CREATE INDEX IF NOT EXISTS external_observations_monitor_fetched_idx
    ON external_observations (source, dataset, fetched_at DESC);
