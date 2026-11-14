-- ============================================================================
-- nostos: FAKE seed data so the app / ML / MCP work can start before ingest.
--
--   Window : periods 2026-11-12 01:00Z .. 2026-11-14 00:00Z (48 hour-ending hours), BA = ERCO
--   Tags   : predictions use model_version = 'fake-v0'; drivers->>'fake' = 'true'
--   Remove : python db/run_sql.py db/99_clear_fake.sql   (run BEFORE the real backfill)
--
-- Values are deterministic (sin waves, no random()) so re-runs give identical rows,
-- and ON CONFLICT DO NOTHING makes the file safe to re-run.
-- Must run in ONE session (python db/run_sql.py db/02_seed_fake.sql): it uses a temp view.
-- ============================================================================

-- Hourly skeleton: local hour, a smooth "noise" clock, load-weighted temp, actual demand
CREATE TEMP VIEW fake_hours AS
SELECT t                                                            AS period,
       h.lh,
       h.eh,
       w.wtemp,
       43000 + 5000 * sin(2 * pi() * (h.lh - 13) / 24)              -- evening peak, ~4am trough
             + 350 * abs(w.wtemp - 65)                              -- heating/cooling load
             + 700 * sin(h.eh * 0.9)                                AS d_mwh
FROM generate_series('2026-11-12 01:00+00'::timestamptz,
                     '2026-11-14 00:00+00'::timestamptz, INTERVAL '1 hour') AS t
CROSS JOIN LATERAL (
    SELECT extract(hour FROM t AT TIME ZONE 'America/Chicago')::float8 AS lh,
           (extract(epoch FROM t) / 3600)::float8                     AS eh) h
CROSS JOIN LATERAL (
    SELECT 62.86 + 10 * sin(2 * pi() * (h.lh - 9) / 24) + 2 * sin(h.eh * 0.7 + 1.2) AS wtemp) w;


-- ---------------------------------------------------------------------------
-- EIA-930 demand (D) and the BA's day-ahead forecast (DF)
-- ---------------------------------------------------------------------------
INSERT INTO grid_hourly (period, ba_code, series, value_mwh, available_at)
SELECT period, 'ERCO', 'D', round(d_mwh::numeric, 0),
       eia_backfill_available_at('D', period, 'America/Chicago')
FROM fake_hours
ON CONFLICT DO NOTHING;

INSERT INTO grid_hourly (period, ba_code, series, value_mwh, available_at)
SELECT period, 'ERCO', 'DF', round((d_mwh + 600 + 1100 * sin(eh * 0.37))::numeric, 0),
       eia_backfill_available_at('DF', period, 'America/Chicago')
FROM fake_hours
ON CONFLICT DO NOTHING;


