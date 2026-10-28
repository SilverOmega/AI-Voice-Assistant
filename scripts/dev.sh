#!/usr/bin/env bash
# Runs the three backend services and the Vite dev server natively. Ctrl+C stops everything.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "Virtualenv not found - run scripts/setup.sh first." >&2; exit 1; }
export WAKEWORD_CUSTOM_DIR="$ROOT/models/wakeword"

pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT
for svc in chat:8001 voice:8002 wakeword:8003; do
  name="${svc%%:*}"; port="${svc##*:}"
  echo "Starting $name service on :$port"
  (cd "$ROOT/services/$name" && exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$port") &
  pids+=($!)
done
echo "Open http://localhost:5173"
cd "$ROOT/frontend" && npm run dev
