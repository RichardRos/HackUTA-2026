import os
from pathlib import Path as FilePath
from typing import Annotated

from dotenv import load_dotenv

load_dotenv(FilePath(__file__).resolve().with_name(".env"))

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Path, Query  # noqa: E402
from fastmcp.server.auth.providers.jwt import JWTVerifier  # noqa: E402

import mcp_server as tools  # noqa: E402
from mcp_server import app as mcp_app  # noqa: E402

CLERK_DOMAIN = os.getenv("CLERK_DOMAIN", "").removeprefix("https://").rstrip("/")
WEB_ORIGINS = os.getenv("WEB_ORIGINS", "http://localhost:5173,http://localhost:8080").split(",")
_verifier = JWTVerifier(
    jwks_uri=f"https://{CLERK_DOMAIN}/.well-known/jwks.json",
    issuer=f"https://{CLERK_DOMAIN}",
) if CLERK_DOMAIN else None


async def require_user(authorization: Annotated[str | None, Header()] = None) -> str:
    """Verify the Clerk session token the web app sends as a bearer token."""
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


# Tools are called directly, so their pydantic bounds don't apply: repeat them on each query param.
Text = Annotated[str | None, Query(max_length=200)]
Limit = Annotated[int, Query(ge=1, le=500)]
api = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


@api.get("/regions/{region}")
def region_context(
    region: Annotated[str, Path(max_length=16)],
    q: Text = None,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
):
    return _run(tools.get_region_context, region, q, hours)


@api.get("/search")
def search(q: Annotated[str, Query(min_length=1, max_length=500)], limit: Limit = 25):
    return _run(tools.semantic_search, q, limit)


@api.get("/markets")
def markets(q: Text = None, limit: Limit = 50):
    return _run(tools.search_prediction_markets, q=q, limit=limit)


@api.get("/news")
def news(q: Text = None, hours: Annotated[int, Query(ge=1, le=8760)] = 168, limit: Limit = 50):
    return _run(tools.get_news, q=q, hours=hours, limit=limit)


app = FastAPI(title="Nostos API", lifespan=mcp_app.lifespan)
app.include_router(api)
app.mount("/", mcp_app)  # Last: the root mount would otherwise swallow /api.
