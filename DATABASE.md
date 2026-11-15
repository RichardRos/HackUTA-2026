# Database and MCP handoff

PostgreSQL + TimescaleDB storage for ERCOT demand, normalized prediction markets, financial news, and open commodity/weather/shipping observations. `db/03_scoreboard.sql` and `db/04_backtest.sql` depend on earlier migrations.

## Setup

Use an isolated environment; current FastMCP dependencies can conflict with globally installed FastAPI versions.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Set `TIMESCALE_SERVICE_URL` in the root `.env`. `db/run_sql.py:get_url()` also honors `NOSTOS_ENV`. Market ingestion additionally accepts `DATABASE_URL` and `TIGER_URL`.

Apply the real schema:

```powershell
python db/run_sql.py db/01_schema.sql db/03_scoreboard.sql db/04_backtest.sql db/05_api.sql db/06_prediction_markets.sql db/07_rss_feeds.sql db/08_open_observations.sql db/09_ingest_monitoring.sql db/10_news_tags.sql
```

`02_seed_fake.sql` is optional demo data. Do not load it into a production run. `99_clear_fake.sql` deletes that tagged fake dataset; inspect it before running.

## Ingest

```powershell
# Public Kalshi/Polymarket markets; one page per platform
python markets.py scrape --platform both --pages 1

# Ten live WSJ/FT feeds
python news.py --once

# NOAA NWS + FRED + CFTC + NOAA CO-OPS + Shanghai freight indices
python open_feeds.py scrape --source all

# Parse without database writes
python open_feeds.py scrape --source noaa --dry-run
python open_feeds.py scrape --source commodities --dry-run
python open_feeds.py scrape --source shipping --dry-run
```

Current tables are upserted idempotently. Raw source payloads are retained without exposing them through the default MCP market/news reads.

## Refresh supervisor

`ingest_service.py` runs the jobs sequentially with no overlapping workers:

| Job | Default cadence | Timeout |
|---|---:|---:|
| `markets` | 5 minutes | 15 minutes |
| `news` | 15 minutes | 10 minutes |
| `noaa` | 1 hour | 15 minutes |
| `commodities` | 6 hours | 30 minutes |
| `shipping` | 1 hour | 15 minutes |

Workers receive one pinned `TIMESCALE_SERVICE_URL`, so monitoring and ingestion cannot target different databases. Sessions require a direct connection or session-mode pooler for the advisory lock; transaction-mode PgBouncer is unsuitable.

```powershell
python db/run_sql.py db/09_ingest_monitoring.sql
python ingest_service.py start --host 127.0.0.1 --port 8001
```

Failures retry after 60 and 300 seconds. A third failure marks the job `dead` and stops automatic attempts until replay. Each attempt retains its exit code, PID, row count, error, and truncated log in `ingest_runs`. `once --job JOB` runs manually while holding the same singleton lock; use it only when the daemon is stopped.

Replay through a healthy running supervisor:

```powershell
# Local
python ingest_service.py replay markets

# Docker, without starting another long-lived container
docker compose exec ingest python ingest_service.py replay markets
```

Monitoring storage in migration 09:

- `ingest_runs`: every running/succeeded/failed attempt.
- `ingest_dead_letters`: `pending`, `dead`, or `resolved` state per job; HTTP replay is intentionally absent, so the API remains GET-only.
- `ingest_check_results`: source-scoped freshness and data-quality results. Legacy energy-integrity checks are informational and excluded from supervisor health.
- `ingest_service_state`: singleton instance, heartbeat, scheduler tick, and job configuration.
- `ingest_latest_runs`, `ingest_latest_checks`: current status views. Run/check history is retained for 30 days; unresolved dead letters are retained.

Refresh endpoints on loopback port 8001: `/health`, `/jobs`, `/runs?limit=20`, `/checks`, `/dead-letters`. A dead/stale supervisor returns HTTP 503; data-check failures or dead letters return HTTP 200 with `status: degraded` so Docker does not confuse process health with data quality.

## Two-container deployment

`compose.yaml` defines independent services with no bundled database or shared volumes:

| Service | Purpose | Loopback endpoint |
|---|---|---|
| `ingest` | Refresh scheduler and monitor | `http://127.0.0.1:8001` |
| `api` | FastAPI docs + existing FastMCP tools | `http://127.0.0.1:8000` |

