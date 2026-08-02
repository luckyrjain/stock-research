# Dev Makefile: easier setup, prerequisite check, easier testing — design

## Problem

Manual setup today is 6+ separate commands across two stacks (venv, pip install, `.env` copy,
`npm install`) with no way to verify prerequisites before running the app, and no single command
to run both stacks' fast test suites. Contributors/future-self hit failures with no clear
diagnosis of *why* (missing Python version, no `.env`, DB not reachable, etc).

## Scope

Root-level `Makefile` as the cross-stack task runner (no new dependency — `make` is a standard
Unix tool, consistent with the "prefer stdlib/native tools" architectural stance). Two
Python 3 scripts already under a `Python 3.13` requirement, callable directly or via `make`:

- `scripts/setup.sh` — one-shot local setup
- `scripts/check-prereqs.sh` — prerequisite verification, safe to re-run anytime

## make setup

Idempotent — safe to re-run.

1. Create `.venv` at repo root if missing (`python3.13 -m venv .venv`, falling back to `python3` if
   `python3.13` isn't on `PATH`, so the script itself doesn't hard-fail before `check` gets a
   chance to report the version problem clearly)
2. `.venv/bin/pip install -r backend/requirements.txt` (no activation needed — invoke the venv's
   pip directly)
3. `npm install --prefix frontend`
4. Copy `.env.example` → `.env` at repo root if `.env` doesn't already exist (never overwrites an
   existing `.env`)
5. Does **not** run `alembic upgrade head` — that needs `DATABASE_URL` configured first, which
   requires editing `.env` (a manual step). Setup prints a follow-up hint instead.

## make check

Non-destructive, re-runnable anytime, including before `make setup`. Runs every check and reports
all of them (doesn't stop at the first failure), then exits non-zero only if something in the
**FAIL** category failed:

| Check | Category |
|---|---|
| `python3.13` on `PATH` | FAIL if missing |
| Node.js version ≥ 18 | FAIL if missing or too old |
| `npm` on `PATH` | FAIL if missing |
| `docker` on `PATH` | WARN if missing (optional — only needed for the Docker Compose path) |
| `.env` exists | FAIL if missing (with a hint to run `make setup`) |
| At least one LLM provider key set in `.env` (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GROQ_API_KEY` / `GOOGLE_API_KEY` / `OPENROUTER_API_KEY`), or `OLLAMA_BASE_URL` set | WARN if none found |
| `DATABASE_URL` set in `.env` | WARN if unset (core stock-analysis works without it) |
| If `DATABASE_URL` set: TCP-reachable | FAIL if set but unreachable |
| `REDIS_URL` set in `.env` | WARN if unset (optional, only for multi-worker) |
| If `REDIS_URL` set: TCP-reachable | FAIL if set but unreachable |

DB/Redis reachability is a plain TCP connect to `host:port` parsed from the DSN (stdlib `socket`
+ `urllib.parse` via an inline `python3` call from the shell script) — not a real login/auth
check, just "is something listening." Good enough to catch "Postgres container isn't up yet,"
which is the actual common failure mode.

## make test / make test-e2e

- `make test` — `backend`'s `pytest tests/` (via `.venv/bin/python -m pytest`) then `frontend`'s
  `npx tsc --noEmit`. Guards with a clear error if `.venv` doesn't exist yet ("run `make setup`
  first") instead of a confusing pip/python-not-found error.
- `make test-e2e` — separate target: installs Playwright's Chromium (`npx playwright install
  --with-deps chromium`) then `npm run test:e2e`. Kept separate from `make test` because it's
  slower and needs a browser download on first run; `make test` should stay fast enough to run
  before every commit.

## Non-goals

- No change to what the tests themselves cover — this only changes how they're invoked
- No CI changes — `.github/workflows/ci.yml` keeps its own explicit steps; the Makefile is a local
  dev convenience, not a CI dependency (CI stays reproducible without `make` in the loop)
- Doesn't run `alembic upgrade head` automatically from `make setup` (see above)
- No Windows-native support — scripts are `bash`, consistent with the project's existing
  Unix-shell examples throughout `README.md`/`docs/setup.md`

## Docs touched

- `README.md` Quickstart's `<details>` (manual setup) block replaced with `make setup` / `make
  check`; Tests section gets `make test` / `make test-e2e` alongside the existing raw commands
- `docs/setup.md` gets one short "Quick check" pointer at the top of "What you need" noting `make
  check` verifies this list automatically — the existing manual command list stays as-is
  underneath, since it's still the ground truth `scripts/setup.sh` implements

## Success criteria

- `make setup` on a clean checkout gets you to "edit `.env`, then `make check` passes" in one
  command
- `make check` on a machine missing Python 3.13 (or with no `.env`, or with `DATABASE_URL` pointed
  at nothing listening) reports the specific problem, not a generic failure
- `make test` runs both stacks' fast tests in one command with no setup beyond `make setup`
- No existing manual-command paths in README/docs/setup.md are removed — only supplemented, since
  someone without `make` still needs them to work
