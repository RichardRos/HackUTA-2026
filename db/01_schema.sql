-- ============================================================================
-- nostos — Tiger Cloud (TimescaleDB) schema, v2
-- Data sources: Open-Meteo (weather) + EIA-930 Hourly Electric Grid Monitor
--
-- Run once:  psql "$TIGER_URL" -f nostos_schema.sql      (safe to re-run)
--
-- RULES EVERY INGEST SCRIPT FOLLOWS
--   * All timestamps are UTC (TIMESTAMPTZ). Never store local times.
--   * Every row has the time it is ABOUT (valid_time / period) and the time it
--     was KNOWN (issued_at / available_at). Backtests filter on the KNOWN time.
--   * EIA `period` is HOUR-ENDING: period 06:00Z = the hour 05:00–06:00Z.
--     Open-Meteo temperature at 06:00Z is the instantaneous value at 06:00Z.
--     Join the two on equal timestamps.
--   * Open-Meteo request params: temperature_unit=fahrenheit, wind_speed_unit=mph,
--     timezone=GMT
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Reference tables
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS balancing_authorities (
    ba_code   TEXT PRIMARY KEY,               -- EIA respondent code, e.g. 'ERCO'
    name      TEXT,
    timezone  TEXT NOT NULL                   -- IANA zone of the BA's operating day
);

CREATE TABLE IF NOT EXISTS weather_points (
    point_id   TEXT PRIMARY KEY,              -- short slug, e.g. 'dfw'
    ba_code    TEXT NOT NULL REFERENCES balancing_authorities (ba_code),
    name       TEXT,
    latitude   DOUBLE PRECISION NOT NULL,
    longitude  DOUBLE PRECISION NOT NULL,
    weight     DOUBLE PRECISION NOT NULL DEFAULT 0,   -- share of BA load (load points sum to 1)
    purpose    TEXT NOT NULL DEFAULT 'load'
               CHECK (purpose IN ('load', 'renewables'))
);


-- ----------------------------------------------------------------------------
-- 2. Weather forecasts (hypertable) — Open-Meteo
--    Live pulls  (api.open-meteo.com/v1/forecast):
--        issued_at = time you fetched it; lead_days = NULL; source_api = 'forecast'
--    Backfill    (previous-runs-api.open-meteo.com/v1/forecast, var_previous_dayN):
--        issued_at = valid_time - N days;  lead_days = N; source_api = 'previous_runs'
--    Do NOT backfill from the Historical Forecast API: it stitches each run's
--    first few hours together, so it is closer to a nowcast than a forecast.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_forecasts (
    valid_time       TIMESTAMPTZ NOT NULL,    -- hour the forecast is for
    issued_at        TIMESTAMPTZ NOT NULL,    -- when it was knowable
    point_id         TEXT        NOT NULL REFERENCES weather_points (point_id),
    model            TEXT        NOT NULL,    -- Open-Meteo `models` value: 'best_match', 'gfs_seamless', 'ecmwf_ifs025', ...
    source_api       TEXT        NOT NULL DEFAULT 'forecast'
                     CHECK (source_api IN ('forecast', 'previous_runs')),
    lead_days        SMALLINT,
    temp_f           DOUBLE PRECISION,        -- temperature_2m
    dewpoint_f       DOUBLE PRECISION,        -- dew_point_2m
    apparent_temp_f  DOUBLE PRECISION,        -- apparent_temperature
    rh_pct           DOUBLE PRECISION,        -- relative_humidity_2m
    cloud_cover_pct  DOUBLE PRECISION,        -- cloud_cover
    wind_10m_mph     DOUBLE PRECISION,        -- wind_speed_10m
    wind_100m_mph    DOUBLE PRECISION,        -- wind_speed_100m
    shortwave_wm2    DOUBLE PRECISION,        -- shortwave_radiation (mean over preceding hour)
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (point_id, model, valid_time, issued_at)
);
SELECT create_hypertable('weather_forecasts', by_range('valid_time'), if_not_exists => TRUE);


