#!/bin/bash
set -e

# Start virtual display for headless browser preview
echo "[start.sh] Starting Xvfb virtual display on :99..."
Xvfb :99 -screen 0 1280x720x24 -ac &
sleep 1

# Start FastAPI with uvicorn
echo "[start.sh] Starting FastAPI server..."
exec uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --ws-ping-interval 20 \
    --ws-ping-timeout 60 \
    --timeout-keep-alive 65
