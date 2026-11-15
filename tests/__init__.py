import os

# Tests never touch the shared api_cache table or skip sign-in, even inside the dev container that enables both.
os.environ["NOSTOS_DB_CACHE"] = "0"
os.environ["DEV_AUTH_BYPASS"] = "0"
