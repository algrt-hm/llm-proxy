#!/bin/sh
# Kill any running llm-proxy instance (uvicorn on LLM_PROXY_PORT, default 6969
# to match the makefile run targets).
set -e

PORT="${LLM_PROXY_PORT:-6969}"
PIDS=$(lsof -ti "tcp:$PORT" 2>/dev/null) || true

if [ -z "$PIDS" ]; then
    echo "No process listening on port $PORT"
    exit 0
fi

echo "Killing PIDs on port $PORT: $PIDS"
echo "$PIDS" | xargs kill
