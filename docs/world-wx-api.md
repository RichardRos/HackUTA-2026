# Global Nostos API: point + Odyssey route

For Person D (frontend). Both endpoints sit under `/api/wx/world`, need the same Clerk bearer token as the
other `/api/*` routes, and return UTC everywhere (ISO 8601 with `Z`). Examples below are real live responses
from 2026-11-15T09:41:53Z, trimmed: arrays are cut to the first item or two, and the real ones have every
model / point / departure.

Errors: `400` with `{"detail": "readable message"}` for bad input that passes validation (route too long for
the 96 h forecast, departure in the past, duplicate route names), `422` for schema violations (lat outside
[-90, 90], missing fields), `502` with `{"detail": "Weather source failed: ..."}` when Open-Meteo is down or slow.
A first (uncached) call takes about 3 s for a point and 5–7 s for two routes; repeats within 30 min take < 1 s.

## 1. `GET /api/wx/world/point?lat=48.8566&lon=2.3522`

```json
{
  "lat": 48.8566,
  "lon": 2.3522,
  "grid": {
    "lat": 48.9,
    "lon": 2.4
  },
  "issued_at": "2026-11-15T09:41:53Z",
  "std_utc_offset_hours": 0,
  "current": {
    "time": "2026-11-15T09:30Z",
    "interval": 900,
    "temperature_2m": 9.6,
    "wind_speed_10m": 3.71,
    "wind_gusts_10m": 8.2,
    "precipitation": 0.0,
    "weather_code": 3,
    "units": {
      "temperature_2m": "°C",
      "wind_speed_10m": "m/s",
      "wind_gusts_10m": "m/s",
      "precipitation": "mm",
      "weather_code": "wmo code"
    }
  },
  "daily": [
    {
      "date": "2026-11-16",
      "lead_days": 1,
      "day_start_utc": "2026-11-16T00:00:00Z",
      "nostos_high_f": 53.1,
      "nostos_high_c": 11.7,
      "spread_f": 1.54,
      "models": [
        {
          "model": "icon_seamless",
          "raw_high_f": 56.1,
          "raw_high_c": 13.4,
          "bias_f": 0.95,
          "weight": 0.238
        },
        {
          "model": "ecmwf_ifs025",
          "raw_high_f": 52.3,
          "raw_high_c": 11.3,
          "bias_f": -0.28,
          "weight": 0.214
        }
      ]
    }
  ],
  "rankings": [
    {
      "model": "icon_seamless",
      "lead_days": 1,
      "mae_f": 1.57,
      "bias_f": 0.95,
      "weight": 0.238,
      "n_days": 30,
      "truth": "ERA5"
    },
    {
      "model": "ecmwf_ifs025",
      "lead_days": 1,
      "mae_f": 1.74,
      "bias_f": -0.28,
      "weight": 0.214,
      "n_days": 30,
      "truth": "ERA5"
    }
  ],
  "hourly_rankings": {
    "temperature_2m": {
      "unit": "°C",
      "lead_days": 1,
      "truth": "ERA5",
      "note": "5 models",
      "bias_corrected": true,
      "models": [
        {
          "model": "ecmwf_ifs025",
          "mae": 0.804,
          "bias": 0.102,
          "weight": 0.275,
          "n_hours": 720
        }
      ]
    },
    "wind_gusts_10m": {
      "unit": "m/s",
      "lead_days": 1,
      "truth": "ERA5",
      "note": "3 models (ECMWF/JMA lack gust history)",
      "bias_corrected": true,
      "models": [
        {
          "model": "icon_seamless",
          "mae": 1.077,
          "bias": -0.646,
          "weight": 0.407,
          "n_hours": 720
        }
      ]
    }
  },
  "truth_through": "2026-11-09T23:00:00Z",
  "truth_note": "ERA5 is a ~25 km ECMWF reanalysis, not a station; it may slightly favor ECMWF.",
  "nearest_us_station": null,
  "openmeteo_cost": {
    "weighted_calls_est": 11.4,
    "if_uncached_est": 11.4,
    "calls": [
      {
        "call": "current",
        "points_fetched": 1,
        "points_cached": 0,
        "variables": 5,
        "days": 1,
        "weight_per_point": 1.0,
        "weighted_calls": 1.0
      }
    ]
  }
}
```

