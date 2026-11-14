-- ============================================================================
-- nostos: backtest views + data integrity checks.  Safe to re-run.
-- All backtest views build on scoreboard_day_ahead (03_scoreboard.sql), so they
-- use the same fairness rule as the live scoreboard.
-- ============================================================================

-- Backtest accuracy per model and month (compare model versions side by side)
CREATE OR REPLACE VIEW backtest_monthly AS
SELECT s.ba_code,
       s.model_version,
       date_trunc('month', s.operating_day)::date                   AS month,
       count(*)                                                     AS hours_scored,
       round(avg(abs(s.nostos_error_mwh))::numeric, 0)              AS nostos_mae_mwh,
       round(avg(abs(s.eia_error_mwh))::numeric, 0)                 AS eia_mae_mwh,
       round(100 * avg(s.nostos_ape)::numeric, 2)                   AS nostos_mape_pct,
       round(100 * avg(s.eia_ape)::numeric, 2)                      AS eia_mape_pct,
       round(100 * avg(s.nostos_wins::int)::numeric, 1)             AS nostos_win_rate_pct,
       round((1 - avg(abs(s.nostos_error_mwh)) / nullif(avg(abs(s.eia_error_mwh)), 0))::numeric, 3)
                                                                    AS skill_vs_eia,
       -- share of actuals inside nostos' p10-p90 band; well calibrated ~= 80%
       round(100 * avg((s.actual_mwh BETWEEN p.p10_mwh AND p.p90_mwh)::int)::numeric, 1)
                                                                    AS p10_p90_coverage_pct
FROM scoreboard_day_ahead s
JOIN demand_predictions p
  ON p.ba_code = s.ba_code AND p.model_version = s.model_version
 AND p.valid_time = s.valid_time AND p.issued_at = s.issued_at
WHERE s.is_backtest
GROUP BY s.ba_code, s.model_version, month;

-- Error by local hour of day: shows WHERE nostos beats the BA (e.g. evening peak)
CREATE OR REPLACE VIEW backtest_by_hour AS
SELECT s.ba_code,
       s.model_version,
       extract(hour FROM (s.valid_time - INTERVAL '1 hour') AT TIME ZONE ba.timezone)::int
                                                                    AS hour_starting_local,
       count(*)                                                     AS hours_scored,
       round(avg(abs(s.nostos_error_mwh))::numeric, 0)              AS nostos_mae_mwh,
       round(avg(abs(s.eia_error_mwh))::numeric, 0)                 AS eia_mae_mwh,
       round(100 * avg(s.nostos_wins::int)::numeric, 1)             AS nostos_win_rate_pct
FROM scoreboard_day_ahead s
JOIN balancing_authorities ba ON ba.ba_code = s.ba_code
WHERE s.is_backtest
GROUP BY s.ba_code, s.model_version, hour_starting_local;

-- Integrity checks: every row should show violations = 0 before the demo.
-- This is the "we didn't peek at the future" evidence.
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
SELECT 'weather actual marked public before its hour',
       count(*)
FROM weather_actuals WHERE available_at < valid_time
UNION ALL
SELECT 'backfilled weather forecast issued after valid_time',
       count(*)
FROM weather_forecasts WHERE source_api = 'previous_runs' AND issued_at > valid_time
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
