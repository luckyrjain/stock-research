#!/usr/bin/env bash
# Recaptures docs/screenshots/*.png (used in README.md) from a live local
# instance. Requires `make dev` already running in another terminal — this
# script only drives a browser against it, it doesn't start/stop servers.
#
# For a genuine completed analysis report (not the safe HOLD fallback),
# the backend also needs a configured LLM provider key and real network
# access to NSE/Screener.in/yfinance/Google News.
set -euo pipefail
cd "$(dirname "$0")/.."

test -d frontend/node_modules || { echo "frontend/node_modules missing — run 'make setup' first."; exit 1; }

for url in http://localhost:8000/docs http://localhost:3000/; do
  if ! curl -sf -o /dev/null "$url"; then
    echo "$url is not reachable — start the app first with 'make dev' (in another terminal), then re-run this."
    exit 1
  fi
done

mkdir -p docs/screenshots
node frontend/scripts/capture-screenshots.js
