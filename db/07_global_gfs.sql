-- ============================================================================
-- nostos schema v4: global weather from NOAA GFS. Run after 06:
--     python db/run_sql.py db/07_global_gfs.sql          (safe to re-run)
--
--   * weather_forecasts.model 'gfs_0p25', source_api 'gfs_grib':
--     NOAA Global Forecast System, 0.25° grid, read by byte range from NOAA's
--     AWS bucket. Extracted at every weather point (US stations and the
--     global city points below). issued_at = when NOAA had posted the last
--     forecast step used from that run.
--   * weather_points can now hold global points: purpose 'global', no BA
--     (ba_code NULL), with an ISO country code.
-- ============================================================================

ALTER TABLE weather_points ALTER COLUMN ba_code DROP NOT NULL;
ALTER TABLE weather_points ADD COLUMN IF NOT EXISTS country TEXT;      -- ISO 3166-1 alpha-2

ALTER TABLE weather_points DROP CONSTRAINT IF EXISTS weather_points_purpose_check;
ALTER TABLE weather_points ADD CONSTRAINT weather_points_purpose_check
    CHECK (purpose IN ('load', 'renewables', 'global'));

-- BA points must still name their BA
ALTER TABLE weather_points DROP CONSTRAINT IF EXISTS weather_points_ba_required;
ALTER TABLE weather_points ADD CONSTRAINT weather_points_ba_required
    CHECK (purpose = 'global' OR ba_code IS NOT NULL);

ALTER TABLE weather_forecasts DROP CONSTRAINT IF EXISTS weather_forecasts_source_api_check;
ALTER TABLE weather_forecasts ADD CONSTRAINT weather_forecasts_source_api_check
    CHECK (source_api IN ('forecast', 'previous_runs', 'nbm_text', 'nws_api', 'gfs_grib'));


-- Latest forecast per point and hour that was public at p_asof, for any points
-- (weather_forecast_asof only works per BA). p_points NULL = all global points.
--   SELECT * FROM point_forecast_asof(NULL, 'gfs_0p25', now(), now() + interval '2 days', now());
--   SELECT * FROM point_forecast_asof(ARRAY['glb_london','glb_paris'], 'gfs_0p25', ...);
CREATE OR REPLACE FUNCTION point_forecast_asof(
    p_points TEXT[], p_model TEXT, p_from TIMESTAMPTZ, p_to TIMESTAMPTZ, p_asof TIMESTAMPTZ)
RETURNS SETOF weather_forecasts
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (f.point_id, f.valid_time) f.*
    FROM weather_forecasts f
    JOIN weather_points w ON w.point_id = f.point_id
    WHERE (CASE WHEN p_points IS NULL THEN w.purpose = 'global' ELSE f.point_id = ANY (p_points) END)
      AND f.model = p_model
      AND f.valid_time >= p_from AND f.valid_time < p_to
      AND f.issued_at <= p_asof
    ORDER BY f.point_id, f.valid_time, f.issued_at DESC;
$$;
