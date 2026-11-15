# Ingest: EIA-930 (national) + NOAA weather (US + global) -> Tiger Cloud

| Script | What it does | When |
|---|---|---|
| `backfill.py` | History: EIA demand + day-ahead forecast for every respondent and sub-BA zone, NBM forecast bulletins and GFS global forecasts from NOAA's AWS archives, and NCEI GHCNh observations | Once (safe to re-run) |
| `update.py` | Newest EIA data, newest NBM bulletins, the NWS gridpoint forecast, NWS observations, and each new GFS run | Every hour |

Supporting modules: `eia.py`, `noaa_nbm.py`, `noaa_nws.py`, `noaa_ghcnh.py`, `noaa_gfs.py`, `weather.py` (points + DB writes),
`stations.py` (BA time zones and the stations that represent each BA), `global_points.py` (world cities), `common.py`.

## Data

**Grid (EIA API v2, Form EIA-930)**
| Table | Contents |
|---|---|
| `grid_hourly` | `D` (demand) and `DF` (day-ahead forecast) for all 83 respondents: 69 balancing authorities, 13 EIA regions, `US48`. `balancing_authorities.kind` tells them apart. |
| `grid_subba_hourly` | Demand for 81 sub-BA zones: ERCOT weather zones, PJM utility zones, NYISO zones A–K, CAISO utilities, SPP members, MISO local zones, … Names in `eia_subbas`. |

**Weather (NOAA).** Each BA's load is represented by airport stations in `weather_points` (one row per BA and station,
with load weights that sum to 1 per BA). There are 162 points on 148 stations, covering every BA that has load. Generation-only BAs and the
EIA regions have none.

Global coverage comes from 106 world-city points (`purpose = 'global'`, `point_id` like `glb_tokyo`, no BA, ISO `country`),
listed in `global_points.py`. Only GFS covers them; NBM, NWS and GHCNh are US-only.

| Table | `model` / `source` | From | Coverage |
|---|---|---|---|
| `weather_forecasts` | `nbm_nbh` | National Blend of Models hourly bulletin | 1–25 h ahead, hourly; history + live |
| | `nbm_nbs` | NBM short-range bulletin | 6–72 h ahead, 3-hourly; history + live |
| | `nws_ndfd` | NWS forecaster grid (`api.weather.gov/gridpoints`) | 0–7 days, hourly; **live only** (NWS keeps no archive) |
| | `gfs_0p25` | NOAA Global Forecast System, 0.25° grid | **every point, US + global**; live: hourly 1–72 h from each 00/06/12/18Z run; history back to 2021 |
| `weather_actuals` | `ghcnh` | NCEI Global Historical Climatology Network hourly | history, ~2–3 day lag |
| | `nws_obs` | NWS station observations (METAR) | last 30 h, refreshed hourly |

`issued_at` is when NOAA actually published the forecast (the NBM file's upload time, the NWS grid's `updateTime`, or the upload
time of the last GFS step used), so as-of
backtests are honest. Forecast hours that start before `issued_at` are dropped.

Typed columns (°F, mph, in, mi, ft, W/m², %): temperature, dew point, RH, apparent temperature, cloud cover, wind speed/direction/gust,
solar radiation (NBM, GFS), precipitation probability and amount, snow, thunder probability, visibility, ceiling, and WBGT (forecasts),
or sea-level/station pressure and present weather (actuals). `wind_100m_mph` is filled by GFS only; NOAA's station products don't carry it.

`extra` (JSONB) keeps everything else the source provides:
- **NBM:** the raw bulletin values in NBM's native units, keyed by element code (`TSD`/`DSD` temp/dew-point spread, `MHT` mixing height,
  `TWS`/`TWD` transport wind, `PZR`/`PSN`/`PPL`/`PRA` precip-type odds, `SLV` snow level, `LCB` cloud base, `IFC`/`IFV` IFR odds, …).
  NOAA/MDL's NBM text product documentation defines every code.
- **NWS gridpoint:** every other numeric layer, converted and suffixed with its unit (`maxTemperature_f`, `mixingHeight_ft`,
  `transportWindSpeed_mph`, `iceAccumulation_in`, `heatRisk`, …), plus `weather` and `hazards` text.
