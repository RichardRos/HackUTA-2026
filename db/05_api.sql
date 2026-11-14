-- ============================================================================
-- nostos: index + views for the MCP server / app.  Safe to re-run.
-- The primary keys already cover grid_asof, weather_forecast_asof and the
-- scoreboard joins; this adds the one access path they don't: "newest run".
-- ============================================================================

CREATE INDEX IF NOT EXISTS demand_predictions_latest_idx
    ON demand_predictions (ba_code, issued_at DESC);

-- The most recent LIVE nostos run per BA and model: every hour it predicted.
-- MCP usage:  SELECT * FROM latest_forecast WHERE ba_code = 'ERCO' ORDER BY valid_time;
CREATE OR REPLACE VIEW latest_forecast AS
SELECT p.ba_code,
       p.model_version,
       p.issued_at,
       p.valid_time,
       p.valid_time - p.issued_at   AS lead,
       p.demand_mwh,
       p.p10_mwh,
       p.p90_mwh,
       df.value_mwh                 AS eia_df_mwh,      -- NULL until EIA posts it
       d.value_mwh                  AS actual_mwh,      -- NULL until the hour is reported
       p.drivers
FROM demand_predictions p
JOIN (SELECT ba_code, model_version, max(issued_at) AS issued_at
      FROM demand_predictions
      WHERE NOT is_backtest
      GROUP BY ba_code, model_version) latest
  ON latest.ba_code = p.ba_code AND latest.model_version = p.model_version
 AND latest.issued_at = p.issued_at
LEFT JOIN grid_hourly df ON df.ba_code = p.ba_code AND df.period = p.valid_time AND df.series = 'DF'
LEFT JOIN grid_hourly d  ON d.ba_code  = p.ba_code AND d.period  = p.valid_time AND d.series  = 'D'
WHERE NOT p.is_backtest;
