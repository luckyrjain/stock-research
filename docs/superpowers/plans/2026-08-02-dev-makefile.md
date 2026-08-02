# Dev Makefile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a root `Makefile` with `setup`/`check`/`test`/`test-e2e` targets backed by two shell
scripts, so local dev setup, prerequisite verification, and fast-test invocation are each one
command — and wire the new commands into `README.md`/`docs/setup.md`.

**Architecture:** Two standalone bash scripts under `scripts/` (`setup.sh`, `check-prereqs.sh`),
each independently runnable and independently verifiable by executing them and inspecting
output/exit code. A thin `Makefile` at repo root wraps them plus two inline test targets. No new
runtime dependency — `make`, `bash`, `python3`/`python3.13` are already required or already
present per `backend/CLAUDE.md`'s stdlib-first stance.

**Tech Stack:** bash, GNU Make, Python 3 stdlib (`socket`, `urllib.parse`) for the TCP-reachability
check inside `check-prereqs.sh`.

## Global Constraints

- No new dependency in either stack (spec §Scope)
- `make setup` never overwrites an existing `.env` or an existing `.venv` (spec §make setup)
- `make check` runs every check and reports all of them before exiting — doesn't stop at first
  failure (spec §make check)
- `make check` exits non-zero only on a FAIL-category item; WARN items don't affect exit code
  (spec §make check)
- `make test` stays fast (pytest + tsc only); Playwright e2e is a separate `make test-e2e` target
  (spec §make test / make test-e2e)
- No CI changes — `.github/workflows/ci.yml` keeps its own explicit steps (spec §Non-goals)
- No existing manual-command documentation is deleted from README/docs/setup.md, only supplemented
  (spec §Docs touched)

---

### Task 1: `scripts/check-prereqs.sh`

**Files:**
- Create: `scripts/check-prereqs.sh`

**Interfaces:**
- Produces: an executable script, invocable as `bash scripts/check-prereqs.sh` from any cwd
  (it `cd`s to the repo root itself). Exit code `0` = all FAIL-category checks passed (WARNs may
  still be present); exit code `1` = at least one FAIL-category check failed. Later tasks (the
  Makefile's `check` target) call it exactly this way.

- [ ] **Step 1: Create the scripts directory and write the script**

```bash
mkdir -p scripts
```

Write `scripts/check-prereqs.sh`:

```bash
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
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/check-prereqs.sh
```

- [ ] **Step 3: Run it against the current repo state and verify output**

Run: `bash scripts/check-prereqs.sh; echo "exit code: $?"`

Expected: no bash errors (no "unbound variable", no syntax errors); one `OK`/`WARN`/`FAIL` line
per check; a final summary line; `exit code: 0` if this machine has Python 3.13/Node 18+/npm and
an existing `.env` (true for this repo's dev machine — verified: `python3.13`, `node v26`, `npm`,
`docker` all present, `.env` exists at repo root).

- [ ] **Step 4: Verify the FAIL path**

Run: `DATABASE_URL_test=1 bash -c 'cd '"$(pwd)"' && grep -q "^DATABASE_URL=" .env && echo "DATABASE_URL is set in .env — reachability check below is meaningful" || echo "DATABASE_URL not set in .env — check should WARN, not FAIL"'`

Then inspect the actual output from Step 3 for the `DATABASE_URL` / `REDIS_URL` lines and confirm
they say `WARN` (not set) or `OK`/`FAIL` (set + reachable/unreachable) consistently with what's
actually in this repo's `.env` — don't hand-wave this, read the printed line.

- [ ] **Step 5: Commit**

```bash
git add scripts/check-prereqs.sh
git commit -m "feat: add make-check prerequisite verification script"
```

---

### Task 2: `scripts/setup.sh`

**Files:**
- Create: `scripts/setup.sh`

**Interfaces:**
- Produces: an executable script, invocable as `bash scripts/setup.sh` from any cwd. Idempotent —
  re-running it after `.venv`/`.env` already exist skips those steps without error.
- Consumes: nothing from Task 1.

- [ ] **Step 1: Write the script**

```bash
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
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/setup.sh
```

- [ ] **Step 3: Dry-run against this repo (already-set-up state) and verify idempotency**

Run: `bash scripts/setup.sh`

Expected: prints ".venv already exists, skipping creation." and ".env already exists, leaving it
untouched." (this repo already has both), reinstalls backend/frontend deps without error, exits 0.
Confirm `.env`'s contents are unchanged after the run: `git status .env` shows no diff (it's
gitignored, so confirm via `diff <(git show HEAD:.env 2>/dev/null) .env` is skipped — instead just
`ls -la .env` before/after and confirm the mtime only changes if the file was actually rewritten;
since the script's own guard is `[ ! -f .env ]`, an existing file is never touched — verify the
guard logic by reading the script, not by racing mtimes).

- [ ] **Step 4: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat: add make-setup local dev setup script"
```

---

### Task 3: Root `Makefile`

**Files:**
- Create: `Makefile`

**Interfaces:**
- Consumes: `scripts/check-prereqs.sh` (Task 1), `scripts/setup.sh` (Task 2) — both invoked via
  `bash <path>`, not relying on the execute bit or `$PATH`.
- Produces: `make help` (default target), `make setup`, `make check`, `make test`, `make test-e2e`
  — the four command names referenced in the design spec and in README/docs/setup.md updates
  (Tasks 4-5).

- [ ] **Step 1: Write the Makefile**

```makefile
.DEFAULT_GOAL := help

