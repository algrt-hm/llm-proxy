#!/bin/sh
# Kill any running llm-proxy instance (uvicorn on LLM_PROXY_PORT, default 8000).
set -e

PORT="${LLM_PROXY_PORT:-8000}"
PIDS=$(lsof -ti "tcp:$PORT" 2>/dev/null) || true

if [ -z "$PIDS" ]; then
    echo "No process listening on port $PORT"
    exit 0
fi

echo "Killing PIDs on port $PORT: $PIDS"
echo "$PIDS" | xargs kill