-- ----------------------------------------------------------------------------
-- 3. Weather actuals (hypertable) — what the weather turned out to be
--    'era5'        : archive-api.open-meteo.com/v1/archive (lags several days)
--    'om_analysis' : forecast API with past_days=N (recent days, near-analysis)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_actuals (
    valid_time       TIMESTAMPTZ NOT NULL,
    point_id         TEXT        NOT NULL REFERENCES weather_points (point_id),
    source           TEXT        NOT NULL CHECK (source IN ('era5', 'om_analysis')),
    temp_f           DOUBLE PRECISION,
    dewpoint_f       DOUBLE PRECISION,
    apparent_temp_f  DOUBLE PRECISION,
    rh_pct           DOUBLE PRECISION,
    cloud_cover_pct  DOUBLE PRECISION,
    wind_10m_mph     DOUBLE PRECISION,
    wind_100m_mph    DOUBLE PRECISION,
    shortwave_wm2    DOUBLE PRECISION,
    available_at     TIMESTAMPTZ NOT NULL,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (point_id, source, valid_time)
);
SELECT create_hypertable('weather_actuals', by_range('valid_time'), if_not_exists => TRUE);


-- ----------------------------------------------------------------------------
-- 4. Grid by BA (hypertable) — EIA-930 region-data, kept in EIA's long format
--    GET https://api.eia.gov/v2/electricity/rto/region-data/data/
--        ?api_key=...&frequency=hourly&data[]=value&facets[respondent][]=ERCO
--        &start=2026-01-01T00&end=...&offset=0&length=5000
--    EIA returns `value` as a string in MWh; cast it.
--    series: D = demand, DF = BA's day-ahead demand forecast,
--            NG = net generation, TI = total interchange
--    Each series has its own available_at because each is published on a
--    different schedule (DF for today appears around 10:00 ET today; NG/TI lag
--    about a day). Long format keeps one known-time per value.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS grid_hourly (
    period         TIMESTAMPTZ      NOT NULL,   -- UTC, hour-ending
    ba_code        TEXT             NOT NULL,   -- EIA respondent
    series         TEXT             NOT NULL CHECK (series IN ('D', 'DF', 'NG', 'TI')),
    value_mwh      DOUBLE PRECISION,
    available_at   TIMESTAMPTZ      NOT NULL,   -- live: fetch time on first sight; backfill: eia_backfill_available_at()
    first_seen_at  TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ      NOT NULL DEFAULT now(),   -- bumps when EIA revises the value
    PRIMARY KEY (ba_code, series, period)
);
SELECT create_hypertable('grid_hourly', by_range('period'), if_not_exists => TRUE);


-- ----------------------------------------------------------------------------
-- 5. Generation by fuel (hypertable) — EIA-930 fuel-type-data
--    GET .../electricity/rto/fuel-type-data/data/ (same params; facet fueltype)
--    fuel: EIA code as returned (COL, NG, NUC, OIL, WAT, SUN, WND, OTH, ...)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS grid_fuel_hourly (
    period         TIMESTAMPTZ      NOT NULL,
    ba_code        TEXT             NOT NULL,
    fuel           TEXT             NOT NULL,
    value_mwh      DOUBLE PRECISION,
    available_at   TIMESTAMPTZ      NOT NULL,
    first_seen_at  TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (ba_code, fuel, period)
);
SELECT create_hypertable('grid_fuel_hourly', by_range('period'), if_not_exists => TRUE);

-- Upsert template for both grid tables. It keeps available_at and first_seen_at
-- from the first time the value was seen and records revisions in updated_at:
--
--   INSERT INTO grid_hourly (period, ba_code, series, value_mwh, available_at)
--   VALUES (...)
--   ON CONFLICT (ba_code, series, period) DO UPDATE
--     SET value_mwh = EXCLUDED.value_mwh, updated_at = now()
--     WHERE grid_hourly.value_mwh IS DISTINCT FROM EXCLUDED.value_mwh;


