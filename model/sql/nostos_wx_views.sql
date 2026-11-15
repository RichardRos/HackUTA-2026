-- Read-only views for the website, all in schema nostos_wx. Idempotent (CREATE OR REPLACE).
-- Test period = the held-out window 2026-09-14 .. 2026-11-12 (fixed; same as wx_scores.py).
-- Temperatures in °F, dates are NWS climate days, lead 1 = issued the night before.
-- "NBM" columns come in two flavours: nbm_corrected (NBM after our bias correction)
-- and nbm (raw ncep_nbm_conus).

-- Test-period leaderboard by lead and model, re-aggregated exactly from the
-- continuous aggregate leaderboard_daily (sums / counts, not an average of averages).
CREATE OR REPLACE VIEW nostos_wx.leaderboard_test AS
SELECT lead_days,
       model,
       role,
       sum(n)::int                                                            AS days,
       round((sum(sum_abs_err_f) / sum(n))::numeric, 2)::float8               AS mae_f,
       round((sum(sum_err_f) / sum(n))::numeric, 2)::float8                   AS bias_f,
       round((100.0 * sum(n_within_2f) / sum(n))::numeric, 1)::float8         AS within_2f_pct
FROM nostos_wx.leaderboard_daily
WHERE day BETWEEN DATE '2026-09-14' AND DATE '2026-11-12'
GROUP BY lead_days, model, role;

-- Per city: Nostos and NBM mean absolute error at lead 1 over the test period, plus location.
CREATE OR REPLACE VIEW nostos_wx.city_accuracy AS
SELECT l.location,
       l.station_id,
       l.latitude,
       l.longitude,
       count(f.error_f) FILTER (WHERE f.model = 'nostos')::int                                       AS days,
       round(avg(abs(f.error_f)) FILTER (WHERE f.model = 'nostos')::numeric, 2)::float8              AS nostos_mae_f,
       round(avg(abs(f.error_f)) FILTER (WHERE f.model = 'nbm_corrected')::numeric, 2)::float8       AS nbm_corrected_mae_f,
       round(avg(abs(f.error_f)) FILTER (WHERE f.model = 'ncep_nbm_conus')::numeric, 2)::float8      AS nbm_mae_f
FROM nostos_wx.locations l
LEFT JOIN nostos_wx.daily_high_forecasts f
       ON f.location = l.location
      AND f.lead_days = 1
      AND f.model IN ('nostos', 'nbm_corrected', 'ncep_nbm_conus')
      AND f.climate_date BETWEEN DATE '2026-09-14' AND DATE '2026-11-12'
      AND f.error_f IS NOT NULL
GROUP BY l.location, l.station_id, l.latitude, l.longitude;

-- Per city and climate day: observed high vs Nostos vs NBM at lead 1.
-- Starts at the test period and has no end, so days added by the nightly run appear here
-- (observed_f is NULL until the NWS high is published).
CREATE OR REPLACE VIEW nostos_wx.city_timeline AS
SELECT location,
       climate_date,
       max(observed_f)                                        AS observed_f,
       round(max(high_f) FILTER (WHERE model = 'nostos')::numeric, 1)::float8          AS nostos_f,
       round(max(high_f) FILTER (WHERE model = 'nbm_corrected')::numeric, 1)::float8   AS nbm_corrected_f,
       round(max(high_f) FILTER (WHERE model = 'ncep_nbm_conus')::numeric, 1)::float8  AS nbm_f
FROM nostos_wx.daily_high_forecasts
WHERE lead_days = 1
  AND model IN ('nostos', 'nbm_corrected', 'ncep_nbm_conus')
  AND climate_date >= DATE '2026-09-14'
GROUP BY location, climate_date;
