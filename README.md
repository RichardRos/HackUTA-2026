# HackUTA-2026

## Setup
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Set `TIMESCALE_SERVICE_URL` in `.env`. `GEMINI_API_KEY` is used only when market regex normalization needs a fallback; Gemini model defaults to `gemini-2.5-flash`.

## Tests
```sh
python -m unittest discover -s tests
```

## Prediction markets
```sh
python db/run_sql.py db/08_prediction_markets.sql
python markets.py scrape --platform both --pages 1
python markets.py serve
```
`GET /markets` accepts `platform=kalshi|polymarket`, `q`, and `limit`. `/health` checks the server.

## News feeds (WSJ + FT)
```sh
python db/run_sql.py db/09_rss_feeds.sql
python news.py --once
```
Use `--source wsj|ft|all` or repeat `--feed URL`. Saves latest raw XML and normalized article rows.

## Open commodity, weather, and shipping feeds
```sh
python db/run_sql.py db/10_open_observations.sql
python open_feeds.py scrape --source all
```
NOAA NWS/CO-OPS, FRED, CFTC, and Shanghai freight indices require no keys. Use `--dry-run` to parse without writing.

See [DATABASE.md](DATABASE.md) for schema details, source coverage, optional feeds, and MCP queries.

## Docker services
Apply the migrations in [DATABASE.md](DATABASE.md), then:
```sh
docker compose up -d --build
docker compose ps
```
- FastAPI + MCP: `http://127.0.0.1:8000/docs`, `/mcp`, `/health`
- Refresh monitor: `http://127.0.0.1:8001/health`, `/jobs`, `/runs`, `/checks`, `/dead-letters`

Local equivalents: `uvicorn api.main:app` and `python ingest_service.py start`. The refresh service schedules all feeds, retries failures into a dead-letter queue, and records freshness/data-quality checks. See [DATABASE.md](DATABASE.md) for operations and replay commands and [docs/API.md](docs/API.md) for every MCP tool.

Internal Gemini-generated TimescaleDB search is separate from MCP; see [docs/SEARCH_SERVICE.md](docs/SEARCH_SERVICE.md).

#ripdplynngithubaccount
# Focus is on Homer Odyssey?