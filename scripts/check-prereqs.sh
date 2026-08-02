#!/usr/bin/env bash
# Verifies local dev prerequisites. Safe to re-run anytime; never modifies anything.
set -u
cd "$(dirname "$0")/.."

FAIL=0

pass() { printf "  OK    %s\n" "$1"; }
warn() { printf "  WARN  %s\n" "$1"; }
fail() { printf "  FAIL  %s\n" "$1"; FAIL=1; }

env_var() {
  # env_var NAME: prints the value of NAME=... from .env, stripping surrounding quotes.
  # Empty output if .env is missing or the key isn't set.
  [ -f .env ] || return 0
  grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

tcp_reachable() {
  # tcp_reachable DSN: TCP-connects to the DSN's host:port using stdlib only. Exit 0 if reachable.
  python3 - "$1" <<'PYEOF'
import socket, sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
port = u.port or (6379 if u.scheme.startswith("redis") else 5432)
try:
    socket.create_connection((u.hostname, port), timeout=2).close()
except Exception:
    sys.exit(1)
PYEOF
}

echo "Checking prerequisites..."

if command -v python3.13 >/dev/null 2>&1; then
  pass "Python 3.13 found ($(python3.13 --version 2>&1))"
else
  fail "python3.13 not found on PATH (required)"
fi

if command -v node >/dev/null 2>&1; then
  node_major=$(node --version | sed 's/^v//' | cut -d. -f1)
  if [ "$node_major" -ge 18 ] 2>/dev/null; then
    pass "Node $(node --version) found (>=18 required)"
  else
    fail "Node $(node --version) found, but 18+ required"
  fi
else
  fail "Node.js not found on PATH (18+ required)"
fi

if command -v npm >/dev/null 2>&1; then
  pass "npm found"
else
  fail "npm not found on PATH"
fi

if command -v docker >/dev/null 2>&1; then
  pass "Docker found (optional — only needed for the Docker Compose path)"
else
  warn "Docker not found (optional — only needed for the Docker Compose path)"
fi

if [ -f .env ]; then
  pass ".env found"

  has_llm_key=0
  for key in ANTHROPIC_API_KEY OPENAI_API_KEY GROQ_API_KEY GOOGLE_API_KEY OPENROUTER_API_KEY; do
    val=$(env_var "$key")
    [ -n "$val" ] && has_llm_key=1
  done
  ollama=$(env_var OLLAMA_BASE_URL)
  if [ "$has_llm_key" -eq 1 ] || [ -n "$ollama" ]; then
    pass "LLM provider key configured"
  else
    warn "No LLM provider key set in .env — analysis calls will fail"
  fi

  db_url=$(env_var DATABASE_URL)
  if [ -n "$db_url" ]; then
    if tcp_reachable "$db_url"; then
      pass "PostgreSQL reachable at DATABASE_URL"
    else
      fail "DATABASE_URL is set but nothing is reachable at that host:port"
    fi
  else
    warn "DATABASE_URL not set — required for SME Signals, Screener, Watchlist, accounts, Portfolio Aggregator"
  fi

  redis_url=$(env_var REDIS_URL)
  if [ -n "$redis_url" ]; then
    if tcp_reachable "$redis_url"; then
      pass "Redis reachable at REDIS_URL"
    else
      fail "REDIS_URL is set but nothing is reachable at that host:port"
    fi
  else
    warn "REDIS_URL not set (optional — only needed for multiple backend workers/replicas)"
  fi
else
  fail ".env not found — run 'make setup' or 'cp .env.example .env'"
fi

echo
if [ "$FAIL" -eq 1 ]; then
  echo "Prerequisite check FAILED — fix the FAIL items above."
  exit 1
else
  echo "Prerequisite check passed (WARN items above are informational, not blocking)."
fi
