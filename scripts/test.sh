#!/usr/bin/env bash
# Runs every test suite: three Python services + the frontend.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
failed=()
for svc in chat voice wakeword; do
  echo -e "\n=== $svc ==="
  (cd "$ROOT/services/$svc" && "$PY" -m pytest -q) || failed+=("$svc")
done
echo -e "\n=== frontend ==="
(cd "$ROOT/frontend" && npm test && npx tsc -b) || failed+=(frontend)
if [ ${#failed[@]} -gt 0 ]; then echo "FAILED: ${failed[*]}"; exit 1; fi
echo "All test suites passed."
