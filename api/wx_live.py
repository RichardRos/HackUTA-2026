"""Live Nostos forecast for the website: GET /api/wx/forecast/tomorrow.

Read-only. Rows come from nostos_wx.live_forecasts, written nightly by model/predict_live.py.
The other /api/wx/* endpoints (leaderboard, trend, cities, city) stay in api/main.py.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

import wx_scores


def make_router(require_user) -> APIRouter:
    router = APIRouter(prefix="/api/wx", tags=["wx"])

    @router.get("/forecast/tomorrow")
    def forecast_tomorrow(user: Annotated[str, Depends(require_user)]):
        """Per city: tomorrow's Nostos high, NBM (raw and corrected) and each raw model, °F."""
        try:
            return wx_scores.forecast_tomorrow()
        except RuntimeError as exc:  # e.g. no database URL configured
            raise HTTPException(502, f"Upstream source failed: {exc}") from exc

    return router
