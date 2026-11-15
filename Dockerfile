FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.ingest.txt ./
RUN pip install --no-cache-dir -r requirements.ingest.txt

RUN useradd --create-home --uid 10001 ingest
COPY --chown=ingest:ingest markets.py news.py open_feeds.py ingest_service.py ships.py ./
COPY --chown=ingest:ingest ingest/common.py ingest/ships_ais.py ingest/ais_dk.py ./ingest/
COPY --chown=ingest:ingest db ./db

USER ingest
EXPOSE 8001

CMD ["python", "ingest_service.py", "start", "--host", "0.0.0.0", "--port", "8001"]
