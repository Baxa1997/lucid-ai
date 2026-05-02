#!/bin/bash
set -e

# Start virtual display for headless browser preview
echo "[start.sh] Starting Xvfb virtual display on :99..."
Xvfb :99 -screen 0 1280x720x24 -ac &
sleep 1

# Start FastAPI with uvicorn.
# IMPORTANT — --reload watcher is SCOPED to the source-code dirs.
# Default behavior watches the entire CWD (/app), which includes
# /app/storage/preview_ws/* where pnpm extracts node_modules. Some
# packages (e.g. flatted) ship .py files; the watcher would detect
# them and restart the backend mid-install — killing the WebSocket,
# the install subprocess, and the user's session.
echo "[start.sh] Starting FastAPI server..."
exec uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --reload-dir /app/app \
    --reload-dir /app/main.py \
    --reload-exclude '*/storage/*' \
    --reload-exclude '*/node_modules/*' \
    --reload-exclude '*/preview_ws/*' \
    --reload-exclude '*.lock' \
    --reload-exclude '*.log' \
    --ws-ping-interval 20 \
    --ws-ping-timeout 60 \
    --timeout-keep-alive 65
