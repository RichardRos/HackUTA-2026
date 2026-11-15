# Nostos FastMCP API

Read-only access to energy forecasts, model accuracy, EIA grid data, weather, prediction markets, and financial news. The API reads PostgreSQL/TimescaleDB; ingestion, model training, and trade execution stay outside this service.

## Run

Requires Python 3.11+.

```sh
pip install -r requirements.txt
# Set DATABASE_URL, TIGER_URL, or TIMESCALE_SERVICE_URL in .env.
python mcp_server.py --host 127.0.0.1 --port 8000
```

- MCP Streamable HTTP: `http://127.0.0.1:8000/mcp`
- Liveness probe: `GET http://127.0.0.1:8000/health`
- Local MCP subprocess: `python mcp_server.py --transport stdio`
- ASGI server: `uvicorn mcp_server:app --host 127.0.0.1 --port 8000`

`GET /health` only proves the HTTP process is up. Use `get_data_integrity` to inspect the data.

### Mount beside FastAPI

```python
from fastapi import FastAPI
from mcp_server import app as mcp_app

api = FastAPI(lifespan=mcp_app.lifespan)  # Required by FastMCP.
api.mount("/analytics", mcp_app)
```

This exposes MCP at `/analytics/mcp` and health at `/analytics/health`. FastMCP 4.2 requires Starlette 1.x, so use a FastAPI release compatible with that Starlette version.

## Call from an MCP client

MCP tools are not individual REST routes. Clients initialize an MCP session, discover tools with `tools/list`, and invoke them with `tools/call` at `/mcp`.

```python
from fastmcp import Client

async with Client("http://127.0.0.1:8000/mcp") as client:
    tools = await client.list_tools()
    result = await client.call_tool(
        "get_region_context",
        {"region": "ERCO", "query": "Texas", "hours": 24},
    )
    snapshot = result.data
```