-- ----------------------------------------------------------------------------
-- 6. nostos predictions (hypertable) — model output, for live use and backtests
--    The MCP server reads from this table, and so does Gemini (via `drivers`).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS demand_predictions (
    valid_time     TIMESTAMPTZ      NOT NULL,   -- hour-ending; matches grid_hourly.period
    issued_at      TIMESTAMPTZ      NOT NULL,   -- when nostos made it (backtest: simulated as-of time)
    ba_code        TEXT             NOT NULL,
    model_version  TEXT             NOT NULL,
    is_backtest    BOOLEAN          NOT NULL DEFAULT FALSE,
    demand_mwh     DOUBLE PRECISION NOT NULL,
    p10_mwh        DOUBLE PRECISION,
    p90_mwh        DOUBLE PRECISION,
    drivers        JSONB,                       -- feature values / attributions for the explanation
    created_at     TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (ba_code, model_version, valid_time, issued_at)
);
SELECT create_hypertable('demand_predictions', by_range('valid_time'), if_not_exists => TRUE);


-- ----------------------------------------------------------------------------
-- 7. Functions
-- ----------------------------------------------------------------------------

-- When a backfilled EIA value would have been public. Live ingest uses fetch time instead.
--   D      : about 1.5h after the hour ends -> period + 2h
--   DF     : posted around 10:00 ET on the operating day it covers
--   NG, TI, fuel : roughly a one-day lag -> 10:00 ET the next day
-- period is hour-ending, so (period - 1h) in the BA's zone gives the operating day.
CREATE OR REPLACE FUNCTION eia_backfill_available_at(p_series TEXT, p_period TIMESTAMPTZ, p_ba_tz TEXT)
RETURNS TIMESTAMPTZ
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN p_series = 'D' THEN p_period + INTERVAL '2 hours'
        WHEN p_series = 'DF' THEN
            ((((p_period - INTERVAL '1 hour') AT TIME ZONE p_ba_tz)::date) + TIME '10:00')
                AT TIME ZONE 'America/New_York'
        ELSE
            ((((p_period - INTERVAL '1 hour') AT TIME ZONE p_ba_tz)::date + 1) + TIME '10:00')
                AT TIME ZONE 'America/New_York'
    END;
$$;

-- Latest forecast per point and hour that was public at p_asof
CREATE OR REPLACE FUNCTION weather_forecast_asof(
    p_ba TEXT, p_model TEXT, p_from TIMESTAMPTZ, p_to TIMESTAMPTZ, p_asof TIMESTAMPTZ)
RETURNS TABLE (
    point_id TEXT, valid_time TIMESTAMPTZ, issued_at TIMESTAMPTZ,
    temp_f DOUBLE PRECISION, dewpoint_f DOUBLE PRECISION, apparent_temp_f DOUBLE PRECISION,
    rh_pct DOUBLE PRECISION, cloud_cover_pct DOUBLE PRECISION,
    wind_10m_mph DOUBLE PRECISION, wind_100m_mph DOUBLE PRECISION, shortwave_wm2 DOUBLE PRECISION)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (f.point_id, f.valid_time)
           f.point_id, f.valid_time, f.issued_at,
           f.temp_f, f.dewpoint_f, f.apparent_temp_f, f.rh_pct, f.cloud_cover_pct,
           f.wind_10m_mph, f.wind_100m_mph, f.shortwave_wm2
    FROM weather_forecasts f
    JOIN weather_points w ON w.point_id = f.point_id
    WHERE w.ba_code = p_ba
      AND f.model = p_model
      AND f.valid_time >= p_from AND f.valid_time < p_to
      AND f.issued_at <= p_asof
    ORDER BY f.point_id, f.valid_time, f.issued_at DESC;
$$;

-- Load-weighted BA weather as of p_asof, plus cooling/heating degree-hours (base 65°F).
-- This is the main weather feature set for the demand model.
CREATE OR REPLACE FUNCTION ba_weather_asof(
    p_ba TEXT, p_model TEXT, p_from TIMESTAMPTZ, p_to TIMESTAMPTZ, p_asof TIMESTAMPTZ)
