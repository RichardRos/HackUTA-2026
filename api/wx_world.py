"""Global Nostos for the website (live, any point on Earth):

    GET  /api/wx/world/point?lat=&lon=   conditions, D+1..D+3 Nostos high, per-model rankings
    POST /api/wx/world/route             Odyssey: rank routes (and departure hours) by weather hazard

The logic lives in model/world_wx.py (Open-Meteo live run + previous runs, ERA5 as truth).
"""
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

MODEL_DIR = Path(__file__).resolve().parent.parent / "model"
if str(MODEL_DIR) not in sys.path:
    sys.path.append(str(MODEL_DIR))  # appended, so repo-root modules still win on a name clash

import world_wx  # noqa: E402

Lat = Annotated[float, Field(ge=-90, le=90)]
Lon = Annotated[float, Field(ge=-180, le=180)]


PlaceName = Annotated[str, Field(min_length=1, max_length=60)]


class Route(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=40)]
    # [lat, lon] or [lat, lon, "Waco"]; a name labels that spot in the "why" line
    waypoints: Annotated[list[tuple[Lat, Lon] | tuple[Lat, Lon, PlaceName]], Field(min_length=2, max_length=50)]


class RouteRequest(BaseModel):
    routes: Annotated[list[Route], Field(min_length=1, max_length=5)]
    depart_at: datetime | None = None          # UTC; naive times are read as UTC; default next whole hour
    speed_kmh: Annotated[float, Field(gt=5, le=300)] = 80
    optimize_departure: bool = False
    window_hours: Annotated[int, Field(ge=0, le=72)] = 24


def _run(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:  # Open-Meteo down, timed out or returned an error
        raise HTTPException(502, f"Weather source failed: {exc}") from exc


def make_router(require_user) -> APIRouter:
    router = APIRouter(prefix="/api/wx/world", tags=["wx"])

    @router.get("/point")
    def world_point(
        user: Annotated[str, Depends(require_user)],
        lat: Annotated[float, Query(ge=-90, le=90)],
        lon: Annotated[float, Query(ge=-180, le=180)],
    ):
        """Live conditions, D+1..D+3 Nostos high and per-model rankings vs ERA5 at one point."""
        return _run(world_wx.point, lat, lon)

    @router.post("/route")
    def world_route(user: Annotated[str, Depends(require_user)], body: RouteRequest):
        """Rank routes best first: score = Σ hazard × road hours + total hours (lower is better)."""
        return _run(world_wx.routes, [r.model_dump() for r in body.routes], body.depart_at, body.speed_kmh,
                    body.optimize_departure, body.window_hours)

    return router
