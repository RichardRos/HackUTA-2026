"""nostos API.  Run from the repo root with ONE worker (open WebSockets live in memory):

    uvicorn api.main:app --proxy-headers

--proxy-headers matters behind a tunnel or reverse proxy (cloudflared, nginx on Vultr):
without it every visitor looks like 127.0.0.1 and they all share one rate limit.
If you ever need several workers, replace the socket dict with Postgres LISTEN/NOTIFY.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api import ask, assistant_store
from api.config import load_env

load_env()   # modules read env vars at call time, so loading after import is fine


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    assistant_store.close_pool()


app = FastAPI(title="nostos API", lifespan=lifespan)
app.include_router(ask.router)


@app.get("/api/health")
def health():
    return {"ok": True}
