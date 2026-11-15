"""Run the speed-model training on a throwaway Vultr instance, then delete it.

    python -m voyage.vultr                       # 4 vCPU instance in ewr, default training args
    python -m voyage.vultr --dry-run             # print the request, create nothing
    python -m voyage.vultr -- --no-ais --n-jobs 4   # args after -- go to python -m voyage.train

Needs VULTR_API_KEY (Vultr console > Account > API, with this machine's IP allowed) and the
VULTR_OBJECT_STORAGE_* bucket, since the model and the log leave the instance only through it.
The instance clones the public repo, builds the image, runs the `voyage-train` job, uploads its log
to voyage/logs/<label>.log and powers off. This script polls until then, prints the log and deletes
the instance (also on Ctrl-C or timeout), so it bills for minutes, not a month. The API picks the
new model up from the bucket within an hour.
"""
from __future__ import annotations

import argparse
import base64
import os
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

API = "https://api.vultr.com/v2"
REPO = "https://github.com/RichardRos/HackUTA-2026.git"
# Only what the training job reads; VULTR_API_KEY never goes to the instance.
SENT = ["TIMESCALE_SERVICE_URL", "VITE_CLERK_PUBLISHABLE_KEY", "OPEN_METEO_API_KEY", "VOYAGE_N_JOBS",
        "VULTR_OBJECT_STORAGE_ENDPOINT", "VULTR_OBJECT_STORAGE_BUCKET",
        "VULTR_OBJECT_STORAGE_ACCESS_KEY", "VULTR_OBJECT_STORAGE_SECRET_KEY"]
REQUIRED = ["VULTR_API_KEY", "TIMESCALE_SERVICE_URL", "VITE_CLERK_PUBLISHABLE_KEY", "VULTR_OBJECT_STORAGE_ENDPOINT",
            "VULTR_OBJECT_STORAGE_BUCKET", "VULTR_OBJECT_STORAGE_ACCESS_KEY", "VULTR_OBJECT_STORAGE_SECRET_KEY"]


def user_data(env: dict[str, str], label: str, train_args: list[str], branch: str = "main") -> str:
    """cloud-init script: install Docker, train, upload the log, power off (even when training fails)."""
    dotenv = "".join(f"{k}={env[k]}\n" for k in SENT if env.get(k))
    cmd = " ".join(shlex.quote(a) for a in ["python", "-m", "voyage.train", *train_args])
    upload = ("from voyage import storage; c, b = storage._bucket(); "
              f"c.upload_file('/tmp/train.log', b, 'voyage/logs/{label}.log')")
    return f"""#!/bin/bash
exec > /root/train.log 2>&1
trap 'poweroff' EXIT
curl -fsSL https://get.docker.com | sh
git clone --depth 1 --branch {shlex.quote(branch)} {REPO} /root/app && cd /root/app
echo {base64.b64encode(dotenv.encode()).decode()} | base64 -d > .env
docker compose --profile train run --rm voyage-train {cmd}
echo "training exit $?"
cp /root/train.log /root/upload.log
docker compose --profile train run --rm -v /root/upload.log:/tmp/train.log:ro voyage-train python -c "{upload}"
"""


def _os_id(client: httpx.Client) -> int:
    for os_ in client.get("/os", params={"per_page": 500}).json()["os"]:
        if os_["name"] == "Ubuntu 24.04 LTS x64":
            return os_["id"]
    raise RuntimeError("Ubuntu 24.04 LTS x64 not in Vultr's OS list")


def _log(label: str) -> str:
    from voyage import storage

    client, bucket = storage._bucket()
    try:
        return client.get_object(Bucket=bucket, Key=f"voyage/logs/{label}.log")["Body"].read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001 (instance died before uploading)
        return f"(no log in the bucket: {exc})"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ours, train_args = (argv[:argv.index("--")], argv[argv.index("--") + 1:]) if "--" in argv else (argv, [])
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", default="ewr")
    p.add_argument("--plan", default="vc2-4c-8gb")
    p.add_argument("--branch", default="main")
    p.add_argument("--timeout-min", type=int, default=45)
    p.add_argument("--keep", action="store_true", help="do not delete the instance afterwards")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(ours)
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    env = {k: os.getenv(k, "") for k in set(SENT) | set(REQUIRED)}
    missing = [k for k in REQUIRED if not env[k]]
    label = "nostos-train-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    script = user_data(env, label, train_args, args.branch)
    if args.dry_run:
        print(f"POST {API}/instances region={args.region} plan={args.plan} label={label}")
        print(user_data({}, label, train_args, args.branch))  # secrets left out of the printout
        print("missing:", ", ".join(missing) or "nothing")
        return 0
    if missing:
        sys.exit(f"set in .env: {', '.join(missing)}")

    client = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {env['VULTR_API_KEY']}"}, timeout=30)
    r = client.post("/instances", json={"region": args.region, "plan": args.plan, "os_id": _os_id(client),
                                        "label": label, "user_data": base64.b64encode(script.encode()).decode()})
    r.raise_for_status()
    iid = r.json()["instance"]["id"]
    print(f"created {label} ({iid}); training, polling every 30 s")
    ran, deadline = False, time.monotonic() + args.timeout_min * 60
    try:
        while time.monotonic() < deadline:
            time.sleep(30)
            inst = client.get(f"/instances/{iid}").json()["instance"]
            ran = ran or inst["power_status"] == "running"
            print(f"  {inst['status']} / {inst['power_status']} / {inst['server_status']}", flush=True)
            if ran and inst["power_status"] == "stopped":  # cloud-init powers off when done
                break
        else:
            print(f"timed out after {args.timeout_min} min")
        print(_log(label)[-4000:])
    finally:
        if args.keep:
            print(f"kept instance {iid}: delete it in the Vultr console")
        else:
            client.delete(f"/instances/{iid}").raise_for_status()
            print(f"deleted instance {iid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
