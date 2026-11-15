# nostos database (Tiger Cloud / TimescaleDB)

The database is live and loaded with **fake data** (ERCO, 2026-11-12 01:00Z to 2026-11-14 00:00Z), so you can build against it now.

## Connect
Get the connection string from Person B, never from git (`*.env` is gitignored). Then:
```
pip install "psycopg[binary]" python-dotenv
set TIMESCALE_SERVICE_URL=postgres://...        # or put it in a .env file
python db/run_sql.py -c "SELECT * FROM scoreboard_summary"
```

## What to read
| You are... | Read this |
|---|---|
| **ML** (training features) | `ba_weather_asof(ba, model, from, to, asof)`, `grid_asof(ba, 'D', from, to, asof)`. Always pass the as-of time so you never train on the future. |
| **ML** (writing output) | `INSERT INTO demand_predictions` with your own `model_version`, `issued_at` = when the forecast was made, and `is_backtest` = true for simulated runs. |
| **MCP / app** (current forecast) | `latest_forecast WHERE ba_code = 'ERCO'` |
| **MCP / app** (scoreboard) | `scoreboard_summary` (headline numbers), `scoreboard_daily` (chart), `scoreboard_by_lead` |
| **Backtest results** | `backtest_monthly`, `backtest_by_hour` |
| **Raw data** | `grid_hourly` (EIA D / DF for every BA, region and US48), `grid_subba_hourly` (sub-BA zones), `grid_fuel_hourly`, `weather_forecasts` (NOAA NBM + NWS), `weather_actuals` (NOAA GHCNh + NWS obs), `grid_daily` / `grid_subba_daily` (daily rollups). How they're filled: `ingest/README.md`. |
| **Before the demo** | `data_integrity_checks`: every row must show 0 |

**Scoreboard fairness rule:** a nostos prediction is compared with EIA's day-ahead forecast only if it was issued **before the operating day started** (local midnight). Shorter-lead predictions are still stored, but they appear only in `scoreboard_by_lead`.

## Files (run in order; each is safe to re-run)
| File | What it does |
|---|---|
| `01_schema.sql` | Tables, hypertables, as-of functions, base views, ERCOT seed, `grid_daily` continuous aggregate |
| `02_seed_fake.sql` | Fake data (predictions tagged `model_version = 'fake-v0'`) |
| `03_scoreboard.sql` | Fair head-to-head scoreboard views |
| `04_backtest.sql` | Backtest views and integrity checks |
| `05_api.sql` | Latest-run index and `latest_forecast` view |
| `06_national_noaa.sql` | National EIA (`balancing_authorities.kind`, `grid_subba_hourly`, `eia_subbas`), NOAA weather columns + `extra` JSONB, station ids on `weather_points`, widened integrity checks. Supersedes the Open-Meteo notes in `01_schema.sql`. |
| `99_clear_fake.sql` | Deletes the fake data. **Run it BEFORE the real backfill.** |

`run_sql.py` runs each statement with autocommit, like `psql -f`. Continuous aggregates can't be created inside a transaction.