.PHONY: help setup check test test-e2e

help:
	@echo "make setup     - create venv, install backend+frontend deps, copy .env.example -> .env"
	@echo "make check     - verify prerequisites (tool versions, .env, DB/Redis reachability)"
	@echo "make test      - run backend pytest + frontend tsc --noEmit"
	@echo "make test-e2e  - run frontend Playwright e2e tests (installs Chromium on first run)"

setup:
	@bash scripts/setup.sh

check:
	@bash scripts/check-prereqs.sh

test:
	@test -d .venv || (echo "No .venv found — run 'make setup' first." && exit 1)
	cd backend && ../.venv/bin/python -m pytest tests/
	cd frontend && npx tsc --noEmit

test-e2e:
	cd frontend && npx playwright install --with-deps chromium && npm run test:e2e
```

Note: Makefile recipes require **tab**-indented lines, not spaces — if your editor auto-converts
tabs, verify with `cat -A Makefile | head -20` that recipe lines start with `^I` not spaces.

- [ ] **Step 2: Verify `make help` and `make check`**

Run: `make help`
Expected: prints the four-line command summary, exits 0.

Run: `make check`
Expected: identical output to Task 1 Step 3's direct-script run, same exit code.

- [ ] **Step 3: Verify `make test`**

Run: `make test`
Expected: backend pytest runs (existing suite, should pass — this plan makes no backend code
changes), then frontend `tsc --noEmit` runs and exits 0 with no output (clean type-check).

- [ ] **Step 4: Verify `make setup` via make (not just direct script call)**

Run: `make setup`
Expected: same idempotent-skip output as Task 2 Step 3, invoked through `make` this time — confirms
the Makefile wiring (not just the underlying script) works.

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "feat: add root Makefile wrapping setup/check/test/test-e2e"
```

---

### Task 4: Update `README.md`

**Files:**
- Modify: `README.md` — the `<details><summary>Manual setup (no Docker)</summary>` block and the
  `## Tests` section (both added in the prior README-reformat plan; current line numbers may have
  shifted since — locate by heading text, not line number)

**Interfaces:** N/A — content-only change.

- [ ] **Step 1: Replace the manual-setup `<details>` block's setup commands**

Find the `<details><summary>Manual setup (no Docker)</summary>` block. Replace its first code
fence (the venv/pip/npm-install block) with:

```markdown
```bash
make setup   # creates .venv, installs backend+frontend deps, copies .env.example -> .env
make check   # verifies Python/Node/npm/.env/DB/Redis are all in place
```

Edit `.env` with your provider key. If `DATABASE_URL` is set, run the migration once:

```bash
cd backend && alembic upgrade head && cd ..
```
```

Keep the rest of the `<details>` block (the "Run it" Terminal A/B instructions, the CLI line, and
the `docs/setup.md` pointer) unchanged below this replacement.

- [ ] **Step 2: Add `make test` to the `## Tests` section**

Prepend a line above the existing fenced test commands:

```markdown
## Tests

```bash
make test        # backend pytest + frontend tsc --noEmit
make test-e2e     # frontend Playwright e2e (installs Chromium on first run)
```

Equivalent, run directly:

```bash
cd backend && python -m pytest tests/       # no live network calls
cd frontend && npx tsc --noEmit && npm run test:e2e   # Playwright, backend fully mocked
```
```

- [ ] **Step 3: Verify length and links still hold**

Run: `wc -l README.md`
Expected: still under ~160 lines (this task adds ~10 lines net).

Run the same link-check as the prior README plan:
```bash
grep -oE '\]\(([^)]+\.md)\)' README.md | sed 's/[](]//g; s/)//g' | sort -u | while read f; do
  test -f "$f" && echo "OK  $f" || echo "MISSING  $f"
done
```
Expected: every line `OK`.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: wire make setup/check/test into the README quickstart"
```

---

### Task 5: Update `docs/setup.md`

**Files:**
- Modify: `docs/setup.md:1-15` (the `## What you need` section header area)

**Interfaces:** N/A — content-only change.

- [ ] **Step 1: Add a one-line pointer above "## What you need"**

Insert immediately after the `# Setup & Configuration` title (before `## What you need`):

```markdown
> Run `make check` from the repo root anytime to verify the prerequisites below automatically
> (tool versions, `.env`, and — if set — `DATABASE_URL`/`REDIS_URL` reachability). `make setup`
> automates the "Backend setup" steps further down this page.
```

Leave everything else in the file unchanged — the manual command list stays as the ground truth
`scripts/setup.sh` implements.

- [ ] **Step 2: Verify**

Run: `head -10 docs/setup.md`
Expected: title, blank line, the new blockquote pointer, blank line, `## What you need` heading —
in that order, nothing else disturbed.

- [ ] **Step 3: Commit**

```bash
git add docs/setup.md
git commit -m "docs: point docs/setup.md at make check / make setup"
```
