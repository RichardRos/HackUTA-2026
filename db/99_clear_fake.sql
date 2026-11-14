-- ============================================================================
-- Remove the fake seed rows from 02_seed_fake.sql.
-- Run BEFORE the real backfill: the grid/weather deletes are by time window, so
-- running this after real data lands in Nov 12-14 2026 would delete real rows too.
-- ============================================================================
DELETE FROM demand_predictions WHERE model_version = 'fake-v0';

DELETE FROM grid_hourly
WHERE ba_code = 'ERCO'
  AND period BETWEEN '2026-11-12 01:00+00' AND '2026-11-14 00:00+00';

DELETE FROM grid_fuel_hourly
WHERE ba_code = 'ERCO'
  AND period BETWEEN '2026-11-12 01:00+00' AND '2026-11-14 00:00+00';

DELETE FROM weather_forecasts
WHERE valid_time BETWEEN '2026-11-12 01:00+00' AND '2026-11-14 00:00+00'
  AND source_api = 'previous_runs' AND model = 'best_match';

DELETE FROM weather_actuals
WHERE valid_time BETWEEN '2026-11-12 01:00+00' AND '2026-11-14 00:00+00'
  AND source = 'era5';

CALL refresh_continuous_aggregate('grid_daily', NULL, NULL);
