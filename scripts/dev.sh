#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

redis-cli ping >/dev/null 2>&1 || { echo "redis is not running: brew services start redis"; exit 1; }

uv run celery -A app.worker.celery_app.celery worker --loglevel=info --pool=prefork --concurrency=2 &
WORKER=$!
uv run uvicorn app.main:app --port 8000 &
API=$!
(cd frontend && npm run dev) &
UI=$!

trap 'kill $WORKER $API $UI 2>/dev/null || true' EXIT
echo "API http://localhost:8000/docs   UI http://localhost:5173"
wait
