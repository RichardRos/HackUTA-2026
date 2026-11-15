"""Global Nostos for the website: GET /api/wx/world/point?lat=&lon= (live, any point on Earth).

The logic lives in model/world_wx.py (Open-Meteo live run + previous runs, ERA5 as truth).
"""
import sys
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

MODEL_DIR = Path(__file__).resolve().parent.parent / "model"
if str(MODEL_DIR) not in sys.path:
    sys.path.append(str(MODEL_DIR))  # appended, so repo-root modules still win on a name clash

import world_wx  # noqa: E402


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

    return router