Both read `.env`, restart unless stopped, run as non-root, and have health checks. Apply migrations first, then:

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f ingest
```

FastAPI routes: `/docs`, `/mcp`, `/health` (liveness). The supervisor uses port 8001, including real heartbeat/lock health. Compose publishes on loopback only; add authentication before exposing either service publicly.

`Dockerfile` and `requirements.ingest.txt` keep refresh dependencies minimal. `Dockerfile.api` and `requirements.api.txt` isolate FastAPI/FastMCP from both the supervisor and globally installed packages.

## Original energy schema

| Object | Grain / purpose |
|---|---|
| `balancing_authorities` | One EIA balancing authority; `ba_code` such as `ERCO`. |
| `weather_points` | Weighted weather locations belonging to a balancing authority. |
| `weather_forecasts` | Hypertable keyed by point/model/valid time/issued time. |
| `weather_actuals` | Hypertable of observed weather by point/source/valid time. |
| `grid_hourly` | EIA demand (`D`), day-ahead forecast (`DF`), net generation (`NG`), interchange (`TI`). |
| `grid_fuel_hourly` | Hourly EIA generation by BA and fuel. |
| `demand_predictions` | Nostos demand predictions, intervals, model version, and JSON drivers. |
| `grid_daily` | Continuous daily peak/total grid aggregate. |

Main read surfaces: `latest_forecast`, `scoreboard_summary`, `scoreboard_daily`, `scoreboard_by_lead`, `backtest_monthly`, `backtest_by_hour`, `ba_weather_actuals`, `prediction_scores`, and `data_integrity_checks`. As-of SQL functions are `weather_forecast_asof`, `ba_weather_asof`, and `grid_asof`.

## Prediction markets

### Tables

- `prediction_markets`: one current row per `(platform, market_id)`. Includes raw and normalized questions, `normalization_method` (`regex` or `gemini`), status, close time, rules, full raw JSON, scrape time, and normalization time.
- `prediction_market_outcomes`: one row per market/outcome index. Outcome labels are normalized independently; `price` is normalized to `[0,1]` or `NULL`.

### Serving view

`normalized_prediction_markets` contains one market row with `outcomes` as an ordered JSONB array:

```json
[{"raw": "Yes", "normalized": "yes", "price": 0.42}]
```

```sql
SELECT *
FROM normalized_prediction_markets
WHERE platform = %(platform)s
  AND question_normalized ILIKE %(query)s
ORDER BY scraped_at DESC
LIMIT %(limit)s;
```

Normalization removes punctuation differences and selected stop words, preserves negation and directional/deadline terms, and uses Gemini only when regex yields fewer than two tokens. Exact normalized questions can join platforms, but no fuzzy/semantic cross-platform match table exists.

Standalone HTTP alternative:

```powershell
python markets.py serve --host 127.0.0.1 --port 8000
# GET /health
# GET /markets?platform=kalshi&q=temperature&limit=100
```

## Financial news

- `rss_feeds`: configured WSJ/FT feed, title, latest raw XML, and fetch time.
- `rss_articles`: normalized article rows keyed by a stable source+GUID/link SHA-256. Includes canonical link, title, plain-text summary, author, publish/update times, raw item JSON, fetch time, and `tags` (feed sections an article appeared in plus keyword topics from `news.py`, GIN-indexed).

```sql
SELECT a.id, f.source, a.link, a.title, a.summary, a.author,
       a.published_at, a.updated_at, a.fetched_at
FROM rss_articles a
JOIN rss_feeds f ON f.id = a.feed_id
WHERE a.published_at >= now() - (%(hours)s * interval '1 hour')
  AND (%(source)s IS NULL OR f.source = %(source)s)
ORDER BY a.published_at DESC NULLS LAST
LIMIT %(limit)s;
```

Only public RSS metadata/standfirst is collected, not paywalled article HTML. Duplicate articles across feeds from the same publisher collapse to the same article ID.

## Open observations

`external_observations` is a Timescale hypertable with this key:

```text
(source, dataset, series_id, observed_at)
```

Columns:

| Column | Meaning |
|---|---|
| `observed_at` | Time the value describes: forecast hour, price date, CFTC report date, observation/publication date. |
| `available_at` | Conservative known/public time. NWS uses its update time; unrevisioned feeds use first fetch time. A changed value advances availability. |
| `value` | Numeric observation. |
| `unit` | Unit when the source defines one; `NULL` means unavailable or lane-specific. |
| `dimensions` | Small JSON metadata: anchor/grid, contract, station, or shipping lane. |
| `raw_data` | Source record used to build the row. |
| `fetched_at` | Latest successful fetch/upsert time. |

Current sources:

| `source` | `dataset` | Examples of `series_id` |
|---|---|---|
| `noaa_nws` | `nws_hourly_forecast` | `cushing_ok.temperature`, `henry_hub_la.wind_speed` |
| `fred` | `commodity_price` | `DCOILWTICO`, `DHHNGSP`, `PCOPPUSDM` |
| `cftc` | `cot_disaggregated` | `CORN.open_interest_all`, `WHEAT-HRW.m_money_positions_long_all` |
| `noaa_coops` | `{station}.{product}` | `9414290.v`, `9414290.s`, `9414290.g` |
| `sse` | `container_freight_index` | `scfi.COMPOSITE INDEX`, `ccfi.EUROPE SERVICE` |

Default FRED coverage: WTI, Brent, Henry Hub, copper, wheat, and corn. NWS anchors: Cushing, Henry Hub, Iowa corn belt, Permian, and Haynesville. Shipping freight values are internal features, not licensed redistributable index products.

### MCP query patterns

Latest value per series:

```sql
SELECT DISTINCT ON (source, dataset, series_id)
       source, dataset, series_id, observed_at, available_at,
       value, unit, dimensions
