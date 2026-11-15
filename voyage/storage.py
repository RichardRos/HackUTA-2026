"""Speed-model artifacts on Vultr Object Storage, with local disk as the fallback.

Vultr Object Storage speaks the S3 API, so this is boto3 pointed at the Vultr endpoint:
    VULTR_OBJECT_STORAGE_ENDPOINT   e.g. https://ewr1.vultrobjects.com (the bucket's region host)
    VULTR_OBJECT_STORAGE_ACCESS_KEY / VULTR_OBJECT_STORAGE_SECRET_KEY
    VULTR_OBJECT_STORAGE_BUCKET     e.g. nostos-models
Layout:  voyage/models/<UTC stamp>.joblib   plus   voyage/models/latest.json -> {"key", "meta"}
The training job (a Vultr compute instance or `docker compose run voyage-train`) uploads; every API
replica reads latest.json and reloads when it changes, so a retrain needs no redeploy.

Without the bucket variables everything goes to VOYAGE_MODEL_DIR (default ./models/voyage).
Artifacts are pickles: only load them from a bucket you control.
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib

from voyage.model import SpeedModel, fit_speed_model, synthetic

PREFIX = "voyage/models/"
CHECK_EVERY_S = 3600
log = logging.getLogger("voyage")
_lock = threading.Lock()
_state: dict = {"model": None, "key": None, "checked": 0.0}


def local_dir() -> Path:
    path = Path(os.getenv("VOYAGE_MODEL_DIR", Path(__file__).resolve().parent.parent / "models" / "voyage"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _bucket():
    """(boto3 client, bucket) for Vultr Object Storage, or None when not configured."""
    endpoint = os.getenv("VULTR_OBJECT_STORAGE_ENDPOINT")
    bucket = os.getenv("VULTR_OBJECT_STORAGE_BUCKET")
    if not endpoint or not bucket:
        return None
    if not (os.getenv("VULTR_OBJECT_STORAGE_ACCESS_KEY") and os.getenv("VULTR_OBJECT_STORAGE_SECRET_KEY")):
        log.warning("VULTR_OBJECT_STORAGE_ACCESS_KEY/SECRET_KEY not both set: using the local model only")
        return None  # half-configured credentials made boto3 raise and broke every route
    import boto3

    client = boto3.client("s3", endpoint_url=endpoint if endpoint.startswith("http") else f"https://{endpoint}",
                          aws_access_key_id=os.getenv("VULTR_OBJECT_STORAGE_ACCESS_KEY"),
                          aws_secret_access_key=os.getenv("VULTR_OBJECT_STORAGE_SECRET_KEY"),
                          region_name="us-east-1")  # ignored by Vultr; boto3 wants one
    return client, bucket


def save(model: SpeedModel, remote: bool = True) -> str:
    """Write the artifact locally and, when configured (and `remote`), to Vultr Object Storage."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"{PREFIX}{stamp}.joblib"
    buf = io.BytesIO()
    joblib.dump(model, buf, compress=3)
    data = buf.getvalue()
    pointer = json.dumps({"key": key, "meta": model.meta}, indent=2).encode()
    (local_dir() / f"{stamp}.joblib").write_bytes(data)
    (local_dir() / "latest.json").write_bytes(pointer)
    bucket_conf = _bucket() if remote else None
    if bucket_conf:
        client, bucket = bucket_conf
        client.put_object(Bucket=bucket, Key=key, Body=data)
        client.put_object(Bucket=bucket, Key=f"{PREFIX}latest.json", Body=pointer, ContentType="application/json")
        log.info("uploaded %s to Vultr Object Storage bucket %s", key, bucket)
    return key


def _latest_key() -> tuple[str | None, str]:
    remote = _bucket()
    if remote:
        client, bucket = remote
        try:
            body = client.get_object(Bucket=bucket, Key=f"{PREFIX}latest.json")["Body"].read()
            return json.loads(body)["key"], "vultr"
        except Exception as exc:  # noqa: BLE001 (bucket empty or unreachable: fall back to disk)
            log.warning("no model in Vultr Object Storage (%s); trying local disk", exc)
    pointer = local_dir() / "latest.json"
    if pointer.is_file():
        return json.loads(pointer.read_text())["key"], "local"
    return None, "none"


def _load(key: str, where: str) -> SpeedModel:
    path = local_dir() / Path(key).name
    if not path.is_file() and where == "vultr":
        client, bucket = _bucket()
        path.write_bytes(client.get_object(Bucket=bucket, Key=key)["Body"].read())
    return joblib.load(path)


def current() -> SpeedModel:
    """The latest trained model, re-checked hourly. With no artifact anywhere, fit a small one on the
    physics prior (~10 s) so routing works out of the box, and keep it on local disk."""
    with _lock:
        if _state["model"] is not None and time.monotonic() - _state["checked"] < CHECK_EVERY_S:
            return _state["model"]
        _state["checked"] = time.monotonic()
        key, where = _latest_key()
        if key and key != _state["key"]:
            try:
                _state["model"], _state["key"] = _load(key, where), key
                log.info("loaded speed model %s from %s", key, where)
            except Exception as exc:  # noqa: BLE001 (corrupt or missing file: keep what we have)
                log.warning("could not load %s: %s", key, exc)
        if _state["model"] is None:
            log.info("no trained speed model; fitting a small one on the physics prior")
            X, y, groups = synthetic(rows_per_vessel=2500)
            model = fit_speed_model(X, y, groups, small=True, n_jobs=int(os.getenv("VOYAGE_N_JOBS", "2")))
            model.meta["note"] = "bootstrap model: physics prior only, run python -m voyage.train"
            _state["model"], _state["key"] = model, save(model, remote=False)  # never overwrite a real model in the bucket
        return _state["model"]
