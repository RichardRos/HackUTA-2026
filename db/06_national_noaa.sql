-- ============================================================================
-- nostos schema v3: national EIA-930 coverage + NOAA weather (replaces Open-Meteo)
-- Run after 01-05:   python db/run_sql.py db/06_national_noaa.sql   (safe to re-run)
--
-- WHAT CHANGES
--   * Every EIA-930 respondent (all balancing authorities, the 13 EIA regions
--     and US48), plus sub-BA zones (ERCOT weather zones, PJM utility zones,
--     NYISO zones A-K, ...) in the new grid_subba_hourly table.
--   * Weather now comes from NOAA. Points are airport (ICAO) stations:
--       forecasts  weather_forecasts.model
--         'nbm_nbh'  National Blend of Models hourly text bulletin (1-25 h ahead)
--         'nbm_nbs'  National Blend of Models short-range bulletin (3-hourly, 6-72 h)
--             source_api 'nbm_text'; issued_at = when NOAA published the bulletin
--             (S3 upload time); backfilled from the NOAA archive on AWS.
--         'nws_ndfd' NWS forecaster grid (api.weather.gov gridpoints), hourly, 7 days.
--             source_api 'nws_api'; issued_at = the grid's updateTime. Live only.
--       actuals    weather_actuals.source
--         'ghcnh'    NCEI Global Historical Climatology Network hourly (history; lags ~2-3 days)
--         'nws_obs'  api.weather.gov station observations (recent hours)
--             available_at = valid_time + 15 min (METARs are public within minutes)
--   * Typed columns for the main variables, plus `extra` JSONB holding every
--     other element the source provides (see ingest/README.md for keys/units).
--   * Units stay as in v2: °F, mph, inches, miles, feet, W/m², %.
--
-- Old Open-Meteo rows (model 'best_match', source_api 'forecast'/'previous_runs',
-- sources 'era5'/'om_analysis') remain valid; delete them if you don't want them.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Reference tables
-- ----------------------------------------------------------------------------
ALTER TABLE balancing_authorities
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'ba';     -- 'ba' | 'region' | 'total'

ALTER TABLE weather_points
    ADD COLUMN IF NOT EXISTS icao        TEXT,      -- NBM / NWS station id, e.g. 'KDFW'
    ADD COLUMN IF NOT EXISTS ghcn_id     TEXT,      -- NCEI GHCNh id, e.g. 'USW00003927'
    ADD COLUMN IF NOT EXISTS nws_grid_id TEXT,      -- NWS office, e.g. 'FWD' (filled on first use)
    ADD COLUMN IF NOT EXISTS nws_grid_x  INT,
    ADD COLUMN IF NOT EXISTS nws_grid_y  INT;

-- Sub-BA zones as EIA names them
CREATE TABLE IF NOT EXISTS eia_subbas (
    ba_code  TEXT NOT NULL,                 -- EIA `parent`
    subba    TEXT NOT NULL,                 -- EIA `subba`
    name     TEXT,
    PRIMARY KEY (ba_code, subba)
);


-- ----------------------------------------------------------------------------
-- 2. Sub-BA demand (hypertable) — EIA-930 region-sub-ba-data
--    Published with roughly a one-day lag, so backfill uses the NG/TI rule of
--    eia_backfill_available_at() (10:00 ET the next day).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS grid_subba_hourly (
    period         TIMESTAMPTZ      NOT NULL,   -- UTC, hour-ending
    ba_code        TEXT             NOT NULL,   -- parent BA
    subba          TEXT             NOT NULL,
    value_mwh      DOUBLE PRECISION,
    available_at   TIMESTAMPTZ      NOT NULL,
    first_seen_at  TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (ba_code, subba, period)
);
SELECT create_hypertable('grid_subba_hourly', by_range('period'), if_not_exists => TRUE);


