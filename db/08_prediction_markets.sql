-- Current Kalshi/Polymarket rows; run after 01_schema.sql:
-- python db/run_sql.py db/08_prediction_markets.sql

CREATE TABLE IF NOT EXISTS prediction_markets (
    platform             TEXT NOT NULL CHECK (platform IN ('kalshi', 'polymarket')),
    market_id            TEXT NOT NULL,
    event_id             TEXT,
    slug                 TEXT,
    url                  TEXT,
    category             TEXT,
    question_raw         TEXT NOT NULL,
    question_normalized  TEXT NOT NULL,
    normalization_method TEXT NOT NULL CHECK (normalization_method IN ('regex', 'gemini')),
    status               TEXT NOT NULL,
    close_time           TIMESTAMPTZ,
    rules                TEXT,
    raw_data             JSONB NOT NULL,
    scraped_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    normalized_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (platform, market_id)
);

CREATE TABLE IF NOT EXISTS prediction_market_outcomes (
    platform           TEXT     NOT NULL,
    market_id          TEXT     NOT NULL,
    outcome_index      SMALLINT NOT NULL,
    outcome_raw        TEXT     NOT NULL,
    outcome_normalized TEXT     NOT NULL,
    price              DOUBLE PRECISION CHECK (price BETWEEN 0 AND 1),
    scraped_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (platform, market_id, outcome_index),
    FOREIGN KEY (platform, market_id)
        REFERENCES prediction_markets (platform, market_id) ON DELETE CASCADE
);

-- Match normalized questions across platforms.
CREATE INDEX IF NOT EXISTS prediction_markets_question_normalized_idx
    ON prediction_markets (question_normalized);

-- One row per market with its outcomes as a JSONB array ordered by outcome_index.
CREATE OR REPLACE VIEW normalized_prediction_markets AS
SELECT m.platform,
       m.market_id,
       m.event_id,
       m.slug,
       m.url,
       m.category,
       m.question_raw,
       m.question_normalized,
       m.normalization_method,
       m.status,
       m.close_time,
       m.rules,
       m.scraped_at,
       m.normalized_at,
       coalesce(
           jsonb_agg(
               jsonb_build_object(
                   'raw',        o.outcome_raw,
                   'normalized', o.outcome_normalized,
                   'price',      o.price
               )
               ORDER BY o.outcome_index
           ) FILTER (WHERE o.market_id IS NOT NULL),
           '[]'::jsonb
       ) AS outcomes
FROM prediction_markets m
LEFT JOIN prediction_market_outcomes o
  ON o.platform = m.platform
 AND o.market_id = m.market_id
GROUP BY m.platform, m.market_id;
