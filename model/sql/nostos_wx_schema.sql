-- Nostos weather-scorer tables. Additive only: everything lives in schema nostos_wx;
-- nothing outside it is created, altered or dropped. Every statement is idempotent.
-- All timestamps are UTC (timestamptz). climate_date is the NWS climate day (local standard time).

CREATE SCHEMA IF NOT EXISTS nostos_wx;

-- 20 scored cities (mirrors model/data/locations.csv)
CREATE TABLE IF NOT EXISTS nostos_wx.locations (
    location              text PRIMARY KEY,
    station_id            text NOT NULL,          -- ICAO of the NWS CLI station
    latitude              double precision NOT NULL,
    longitude             double precision NOT NULL,
    std_utc_offset_hours  smallint NOT NULL,
    kalshi_series         text
);

-- Official NWS daily highs (IEM CLI). Hypertable on climate_date.
CREATE TABLE IF NOT EXISTS nostos_wx.daily_high_obs (
    location      text NOT NULL REFERENCES nostos_wx.locations(location),
    climate_date  date NOT NULL,
    day_start     timestamptz NOT NULL,           -- local standard midnight of climate_date, in UTC
    station_id    text NOT NULL,
    high_f        double precision NOT NULL,
    published_at  timestamptz NOT NULL,           -- CLI product issuance time
    source        text NOT NULL DEFAULT 'iem_cli',
    PRIMARY KEY (location, climate_date)
);
SELECT create_hypertable('nostos_wx.daily_high_obs', by_range('climate_date', INTERVAL '90 days'),
                         if_not_exists => TRUE);

-- Daily-high forecasts, one row per location/model/lead/climate day. Hypertable on climate_date.
--   role: input (the 5 blended models) | benchmark (ncep_nbm_conus, nbm_corrected)
--         | nostos | baseline (naive_persistence)
--   observed_f / error_f are VERIFICATION columns, filled after the fact from daily_high_obs.
--   They are never inputs to any forecast; they sit here so the leaderboard can be a
--   continuous aggregate (which cannot join two hypertables).
CREATE TABLE IF NOT EXISTS nostos_wx.daily_high_forecasts (
    location      text NOT NULL REFERENCES nostos_wx.locations(location),
    model         text NOT NULL,
    role          text NOT NULL CHECK (role IN ('input', 'benchmark', 'nostos', 'baseline')),
    lead_days     smallint NOT NULL,
    climate_date  date NOT NULL,
    day_start     timestamptz NOT NULL,
    issued_at     timestamptz NOT NULL,           -- 23:00 local std on climate_date - lead_days
    high_f        double precision NOT NULL,
    observed_f    double precision,
    error_f       double precision GENERATED ALWAYS AS (high_f - observed_f) STORED,
    PRIMARY KEY (location, model, lead_days, climate_date),
    CHECK (issued_at < day_start)
);
SELECT create_hypertable('nostos_wx.daily_high_forecasts', by_range('climate_date', INTERVAL '90 days'),
                         if_not_exists => TRUE);

-- Leaderboards from the Python pipeline.
--   split: full_period (scores.csv) | validation | test;  lead_days 0 = all leads pooled
CREATE TABLE IF NOT EXISTS nostos_wx.scores (
    split          text NOT NULL,
    lead_days      smallint NOT NULL,
    model          text NOT NULL,
    days           integer NOT NULL,
    mae_f          double precision NOT NULL,
    bias_f         double precision NOT NULL,
    within_2f_pct  double precision NOT NULL,
    vs_nbm_f       double precision,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (split, model, lead_days)
);

-- Continuous aggregate: daily leaderboard by model and lead, pooled over cities.
-- Keeps sums and counts so any date range re-aggregates exactly:
--   MAE = sum(sum_abs_err_f) / sum(n),  bias = sum(sum_err_f) / sum(n)
CREATE MATERIALIZED VIEW IF NOT EXISTS nostos_wx.leaderboard_daily
WITH (timescaledb.continuous) AS
SELECT time_bucket(INTERVAL '1 day', climate_date) AS day,
       model,
       role,
       lead_days,
       count(error_f)                              AS n,
       sum(abs(error_f))                           AS sum_abs_err_f,
       sum(error_f)                                AS sum_err_f,
       count(*) FILTER (WHERE abs(error_f) <= 2)   AS n_within_2f,
       avg(abs(error_f))                           AS mae_f,
       avg(error_f)                                AS bias_f
FROM nostos_wx.daily_high_forecasts
WHERE error_f IS NOT NULL
GROUP BY 1, 2, 3, 4
WITH NO DATA;
