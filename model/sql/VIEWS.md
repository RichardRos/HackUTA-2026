# nostos_wx views (for the website)

Read-only views in Tiger, schema `nostos_wx`, defined in `nostos_wx_views.sql`.
Temperatures in °F, dates are NWS climate days, all timestamps UTC.
Test period = 2026-09-14 .. 2026-11-12 (fixed). "NBM corrected" = NBM after our bias correction; "NBM" = raw `ncep_nbm_conus`.

## `leaderboard_test`: test-period leaderboard, one row per (lead_days, model)
| column | meaning |
|---|---|
| `lead_days` | 1, 2 or 3 (1 = forecast issued the night before) |
| `model` | `nostos`, `nbm_corrected`, `ncep_nbm_conus`, the 5 raw models, `naive_persistence` |
| `role` | `nostos` · `benchmark` · `input` · `baseline` |
| `days` | number of scored city-days |
| `mae_f` | mean absolute error (lower is better) |
| `bias_f` | mean error (+ = forecast too warm) |
| `within_2f_pct` | % of days within 2 °F |

Sort by `mae_f`. Example: `WHERE lead_days = 1 ORDER BY mae_f`.

## `city_accuracy`: one row per city (20), lead 1, test period
| column | meaning |
|---|---|
| `location` | city key, lowercase (e.g. `austin`) |
| `station_id` | NWS station (e.g. `KAUS`) |
| `latitude`, `longitude` | for the map |
| `days` | scored days for Nostos |
| `nostos_mae_f` | Nostos MAE |
| `nbm_corrected_mae_f` | NBM-corrected MAE |
| `nbm_mae_f` | raw NBM MAE |

## `city_timeline`: one row per (city, climate day), lead 1, from 2026-09-14 onward
| column | meaning |
|---|---|
| `location` | city key |
| `climate_date` | the day |
| `observed_f` | official NWS high (NULL until published) |
| `nostos_f` | Nostos forecast (0.1 °F) |
| `nbm_corrected_f` | NBM-corrected forecast |
| `nbm_f` | raw NBM forecast |

Order by `climate_date`. New days appear after each nightly run.