- **GFS:** `prmsl_hpa`, `cape_jkg`; with `--gfs-fields all` also surface pressure, frozen-precip %, snow/rain/freezing-rain flags,
  precipitable water, boundary-layer height, snow depth, downward longwave, and 80 m wind.
- **GHCNh / NWS obs:** wet bulb, altimeter, 3-hour pressure change, snow depth, 3/6/12/24-hour precip, and the text description.

Use them like before: `ba_weather_asof('PJM', 'nbm_nbs', from, to, asof)` (any model, including `gfs_0p25`). For global points,
use `point_forecast_asof(points[], model, from, to, asof)`; pass `NULL` for all global points, or e.g. `ARRAY['glb_london']`.

### How GFS is read
Each GFS step is a ~500 MB GRIB2 file with ~740 fields, plus a small `.idx` listing every field's byte offset. `noaa_gfs.py` reads
the index, downloads only the 16 fields it needs with HTTP Range requests (~0.9 MB each), decodes them with ECMWF's `eccodes`
(pip wheel, no system install), and reads all points out of each grid in one numpy indexing step. Each point uses its nearest
land cell (coastal cities are moved off water cells). A 72-step live run is ~1 GB of downloads and takes ~1–2 minutes;
full grids are never stored. Solar radiation and precipitation, which GFS gives as 6-hour-reset averages and run totals,
are converted to the mean or total over the period since the previous stored step.

## Setup
```
pip install -r requirements.txt
export TIMESCALE_SERVICE_URL=postgres://...      # or put it in the NOSTOS_ENV / repo .env file
export EIA_API_KEY=...                          # free: https://www.eia.gov/opendata/register.php (DEMO_KEY is too limited for national pulls)
export NWS_USER_AGENT="nostos-ingest (contact@yourteam)"   # optional, recommended by api.weather.gov
python db/run_sql.py db/06_national_noaa.sql    # adds the NOAA/national columns and tables
python db/run_sql.py db/07_global_gfs.sql       # global points + GFS
```

## History (once)
```
python db/run_sql.py db/99_clear_fake.sql       # remove fake seed data first (backfill.py checks)
python ingest/backfill.py                       # 2024-02-01 -> now, all parts
```
Options: `--start/--end`, `--only eia,subba,forecasts,actuals,gfs` or `--skip ...`, `--eia-series D,DF,NG,TI`,
`--nbm-products nbs,nbh`, `--nbm-cycles 0,12`, `--workers 4`; for GFS `--gfs-start` (default 90 days before `--end`),
`--gfs-cycles 0,6,12,18`, `--gfs-leads 12-48/3` (or e.g. `1-72`), `--gfs-fields core|all`.

Rough cost for the default 2024-02 → now run:
- **EIA:** about 4M grid rows and 2M sub-BA rows.
- **NBM:** one ~30 MB file per product, cycle and day. The default (NBS at 12z) is ~30 GB downloaded and streamed, not stored.
- **GHCNh:** ~650 MB downloaded.
- **GFS:** ~180 MB per run with the defaults (12z, 13 steps, 16 fields), so ~16 GB for 90 days at ~16 s per run.
  Going back to 2021 is ~1,400 runs, ~250 GB of downloads; fewer leads or `--gfs-cycles` keep it smaller.

A failed chunk is logged and skipped. Re-running the same command fills the gaps, because every write is an upsert.

## Scheduled job
`.github/workflows/ingest.yml` runs `update.py` at :17 every hour on GitHub Actions. Add the secrets `TIMESCALE_SERVICE_URL`,
`EIA_API_KEY` and (optionally) `NWS_USER_AGENT`, then use "Run workflow" to test it. Each part runs on its own (`--only`/`--skip`),
and one failing doesn't stop the others.

To run it from your own machine instead, use cron (`crontab -e`):
```
17 * * * * cd /path/to/HackUTA-2026 && .venv/bin/python ingest/update.py >> ingest.log 2>&1
```

## Changing points
Global cities: edit `GLOBAL_CITIES` in `global_points.py`. Both scripts sync them, and GFS picks them up on its next run.

US stations:
edit `BA_STATIONS` in `stations.py` (ICAO codes and weights). Both scripts sync `weather_points` from it, and `update.py` does so
automatically when it sees a new point. A station must appear in the NBM bulletins to get forecasts. It needs an NCEI GHCNh record
to get history; `GHCN_ID_OVERRIDES` fixes stations NCEI files under another ICAO.
