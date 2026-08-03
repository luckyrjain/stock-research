#!/usr/bin/env bash
# Populates local Postgres with real data via the SME Signals and Screener
# batch pipelines. This app never seeds with synthetic/fixture data — see
# CLAUDE.md's "never invent" convention — so this is the same live
# NSE/yfinance fetch the scheduled cron workflows run, just triggered by
# hand. Idempotent (safe to re-run), but slow: the Screener pipeline fetches
# ~500 NIFTY 500 stocks one at a time and can take several minutes.
#
# Does NOT seed Market Picks — that pipeline makes real, billed LLM calls,
# so it's left to be triggered explicitly (POST /api/market-picks or the
# "Fresh scan" button) rather than run automatically here.
set -euo pipefail
cd "$(dirname "$0")/.."

test -d .venv || { echo "No .venv found — run 'make setup' first."; exit 1; }

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${DATABASE_URL:?DATABASE_URL not set — add it to .env or export it first (see docs/setup.md)}"

echo "Seeding SME Signals (NSE Emerge + BSE SME golden/death cross data)..."
(cd backend && ../.venv/bin/python sme_ema_pipeline.py) \
  || echo "SME Signals pipeline reported an unhealthy run (see output above) — continuing."

echo
echo "Seeding Screener (NIFTY 500 quant + technical metrics — this is the slow one)..."
(cd backend && ../.venv/bin/python screener_pipeline.py) \
  || echo "Screener pipeline reported an unhealthy run (see output above) — continuing."

echo
echo "Done. Visit /sme-signals and /screener to see the data."
