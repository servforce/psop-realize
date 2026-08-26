#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source <(sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' .env)
  set +a
fi

export OCTOPUS_VIDEO_API_BASE_URL="${OCTOPUS_VIDEO_API_BASE_URL:-http://127.0.0.1:8090}"
export PYTHONPATH="$PWD"
python -m app.mcp_server
