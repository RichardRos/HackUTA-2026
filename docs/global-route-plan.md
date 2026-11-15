Final push: global Nostos (any coordinates, live) + Odyssey route ranking

We're adding two features to Nostos before the 11:59 AM deadline. Reuse the existing Nostos blend logic from the US pipeline (per model and lead: subtract the model's recent bias, then weight the models by inverse recent MAE). No Kalshi, no NWS CLI and no NBM for global. Those stay US-only. Everything stays in UTC. Keep it to one new module plus one API router, and don't touch the US pipeline, the Tiger tables or the existing endpoints.

Feature 1: Nostos at any point on Earth (live)

The website sends lat, lon. We return live conditions, the Nostos forecast and a per-point model ranking.

Data (Open-Meteo, free, no key, global). Read the docs and confirm every parameter name before coding:

Live conditions: https://api.open-meteo.com/v1/forecast with current=temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation,weather_code.
Forecasts and archived forecasts in one call: https://previous-runs-api.open-meteo.com/v1/forecast with past_days=35, forecast_days=4, models= the same 5 models as the US blend (ECMWF, GFS, ICON, GEM, JMA; use the exact model names already in config.py), and hourly= for temperature_2m, wind_speed_10m, wind_gusts_10m, precipitation, each plus _previous_day1, _previous_day2 and _previous_day3. Use timezone=GMT and wind_speed_unit=ms. Columns come back model-suffixed (for example temperature_2m_previous_day1_gfs_seamless). Print the keys once to confirm. Plain temperature_2m (no suffix) is the latest run, which is the live forecast.
Truth (replaces NWS CLI): ERA5 from https://archive-api.open-meteo.com/v1/archive, same hourly variables, timezone=GMT. ERA5 is about 5 days behind real time, so request through today − 5 days and drop nulls. Note in a comment: ERA5 is a ~25 km reanalysis, not a station, and it comes from ECMWF, which may slightly favor the ECMWF model in the rankings.

All three APIs accept comma-separated lists of latitudes and longitudes (the response becomes a JSON list). Use that to batch route points.

Model, the same as the US blend:

Daily high: use a standard-time local day, like NWS. Offset = round(lon / 15) hours, a fixed offset with no DST (comment why). For each model × lead (1–3), take the daily max of temperature_2m_previous_dayL over each local day and compare it to the ERA5 daily max. Over the last 30 days with full truth, compute bias and MAE. Forecast: for tomorrow, apply lead-1 stats to the current run's daily max; use lead-2 stats for D+2 and lead-3 stats for D+3. nostos = Σ w·(f − bias), with w ∝ 1/MAE.
Hourly (needed for routes): the same idea, per variable. An hour t hours ahead uses lead clip(ceil(t/24), 1, 3). Subtract bias for temperature, wind and gusts, and clip wind at ≥ 0. For precipitation, apply only the weights (no bias subtraction), and clip at ≥ 0.
Uncertainty: the standard deviation of the bias-corrected model values at each hour (model spread).
No leakage: stats only use truth that exists now, which ERA5's 5-day lag already guarantees. Never use target-day truth.

Endpoint: GET /api/wx/world/point?lat=&lon= returns:

current (live), issued_at (now, UTC)
daily: D+1 to D+3, with Nostos high in °F and °C, plus each model's raw high
rankings: per model at lead 1, MAE °F, bias and weight, sorted best first, with n_days and truth: "ERA5"
nearest_us_station: if within 25 km of one of our 20 trained cities, its name, so the frontend can say "station-verified model available"
Feature 2: Odyssey route ranking

Endpoint: POST /api/wx/world/route

json
{"routes": [{"name": "A", "waypoints": [[lat, lon], ...]}, ...],
 "depart_at": "2026-11-15T15:00Z", "speed_kmh": 80,
 "optimize_departure": true, "window_hours": 24}
Densify each route so points are ≤ 75 km apart (haversine), capped at 20 points per route. Compute each point's ETA from the cumulative distance ÷ speed_kmh.
Fetch all points of all routes in one batched previous-runs call and one ERA5 call. Run the hourly Nostos blend at each point. Look up the blended values at the ETA hour.
Hazard per point (weights in a dict at the top of the file so we can tune them):
precipitation mm × 3
max(0, wind − 8 m/s) × 1
max(0, gust − 15 m/s) × 1.5
+5 if temp ≤ 1°C and precipitation > 0.1 mm (ice)
max(0, temp − 35°C) × 0.5
uncertainty: 0.5 × temperature spread + 0.5 × wind spread. This is where the rankings matter: routes through places where the models disagree score worse.
Route score = Σ(hazard × segment hours) + 1.0 × total hours. Lower is better. Include time so it doesn't pick a much longer route just because it's dry.
If optimize_departure is set, sweep departures hourly across window_hours. Reuse the fetched data, with no new API calls. ETAs must stay inside the 96 h forecast. Return the best (route, departure) pair.
Response: routes ranked best first, each with score, duration_h, worst_point (lat, lon, ETA, reason), and per-point eta, temp_c, wind_ms, gust_ms, precip_mm, spread, hazard so the map can color segments. Also return best: {route, depart_at} and a one-line why, for example "Route B avoids 6 mm of rain near Waco; models agree within 0.8°C."
Engineering
An in-memory TTL cache (30 min), keyed by the batch of points rounded to 0.1°, so the demo doesn't hammer Open-Meteo. Use a 15 s timeout. Errors return 502 with a readable message.
Tests with mocked HTTP (no network): column parsing for multiple models, the blend on a synthetic case where one model has a +3°C bias and another is noisy (Nostos should beat both), the standard-time day boundaries, ETA and densify math, and a route that crosses a rain blob ranking last.
Then one live smoke test each: Paris, Tokyo, and a two-route Dallas→Austin comparison (I-35 vs. a western route through Lampasas). Print the outputs.
Add 5 lines to README and update the Devpost tech list (Open-Meteo Previous Runs + ERA5 for global).
How to work with me
Show me the parsed columns for one live point, then pause.
Build the point endpoint and run its tests plus the Paris and Tokyo smoke tests.
Build the route endpoint and run its tests plus the Dallas→Austin demo.
Give Person D the two endpoint shapes as example JSON.
