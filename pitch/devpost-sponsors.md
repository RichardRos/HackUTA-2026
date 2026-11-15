# Nostos: sponsor tools for the HackUTA '26 Devpost submission

Paste these sections into the Devpost description. Every claim below matches the repo.

## Built with
Tiger Data (TimescaleDB on Tiger Cloud) · Vultr Cloud Compute · Vultr Object Storage · Google Gemini API · Grok Bot · Cursor · GoDaddy Registry (.biz) · FastAPI · FastMCP · React · three.js · Clerk · Cloudflare Tunnel · Docker Compose · scikit-learn · XGBoost

## Theme: Homer's Odyssey (General Track)
Nostos is Homer's word for the voyage home. The app's Greek mode turns live ships into triremes and draws Odysseus' 14 stops from Troy to Ithaca. The voyage router can plan that route for a trireme at 6 knots under oar and sail.

## How we used Tiger Data (Best Use of Tiger Data)
- One TimescaleDB on Tiger Cloud holds every source: weather forecasts and observations, EIA-930 grid hours for 83 respondents, daily-high forecasts, 35 commodity futures and other price series, AIS ship positions, and prediction markets.
- Hypertables for every time series. Every row stores the time it describes and the time it was known.
- Continuous aggregates for daily grid peaks (`grid_daily`) and the daily-high leaderboard (`leaderboard_daily`).
- As-of SQL functions (`weather_forecast_asof`, `ba_weather_asof`, `grid_asof`, `point_forecast_asof`) so backtests never see the future.
- A 90-day retention policy on `ship_positions`, which is also the voyage speed model's training window.
- Integrity views that list any row used before it was known.
- FastAPI, the React globe and all 47 MCP tools read the same database.

## How we used Vultr (Best Use of Vultr)
- **App server:** Vultr Cloud Compute runs the API and the ingest service from Docker Compose, behind a Cloudflare Tunnel.
- **Route service:** a separate Vultr instance computes every voyage route (`voyage/service.py`) behind Caddy HTTPS. If it can't be reached, the API computes the route itself.
- **Training runs:** `python -m voyage.vultr` starts a throwaway 4 vCPU instance, trains the SuperLearner speed model, then deletes the instance.
- **Object Storage:** model files go to a Vultr bucket over the S3 API. Every API instance reloads the newest model within the hour, so retraining never needs a redeploy.

## How we used the Gemini API (Best Use of Gemini API)
- **In-app assistant:** a Gemini function-calling loop (`api/assistant.py`) offers Gemini all 47 MCP tools, runs each call it makes against the in-process FastMCP server, and returns the answer with the tools it used.
- **Market matching:** normalises Kalshi and Polymarket questions into the same fields when the regex rules can't.
- **Plain-English search:** turns a question into SQL over TimescaleDB. Every query is parsed and checked (pglast) before it runs.
- **Commodity notes:** for any commodity and country, writes a headline, two paragraphs and "what to watch", using only that commodity's futures, ETFs, Kalshi markets and news.

## How we used the Grok Bot (Build Your Hackathon A-Team with SpaceXSI)
Live government announcements in every country panel. When you open a country, the Grok Bot searches X for posts from the last 7 days by verified official accounts only: government, ministry, agency, head-of-state and central-bank. It returns up to five, newest first, each with the date, the account's name and handle, a short factual summary and a link to the post, and says plainly when nothing can be verified. The request goes to the Grok Bot routine by webhook and the result comes back to the browser over a WebSocket, so the webhook keys stay on the server.

## How we used Cursor (Build Your Hackathon A-Team with SpaceXSI)
Cursor was our main AI coding agent for the whole build: the FastAPI and FastMCP server with its 47 tools, the ingest service, the TimescaleDB schema, the voyage router and speed model, and the React globe. Together with the Grok Bot's live government announcements above, this covers both parts of the A-Team challenge. Remember to opt in to the raffle when you submit.

## Domain (Best Domain Name from GoDaddy Registry)
The app runs at **trynostos.biz**. The .biz domain is operated by GoDaddy Registry.
