# Running nostos on Vultr

Everything runs from `compose.yaml` on one Vultr Cloud Compute instance. The voyage speed model is
trained by a one-shot job and published to Vultr Object Storage, which every API replica reads.

```
            Cloudflare Tunnel (trynostos.biz)
                        │
┌───────────────── Vultr Cloud Compute (Ubuntu + Docker) ─────────────────┐
│  api        uvicorn: React app, REST /api/*, MCP /mcp, voyage router     │
│  ingest     scheduled feeds -> Timescale                                 │
│  voyage-train (profile "train", cron)   AIS + hindcast -> SuperLearner   │
└───────────────┬───────────────────────────────┬─────────────────────────┘
                │ models/voyage/latest.json     │ ship_positions, vessels
     Vultr Object Storage (S3 API)       Timescale / Vultr Managed PostgreSQL
```

## 1. Instance

- **Cloud Compute, CPU-optimized, 4 vCPU / 8 GB** is enough. Routing is CPU-bound (A* plus one
  batched SuperLearner prediction per leg) and needs about 1 GB per API worker. No GPU is used.
- Training takes 1–3 minutes on 4 vCPU with the default sizes (`--prior-rows 8000`, up to 15k AIS
  fixes). Set `VOYAGE_N_JOBS` to the vCPU count.
- Firewall: allow SSH only. The tunnel makes outbound connections, so no inbound HTTP port is needed.

```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/RichardRos/HackUTA-2026.git && cd HackUTA-2026
cp .env.example .env   # fill it in (below)
docker compose up -d --build                     # api + ingest
docker compose --profile tunnel up -d            # public URL through Cloudflare
```

## 2. Object Storage for the speed model

1. Vultr console > Object Storage > add (pick the instance's region) > create a bucket, e.g. `nostos-models`.
2. Copy the hostname and keys into `.env`:

```
VULTR_OBJECT_STORAGE_ENDPOINT=https://ewr1.vultrobjects.com
VULTR_OBJECT_STORAGE_BUCKET=nostos-models
VULTR_OBJECT_STORAGE_ACCESS_KEY=...
VULTR_OBJECT_STORAGE_SECRET_KEY=...
```

Artifacts land at `voyage/models/<UTC stamp>.joblib`, with `voyage/models/latest.json` pointing to
the newest. The API checks the pointer hourly and hot-swaps the model, so retraining never needs a
redeploy, and several API instances (behind a Vultr Load Balancer) all serve the same model. Keep
the bucket private: artifacts are pickles, so load them only from a bucket you control.

Without these variables the model lives only in the `voyage-models` Docker volume. If no model
exists at all, the API fits a small physics-prior model on its first route request (~10 s).

## 3. Train

```bash
docker compose --profile train run --rm voyage-train                      # AIS + prior, upload
docker compose --profile train run --rm voyage-train python -m voyage.train --no-ais   # prior only
```

Weekly, via cron on the instance:

```
0 3 * * 1  cd /root/HackUTA-2026 && docker compose --profile train run --rm voyage-train >> /var/log/voyage-train.log 2>&1
```

The job reads `ship_positions` from `TIMESCALE_SERVICE_URL` (read-only) and fetches the past waves,
wind and currents for each fix from Open-Meteo. Open-Meteo keeps about 3 months of past data, which
matches the 90-day AIS retention. The log prints cross-validated R², the base-learner weights, the
conformal interval, and how far the physics prior alone is off on real AIS rows.

## 4. Forecast quota

Routing fetches hourly forecasts for 2° lattice points around each route, cached for 3 hours per
process. Open-Meteo's free tier (600 points/min, 10k/day) is shared with the weather, wave and tide
layers. A new ocean crossing costs ~300–700 points; repeated or nearby routes cost nothing. For
production, set `OPEN_METEO_API_KEY` (commercial endpoints). When the free tier throttles, the API
answers 502 with "try again in a minute".

## 5. Scaling out

- Run more API instances behind a **Vultr Load Balancer**. Each builds the ocean grid on its first
  route (~2 s, ~60 MB) and reads the model from Object Storage.
- Moving the database to **Vultr Managed PostgreSQL** (with the TimescaleDB extension) needs only a
  new `TIMESCALE_SERVICE_URL`.
- The forecast cache is per process. With many replicas, put Redis (Vultr Managed Valkey) in front of
  `voyage/conditions.py` if the Open-Meteo quota becomes the limit.
