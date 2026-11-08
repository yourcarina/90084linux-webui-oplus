#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${EDL_BENCH_PORT:-8765}"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r server/requirements.txt
fi

./.venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port "${PORT}" --reload &
BACK=$!
trap 'kill $BACK 2>/dev/null || true' EXIT
cd web
[ -d node_modules ] || npm install
npm run dev