RETURNS TABLE (
    valid_time TIMESTAMPTZ, oldest_issue TIMESTAMPTZ,
    temp_f DOUBLE PRECISION, dewpoint_f DOUBLE PRECISION, apparent_temp_f DOUBLE PRECISION,
    cdh_65 DOUBLE PRECISION, hdh_65 DOUBLE PRECISION, points_used INT)
LANGUAGE sql STABLE AS $$
    SELECT f.valid_time,
           min(f.issued_at),
           sum(w.weight * f.temp_f)                        / nullif(sum(w.weight), 0),
           sum(w.weight * f.dewpoint_f)                    / nullif(sum(w.weight), 0),
           sum(w.weight * f.apparent_temp_f)               / nullif(sum(w.weight), 0),
           sum(w.weight * greatest(f.temp_f - 65, 0))      / nullif(sum(w.weight), 0),
           sum(w.weight * greatest(65 - f.temp_f, 0))      / nullif(sum(w.weight), 0),
           count(*)::int
    FROM weather_forecast_asof(p_ba, p_model, p_from, p_to, p_asof) f
    JOIN weather_points w ON w.point_id = f.point_id
    WHERE w.purpose = 'load'
      AND f.temp_f IS NOT NULL
    GROUP BY f.valid_time
    ORDER BY f.valid_time;
$$;

-- EIA values that were public at p_asof (e.g. recent demand for lag features)
CREATE OR REPLACE FUNCTION grid_asof(
    p_ba TEXT, p_series TEXT, p_from TIMESTAMPTZ, p_to TIMESTAMPTZ, p_asof TIMESTAMPTZ)
RETURNS TABLE (period TIMESTAMPTZ, value_mwh DOUBLE PRECISION)
LANGUAGE sql STABLE AS $$
    SELECT g.period, g.value_mwh
    FROM grid_hourly g
    WHERE g.ba_code = p_ba
      AND g.series = p_series
      AND g.period >= p_from AND g.period < p_to
      AND g.available_at <= p_asof
    ORDER BY g.period;
$$;
-- Note: revised values overwrite in place, so a backtest sees the latest
-- revision of anything already public. That's fine for D/DF at hackathon scale.


-- ----------------------------------------------------------------------------
-- 8. Views
-- ----------------------------------------------------------------------------

-- Load-weighted actual weather per BA and hour (training labels/analysis)
CREATE OR REPLACE VIEW ba_weather_actuals AS
SELECT w.ba_code,
       a.source,
       a.valid_time,
       sum(w.weight * a.temp_f)                   / nullif(sum(w.weight), 0) AS temp_f,
       sum(w.weight * a.dewpoint_f)               / nullif(sum(w.weight), 0) AS dewpoint_f,
       sum(w.weight * greatest(a.temp_f - 65, 0)) / nullif(sum(w.weight), 0) AS cdh_65,
       sum(w.weight * greatest(65 - a.temp_f, 0)) / nullif(sum(w.weight), 0) AS hdh_65,
       count(*)::int                                                         AS points_used
FROM weather_actuals a
JOIN weather_points w ON w.point_id = a.point_id
WHERE w.purpose = 'load'
  AND a.temp_f IS NOT NULL
GROUP BY w.ba_code, a.source, a.valid_time;

-- The benchmark to beat: the BA's own day-ahead forecast vs actual demand
CREATE OR REPLACE VIEW demand_vs_eia_forecast AS
SELECT d.ba_code,
       d.period,
       d.value_mwh                                         AS demand_mwh,
       f.value_mwh                                         AS eia_forecast_mwh,
       f.value_mwh - d.value_mwh                           AS error_mwh,
       abs(f.value_mwh - d.value_mwh) / nullif(d.value_mwh, 0) AS abs_pct_error
FROM grid_hourly d
JOIN grid_hourly f
  ON f.ba_code = d.ba_code AND f.period = d.period AND f.series = 'DF'
WHERE d.series = 'D';