-- ---------------------------------------------------------------------------
-- Generation by fuel (gas fills whatever the others don't cover)
-- ---------------------------------------------------------------------------
INSERT INTO grid_fuel_hourly (period, ba_code, fuel, value_mwh, available_at)
SELECT f.period, 'ERCO', x.fuel, round(x.mwh::numeric, 0),
       eia_backfill_available_at('NG', f.period, 'America/Chicago')
FROM (
    SELECT period, d_mwh,
           greatest(0, 14000 * sin(pi() * (lh - 7) / 11))   AS sun,
           11000 + 5000 * sin(eh * 0.25)                    AS wnd,
           4900.0                                           AS nuc,
           7500 + 1000 * sin(2 * pi() * (lh - 13) / 24)     AS col,
           300.0                                            AS oth
    FROM fake_hours) f
CROSS JOIN LATERAL (VALUES
    ('SUN', f.sun), ('WND', f.wnd), ('NUC', f.nuc), ('COL', f.col), ('OTH', f.oth),
    ('NG',  greatest(5000, f.d_mwh - f.sun - f.wnd - f.nuc - f.col - f.oth))
) AS x(fuel, mwh)
ON CONFLICT DO NOTHING;


-- ---------------------------------------------------------------------------
-- Weather forecasts: 1- and 2-day-ahead runs, mimicking the previous_runs backfill
-- ---------------------------------------------------------------------------
INSERT INTO weather_forecasts (valid_time, issued_at, point_id, model, source_api, lead_days,
                               temp_f, dewpoint_f, apparent_temp_f, rh_pct, cloud_cover_pct,
                               wind_10m_mph, wind_100m_mph, shortwave_wm2)
SELECT h.period, h.period - make_interval(days => l), p.point_id, 'best_match', 'previous_runs', l,
       round(x.tmp::numeric, 1),
       round((x.tmp - 12 + 2 * sin(h.eh * 0.2 + p.k))::numeric, 1),
       round((x.tmp - 1.5)::numeric, 1),
       round((60 - 20 * sin(2 * pi() * (h.lh - 9) / 24))::numeric, 0),
       round((40 + 30 * sin(h.eh * 0.2 + p.k))::numeric, 0),
       round(x.wnd::numeric, 1),
       round((x.wnd * 1.6)::numeric, 1),
       round(greatest(0, 600 * sin(pi() * (h.lh - 7) / 11))::numeric, 0)
FROM fake_hours h
CROSS JOIN (VALUES ('dfw', 60.0::float8, 0.0::float8), ('hou', 66, 1), ('sat', 64, 2),
                   ('aus', 62, 3), ('maf', 58, 4)) AS p(point_id, base, k)
CROSS JOIN generate_series(1, 2) AS l
CROSS JOIN LATERAL (
    SELECT p.base + 10 * sin(2 * pi() * (h.lh - 9) / 24) + 2 * sin(h.eh * 0.7 + p.k)
                  + 0.8 * l * sin(h.eh * 1.3 + p.k)              AS tmp,   -- forecast error grows with lead
           10 + 5 * sin(h.eh * 0.3 + p.k) + (p.point_id = 'maf')::int * 6 AS wnd) x
ON CONFLICT DO NOTHING;


-- ---------------------------------------------------------------------------
-- Weather actuals (ERA5-style, lags ~5 days)
-- ---------------------------------------------------------------------------
INSERT INTO weather_actuals (valid_time, point_id, source, temp_f, dewpoint_f, apparent_temp_f,
                             rh_pct, cloud_cover_pct, wind_10m_mph, wind_100m_mph, shortwave_wm2,
                             available_at)
SELECT h.period, p.point_id, 'era5',
       round(x.tmp::numeric, 1),
       round((x.tmp - 12 + 2 * sin(h.eh * 0.2 + p.k))::numeric, 1),
       round((x.tmp - 1.5)::numeric, 1),
       round((60 - 20 * sin(2 * pi() * (h.lh - 9) / 24))::numeric, 0),
       round((40 + 30 * sin(h.eh * 0.2 + p.k))::numeric, 0),
       round(x.wnd::numeric, 1),
       round((x.wnd * 1.6)::numeric, 1),
       round(greatest(0, 600 * sin(pi() * (h.lh - 7) / 11))::numeric, 0),
       h.period + INTERVAL '5 days'
FROM fake_hours h
CROSS JOIN (VALUES ('dfw', 60.0::float8, 0.0::float8), ('hou', 66, 1), ('sat', 64, 2),
                   ('aus', 62, 3), ('maf', 58, 4)) AS p(point_id, base, k)
CROSS JOIN LATERAL (
    SELECT p.base + 10 * sin(2 * pi() * (h.lh - 9) / 24) + 2 * sin(h.eh * 0.7 + p.k) AS tmp,
           10 + 5 * sin(h.eh * 0.3 + p.k) + (p.point_id = 'maf')::int * 6     AS wnd) x
ON CONFLICT DO NOTHING;


-- ---------------------------------------------------------------------------
-- nostos predictions, model_version 'fake-v0'
--   Set A, day-ahead: issued 10:00 local the day BEFORE the operating day.
--          First 24h flagged is_backtest = true, last 24h = live, so both views have rows.
--   Set B, 1h-ahead : issued one hour before valid_time (should NOT count on the
--          fair day-ahead scoreboard; it's here to test that filter).
-- ---------------------------------------------------------------------------
INSERT INTO demand_predictions (valid_time, issued_at, ba_code, model_version, is_backtest,
                                demand_mwh, p10_mwh, p90_mwh, drivers)
SELECT period,
       ((((period - INTERVAL '1 hour') AT TIME ZONE 'America/Chicago')::date - 1) + TIME '10:00')
           AT TIME ZONE 'America/Chicago',
       'ERCO', 'fake-v0',
       period < '2026-11-13 01:00+00',
       round(x.pred::numeric, 0), round((x.pred - 1500)::numeric, 0), round((x.pred + 1500)::numeric, 0),
       jsonb_build_object('fake', true, 'lead', 'day_ahead',
                          'temp_f', round(wtemp::numeric, 1),
                          'cdh_65', round(greatest(wtemp - 65, 0)::numeric, 1),
                          'hdh_65', round(greatest(65 - wtemp, 0)::numeric, 1),
                          'hour_local', lh)
FROM fake_hours
CROSS JOIN LATERAL (SELECT d_mwh - 150 + 750 * sin(eh * 0.53 + 1) AS pred) x
ON CONFLICT DO NOTHING;

INSERT INTO demand_predictions (valid_time, issued_at, ba_code, model_version, is_backtest,
                                demand_mwh, p10_mwh, p90_mwh, drivers)
SELECT period, period - INTERVAL '1 hour', 'ERCO', 'fake-v0', FALSE,
       round(x.pred::numeric, 0), round((x.pred - 600)::numeric, 0), round((x.pred + 600)::numeric, 0),
       jsonb_build_object('fake', true, 'lead', 'hour_ahead',
                          'temp_f', round(wtemp::numeric, 1), 'hour_local', lh)
FROM fake_hours
CROSS JOIN LATERAL (SELECT d_mwh + 250 * sin(eh * 1.1) AS pred) x
ON CONFLICT DO NOTHING;


DROP VIEW fake_hours;

-- Materialize the daily rollup for the fake window
CALL refresh_continuous_aggregate('grid_daily', NULL, NULL);
