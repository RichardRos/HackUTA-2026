-- ============================================================================
-- nostos: live accuracy scoreboard.  Safe to re-run.
--
-- FAIRNESS RULE (head-to-head vs the BA's day-ahead forecast, EIA series DF):
--   A nostos prediction counts only if it was issued BEFORE the operating day
--   began (local midnight in the BA's time zone). The BA's DF is also produced
--   before the operating day, so both sides commit with the same horizon.
--   (EIA *publishes* DF around 10:00 ET on the operating day, but it was made
--   earlier; using publish time would let nostos peek at that morning's demand.)
--   For each hour we score the LATEST qualifying nostos prediction.
--
-- Shorter-lead predictions stay visible in scoreboard_by_lead, labelled by lead.
-- ============================================================================

-- Operating day an hour-ending period belongs to, in the BA's local time
CREATE OR REPLACE FUNCTION operating_day(p_period TIMESTAMPTZ, p_tz TEXT)
RETURNS DATE
LANGUAGE sql STABLE AS $$
    SELECT ((p_period - INTERVAL '1 hour') AT TIME ZONE p_tz)::date;
$$;

-- One row per BA / model / hour: the latest nostos prediction issued before the
-- operating day started, next to actual demand and the BA's DF.
CREATE OR REPLACE VIEW scoreboard_day_ahead AS
SELECT DISTINCT ON (ps.ba_code, ps.model_version, ps.is_backtest, ps.valid_time)
       ps.ba_code,
       ps.model_version,
       ps.is_backtest,
       operating_day(ps.valid_time, ba.timezone)                    AS operating_day,
       ps.valid_time,
       ps.issued_at,
       ps.lead,
       ps.nostos_mwh,
       ps.actual_mwh,
       ps.eia_df_mwh,
       ps.nostos_error_mwh,
       ps.eia_error_mwh,
       abs(ps.nostos_error_mwh) / nullif(ps.actual_mwh, 0)          AS nostos_ape,
       abs(ps.eia_error_mwh)    / nullif(ps.actual_mwh, 0)          AS eia_ape,
       abs(ps.nostos_error_mwh) < abs(ps.eia_error_mwh)             AS nostos_wins
FROM prediction_scores ps
JOIN balancing_authorities ba ON ba.ba_code = ps.ba_code
WHERE ps.eia_df_mwh IS NOT NULL
  AND ps.issued_at < (operating_day(ps.valid_time, ba.timezone)::timestamp AT TIME ZONE ba.timezone)
ORDER BY ps.ba_code, ps.model_version, ps.is_backtest, ps.valid_time, ps.issued_at DESC;

-- Headline numbers: one row per BA / model / backtest-vs-live
CREATE OR REPLACE VIEW scoreboard_summary AS
SELECT ba_code,
       model_version,
       is_backtest,
       count(*)                                                     AS hours_scored,
       round(avg(abs(nostos_error_mwh))::numeric, 0)                AS nostos_mae_mwh,
       round(avg(abs(eia_error_mwh))::numeric, 0)                   AS eia_mae_mwh,
       round(100 * avg(nostos_ape)::numeric, 2)                     AS nostos_mape_pct,
       round(100 * avg(eia_ape)::numeric, 2)                        AS eia_mape_pct,
       round(sqrt(avg(nostos_error_mwh ^ 2))::numeric, 0)           AS nostos_rmse_mwh,
       round(sqrt(avg(eia_error_mwh ^ 2))::numeric, 0)              AS eia_rmse_mwh,
       round(avg(nostos_error_mwh)::numeric, 0)                     AS nostos_bias_mwh,
       round(avg(eia_error_mwh)::numeric, 0)                        AS eia_bias_mwh,
       round(100 * avg(nostos_wins::int)::numeric, 1)               AS nostos_win_rate_pct,
       -- skill > 0 means nostos beats the BA forecast; 0.25 = 25% lower MAE
       round((1 - avg(abs(nostos_error_mwh)) / nullif(avg(abs(eia_error_mwh)), 0))::numeric, 3)
                                                                    AS skill_vs_eia,
       min(valid_time)                                              AS first_hour,
       max(valid_time)                                              AS last_hour
FROM scoreboard_day_ahead
GROUP BY ba_code, model_version, is_backtest;

-- Same metrics per operating day (for a chart / "yesterday's result" card)
CREATE OR REPLACE VIEW scoreboard_daily AS
SELECT ba_code,
       model_version,
       is_backtest,
       operating_day,
       count(*)                                                     AS hours_scored,
       round(avg(abs(nostos_error_mwh))::numeric, 0)                AS nostos_mae_mwh,
       round(avg(abs(eia_error_mwh))::numeric, 0)                   AS eia_mae_mwh,
       round(100 * avg(nostos_ape)::numeric, 2)                     AS nostos_mape_pct,
       round(100 * avg(eia_ape)::numeric, 2)                        AS eia_mape_pct,
       round(100 * avg(nostos_wins::int)::numeric, 1)               AS nostos_win_rate_pct,
       max(actual_mwh)                                              AS actual_peak_mwh,
       max(nostos_mwh)                                              AS nostos_peak_mwh,
       max(eia_df_mwh)                                              AS eia_peak_mwh
FROM scoreboard_day_ahead
GROUP BY ba_code, model_version, is_backtest, operating_day;

-- Every prediction, bucketed by lead time. EIA's error on the same hours is shown
-- for context, but only the day-ahead buckets are a like-for-like comparison.
CREATE OR REPLACE VIEW scoreboard_by_lead AS
SELECT ba_code,
       model_version,
       is_backtest,
       CASE WHEN lead <  INTERVAL '6 hours'  THEN '1: <6h'
            WHEN lead <  INTERVAL '24 hours' THEN '2: 6-24h'
            WHEN lead <  INTERVAL '48 hours' THEN '3: 24-48h'
            ELSE                                  '4: 48h+' END     AS lead_bucket,
       count(*)                                                     AS predictions,
       round(avg(abs(nostos_error_mwh))::numeric, 0)                AS nostos_mae_mwh,
       round(100 * avg(abs(nostos_error_mwh) / nullif(actual_mwh, 0))::numeric, 2)
                                                                    AS nostos_mape_pct,
       round(avg(abs(eia_error_mwh))::numeric, 0)                   AS eia_mae_same_hours_mwh
FROM prediction_scores
GROUP BY ba_code, model_version, is_backtest, lead_bucket;
