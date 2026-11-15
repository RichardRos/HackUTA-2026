"""Route computation as a service, run on a Vultr Cloud Compute instance (compose.voyage.yaml).

    POST /plan     body: voyage.router.plan keyword arguments; header Authorization: Bearer $VOYAGE_SERVICE_TOKEN
    GET  /health   no auth: model and grid loaded?

The nostos API forwards routes here when VOYAGE_REMOTE_URL is set (voyage/remote.py). Caddy in front
terminates HTTPS; this process is never published directly.
"""
from __future__ import annotations

import hmac
import os
import threading
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException

from voyage import conditions, grid, router, storage

PLAN_ARGS = {"mode", "vessel_id", "start", "end", "start_port", "end_port", "depart", "speed_kn", "dwell_h"}
app = FastAPI(title="nostos voyage service", docs_url=None, redoc_url=None, openapi_url=None)
_ready = threading.Event()


def _warm() -> None:
    storage.current()
    grid.ocean()
    _ready.set()


@app.on_event("startup")
def _startup() -> None:
    if not os.getenv("VOYAGE_SERVICE_TOKEN"):
        raise RuntimeError("VOYAGE_SERVICE_TOKEN is not set")
    threading.Thread(target=_warm, daemon=True).start()


def _auth(authorization: str = Header(default="")) -> None:
    token = os.getenv("VOYAGE_SERVICE_TOKEN", "")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "bad token")


@app.get("/health")
def health() -> dict:
    return {"ready": _ready.is_set()}


@app.post("/plan", dependencies=[Depends(_auth)])
def plan(body: dict[str, Any]) -> dict:
    if extra := set(body) - PLAN_ARGS:
        raise HTTPException(400, f"unknown arguments: {sorted(extra)}")
    for k in ("start", "end"):
        if body.get(k) is not None:
            body[k] = tuple(float(x) for x in body[k])  # JSON has no tuples; router keys ports by (lat, lon)
    try:
        return router.plan(**body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (RuntimeError, httpx.HTTPError, conditions.RateLimited) as exc:
        raise HTTPException(502, f"Upstream source failed: {exc}") from exc