FROM external_observations
WHERE available_at <= coalesce(%(as_of)s, now())
ORDER BY source, dataset, series_id, observed_at DESC;
```

Time-series query:

```sql
SELECT source, dataset, series_id, observed_at, available_at,
       value, unit, dimensions
FROM external_observations
WHERE series_id = %(series_id)s
  AND observed_at >= %(start)s
  AND observed_at < %(end)s
  AND available_at <= coalesce(%(as_of)s, now())
ORDER BY observed_at
LIMIT %(limit)s;
```

`available_at` is conservative for historical FRED/CFTC/Shanghai backfills and is not a replacement for true vendor revision/vintage timestamps. Use FRED ALFRED or equivalent licensed vintages for strict historical point-in-time backtests.

## MCP integration

Existing `mcp_server.py` exposes forecast, grid, weather, market, and news tools; `docs/API.md` documents them. For the new external observations:

1. Add an `observations` source to `Source` and `SearchFilters`.
2. Add a focused read-only tool using the parameterized time-series query above.
3. Validate timezone-aware `start < end`, cap `limit`, and whitelist any text fields used in SQL identifiers.
4. Keep `available_at <= coalesce(%s, now())` for backtest-safe reads.
5. Return normalized rows by default; make `raw_data` an explicit opt-in because it can be large.
6. Add observations to `search_data`/`get_region_context` only after the focused tool is tested.

FastAPI/FastMCP startup:

```powershell
uvicorn api:app --host 127.0.0.1 --port 8000
# Docs: http://127.0.0.1:8000/docs
# MCP: http://127.0.0.1:8000/mcp
# Liveness: http://127.0.0.1:8000/health
```

For a local MCP subprocess instead, run `python mcp_server.py --transport stdio`.

Database sessions in `mcp_server.py` enforce read-only transactions and a 10-second statement timeout. Keep credentials server-side; no authentication is configured for public deployment.

## Configuration and source access

| Variable | Requirement |
|---|---|
| `TIMESCALE_SERVICE_URL` | Required for migrations and ingestion. |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Market fallback and MCP semantic search; defaults to `gemini-2.5-flash`. |
| `NOAA_USER_AGENT` | Set an identifying app name and contact email for NWS requests. |
| XAI/Twitter recency | Owned by Bryson; this repo does not include XAI credentials or ingestion. |
| `AISSTREAM_API_KEY` | Optional live AIS WebSocket; free registration at https://aisstream.io/account. Historical NOAA AIS is not auto-downloaded because national files are very large. |
| `EIA_API_KEY` | Optional detailed EIA API ingestion; register at https://www.eia.gov/opendata/register.php. Existing schema already has EIA grid tables, but no new keyed ingest is added here. |
| `FRED_API_KEY` | Optional JSON/metadata/ALFRED APIs; the implemented CSV ingest needs no key. |
| `USDA_NASS_API_KEY`, `USDA_FAS_API_KEY` | Optional keyed USDA APIs; no USDA ingest is implemented yet. |

Kalshi, Polymarket, WSJ/FT RSS, NWS, CO-OPS, FRED CSV, CFTC Socrata, and Shanghai public index reads need no API keys. Kalshi/Polymarket credentials are unnecessary for public discovery.

## Verification

```powershell
python -m unittest discover -v
```

Last local run: **71 tests passed** in `.venv`, including FastMCP and supervisor tests. Migrations 06–09 pass PostgreSQL grammar parsing and `run_sql.split_sql`; Compose YAML assertions validate exactly two loopback services. Live parse-only checks passed for both market platforms, all 10 RSS feeds, NOAA (3,900 rows), commodities (6,914 rows), and shipping (33 rows); FastAPI `/docs`, `/health`, and MCP initialization passed live HTTP checks. Counts change over time. Actual DB migration/upserts still require the user's `TIMESCALE_SERVICE_URL`. Docker is not installed on this development host, so both image builds remain unverified.
