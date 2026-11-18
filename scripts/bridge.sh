#!/usr/bin/env bash
# Starts the Claude bridge on this computer, so the assistant can use Claude with your Claude
# subscription through Claude Code. Keep it running while you use it; Ctrl+C stops it.
# Needs Claude Code installed and signed in (run `claude` once and sign in).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
PORT="${1:-8005}"
# Docker on Linux reaches this computer through the docker0 bridge, not 127.0.0.1:
# CLAUDE_BRIDGE_HOST=0.0.0.0 scripts/bridge.sh (the token keeps others out).
HOST="${CLAUDE_BRIDGE_HOST:-127.0.0.1}"
[ -x "$PY" ] || { echo "Virtualenv not found - run scripts/setup.sh first." >&2; exit 1; }

# The chat service and the bridge share a token from .env; create one the first time.
ENV_FILE="$ROOT/.env"
[ -f "$ENV_FILE" ] || cp "$ROOT/.env.example" "$ENV_FILE"
if ! grep -Eq '^\s*CLAUDE_BRIDGE_TOKEN\s*=\s*\S' "$ENV_FILE"; then
  token="$("$PY" -c 'import secrets; print(secrets.token_hex(32))')"
  grep -Ev '^\s*CLAUDE_BRIDGE_TOKEN\s*=' "$ENV_FILE" > "$ENV_FILE.tmp" || true
  echo "CLAUDE_BRIDGE_TOKEN=$token" >> "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  echo "Added CLAUDE_BRIDGE_TOKEN to .env."
  echo "If the assistant runs in Docker, restart its chat service: docker compose up -d chat"
fi

echo "Claude bridge on http://$HOST:$PORT (Ctrl+C stops it)"
cd "$ROOT/services/bridge"
exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