-- Scoreboard: every nostos prediction next to actual demand and the BA's forecast
CREATE OR REPLACE VIEW prediction_scores AS
SELECT p.ba_code,
       p.model_version,
       p.is_backtest,
       p.valid_time,
       p.issued_at,
       p.valid_time - p.issued_at       AS lead,
       p.demand_mwh                     AS nostos_mwh,
       d.value_mwh                      AS actual_mwh,
       f.value_mwh                      AS eia_df_mwh,
       p.demand_mwh - d.value_mwh       AS nostos_error_mwh,
       f.value_mwh  - d.value_mwh       AS eia_error_mwh
FROM demand_predictions p
JOIN grid_hourly d
  ON d.ba_code = p.ba_code AND d.period = p.valid_time AND d.series = 'D'
LEFT JOIN grid_hourly f
  ON f.ba_code = p.ba_code AND f.period = p.valid_time AND f.series = 'DF';
-- SELECT model_version, avg(abs(nostos_error_mwh)) AS nostos_mae,
--        avg(abs(eia_error_mwh)) AS eia_mae, count(*)
-- FROM prediction_scores GROUP BY 1;


-- ----------------------------------------------------------------------------
-- 9. Seed data — ERCOT. Weights are rough metro-population shares; tune freely.
--    To switch BAs, add a row to balancing_authorities and its own points.
-- ----------------------------------------------------------------------------
INSERT INTO balancing_authorities (ba_code, name, timezone) VALUES
    ('ERCO', 'Electric Reliability Council of Texas, Inc.', 'America/Chicago')
ON CONFLICT (ba_code) DO NOTHING;

INSERT INTO weather_points (point_id, ba_code, name, latitude, longitude, weight, purpose) VALUES
    ('dfw', 'ERCO', 'Dallas-Fort Worth',          32.8998,  -97.0403, 0.40, 'load'),
    ('hou', 'ERCO', 'Houston',                    29.7604,  -95.3698, 0.35, 'load'),
    ('sat', 'ERCO', 'San Antonio',                29.4241,  -98.4936, 0.13, 'load'),
    ('aus', 'ERCO', 'Austin',                     30.2672,  -97.7431, 0.12, 'load'),
    ('maf', 'ERCO', 'Midland (West TX wind/solar)', 31.9973, -102.0779, 0.00, 'renewables')
ON CONFLICT (point_id) DO NOTHING;


-- ----------------------------------------------------------------------------
-- 10. Timescale extras (for the "Best Use of Tiger Data" demo)
-- ----------------------------------------------------------------------------

-- Daily peak and total per BA and series, in ERCOT local days.
-- materialized_only = false also shows the newest, not-yet-materialized hours.
CREATE MATERIALIZED VIEW IF NOT EXISTS grid_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 day', period, 'America/Chicago') AS day,
       ba_code,
       series,
       max(value_mwh)  AS peak_mwh,
       sum(value_mwh)  AS total_mwh,
       count(*)        AS hours
FROM grid_hourly
GROUP BY day, ba_code, series
WITH NO DATA;

SELECT add_continuous_aggregate_policy('grid_daily',
    start_offset      => INTERVAL '7 days',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => TRUE);
-- The policy only covers the last 7 days. After a backfill, run once:
--   CALL refresh_continuous_aggregate('grid_daily', NULL, NULL);

-- Optional: columnstore compression. Run AFTER the backfill is done, since
-- upserts into compressed chunks are slower.
--   ALTER TABLE grid_hourly SET (timescaledb.enable_columnstore,
--       timescaledb.segmentby = 'ba_code, series', timescaledb.orderby = 'period DESC');
--   CALL add_columnstore_policy('grid_hourly', after => INTERVAL '30 days');
--   ALTER TABLE weather_forecasts SET (timescaledb.enable_columnstore,
--       timescaledb.segmentby = 'point_id, model', timescaledb.orderby = 'valid_time DESC, issued_at DESC');
--   CALL add_columnstore_policy('weather_forecasts', after => INTERVAL '30 days');
