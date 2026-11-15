-- Last good response of rate-limited upstream APIs (Open-Meteo grids, NOAA tides, Yahoo futures,
-- TradingView yields), so restarts and quota errors serve saved data instead of refetching.
-- Written and read by world_data.stored(); key = function, arguments and a hash of its code.
CREATE TABLE IF NOT EXISTS api_cache (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS api_cache_fetched_at ON api_cache (fetched_at);
