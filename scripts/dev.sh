#!/usr/bin/env bash
# Run Phlox backend + frontend together (macOS/Linux).
# Usage: ./scripts/dev.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Starting Phlox backend (:8000) and frontend (:5173)..."

( cd "$ROOT/backend" && uv run uvicorn app.main:app --reload --port 8000 ) &
BACKEND=$!
( cd "$ROOT/frontend" && npm run dev ) &
FRONTEND=$!

trap 'kill $BACKEND $FRONTEND 2>/dev/null || true' EXIT INT TERM
echo "Open http://localhost:5173  (Ctrl+C to stop)"
wait
