#!/usr/bin/env bash
# Nightly Nostos run (Vultr cron, 10:00 UTC): fetch -> daily highs -> blend -> live forecast -> Tiger.
#
#   ./run_pipeline.sh               END_DATE = yesterday (UTC)
#   ./run_pipeline.sh 2026-11-14    explicit END_DATE (or set END_DATE in the environment)
#   VENV=/path/to/.venv ./run_pipeline.sh   another venv (default: model/.venv)
#
# Stops at the first failing step. Every step is logged with a UTC timestamp to logs/pipeline.log.
# The leaderboard's validation/test windows are fixed in config.py (TEST_END) and do not move.
set -uo pipefail

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$MODEL_DIR/logs"
LOG="$LOG_DIR/pipeline.log"
VENV="${VENV:-$MODEL_DIR/.venv}"
export END_DATE="${1:-${END_DATE:-$(date -u -d yesterday +%F)}}"
export PYTHONUNBUFFERED=1

mkdir -p "$LOG_DIR"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG"; }

# One run at a time: a slow night must not overlap the next cron run.
exec 9>"$LOG_DIR/pipeline.lock"
if ! flock -n 9; then
    log "SKIP another pipeline run is still in progress"
    exit 1
fi

if [ ! -f "$VENV/bin/activate" ]; then
    log "FAIL no virtualenv at $VENV"
    exit 1
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
cd "$MODEL_DIR" || exit 1

log "=== pipeline start END_DATE=$END_DATE python=$(command -v python)"
run_start=$SECONDS
for step in fetch_forecasts fetch_obs daily_high blend predict_live load_to_db; do
    log "START $step"
    step_start=$SECONDS
    python "$step.py" >>"$LOG" 2>&1
    status=$?
    if [ $status -ne 0 ]; then
        log "FAIL $step (exit $status after $((SECONDS - step_start))s); stopping"
        exit $status
    fi
    log "OK $step ($((SECONDS - step_start))s)"
done
log "=== pipeline done ($((SECONDS - run_start))s)"
