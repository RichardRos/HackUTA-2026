# Internal agentic search service

`search_service.py` is an internal Python data layer, **not an MCP tool or HTTP route**. Gemini uses its catalog to generate one TimescaleDB-compatible `SELECT`; the service validates and executes it against PostgreSQL.

The user-facing MCP server remains limited to fixed, typed queries in `mcp_server.py`.

## Call internally

```python
from search_service import agentic_search

result = agentic_search(
    "Show ERCOT hourly demand and wind generation for the last 24 hours, "
    "and include related natural-gas market probabilities",
    max_rows=100,
)

print(result["explanation"])
print(result["rows"])
```

Environment:

- `DATABASE_URL`, `TIGER_URL`, or `TIMESCALE_SERVICE_URL` for PostgreSQL.
- `GEMINI_API_KEY` for query generation; optional `GEMINI_MODEL` overrides the default model.

No pgvector, embedding table, or additional search database is used.

## Catalog

```python
from search_service import describe_dataset

catalog = describe_dataset()
```

The catalog documents:

- Forecast, accuracy, grid, fuel, weather, market, news, open-observation, and ingest-monitoring storage/read surfaces.
- Concrete table/view column names available to the SQL planner.
- `ba_code`, `point_id`, `valid_time`/`period`, and `feed_id` relationships.
- UTC, hour-ending, operating-day, and publication/as-of semantics.
- Units and market-probability semantics.
- TimescaleDB `time_bucket` and the schema's as-of functions.

Update `DATASET_GUIDE` whenever relations or columns change. It is the contract given to Gemini; an incomplete catalog causes bad query plans even when the SQL itself is valid.

## Generated SQL contract

Gemini returns structured JSON:

```json
{
  "sql": "SELECT period, value_mwh FROM grid_hourly WHERE ba_code = 'ERCO' ORDER BY period DESC LIMIT 24",
  "explanation": "Latest ERCOT demand observations."
}
```

The service returns:

```json
{
  "request": "latest ERCOT demand",
  "sql": "SELECT period, value_mwh FROM grid_hourly WHERE ba_code = 'ERCO' ORDER BY period DESC LIMIT 24",
  "explanation": "Latest ERCOT demand observations.",
  "rows": [],
  "row_count": 0,
  "truncated": false
}
```

`max_rows` defaults to 100 and is capped at 1,000. The generated query is wrapped as:

```sql
SELECT * FROM (<generated SELECT>) AS nostos_search LIMIT %s
```

The cap is parameterized and applies even if Gemini omits its own `LIMIT`. `truncated=true` means the result reached the cap; it does not prove additional rows exist.

## Execution guardrails

- `pglast` parses actual PostgreSQL syntax rather than using regex SQL validation.
- Exactly one top-level `SELECT`/`WITH ... SELECT` is accepted.
- Multiple statements, DML, writes inside CTEs, `SELECT INTO`, `EXPLAIN`, and locking reads are rejected before database execution.
- Every query runs with PostgreSQL `default_transaction_read_only=on` and a 10-second statement timeout.
- Results use the shared JSON serialization for timestamps, decimals, intervals, and JSONB.
- Generated SQL and Gemini errors are internal diagnostics; do not expose them to untrusted users without review.

For deployment, use a dedicated database role with `SELECT` only on the documented Nostos relations and no access to unrelated schemas. Parser checks are defense in depth, not a substitute for database privileges.

## Search guidance

- Use `latest_forecast` for the newest live prediction run and `demand_predictions` for historical runs.
- Use `grid_hourly.period` for the hour being described and `available_at` for publication cutoffs.
- Use `weather_forecast_asof`/`grid_asof` for backtests to avoid training on future information.
- Use `time_bucket` for TimescaleDB time aggregation and `grid_daily` for existing daily rollups.
- Use `normalized_prediction_markets` for market metadata and outcome probabilities; these are not energy futures quotes.
- Join `rss_articles` to `rss_feeds` for `source`; avoid large raw payloads unless requested.
- Query `external_observations` by `source`/`dataset`/`series_id`, using `observed_at` for valid time and `available_at` for knowledge cutoffs.
- Use `ingest_latest_runs` and `ingest_latest_checks` for current feed health, or the history tables for operational investigations.
- Market/news associations with regions or instruments are text-based relevance hints, not enforced foreign keys.

## Verify

```sh
python -m unittest test_search_service.py -v
```

Tests mock Gemini and database execution. They verify Timescale syntax acceptance, rejection of unsafe statements, schema catalog presence, generated-query execution caps, and that generated writes never reach the database.