-- ----------------------------------------------------------------------------
-- 3. Weather forecasts: NOAA columns
--    precip_prob_pct / precip_in / snow_in / thunder_prob_pct cover the period
--    ending at valid_time: 1 h for nbm_nbh and nws_ndfd, 6 h (thunder 3 h) for nbm_nbs.
-- ----------------------------------------------------------------------------
ALTER TABLE weather_forecasts
    ADD COLUMN IF NOT EXISTS lead_hours        SMALLINT,          -- valid_time - model run time
    ADD COLUMN IF NOT EXISTS wind_dir_deg      DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS wind_gust_mph     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS precip_prob_pct   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS precip_in         DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS snow_in           DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS thunder_prob_pct  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS visibility_mi     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS ceiling_ft        DOUBLE PRECISION,  -- NULL = no ceiling
    ADD COLUMN IF NOT EXISTS wet_bulb_globe_f  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS extra             JSONB;

ALTER TABLE weather_forecasts DROP CONSTRAINT IF EXISTS weather_forecasts_source_api_check;
ALTER TABLE weather_forecasts ADD CONSTRAINT weather_forecasts_source_api_check
    CHECK (source_api IN ('forecast', 'previous_runs', 'nbm_text', 'nws_api'));


-- ----------------------------------------------------------------------------
-- 4. Weather actuals: NOAA columns
-- ----------------------------------------------------------------------------
ALTER TABLE weather_actuals
    ADD COLUMN IF NOT EXISTS wind_dir_deg            DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS wind_gust_mph           DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS precip_in               DOUBLE PRECISION,   -- past hour
    ADD COLUMN IF NOT EXISTS visibility_mi           DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS ceiling_ft              DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sea_level_pressure_hpa  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS station_pressure_hpa    DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS present_weather         TEXT,               -- e.g. '-RA BR'
    ADD COLUMN IF NOT EXISTS extra                   JSONB;

ALTER TABLE weather_actuals DROP CONSTRAINT IF EXISTS weather_actuals_source_check;
ALTER TABLE weather_actuals ADD CONSTRAINT weather_actuals_source_check
    CHECK (source IN ('era5', 'om_analysis', 'ghcnh', 'nws_obs'));


-- ----------------------------------------------------------------------------
-- 5. Integrity checks (same columns as 04_backtest.sql, rules widened)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW data_integrity_checks AS
SELECT 'prediction issued after the hour it predicts' AS check_name,
       count(*) AS violations
FROM demand_predictions WHERE issued_at > valid_time
UNION ALL
SELECT 'prediction outside its own p10-p90 band',
       count(*)
FROM demand_predictions
WHERE (p10_mwh IS NOT NULL AND demand_mwh < p10_mwh)
   OR (p90_mwh IS NOT NULL AND demand_mwh > p90_mwh)
UNION ALL
SELECT 'EIA measured value (D/NG/TI) marked public before its hour ended',
       count(*)
FROM grid_hourly WHERE series <> 'DF' AND available_at < period   -- DF is a forecast: public early by design
UNION ALL
SELECT 'EIA sub-BA value marked public before its hour ended',
       count(*)
FROM grid_subba_hourly WHERE available_at < period
UNION ALL
SELECT 'weather actual marked public before its hour',
       count(*)
FROM weather_actuals WHERE available_at < valid_time
UNION ALL
SELECT 'weather forecast issued after valid_time',
       count(*)
FROM weather_forecasts WHERE issued_at > valid_time
UNION ALL
SELECT 'BA whose load-point weights do not sum to 1',
       count(*)
FROM (SELECT ba_code FROM weather_points WHERE purpose = 'load'
      GROUP BY ba_code HAVING abs(sum(weight) - 1) > 0.001) w
UNION ALL
SELECT 'missing hours of actual demand (D) inside loaded range',
       coalesce(sum(expected - got), 0)
FROM (SELECT ba_code,
             (extract(epoch FROM max(period) - min(period)) / 3600)::int + 1 AS expected,
             count(*)                                                        AS got
      FROM grid_hourly WHERE series = 'D'
      GROUP BY ba_code) g;


-- ----------------------------------------------------------------------------
-- 6. Daily sub-BA rollup (continuous aggregate), like grid_daily
-- ----------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS grid_subba_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 day', period, 'America/New_York') AS day,
       ba_code,
       subba,
       max(value_mwh)  AS peak_mwh,
       sum(value_mwh)  AS total_mwh,
       count(*)        AS hours
FROM grid_subba_hourly
GROUP BY day, ba_code, subba
WITH NO DATA;

SELECT add_continuous_aggregate_policy('grid_subba_daily',
    start_offset      => INTERVAL '7 days',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => TRUE);
