-- Generic time-series observations for no-auth NOAA, commodity, and shipping feeds.
-- Run with db/run_sql.py; safe to re-run.

CREATE TABLE IF NOT EXISTS external_observations (
    source       TEXT NOT NULL,
    dataset      TEXT NOT NULL,
    series_id    TEXT NOT NULL,
    observed_at  TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    value        DOUBLE PRECISION NOT NULL,
    unit         TEXT,
    dimensions   JSONB NOT NULL DEFAULT '{}',
    raw_data     JSONB NOT NULL DEFAULT '{}',
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, dataset, series_id, observed_at)
);

SELECT create_hypertable('external_observations', by_range('observed_at'), if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS external_observations_series_available_idx
    ON external_observations (dataset, series_id, available_at DESC);
