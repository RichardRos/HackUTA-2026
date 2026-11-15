# Nostos nightly run on Vultr

The server is already set up: repo at `/root/HackUTA-2026`, venv at `model/.venv`,
`model/.env` and the data cache in `model/data/`. This page covers only the day-to-day commands.
The run fetches forecasts and official highs through yesterday (UTC), rebuilds Nostos, writes
tomorrow's live forecast and loads everything into Tiger (`nostos_wx`).

## Update the code

```bash
cd /root/HackUTA-2026
git pull
model/.venv/bin/pip install -r model/requirements.txt   # only needed when requirements.txt changed
```

## Run the pipeline manually

```bash
/root/HackUTA-2026/model/run_pipeline.sh              # END_DATE = yesterday (UTC)
/root/HackUTA-2026/model/run_pipeline.sh 2026-11-14   # or a specific END_DATE
```

It exits non-zero and stops at the first failing step. A run takes about 2 minutes.

## Cron (10:00 UTC daily)

`crontab -e` as root, add:

```cron
0 10 * * * mkdir -p /root/HackUTA-2026/model/logs && /root/HackUTA-2026/model/run_pipeline.sh >> /root/HackUTA-2026/model/logs/cron.log 2>&1
```

Cron uses the server's clock. Vultr's Ubuntu images run in UTC; `date` should print `UTC`.

## Check the logs

```bash
cd /root/HackUTA-2026/model/logs
grep -E 'START|OK|FAIL|SKIP|===' pipeline.log | tail -20   # one line per step, UTC timestamps
tail -n 100 pipeline.log                                    # full output of the latest run
cat cron.log                                                # only if a run never started (cron/script errors)
```

A good night ends with `=== pipeline done`. A failure shows `FAIL <step> (exit N ...)`; the
lines above it in `pipeline.log` are that step's error.
