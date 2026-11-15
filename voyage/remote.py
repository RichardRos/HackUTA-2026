"""voyage.router.plan, computed on the Vultr voyage service when VOYAGE_REMOTE_URL is set.

If the service is unreachable or errors (not a 400), the route is computed in this process instead, so
the UI keeps working; `computed_on` in the result says which ran.
"""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("voyage")


def plan(**kw) -> dict:
    url = os.getenv("VOYAGE_REMOTE_URL", "").rstrip("/")
    if url:
        try:
            r = httpx.post(f"{url}/plan", json=kw, timeout=httpx.Timeout(90.0, connect=5.0),
                           headers={"Authorization": f"Bearer {os.getenv('VOYAGE_SERVICE_TOKEN', '')}"})
        except httpx.HTTPError as exc:
            log.warning("voyage service %s unreachable (%s); computing locally", url, exc)
        else:
            if r.status_code == 400:  # the request itself is wrong: computing locally would fail the same way
                raise ValueError(r.json().get("detail", "bad route request"))
            if r.is_success:
                return {**r.json(), "computed_on": "vultr"}
            log.warning("voyage service %s answered %s; computing locally", url, r.status_code)
    from voyage import router  # numpy, scikit-learn: load on first use

    return {**router.plan(**kw), "computed_on": "local"}
