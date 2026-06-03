#!/usr/bin/env bash
# =============================================================================
# start_mood_worker.sh — Mood photo background removal Celery worker
#
# Starts the dedicated mood_photos queue worker that processes background
# removal jobs queued by the /profile/my-mood-photos/{slot}/remove-bg route.
#
# Prerequisites:
#   - Redis must be running (default: localhost:6379)
#   - DATABASE_URL must be set (or defaults to .env value)
#   - BG_REMOVAL_PROCESSOR=rembg must be set to enable real processing
#   - rembg + onnxruntime-cpu must be installed (see requirements.txt)
#
# Usage:
#   bash scripts/start_mood_worker.sh          # foreground (Ctrl+C to stop)
#   bash scripts/start_mood_worker.sh --detach # background (logs to logs/)
#
# Or via Makefile:
#   make worker-mood
#
# PRODUCTION NOTE:
#   In production/staging this process must be managed by your process
#   supervisor (docker-compose, systemd, Heroku Procfile, etc.).
#   Do NOT rely on running this script manually in production.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# ── Load .env if present ──────────────────────────────────────────────────────
if [ -f ".env" ]; then
    set -a; source .env; set +a
fi

# ── Validate BG_REMOVAL_PROCESSOR ────────────────────────────────────────────
BG_PROC="${BG_REMOVAL_PROCESSOR:-null}"
if [ "$BG_PROC" = "null" ]; then
    echo "⚠️  WARNING: BG_REMOVAL_PROCESSOR=null — worker will run but no real"
    echo "   background removal occurs. Set BG_REMOVAL_PROCESSOR=rembg in .env"
    echo "   to enable actual processing."
fi

# ── Log directory ─────────────────────────────────────────────────────────────
mkdir -p logs

# ── Detach mode ──────────────────────────────────────────────────────────────
DETACH="${1:-}"
if [ "$DETACH" = "--detach" ] || [ "$DETACH" = "-d" ]; then
    LOG_FILE="logs/mood_worker_$(date +%Y%m%d_%H%M%S).log"
    echo "🚀  Starting mood_photos worker in background..."
    echo "    Logs: $LOG_FILE"
    nohup celery -A app.celery_app worker \
        -Q mood_photos \
        --pool=solo \
        --concurrency=1 \
        --loglevel=info \
        --logfile="$LOG_FILE" \
        > /dev/null 2>&1 &
    WORKER_PID=$!
    echo "    PID: $WORKER_PID"
    echo "    Stop with: kill $WORKER_PID"
    echo "$WORKER_PID" > logs/mood_worker.pid
    exit 0
fi

# ── Foreground mode (default) ─────────────────────────────────────────────────
echo "🚀  Starting mood_photos Celery worker (foreground)..."
echo "    Queue:     mood_photos"
echo "    Processor: $BG_PROC"
echo "    Press Ctrl+C to stop."
echo ""
exec celery -A app.celery_app worker \
    -Q mood_photos \
    --pool=solo \
    --concurrency=1 \
    --loglevel=info
