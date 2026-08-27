#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  source <(sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' .env)
  set +a
fi

export PYTHONPATH="$PWD"
export PYTHONUNBUFFERED=1

API_HOST="${OCTOPUS_API_HOST:-0.0.0.0}"
API_PORT="${OCTOPUS_API_PORT:-8090}"
MCP_URL="http://${OCTOPUS_MCP_HOST:-0.0.0.0}:${OCTOPUS_MCP_PORT:-8100}${OCTOPUS_MCP_PATH:-/mcp}"

PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  if [ -x .venv/bin/python ]; then
    PYTHON_BIN=".venv/bin/python"
  else
    PYTHON_BIN="python"
  fi
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  echo "Activate the virtual environment or set PYTHON=/path/to/python." >&2
  exit 1
fi

api_pid=""
mcp_pid=""

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  for pid in "$mcp_pid" "$api_pid"; do
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  wait "$mcp_pid" "$api_pid" >/dev/null 2>&1 || true
  exit "$exit_code"
}

wait_for_api() {
  local url="http://127.0.0.1:${API_PORT}/health"
  local attempts=60
  for _ in $(seq 1 "$attempts"); do
    if "$PYTHON_BIN" -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=2).read()' "$url" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$api_pid" >/dev/null 2>&1; then
      wait "$api_pid"
      return $?
    fi
    sleep 1
  done
  echo "Octopus Video API did not become healthy at $url" >&2
  return 1
}

trap cleanup EXIT INT TERM

echo "Starting Octopus Video API with $PYTHON_BIN on http://${API_HOST}:${API_PORT}"
"$PYTHON_BIN" -m uvicorn app.video_main:app --host "$API_HOST" --port "$API_PORT" --log-level info &
api_pid=$!

wait_for_api

export OCTOPUS_VIDEO_API_BASE_URL="${OCTOPUS_VIDEO_API_BASE_URL:-http://127.0.0.1:${API_PORT}}"
echo "Starting Octopus MCP Server on $MCP_URL"
"$PYTHON_BIN" -m app.mcp_server &
mcp_pid=$!

wait -n "$api_pid" "$mcp_pid"
