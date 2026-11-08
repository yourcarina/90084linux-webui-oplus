#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
PORT="${EDL_BENCH_PORT:-8765}"

if [ ! -x .venv/bin/python ]; then
  echo "[edl-bench] creating venv"
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r server/requirements.txt
fi

if [ ! -f web/dist/index.html ] || [ web/src -nt web/dist/index.html ]; then
  echo "[edl-bench] building web"
  (cd web && { [ -d node_modules ] || npm install; } && npm run build)
fi

echo "[edl-bench] http://127.0.0.1:${PORT}"
exec ./.venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port "${PORT}"
