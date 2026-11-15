# Nostos weather scorer

Nostos forecasts the **official NWS daily high temperature** for 20 US cities, 1–3 days
ahead. Those official highs are the numbers Kalshi's daily-high markets settle on. It scores
itself honestly against the raw weather models and against the National Blend of Models (NBM),
the NWS's own blend.

**How it works.** Each night it takes five global weather models (ECMWF IFS, GFS, ICON, GEM, JMA)
from the Open-Meteo Previous Runs API and corrects each one's recent habit: the average error
over the last 30 days, counting only official highs that had already been published at forecast
time. Models that have been more accurate lately get more weight.

## Results (test window 2026-09-14 → 2026-11-12, 1,178 city-days per lead)

Lower MAE (mean absolute error, °F) is better. L1/L2/L3 = forecast issued 1/2/3 days ahead.

| Model | MAE L1 | MAE L2 | MAE L3 | Bias L1 | Within 2°F L1 |
|---|---|---|---|---|---|
| **nostos** | **1.57** | **1.81** | **1.99** | -0.12 | **71%** |
| nbm_corrected | 1.92 | 2.13 | 2.36 | +0.19 | 63% |
| ncep_nbm_conus (NBM) | 2.31 | 2.56 | 2.80 | -1.43 | 52% |
| icon_seamless | 2.07 | 2.37 | 2.65 | -1.07 | 59% |
| gfs_seamless | 2.33 | 2.37 | 2.53 | -1.06 | 54% |
| gem_seamless | 2.26 | 2.73 | 3.37 | -0.72 | 55% |
| ecmwf_ifs025 | 2.66 | 2.87 | 3.01 | -1.42 | 44% |
| jma_seamless | 4.02 | 4.03 | 4.15 | -3.39 | 27% |
| naive_persistence | 5.65 | 6.53 | 6.95 | +0.56 | 34% |

All competitors are scored on exactly the same city-days. `nbm_corrected` is NBM given the
identical rolling bias correction. That's the fairness check: bias correction alone closes about
half the gap, and the multi-model blend still wins by 0.32–0.37 °F at every lead.
Full table: `data/test_scores.csv`. Validation: `data/val_scores.csv`.

## Pipeline: run in order

```bash
source ~/HackUTA26/nostos-model/.venv/bin/activate
cd model
python fetch_forecasts.py   # Open-Meteo Previous Runs, hourly, 5 models + NBM, leads 1-3 -> data/forecasts.parquet
python fetch_obs.py         # IEM CLI official daily highs + publication time       -> data/obs_daily_high.parquet
python daily_high.py        # hourly -> max over the local-standard-time climate day, with issued_at
python score.py             # full-period leaderboard of the raw models             -> data/scores.csv
python blend.py             # Nostos: choose variant on validation, score test once  -> data/nostos_daily_high.parquet, data/test_scores.csv
python -m pytest tests/     # leakage tests
python load_to_db.py        # upsert everything into Tiger (schema nostos_wx); idempotent
python build_locations.py   # optional: check locations.csv against Kalshi settlement stations
```

Dates, models and leads are in `config.py` (`START_DATE=2025-01-01`, `END_DATE=2026-11-12`).
`load_to_db.py` needs `DATABASE_URL` in `model/.env` (never committed).

## Honesty rules

1. **No peeking.** Every forecast has an `issued_at`: 23:00 local standard time on
   D − lead (stored in UTC). A bias or skill weight for day D at lead L only uses official
   highs for days up to **D − (L+1)** whose CLI report was actually **published before `issued_at`**.
   The publication time comes from the NWS product ID. 19 reports published late (corrections)
   are excluded from bias correction until they existed, but still count as truth for scoring.
2. **Validation vs test.** The three blend variants (plain average, bias-corrected average,
   inverse-MAE-weighted corrected) are compared only on the 60-day validation window
   (2026-07-16 → 2026-09-13). The winner, `inv_mae_corrected` (validation MAE 1.80 °F vs 1.82 and 2.25),
   is scored once on the final 60 days. Window length (30 d), minimum history (10 d) and the
   0.5 °F weight floor were fixed in advance, not tuned.
3. **NBM is a competitor, never an input.** Only `config.MODELS` feed the blend; a test enforces it.
4. **Leakage tests** (`tests/test_blend.py`):
   - every bias on the real data uses only days ≤ D − (L+1) published by `issued_at`;
   - *perturbation*: corrupting every observation not yet available at `issued_at` by +500 °F
     leaves the bias unchanged;
   - *test the test*: deliberately leaky versions (using day D − L, or ignoring publication
     times) make the test fail;
   - the same checks cover `nbm_corrected`.

## Database (Tiger / TimescaleDB, schema `nostos_wx`)

Created by `sql/nostos_wx_schema.sql`. It's additive only: nothing outside `nostos_wx` is touched.

| Object | What |
|---|---|
| `locations` | the 20 cities, stations, standard UTC offsets |
| `daily_high_obs` | hypertable on `climate_date`: official highs + `published_at` |
| `daily_high_forecasts` | hypertable on `climate_date`: all models, NBM, nostos, nbm_corrected, naive_persistence, with `issued_at`, `role`, and verification columns `observed_f` / `error_f` |
| `scores` | Python leaderboards (`split` = full_period / validation / test) |
| `leaderboard_daily` | continuous aggregate: per day × model × lead, n, sums of error and \|error\|, MAE, bias |

## Caveats

- **One season of test data.** The test window is 60 days of early fall (Sep–Nov 2026), and
  validation is late summer. Winter cold-air outbreaks and spring convection aren't in the test.
- **20 stations only**, all major US airports and city sites. The results may not transfer to
  rural or complex terrain.
- **Approximate issue times.** `issued_at` is a rule (23:00 local standard time on D − lead),
  not the real availability time of each model run. Previous Runs' `previous_dayN` is roughly
  the run from N × 24 h before each hour. Run latency isn't modelled.
- **Station verification.** Three stations were originally unverified. `build_locations.py`
  now matches all 20 stations to the CLI station named in each Kalshi market's rules, with
  coordinates and offsets agreeing with IEM metadata. But Kalshi settles most markets on *The
  Weather Company's* reading of that report, not on IEM's copy of the CLI, so a settlement value
  can still differ from our "truth" (for example after a late correction). One Kalshi series
  (`KXHIGHTSDF`, titled "SATX") points at Louisville's station, which looks like a Kalshi labelling quirk.
- Truth is IEM's latest version of each CLI report; 42 of 13,620 city-days have no report.


