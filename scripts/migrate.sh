#!/usr/bin/env bash
# Runs Alembic migrations against DATABASE_URL. Safe to re-run — Alembic only
# applies revisions that haven't run yet.
set -euo pipefail
cd "$(dirname "$0")/.."

test -d .venv || { echo "No .venv found — run 'make setup' first."; exit 1; }

if [ -f .env ]; then
  # migrations/env.py reads DATABASE_URL from the process environment
  # directly (not via python-dotenv), so it has to actually be exported here
  # rather than just present in .env.
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${DATABASE_URL:?DATABASE_URL not set — add it to .env or export it first (see docs/setup.md)}"

cd backend && ../.venv/bin/python -m alembic upgrade head