Notes:
- `daily` has three entries (D+1, D+2, D+3 local standard-time days). `models` lists all five, heaviest weight first;
  `weight` values sum to 1. `spread_f` is model disagreement (std of the bias-corrected highs), so use it for a ± band.
- `rankings` is the lead-1 daily-high leaderboard vs ERA5, best (lowest `mae_f`) first. Show `truth` ("ERA5") and
  `truth_note` next to it: ERA5 is a ~25 km reanalysis, not a station.
- `hourly_rankings` has all four variables (`temperature_2m`, `wind_speed_10m`, `wind_gusts_10m`, `precipitation`).
  Show the gust `note` ("3 models (ECMWF/JMA lack gust history)") on screen. Precipitation has `bias: null`
  (`bias_corrected: false`); it is weighted only.
- `nearest_us_station` is `null` unless one of the 20 trained US cities is within 25 km, for example Austin:
  `{"location": "austin", "station_id": "KAUS", "distance_km": 11.2, "note": "station-verified model available"}`.
  Use it to link to the station-verified model.
- `current.weather_code` is a WMO code. `grid` is the 0.1° cell actually fetched.

## 2. `POST /api/wx/world/route`

Request (waypoints are `[lat, lon]` or `[lat, lon, "Name"]`; names label places in `why` and `worst_point.near`):

```json
{
  "routes": [
    {
      "name": "I-35",
      "waypoints": [
        [
          32.78,
          -96.8,
          "Dallas"
        ],
        [
          32.39,
          -96.85,
          "Waxahachie"
        ],
        [
          30.27,
          -97.74,
          "Austin"
        ]
      ]
    },
    {
      "name": "West via Lampasas",
      "waypoints": [
        [
          32.78,
          -96.8,
          "Dallas"
        ],
        [
          31.06,
          -98.18,
          "Lampasas"
        ],
        [
          30.27,
          -97.74,
          "Austin"
        ]
      ]
    }
  ],
  "depart_at": "2026-11-15T15:00Z",
  "speed_kmh": 80,
  "optimize_departure": true,
  "window_hours": 24
}
```

| field | default | limits |
|---|---|---|
| `routes` | (required) | 1–5 routes, unique `name` (≤ 40 chars), 2–50 waypoints each |
| `depart_at` | next whole UTC hour | not in the past; naive times are read as UTC |
| `speed_kmh` | 80 | 5–300 |
| `optimize_departure` | false | sweeps hourly from `depart_at` |
| `window_hours` | 24 | 0–72 (used only when optimizing) |

Response (same request with `optimize_departure: true`):

