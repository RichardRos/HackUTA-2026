# Ingest: EIA-930 (national) + NOAA weather -> Tiger Cloud

| Script | What it does | When |
|---|---|---|
| `backfill.py` | History: EIA demand + day-ahead forecast for every respondent and sub-BA zone, NBM forecast bulletins from NOAA's AWS archive, and NCEI GHCNh observations | Once (safe to re-run) |
| `update.py` | Newest EIA data, newest NBM bulletins, the NWS gridpoint forecast and NWS observations | Every hour |

Supporting modules: `eia.py`, `noaa_nbm.py`, `noaa_nws.py`, `noaa_ghcnh.py`, `weather.py` (points + DB writes),
`stations.py` (BA time zones and the stations that represent each BA), `common.py`.

## Data

**Grid (EIA API v2, Form EIA-930)**
| Table | Contents |
|---|---|
| `grid_hourly` | `D` (demand) and `DF` (day-ahead forecast) for all 83 respondents: 69 balancing authorities, 13 EIA regions, `US48`. `balancing_authorities.kind` tells them apart. |
| `grid_subba_hourly` | Demand for 81 sub-BA zones: ERCOT weather zones, PJM utility zones, NYISO zones A–K, CAISO utilities, SPP members, MISO local zones, … Names in `eia_subbas`. |

**Weather (NOAA).** Each BA's load is represented by airport stations in `weather_points` (one row per BA and station,
with load weights that sum to 1 per BA). There are 162 points on 148 stations, covering every BA that has load. Generation-only BAs and the
EIA regions have none.

| Table | `model` / `source` | From | Coverage |
|---|---|---|---|
| `weather_forecasts` | `nbm_nbh` | National Blend of Models hourly bulletin | 1–25 h ahead, hourly; history + live |
| | `nbm_nbs` | NBM short-range bulletin | 6–72 h ahead, 3-hourly; history + live |
| | `nws_ndfd` | NWS forecaster grid (`api.weather.gov/gridpoints`) | 0–7 days, hourly; **live only** (NWS keeps no archive) |
| `weather_actuals` | `ghcnh` | NCEI Global Historical Climatology Network hourly | history, ~2–3 day lag |
| | `nws_obs` | NWS station observations (METAR) | last 30 h, refreshed hourly |

`issued_at` is when NOAA actually published the forecast (the NBM file's upload time, or the NWS grid's `updateTime`), so as-of
backtests are honest. Forecast hours that start before `issued_at` are dropped.

Typed columns (°F, mph, in, mi, ft, W/m², %): temperature, dew point, RH, apparent temperature, cloud cover, wind speed/direction/gust,
solar radiation (NBM only), precipitation probability and amount, snow, thunder probability, visibility, ceiling, and WBGT (forecasts),
or sea-level/station pressure and present weather (actuals). `wind_100m_mph` stays NULL because NOAA's station products don't carry it.

`extra` (JSONB) keeps everything else the source provides:
- **NBM:** the raw bulletin values in NBM's native units, keyed by element code (`TSD`/`DSD` temp/dew-point spread, `MHT` mixing height,
  `TWS`/`TWD` transport wind, `PZR`/`PSN`/`PPL`/`PRA` precip-type odds, `SLV` snow level, `LCB` cloud base, `IFC`/`IFV` IFR odds, …).
  NOAA/MDL's NBM text product documentation defines every code.
- **NWS gridpoint:** every other numeric layer, converted and suffixed with its unit (`maxTemperature_f`, `mixingHeight_ft`,
  `transportWindSpeed_mph`, `iceAccumulation_in`, `heatRisk`, …), plus `weather` and `hazards` text.
- **GHCNh / NWS obs:** wet bulb, altimeter, 3-hour pressure change, snow depth, 3/6/12/24-hour precip, and the text description.

Use them like before: `ba_weather_asof('PJM', 'nbm_nbs', from, to, asof)`.

## Setup
```
pip install -r requirements.txt
export TIMESCALE_SERVICE_URL=postgres://...      # or put it in the NOSTOS_ENV / repo .env file
export EIA_API_KEY=...                          # free: https://www.eia.gov/opendata/register.php (DEMO_KEY is too limited for national pulls)
export NWS_USER_AGENT="nostos-ingest (contact@yourteam)"   # optional, recommended by api.weather.gov
python db/run_sql.py db/06_national_noaa.sql    # adds the NOAA/national columns and tables
```

## History (once)
```
python db/run_sql.py db/99_clear_fake.sql       # remove fake seed data first (backfill.py checks)
python ingest/backfill.py                       # 2024-02-01 -> now, all parts
```
Options: `--start/--end`, `--only eia,subba,forecasts,actuals` or `--skip ...`, `--eia-series D,DF,NG,TI`,
`--nbm-products nbs,nbh`, `--nbm-cycles 0,12`, `--workers 4`.

Rough cost for the default 2024-02 → now run:
- **EIA:** about 4M grid rows and 2M sub-BA rows.
- **NBM:** one ~30 MB file per product, cycle and day. The default (NBS at 12z) is ~30 GB downloaded and streamed, not stored.
- **GHCNh:** ~650 MB downloaded.

A failed chunk is logged and skipped. Re-running the same command fills the gaps, because every write is an upsert.

## Scheduled job
`.github/workflows/ingest.yml` runs `update.py` at :17 every hour on GitHub Actions. Add the secrets `TIMESCALE_SERVICE_URL`,
`EIA_API_KEY`, and (optionally) `NWS_USER_AGENT`. If your service URL omits the password, also add `TIMESCALE_PASSWORD`
(or `TIMESCALE_SERVICE_PASSWORD`).
Then use "Run workflow" to test it. Each part runs on its own (`--only`/`--skip`),
and one failing doesn't stop the others.

To run it from your own machine instead, use cron (`crontab -e`):
```
17 * * * * cd /path/to/HackUTA-2026 && .venv/bin/python ingest/update.py >> ingest.log 2>&1
```

## Changing stations
Edit `BA_STATIONS` in `stations.py` (ICAO codes and weights). Both scripts sync `weather_points` from it, and `update.py` does so
automatically when it sees a new point. A station must appear in the NBM bulletins to get forecasts. It needs an NCEI GHCNh record
to get history; `GHCN_ID_OVERRIDES` fixes stations NCEI files under another ICAO.
