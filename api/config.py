"""Environment loading, shared by the app and the command-line helpers.

Same lookup as db/run_sql.py (load_dotenv never overrides a variable already set):
  1. real environment variables (a blank TIMESCALE_SERVICE_URL is ignored)
  2. the Tiger credentials file named by NOSTOS_ENV
     (default: ../Research/tiger-cloud-db-nostos_weather-credentials.env)
  3. <repo>/.env        also where GROK_BOT_* and NOSTOS_ANSWER_SECRET live
"""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB_ENV = REPO.parent / "Research" / "tiger-cloud-db-nostos_weather-credentials.env"


def load_env() -> None:
    if not os.getenv("TIMESCALE_SERVICE_URL", "").strip():
        os.environ.pop("TIMESCALE_SERVICE_URL", None)
    load_dotenv(os.getenv("NOSTOS_ENV", DEFAULT_DB_ENV))
    load_dotenv(REPO / ".env")
