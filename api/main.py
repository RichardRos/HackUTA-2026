"""nostos API.  Run from the repo root with ONE worker (open WebSockets live in memory):

    uvicorn api.main:app --proxy-headers

--proxy-headers matters behind a tunnel or reverse proxy (cloudflared, nginx on Vultr):
without it every visitor looks like 127.0.0.1 and they all share one rate limit.
If you ever need several workers, replace the socket dict with Postgres LISTEN/NOTIFY.

Routes: /api/* REST for the web app (Clerk bearer token), /api/ask + /ws/ask (Grok Bot
assistant, see api/README.md), and the read-only MCP server at /mcp with /health.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from datetime import date, datetime
from typing import Annotated, Literal

from api.config import load_env

load_env()  # before mcp_server/markets read the environment

import httpx  # noqa: E402
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Path, Query  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastmcp.server.auth.providers.jwt import JWTVerifier  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

import mcp_server as tools  # noqa: E402
import ships  # noqa: E402
import wx_scores  # noqa: E402
from api import ask, assistant, assistant_store, wx_live  # noqa: E402
from mcp_server import app as mcp_app  # noqa: E402

CLERK_DOMAIN = os.getenv("CLERK_DOMAIN", "").removeprefix("https://").rstrip("/")
WEB_ORIGINS = os.getenv("WEB_ORIGINS", "http://localhost:8000,http://localhost:5173").split(",")
_verifier = JWTVerifier(
    jwks_uri=f"https://{CLERK_DOMAIN}/.well-known/jwks.json",
    issuer=f"https://{CLERK_DOMAIN}",
) if CLERK_DOMAIN else None


# Local test image only (compose.dev.yaml): every request counts as user "dev". Never set in production.
DEV_AUTH_BYPASS = os.getenv("DEV_AUTH_BYPASS") == "1"
if DEV_AUTH_BYPASS:
    logging.getLogger("uvicorn.error").warning("DEV_AUTH_BYPASS=1: sign-in is disabled; local testing only")


async def require_user(authorization: Annotated[str | None, Header()] = None) -> str:
    """Verify the Clerk session token the web app sends as a bearer token."""
    if DEV_AUTH_BYPASS:
        return "dev"
    if _verifier is None:
        raise HTTPException(503, "CLERK_DOMAIN is not set")
    scheme, _, token = (authorization or "").partition(" ")
    access = await _verifier.verify_token(token) if scheme.lower() == "bearer" and token else None
    claims = access.claims if access else {}
    # Clerk's azp claim is the origin that requested the token; reject other sites' tokens.
    if not claims.get("sub") or claims.get("azp") not in (None, *WEB_ORIGINS):
        raise HTTPException(401, "Sign in required")
    return claims["sub"]


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:  # Gemini, Alpaca, SEC or treasury.gov failed
        raise HTTPException(502, f"Upstream source failed: {exc}") from exc


# Tools are called directly, so their pydantic bounds don't apply: repeat them on each query param.
Text = Annotated[str | None, Query(max_length=200)]
Limit = Annotated[int, Query(ge=1, le=500)]
rest = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


@rest.get("/regions/{region}")
def region_context(
    region: Annotated[str, Path(max_length=16)],
    q: Text = None,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
):
    return _run(tools.get_region_context, region, q, hours)


@rest.get("/search")
def search(
    q: Annotated[str | None, Query(max_length=500)] = None,
    limit: Limit = 25,
    sources: Annotated[list[Literal["markets", "news"]] | None, Query()] = None,
    news_source: Literal["wsj", "ft"] | None = None,
    market_platform: Literal["kalshi", "polymarket"] | None = None,
    tag: Annotated[str | None, Query(max_length=50)] = None,
    start: datetime | None = None,
    end: datetime | None = None,
):
    """Natural-language search; the filter params override what Gemini infers. Filters alone skip Gemini."""
    overrides = {k: v for k, v in {
        "sources": sources, "news_source": news_source, "market_platform": market_platform,
        "news_tag": tag, "start": start, "end": end,
    }.items() if v is not None}
    request = (q or "").strip() or tag  # what the summary should answer; filters alone may have no text
    if q and q.strip():
        # This page renders markets and news; don't let Gemini select an unsupported result panel.
        payload = _run(tools.semantic_search, q.strip(), limit,
                       {"sources": ["markets", "news"], **overrides})
    elif not overrides:
        raise HTTPException(400, "Enter a search or pick at least one filter")
    else:
        payload = _run(lambda: {"interpretation": "filters", **tools.search_data(tools.SearchFilters(**overrides, limit=limit))})
    # summarize_search_results never raises, so a failed summary cannot fail the search.
    return {**payload, "summary": tools.summarize_search_results(payload.get("results") or {}, request)}


@rest.get("/markets")
def markets(q: Text = None, limit: Limit = 50):
    return _run(tools.search_prediction_markets, q=q, limit=limit)


@rest.get("/news")
def news(
    q: Text = None,
    tag: Annotated[str | None, Query(max_length=50)] = None,
    hours: Annotated[int, Query(ge=1, le=8760)] = 168,
    limit: Limit = 50,
):
    return _run(tools.get_news, q=q, tag=tag, hours=hours, limit=limit)


@rest.get("/news/local")
def local_news(
    lat: Annotated[float, Query(ge=-90, le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
):
    return _run(tools.get_local_news, lat, lon)


_signals_cache: dict[int, tuple[float, list]] = {}


@rest.get("/signals")
def signals(limit: Annotated[int, Query(ge=1, le=100)] = 12):
    # shortcut: per-process 5-minute cache (EIA data lands hourly); move to Redis if you run several workers.
    hit = _signals_cache.get(limit)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1]
    rows = _run(tools.get_region_signals, limit)
    _signals_cache[limit] = (time.monotonic(), rows)
    return rows


IsoCountry = Annotated[str | None, Query(min_length=2, max_length=2)]


@rest.get("/weather/global")
def global_weather_now(country: IsoCountry = None):
    """Next-hour GFS forecast, one row per world city (for the globe)."""
    return _run(tools.get_global_weather_now, country)


@rest.get("/weather/global/forecast")
def global_weather_forecast(
    point_id: Annotated[str | None, Query(max_length=64)] = None,
    country: IsoCountry = None,
    hours: Annotated[int, Query(ge=1, le=384)] = 72,
):
    return _run(tools.get_global_weather_forecast, point_id, country, hours)


@rest.get("/flights")
def flights_near(
    lat: Annotated[float, Query(ge=-90, le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    radius_nm: Annotated[int, Query(ge=1, le=250)] = 100,
    cargo: bool = False,
):
    """Live aircraft near a point (adsb.lol, ~1 s fresh); cargo=true keeps freight airlines only."""
    return _run(tools.get_flights_near, lat, lon, radius_nm, cargo)


@rest.get("/flights/global")
def flights_global(cargo: bool = False):
    """Every aircraft worldwide (shared OpenSky snapshot, 90 s refresh with credentials)."""
    return _run(tools.get_flights_global, cargo)


WxLead = Annotated[int, Query(ge=1, le=3)]


@rest.get("/wx/leaderboard")
def wx_leaderboard(start: date | None = None, end: date | None = None, lead_days: WxLead | None = None):
    """Nostos vs benchmarks: MAE/bias/within-2°F per model and lead (omit lead_days for all three)."""
    return _run(tools.get_wx_leaderboard, start, end, lead_days)


@rest.get("/wx/trend")
def wx_trend(start: date | None = None, end: date | None = None, lead_days: WxLead = 1,
             model: Annotated[list[str] | None, Query(max_length=20)] = None):
    return _run(tools.get_wx_weekly_trend, start, end, lead_days, model)


@rest.get("/wx/cities")
def wx_cities(start: date | None = None, end: date | None = None, lead_days: WxLead = 1,
              model: Annotated[str, Query(max_length=40)] = "nostos"):
    return _run(tools.get_wx_cities, start, end, lead_days, model)


@rest.get("/wx/cities/{location}")
def wx_city(location: Annotated[str, Path(max_length=40)], start: date | None = None,
            end: date | None = None, lead_days: WxLead = 1):
    return _run(tools.get_wx_city, location, start, end, lead_days)


@rest.get("/wx/views/leaderboard")
def wx_view_leaderboard(lead_days: WxLead | None = None):
    """Test-period leaderboard view (nostos_wx.leaderboard_test); omit lead_days for all three."""
    return _run(wx_scores.leaderboard_test, lead_days)


@rest.get("/wx/views/cities")
def wx_view_cities():
    return _run(wx_scores.city_accuracy)


@rest.get("/wx/views/cities/{location}/timeline")
def wx_view_city_timeline(location: Annotated[str, Path(max_length=40, pattern=r"^[a-z_]+$")]):
    return _run(wx_scores.city_timeline, location)


@rest.get("/ships")
def ships_overview(
    categories: Annotated[str, Query(max_length=100, pattern=r"^[a-z ,-]*$")] = "cargo,tanker",
    cell_deg: Annotated[float, Query(ge=0.1, le=10)] = 1.0,
    limit: Annotated[int, Query(ge=1, le=5000)] = 3000,
):
    """Live ships for the globe, thinned to one per grid cell. categories="" means every type."""
    wanted = {c.strip() for c in categories.split(",") if c.strip()} or None
    return ships.tracker.overview(wanted, cell_deg, limit)


@rest.get("/ships/{mmsi}")
def ship_detail(mmsi: Annotated[int, Path(ge=100000000, le=999999999)]):
    return _run(tools.get_ship, mmsi)


@rest.get("/ships/{mmsi}/track")
def ship_track(
    mmsi: Annotated[int, Path(ge=100000000, le=999999999)],
    hours: Annotated[int, Query(ge=1, le=2160)] = 168,
):
    return _run(tools.get_ship_track, mmsi, hours)


def _stored_vessels() -> list[dict]:
    try:  # warm start only; the live feed fills in the same fields within minutes anyway
        return tools._query("SELECT * FROM vessels WHERE last_seen >= now() - interval '7 days'")
    except Exception as exc:
        logging.getLogger("ships").warning("could not load stored vessels: %s", exc)
        return []


CountryPath = Annotated[str, Path(min_length=2, max_length=60)]


@rest.get("/treasury/curve")
def treasury_curve():
    return _run(tools.get_treasury_curve)


@rest.get("/treasury/etfs")
def treasury_etfs(days: Annotated[int, Query(ge=5, le=365)] = 30):
    return _run(tools.get_treasury_etf_trend, days)


# Fixed /commodities/* paths go before /commodities/{commodity} so they aren't read as a commodity.
@rest.get("/commodities/headlines")
def commodity_headlines(
    country: Annotated[str | None, Query(min_length=2, max_length=60)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    return _run(tools.get_commodity_headlines, country, limit)


@rest.get("/commodities/markets")
def commodity_markets(commodity: tools.Commodity | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return _run(tools.get_commodity_markets, commodity, limit)


@rest.get("/commodities/etfs")
def commodity_etfs(commodity: tools.Commodity | None = None, days: Annotated[int, Query(ge=5, le=365)] = 30):
    return _run(tools.get_commodity_etfs, commodity, days)


@rest.get("/weather/grid")
def weather_grid():
    return _run(tools.get_weather_grid)


@rest.get("/weather/marine")
def marine_grid():
    return _run(tools.get_marine_grid)


class LatLon(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class VoyageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: tools.VoyageMode
    vessel: tools.VesselId = "container"
    start: LatLon | None = None
    end: LatLon | None = None
    start_port: tools.PortId | None = None
    end_port: tools.PortId | None = None
    depart: str | None = Field(default=None, max_length=40)
    speed_kn: float | None = Field(default=None, ge=2, le=30)
    dwell_h: float = Field(default=24, ge=0, le=240)


@rest.get("/voyage/options")
def voyage_options():
    return _run(tools.get_voyage_options)


@rest.post("/voyage/route")
def voyage_route(body: VoyageRequest):
    from voyage import router  # numpy, scikit-learn: load on first use, not at API start

    return _run(router.plan, mode=body.mode, vessel_id=body.vessel,
                start=(body.start.lat, body.start.lon) if body.start else None,
                end=(body.end.lat, body.end.lon) if body.end else None,
                start_port=body.start_port, end_port=body.end_port, depart=body.depart,
                speed_kn=body.speed_kn, dwell_h=body.dwell_h)


@rest.get("/tides")
def tides_overview():
    return _run(tools.get_tides)


@rest.get("/tides/{station_id}")
def tide_station(station_id: Annotated[str, Path(pattern=r"^(\d{7}|om-[a-z0-9-]{1,48})$")]):
    return _run(tools.get_tide_station, station_id)


@rest.get("/commodities/{commodity}")
def commodity_futures(commodity: tools.Commodity, days: Annotated[int, Query(ge=5, le=365)] = 30):
    return _run(tools.get_commodity_futures, commodity, days)


@rest.get("/commodities/{commodity}/news")
def commodity_news(commodity: tools.Commodity, limit: Annotated[int, Query(ge=1, le=50)] = 20):
    return _run(tools.get_commodity_news, commodity, limit)


@rest.get("/commodities/{commodity}/editorial")
def commodity_editorial(
    commodity: tools.Commodity,
    country: Annotated[str, Query(min_length=2, max_length=60)],
):
    return _run(tools.get_commodity_editorial, commodity, country)


@rest.get("/countries/{country}")
def country_rundown(country: CountryPath):
    return _run(tools.get_country_rundown, country)


@rest.get("/countries/{country}/editorial")
def country_editorial(country: CountryPath):
    return _run(tools.get_country_editorial, country)


@rest.get("/countries/{country}/filings")
def country_filings(
    country: CountryPath,
    q: Annotated[str | None, Query(max_length=100)] = None,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
):
    return _run(tools.get_country_filings, country, q, days)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp_app.lifespan(app):  # FastMCP's session manager must run
        ships.tracker.seed(await asyncio.to_thread(_stored_vessels))
        ships.tracker.start()  # AIS websocket; no-op without AISSTREAM_API_KEY
        yield
        await ships.tracker.stop()
    assistant_store.close_pool()


app = FastAPI(title="nostos API", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"ok": True}


app.include_router(ask.router)
app.include_router(rest)
app.include_router(assistant.make_router(require_user))
app.include_router(wx_live.make_router(require_user))

# The single Docker image ships the built React app here; local dev uses the Vite server instead.
WEB_DIST = FilePath(__file__).resolve().parent.parent / "web" / "dist"
if (WEB_DIST / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    # shortcut: one route per React page (the MCP root mount owns every other path); add new pages here.
    @app.get("/", include_in_schema=False)
    @app.get("/world", include_in_schema=False)
    @app.get("/search", include_in_schema=False)
    @app.get("/model", include_in_schema=False)
    def web_app():
        return FileResponse(WEB_DIST / "index.html")


app.mount("/", mcp_app)  # Last: the root mount would otherwise swallow /api.
