#!/usr/bin/env bash
# One-shot local dev setup. Safe to re-run — never overwrites an existing .env or venv.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=python3.13
command -v "$PY" >/dev/null 2>&1 || PY=python3

if [ ! -d .venv ]; then
  echo "Creating virtualenv at .venv with $PY..."
  "$PY" -m venv .venv
else
  echo ".venv already exists, skipping creation."
fi

echo "Installing backend dependencies..."
.venv/bin/pip install -q -r backend/requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env created from .env.example — edit it with your LLM provider key before running the app."
else
  echo ".env already exists, leaving it untouched."
fi

echo "Installing frontend dependencies..."
npm install --prefix frontend

cat <<'EOF'

Setup complete.
Next steps:
  1. Edit .env with your LLM provider key (and DATABASE_URL if you need SME Signals/Screener/etc.)
  2. Run 'make check' to verify everything is in place
  3. If DATABASE_URL is set: cd backend && alembic upgrade head
  4. See README.md's Quickstart for how to run the app
EOF