A tool call's JSON-RPC payload has this shape (the client normally handles initialization, session headers, and response parsing):

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "get_latest_forecast",
    "arguments": {"ba_code": "ERCO", "limit": 48}
  }
}
```

For a web app, call MCP from the backend rather than exposing database credentials or an unauthenticated MCP port to the browser. The default bind is loopback and no authentication is configured; add authentication and proxy/host rules before public deployment.

## Conventions

- All tool calls are read-only. PostgreSQL sessions also enforce `default_transaction_read_only=on` and a 10-second statement timeout.
- Timestamp arguments are ISO 8601 with a timezone, preferably UTC (`2026-11-12T00:00:00Z`). Naive timestamps are rejected.
- Time ranges are `[start, end)`: inclusive start, exclusive end, with `start < end`.
- Collection tools return JSON arrays. Empty results return `[]`, not an error.
- `limit` defaults and maximums are listed below; the absolute cap is 5,000 rows. Responses do not include a next-page cursor.
- `null` means unavailable/not yet published, not zero.
- Database values are serialized as JSON numbers, strings, booleans, arrays/objects, or null. PostgreSQL intervals become strings.
- EIA `period`, prediction `valid_time`, and weather `valid_time` are hour-ending/instant timestamps as defined in `db/01_schema.sql`.
- `as_of` means "only data public at this instant." Omit it for data available now. Use it for backtests to prevent lookahead.
- Errors are MCP tool errors with a message. Invalid literals, ranges, limits, and timestamps are input errors; connection/query failures are server errors.

## Search across the dataset

### `search_data`

Searches multiple sources with one structured `filters` object. Use this for instrument pages, faceted search, and dashboard filters.

Instrument-only example:

```json
{
  "filters": {
    "instrument": "kalshi:TEX-HEAT",
    "query": "temperature",
    "market_platform": "kalshi",
    "news_source": "wsj",
    "limit": 25
  }
}
```

Region/time/series example:

```json
{
  "filters": {
    "region": "ERCO",
    "sources": ["grid", "fuel", "weather", "markets", "news"],
    "series": ["D", "DF"],
    "fuels": ["NG", "WND", "SUN"],
    "start": "2026-11-12T00:00:00Z",
    "end": "2026-11-13T00:00:00Z",
    "as_of": "2026-11-12T12:00:00Z",
    "query": "natural gas",
    "limit": 100
  }
}
```

All filters are optional except the containing `filters` object:

| Filter | Type | Behavior |
|---|---|---|
| `query` | string ≤200 | Any normalized keyword over market questions and news titles/summaries; news also matches inferred topic tags. |
| `instrument` | string ≤200 | Matches market ID, event ID, slug, `platform:market_id`, normalized question, and news text. Unqualified names/fragments work. |
| `region` | string | EIA balancing-authority code. Required when explicitly requesting forecast, accuracy, grid, fuel, or weather. |
| `sources` | array | Any of `forecast`, `accuracy`, `grid`, `fuel`, `weather`, `markets`, `news`. Defaults to all seven with a region, otherwise markets+news. |
| `start`, `end` | timestamps | Explicit valid/published/market-close range, depending on source. If omitted for grid/fuel/weather, defaults to a 24-hour window. |
| `as_of` | timestamp | Knowledge cutoff for grid, fuel, and weather; prevents lookahead. |
| `series` | array | Any of `D`, `DF`, `NG`, `TI`; defaults to all grid series. |
| `fuels` | array of strings | EIA fuel codes; defaults to all fuels. |
| `weather_model` | string | Exact weather model; otherwise selects the most recently issued model visible at `as_of`. |
| `model_version` | string | Exact Nostos model for forecast/accuracy. |
| `is_backtest` | boolean | Live/backtest filter for accuracy. Latest forecast remains live-only. |
| `market_platform` | `kalshi \| polymarket` | Exact market platform. |
| `market_status` | string | Exact source-specific market status. |
| `market_category` | string | Exact source-specific category. |
| `news_source` | `wsj \| ft` | Exact news source. |
| `limit` | integer, 1–5000 | Maximum rows **per source**, default `100`. |

Time behavior:

- With no explicit range, grid/fuel/weather use the trailing 24 hours. If only `start` is supplied, `end = start + 24 hours`; if only `end` is supplied, `start = end - 24 hours`.
- Explicit market times filter `close_time`; explicit news times filter `published_at`.
- Forecast and accuracy are current summary surfaces and do not use `start`/`end`.
- Text wildcards supplied by callers are escaped and matched literally.

Response shape:

```json
{
  "filters": {"instrument": "kalshi:TEX-HEAT", "limit": 25},
  "results": {
    "markets": [],
    "news": []
  }
}
```

Only requested/defaulted sources appear under `results`. Source failures are isolated as `{"error": "..."}` within the affected section.

### `semantic_search`

Converts a natural-language request into `SearchFilters`, validates them, and runs `search_data`. It supports general dataset interaction without allowing an LLM to write SQL.

```json
{"request": "Show ERCOT demand and wind generation yesterday, plus related natural-gas news", "limit": 25}
```

| Parameter | Type | Default |
|---|---|---|
| `request` | string, 1–500 chars | Required natural-language request. |
| `limit` | integer, 1–5000 | `25`; overrides any model-suggested limit. |

Response adds `interpretation` to the normal `search_data` response:

```json
{
  "interpretation": "gemini",
  "filters": {
    "region": "ERCO",
    "sources": ["grid", "fuel", "news"],
    "series": ["D"],
    "fuels": ["WND"],
    "query": "natural gas",
    "limit": 25
  },
  "results": {"grid": [], "fuel": [], "news": []}
}
```

- `interpretation: "gemini"` means Gemini interpreted the request using the constrained JSON schema.
- `interpretation: "keyword_fallback"` means Gemini was unavailable or returned invalid filters; the request became a transparent keyword search over markets+news.
- `GEMINI_API_KEY` and optional `GEMINI_MODEL` configure interpretation. The fallback does not require a key.
- This is **natural-language-to-filter search**, not embedding/vector similarity. Add pgvector and an embedding/backfill pipeline later if semantic similarity over article/rule text becomes necessary.

REST `GET /api/search` runs `semantic_search` when `q` is set and a filter-only `search_data` otherwise. Both responses add a top-level `summary`, built by `summarize_search_results` from at most 8 linked news rows and 6 linked market rows:

```json
{"summary": {
  "text": "One plain factual summary, about 100 words.",
  "sources": [{"id": 1, "kind": "news", "title": "Gas prices rise",
               "url": "https://example.com/gas", "source": "ft"}]
}}
```

`summary` is `null` when no returned row has both a title and a URL (Gemini is not called), and `{"error": "AI summary is unavailable right now."}` when Gemini or citation validation fails; the search results are unaffected either way. Titles and URLs always come from the returned rows, never from the model, and only cited rows are listed.

## One-call region snapshot

### `get_region_context`

The easiest starting point for a dashboard or agent. It bundles the region's current forecast, model accuracy, recent grid and fuel data, upcoming weather, prediction-market prices, and related news.

```json
{"region": "ERCO", "query": "Texas", "hours": 24}
```

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `region` | string | `ERCO` | Balancing-authority/region code. |
| `query` | string up to 200 chars \| null | `null` | Optional shared news/market topic, such as `Texas` or `natural gas`; omit for the latest unfiltered items. |
| `hours` | integer, 1–168 | `24` | Lookback for grid/news and lookahead for weather. |

Response:

| Field | Contents |
|---|---|
| `region`, `query`, `generated_at`, `lookback_hours` | Snapshot metadata. |
| `forecast` | Up to 500 rows from `get_latest_forecast`. |
| `model_accuracy` | Up to 20 live rows from `get_scoreboard_summary`. |
| `grid` | Up to 500 recent rows across all EIA series, newest first. |
| `fuel_mix` | Up to 500 recent generation-by-fuel rows. |
| `weather` | `{model, hours}`; automatically selects the most recently issued weather model and returns up to 500 upcoming point-level rows. `model` is null when no forecast exists. |
| `market_prices` | Up to 20 normalized Kalshi/Polymarket rows, including outcome prices. These are prediction-market probabilities, not energy futures quotes. |
| `news` | Up to 20 matching WSJ/FT articles from the lookback window. |

Sources fail independently: an unavailable optional source returns `{"error": "..."}` in its section instead of discarding the other sections. The snapshot uses fixed per-source caps and has no pagination; use the focused tools below for larger time ranges or chart-specific queries.

## Common filters

| Parameter | Type | Meaning |
|---|---|---|
| `ba_code` | string | EIA balancing-authority code; currently seeded with `ERCO`. |
| `model_version` | string | Exact prediction model version. Omit to include all versions where supported. |
| `is_backtest` | boolean | `false` for live predictions, `true` for simulated runs; omit for both. |
| `series` | `D \| DF \| NG \| TI` | Demand, EIA day-ahead forecast, net generation, or total interchange. |
| `fuel` | string | EIA fuel code such as `NG`, `COL`, `NUC`, `OIL`, `WAT`, `SUN`, `WND`, or `OTH`. |
| `as_of` | timestamp | Knowledge cutoff; only rows with `issued_at`/`available_at <= as_of` are visible. |

## Forecast and accuracy tools

### `get_latest_forecast`

Returns every predicted hour from the newest **live** run for each matching BA/model.

| Parameter | Type | Default |
|---|---|---|
| `ba_code` | string | `ERCO` |
| `model_version` | string \| null | `null` |
| `limit` | integer, 1–5000 | `1000` |

Example:

```json
{"ba_code": "ERCO", "model_version": "v1", "limit": 48}
```

Row fields: `ba_code`, `model_version`, `issued_at`, `valid_time`, `lead`, `demand_mwh`, `p10_mwh`, `p90_mwh`, `eia_df_mwh`, `actual_mwh`, `drivers` (JSON object/null). EIA and actual values are null until published.

### `get_scoreboard_summary`

Headline accuracy for fair day-ahead comparisons.

| Parameter | Type | Default |
|---|---|---|
| `ba_code` | string \| null | `null` |
| `model_version` | string \| null | `null` |
| `is_backtest` | boolean \| null | `null` |
| `limit` | integer, 1–5000 | `100` |

Row fields: `ba_code`, `model_version`, `is_backtest`, `hours_scored`, `nostos_mae_mwh`, `eia_mae_mwh`, `nostos_mape_pct`, `eia_mape_pct`, `nostos_rmse_mwh`, `eia_rmse_mwh`, `nostos_bias_mwh`, `eia_bias_mwh`, `nostos_win_rate_pct`, `skill_vs_eia`, `first_hour`, `last_hour`.

`skill_vs_eia > 0` means Nostos has lower MAE; `0.25` means 25% lower. MAPE and win rate are percentages, not fractions.

### `get_scoreboard_daily`

Daily chart series with the same filters as `get_scoreboard_summary`; `limit` defaults to `365`.

Additional grain/fields: `operating_day`, `hours_scored`, Nostos/EIA MAE and MAPE, `nostos_win_rate_pct`, `actual_peak_mwh`, `nostos_peak_mwh`, `eia_peak_mwh`, plus `ba_code`, `model_version`, and `is_backtest`.

### `get_scoreboard_by_lead`

Accuracy grouped by forecast lead, with the same filters as `get_scoreboard_summary`; `limit` defaults to `100`.

Row fields: `ba_code`, `model_version`, `is_backtest`, `lead_bucket`, `predictions`, `nostos_mae_mwh`, `nostos_mape_pct`, `eia_mae_same_hours_mwh`. Buckets are `1: <6h`, `2: 6-24h`, `3: 24-48h`, and `4: 48h+`.

The summary/daily views include only predictions issued before local midnight of the operating day. The lead view includes shorter-lead predictions and is not always a like-for-like EIA comparison.

### `get_backtest_monthly`

| Parameter | Type | Default |
|---|---|---|
| `ba_code` | string \| null | `null` |
| `model_version` | string \| null | `null` |
| `limit` | integer, 1–5000 | `120` |

Row fields: `ba_code`, `model_version`, `month`, `hours_scored`, `nostos_mae_mwh`, `eia_mae_mwh`, `nostos_mape_pct`, `eia_mape_pct`, `nostos_win_rate_pct`, `skill_vs_eia`, `p10_p90_coverage_pct`. Well-calibrated 10th–90th percentile bands should have coverage near 80%.

### `get_backtest_by_hour`

Same parameters as `get_backtest_monthly`; `limit` defaults to `1000`.

Row fields: `ba_code`, `model_version`, `hour_starting_local` (0–23), `hours_scored`, `nostos_mae_mwh`, `eia_mae_mwh`, `nostos_win_rate_pct`.

## Grid and weather tools

### `get_grid_hourly`

```json
{
  "ba_code": "ERCO",
  "series": "D",
  "start": "2026-11-12T00:00:00Z",
  "end": "2026-11-13T00:00:00Z",
  "as_of": "2026-11-12T12:00:00Z",
  "limit": 1000
}
```

`ba_code`, `series`, `start`, and `end` are required. `as_of` is optional; `limit` defaults to `5000`.

Row fields: `period`, `ba_code`, `series`, `value_mwh`, `available_at`, `first_seen_at`, `updated_at`.

### `get_fuel_mix`

Required: `ba_code`, `start`, `end`. Optional: `fuel`, `as_of`, `limit=5000`.

Row fields: `period`, `ba_code`, `fuel`, `value_mwh`, `available_at`, `first_seen_at`, `updated_at`.

### `get_weather_forecast`

Returns point-level forecasts selected by `weather_forecast_asof`, including the latest issue for each point/hour visible at the cutoff.

```json
{
  "ba_code": "ERCO",
  "model": "gfs_seamless",
  "start": "2026-11-14T00:00:00Z",
  "end": "2026-11-15T00:00:00Z",
  "limit": 1000
}
```

Required: `ba_code`, `model`, `start`, `end`. Optional: `as_of`, `limit=5000`.

Row fields: `ba_code`, `name`, `weight`, `purpose`, `point_id`, `valid_time`, `issued_at`, `temp_f`, `dewpoint_f`, `apparent_temp_f`, `rh_pct`, `cloud_cover_pct`, `wind_10m_mph`, `wind_100m_mph`, `shortwave_wm2`.

### `get_weather_actuals`

Required: `ba_code`, `source` (`era5` or `om_analysis`), `start`, `end`. Optional: `limit=5000`.

Row fields: `ba_code`, `source`, `valid_time`, `temp_f`, `dewpoint_f`, `cdh_65`, `hdh_65`, `points_used`. Values are load-weighted across available load-purpose points. Degree-hours use a 65°F base.

### `get_global_weather_now`

```json
{"country": "JP"}
```

One row per world city (`weather_points.purpose = 'global'`) with the next-hour NOAA GFS 0.25° forecast. `country` (ISO 3166-1 alpha-2) is optional; `as_of` gives the forecast that was public at that instant. Row fields: `point_id`, `name`, `country`, `latitude`, `longitude`, `valid_time`, `issued_at`, `temp_f`, `apparent_temp_f`, `dewpoint_f`, `rh_pct`, `cloud_cover_pct`, `wind_10m_mph`, `wind_gust_mph`, `wind_dir_deg`, `precip_in`, `snow_in`, `shortwave_wm2`. REST: `GET /api/weather/global?country=JP`.

### `get_global_weather_forecast`

```json
{"point_id": "glb_london", "hours": 72}
```

Hourly GFS series (same fields) for one `point_id` (a world city like `glb_london`, or a US station, since GFS covers both) or every world city in one `country`. `hours` 1–384, default 72; `limit` default 2000. REST: `GET /api/weather/global/forecast?point_id=glb_london&hours=72`.

GFS runs at 00/06/12/18Z and is ingested by `ingest/update.py --only gfs` (hourly `grid_weather` job in the ingest container). Points are listed in `ingest/global_points.py`.

## Nostos weather model (daily highs)

Reads Tiger schema `nostos_wx` (`locations`, `daily_high_obs`, `daily_high_forecasts`, `leaderboard_daily`) via `wx_scores.py`. Temperatures °F; dates are NWS climate days; lead 1 = issued the night before. `start`/`end` are dates (default: held-out test window 2026-09-14 → 2026-11-12); `lead_days` is 1–3. Check: lead 1 should give nostos 1.57, nbm_corrected 1.92, ncep_nbm_conus 2.31.

| Dashboard section | REST | MCP tool | Rows |
|---|---|---|---|
| "The Bow" (leaderboard) | `GET /api/wx/leaderboard?lead_days=1` | `get_wx_leaderboard` | `lead_days, model, role, days, mae_f, bias_f, within_2f_pct`, best first |
| "The Voyage" (error vs lead) | `GET /api/wx/leaderboard` (no `lead_days`) | same | all three leads |
| Trend over time | `GET /api/wx/trend?lead_days=1&model=nostos&model=nbm_corrected` | `get_wx_weekly_trend` | `week, model, role, days, mae_f` |
| "Ithaca" (one city) | `GET /api/wx/cities/austin?lead_days=1` | `get_wx_city` | `climate_date, observed_f, nostos_f, nbm_corrected_f, nbm_f` |
| Map | `GET /api/wx/cities?model=nostos` | `get_wx_cities` | `location, station_id, latitude, longitude, kalshi_series, days, mae_f, bias_f` |

`role` is `nostos`, `benchmark` (NBM), `input` (raw NWP models) or `baseline` (persistence).

## Flight tracker

### `get_flights_near`

```json
{"lat": 35.04, "lon": -89.98, "radius_nm": 150, "cargo_only": true}
```

Live aircraft from [adsb.lol](https://api.adsb.lol) (free, no key, ODbL open data), cached 10 s per point. `radius_nm` 1–250. Not stored in the database. Row fields: `hex`, `callsign`, `registration`, `aircraft_type`, `lat`, `lon`, `altitude_ft` (null on the ground), `on_ground`, `ground_speed_kt`, `track_deg`, `vertical_rate_fpm`, `squawk`, `emergency`, `cargo_operator`, `seen_seconds_ago`. ADS-B has no cargo field: `cargo_operator` is set when the callsign's ICAO airline prefix is a freight airline in `flights.CARGO_OPERATORS` (FedEx, UPS, Atlas, Kalitta, Cargolux, DHL...). Mixed passenger/cargo airlines are never flagged. Upstream failures return a tool error (REST: 502). REST: `GET /api/flights?lat=35.04&lon=-89.98&radius_nm=150&cargo=true`.

### `get_flights_global`

```json
{"cargo_only": true}
```

Every tracked aircraft worldwide from the [OpenSky Network](https://opensky-network.org) (`/states/all`), as one shared server-side snapshot: refreshed every 90 s when `OPENSKY_CLIENT_ID`/`OPENSKY_CLIENT_SECRET` are set (free account, 4,000 credits/day, 4 per global call), every 15 min anonymously (400/day). If a refresh fails (e.g. credits used up), the last snapshot is served with `stale_error` set. Response: `{snapshot_time, credits_left, refresh_seconds, authenticated, stale_error, count, aircraft: [...]}`; rows have the `get_flights_near` fields plus `origin_country` (`registration` and `aircraft_type` are null; OpenSky doesn't send them). REST: `GET /api/flights/global?cargo=true`.

## Market and news tools

### `search_prediction_markets`

```json
{"platform": "kalshi", "q": "temperature", "status": "open", "limit": 25}
```

| Parameter | Type | Default |
|---|---|---|
| `platform` | `kalshi \| polymarket` \| null | `null` |
| `q` | string up to 200 chars \| null | `null` |
| `status` | string \| null | `null` |
| `limit` | integer, 1–5000 | `100` |

`q` is a case-insensitive substring search over the normalized question; `%`, `_`, and backslashes are treated literally.

Row fields: `platform`, `market_id`, `event_id`, `slug`, `url`, `category`, `question_raw`, `question_normalized`, `normalization_method`, `status`, `close_time`, `rules`, `scraped_at`, `normalized_at`, `outcomes`.

Each outcome is `{raw, normalized, price}`; `price` is a probability in `[0,1]` or null. Raw source payloads are intentionally omitted.

### `get_news`

```json
{"source": "wsj", "q": "natural gas", "tag": "power-grid", "hours": 72, "limit": 20}
```

| Parameter | Type | Default |
|---|---|---|
| `source` | `wsj \| ft` \| null | `null` |
| `q` | string up to 200 chars \| null | `null` |
| `tag` | string up to 50 chars \| null | `null` |
| `hours` | integer, 1–8760 | `168` |
| `limit` | integer, 1–5000 | `100` |

Returns articles published within the trailing `hours` window. `q` searches title and summary; `tag` keeps articles carrying that tag. Row fields: `id`, `source`, `guid`, `link`, `title`, `summary`, `author`, `published_at`, `updated_at`, `fetched_at`, `tags`. Tags are feed sections (`world`, `markets`, `business`, `economy`, `top-stories`, `commodities`, `energy`, `climate`) plus keyword topics (`oil`, `natural-gas`, `power-grid`, `renewables`, `nuclear`, `coal`, `weather`, `agriculture`, `metals`, `shipping`, `rates`, `inflation`, `stocks`, `crypto`, `texas`, `geopolitics`); see `TOPICS` in `news.py`. Raw feed XML and raw article payloads are omitted.

## Health tool

### `get_data_integrity`

No arguments. Returns:

```json
{
  "healthy": false,
  "checks": [
    {"check_name": "missing hours of actual demand (D) inside loaded range", "violations": 2}
  ]
}
```

`healthy` is true only when every check has zero violations. This does not replace `/health`: one checks data invariants, the other checks process liveness.

## Add a source or analytic

1. Ingest into a table with valid time and known/publication time. Keep timestamps as UTC `TIMESTAMPTZ`.
2. Expose a stable SQL view or function with explicit columns and units. Use as-of filtering for anything used in backtests.
3. Add one typed, bounded `@mcp.tool` in `mcp_server.py`; reuse `_query` so read-only enforcement and JSON serialization remain consistent. Do not expose arbitrary SQL.
4. Add an in-memory client test with `_query` mocked, then document arguments, defaults, fields, and units here.

Potential NOAA, Twitter/Grok, additional exchanges, and model-compute outputs can follow that path without changing existing tool contracts. Add trade execution as a separately authenticated service, not as another read-only tool.

## Internal generated-SQL search

Gemini-generated TimescaleDB queries are deliberately **not MCP tools**. The separate internal service and its dataset catalog are documented in [SEARCH_SERVICE.md](SEARCH_SERVICE.md).

## Verify

```sh
python -m unittest discover -s tests -p test_mcp_server.py -v
```

Tests require dependencies but no database. For a live check, configure a DSN and run `get_data_integrity` plus representative time-bounded calls. The SQL views must first be installed with `db/run_sql.py` in the order documented in `db/README.md`.