```json
{
  "issued_at": "2026-11-15T09:41:41Z",
  "speed_kmh": 80.0,
  "optimize_departure": true,
  "window_hours": 24,
  "best": {
    "route": "I-35",
    "depart_at": "2026-11-16T03:00:00Z"
  },
  "why": "Route I-35 is 0.9 h faster than West via Lampasas with similar weather; models agree within 0.5°C; leave at 03:00Z instead of 15:00Z.",
  "hazard_weights": {
    "precip_per_mm": 3.0,
    "wind_threshold_ms": 8.0,
    "wind_per_ms": 1.0
  },
  "routes": [
    {
      "route": "I-35",
      "score": 5.62,
      "depart_at": "2026-11-16T03:00:00Z",
      "arrive_at": "2026-11-16T06:45:00Z",
      "duration_h": 3.75,
      "distance_km": 299.7,
      "spacing_km": 74.9,
      "n_points": 5,
      "coarse_sampling": false,
      "rain_mm": 0.21,
      "max_gust_ms": 9.2,
      "max_temp_spread_c": 0.5,
      "worst_point": {
        "lat": 32.151,
        "lon": -97.0261,
        "eta": "2026-11-16T03:56:00Z",
        "hazard": 0.6,
        "near": "Hillsboro",
        "reason": "models disagree (±0.5 °C, ±0.3 m/s)"
      },
      "departure_scores": [
        {
          "depart_at": "2026-11-15T15:00:00Z",
          "score": 39.47
        },
        {
          "depart_at": "2026-11-15T16:00:00Z",
          "score": 25.06
        },
        {
          "depart_at": "2026-11-15T17:00:00Z",
          "score": 18.99
        }
      ],
      "points": [
        {
          "lat": 32.78,
          "lon": -96.8,
          "km": 0.0,
          "eta": "2026-11-16T03:00:00Z",
          "temp_c": 8.7,
          "wind_ms": 3.9,
          "gust_ms": 8.7,
          "precip_mm": 0.04,
          "spread": {
            "temp_c": 0.32,
            "wind_ms": 0.34
          },
          "hazard": 0.45
        },
        {
          "lat": 32.151,
          "lon": -97.0261,
          "km": 74.9,
          "eta": "2026-11-16T03:56:00Z",
          "temp_c": 8.5,
          "wind_ms": 4.3,
          "gust_ms": 9.1,
          "precip_mm": 0.06,
          "spread": {
            "temp_c": 0.5,
            "wind_ms": 0.32
          },
          "hazard": 0.6
        }
      ]
    },
    {
      "route": "West via Lampasas",
      "score": 7.1,
      "depart_at": "2026-11-16T11:00:00Z",
      "arrive_at": "2026-11-16T15:40:00Z",
      "duration_h": 4.67,
      "distance_km": 373.8,
      "spacing_km": 74.8,
      "n_points": 6,
      "coarse_sampling": false,
      "rain_mm": 0.06,
      "max_gust_ms": 7.8,
      "max_temp_spread_c": 0.96,
      "worst_point": {
        "lat": 32.78,
        "lon": -96.8,
        "eta": "2026-11-16T11:00:00Z",
        "hazard": 0.62,
        "near": "Dallas",
        "reason": "models disagree (±1.0 °C, ±0.3 m/s)"
      },
      "departure_scores": [
        {
          "depart_at": "2026-11-15T15:00:00Z",
          "score": 24.6
        },
        {
          "depart_at": "2026-11-15T16:00:00Z",
          "score": 19.89
        },
        {
          "depart_at": "2026-11-15T17:00:00Z",
          "score": 18.02
        }
      ],
      "points": [
        {
          "lat": 32.78,
          "lon": -96.8,
          "km": 0.0,
          "eta": "2026-11-16T11:00:00Z",
          "temp_c": 8.1,
          "wind_ms": 2.4,
          "gust_ms": 5.7,
          "precip_mm": 0.0,
          "spread": {
            "temp_c": 0.96,
            "wind_ms": 0.29
          },
          "hazard": 0.62
        },
        {
          "lat": 32.6091,
          "lon": -97.5391,
          "km": 74.8,
          "eta": "2026-11-16T11:56:00Z",
          "temp_c": 8.0,
          "wind_ms": 2.7,
          "gust_ms": 5.7,
          "precip_mm": 0.02,
          "spread": {
            "temp_c": 0.27,
            "wind_ms": 0.56
          },
          "hazard": 0.48
        }
      ]
    }
  ],
  "openmeteo_cost": {
    "weighted_calls_est": 124.1,
    "if_uncached_est": 124.1,
    "calls": [
      {
        "call": "past_runs",
        "points_fetched": 9,
        "points_cached": 0,
        "variables": 40,
        "days": 33,
        "weight_per_point": 9.43,
        "weighted_calls": 84.9
      }
    ],
    "unique_points": 9,
    "max_lead_days": 2
  }
}
```

Notes:
- `routes` is ranked best first (lowest `score`). With `optimize_departure`, each route shows its own best
  `depart_at`; `best` is the overall winner and `departure_scores` (one per hour tried) can feed a small chart.
- Color map segments by `points[].hazard` (0 = calm; the worst rain stretch at 15:00Z scored about 14). Each route has
  at most 8 points along the path, evenly spaced (≤ 75 km, widening on long routes; `coarse_sampling: true` past 100 km).
  `km` is the distance from the start, and `eta` is the arrival time at that point.
- `worst_point.reason` is ready to display; `near` is the closest named waypoint within 50 km, or `null`.
- `why` is the one-liner for the result card. The fixed 15:00Z departure gave
  "Route West via Lampasas avoids 5.7 mm of rain near Waco vs I-35; models agree within 2.6°C."
- `openmeteo_cost` is for us (estimated weighted Open-Meteo calls); there's no need to display it.
