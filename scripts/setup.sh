#!/usr/bin/env bash
# One-time native setup on macOS/Linux: Python venv (3.11 recommended) + frontend packages.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example"; }
(cd frontend && npm install)
echo -e "\nSetup complete. Start everything with: scripts/dev.sh"
