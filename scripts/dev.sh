#!/usr/bin/env bash
# Runs the backend (uvicorn --reload) and frontend (next dev) together.
# Ctrl+C stops both. Safe to re-run anytime.
set -euo pipefail
cd "$(dirname "$0")/.."

test -d .venv || { echo "No .venv found — run 'make setup' first."; exit 1; }
test -d frontend/node_modules || { echo "frontend/node_modules missing — run 'make setup' first."; exit 1; }

pids=()
cleanup() {
  echo
  echo "Stopping dev servers..."
  kill "${pids[@]}" 2>/dev/null || true
  wait "${pids[@]}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(cd backend && exec ../.venv/bin/uvicorn api:app --reload --port 8000) &
pids+=("$!")

(cd frontend && exec npm run dev) &
pids+=("$!")

echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:3000"
wait
