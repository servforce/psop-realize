#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
version=$(python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)
major=${version%%.*}
minor=${version#*.}
if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 11 ]; }; then
  echo "Python 3.11+ is required. Current python3 is $version." >&2
  exit 1
fi
export PYTHONPATH="$PWD"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8090 --reload