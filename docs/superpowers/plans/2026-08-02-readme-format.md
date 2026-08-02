# README Reformat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `README.md` from an exhaustive ~400-line engineering reference into a ~120-150
line GitHub-visitor-facing overview, moving all detail already covered in `docs/*.md` /
`backend/CLAUDE.md` / `frontend/CLAUDE.md` out of the README and replacing it with links.

**Architecture:** Single-file content edit. No code changes, no tests to write — this is prose
restructuring. Verification is: (1) every fact kept is still accurate against current code/docs,
(2) every link resolves, (3) length target met.

**Tech Stack:** Markdown only.

## Global Constraints

- Target length: ~120-150 lines (spec §Structure)
- No new facts invented — trim/restructure existing accurate prose only (spec §Cut entirely)
- Drop drifting counts (endpoint count, table count) from README prose entirely rather than
  re-stating them (spec §Cut entirely — this caused a prior staleness bug)
- No screenshots/GIFs this pass (spec §Out of scope)
- No changes to `docs/*.md` or `CLAUDE.md` content (spec §Out of scope)
- Single README file — no CONTRIBUTING.md split (spec §Out of scope)

---

### Task 1: Rewrite README.md

**Files:**
- Modify: `README.md` (full rewrite, same path)

**Interfaces:** N/A — standalone content file, nothing else in the repo imports or parses it
programmatically.

- [ ] **Step 1: Re-read source-of-truth docs for accuracy**

Before writing, re-check the current facts you'll keep, against their authoritative source (not
against the existing README, which is the thing being trimmed):
- `docs/index.md` — for the doc-links table (7 files: setup, architecture, deployment,
  api-reference, database, tools, output-schema, design, backlog, PRD, feature-catalog — confirm
  exact filenames via `ls docs/*.md`)
- `.github/workflows/ci.yml` — confirm the CI badge URL still matches (org `luckyrjain`, repo
  `stock-research`, workflow file `ci.yml`)
- `backend/requirements.txt` / `frontend/package.json` — confirm Python/Node version floors
  (Python 3.13, Node 18+) still match `.python-version`/`engines` if present, else keep as-is from
  current README since these are stated facts, not derived
- `docker-compose.yml` — confirm service names/ports (backend :8000, frontend :3000, postgres,
  redis) for the Docker quickstart block

- [ ] **Step 2: Write the new README.md**

Replace the full file with this structure (fill placeholder-free — use the current README's
existing prose for each bullet, trimmed to 1-2 lines, not reworded facts):

```markdown
# Stock Research

[![CI](https://github.com/luckyrjain/stock-research/actions/workflows/ci.yml/badge.svg)](https://github.com/luckyrjain/stock-research/actions/workflows/ci.yml)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![Next.js 15](https://img.shields.io/badge/next.js-15-black)
![PostgreSQL](https://img.shields.io/badge/postgres-primary%20store-blue)

**AlphaPulse** is a full-stack Indian equity research platform: point it at an NSE/BSE ticker and
it validates the symbol, scrapes price/fundamentals/news/shareholding/filings in parallel, runs a
quantitative signal engine, and calls an LLM analyst for a structured BUY/HOLD/SELL
recommendation — streamed live over Server-Sent Events.

## What it does

- **Stock Analysis** — full single-stock report: quant signals (valuation, growth, technical,
  macro) + LLM verdict, peer comparison, DCF, verdict-history win-rate.
- **Market Picks** — multi-agent pipeline scraping 20 Indian/global sources into a
  confidence-ranked, sector-balanced weekly watchlist.
- **SME Signals** — batch screener for EMA golden-cross/death-cross events across NSE Emerge + BSE
  SME stocks.
- **Screener** — filterable NIFTY 500 screener (industry, P/E, market cap, RSI/EMA trend).
- **Watchlist & Portfolio** — cross-mode watchlist + "I bought this" position tracking, with daily
  email alerts on recommendation changes.
- **Portfolio Aggregator** — separate personal net-worth tracker (stocks, MFs, FDs, EPF/PPF) with
  an XIRR engine, fed by CAS PDF / broker CSV import.
- **Compare** and a shared **consolidated search** ("what does AlphaPulse think about X") round out
  the cross-mode UX.

## Tech stack

- **Backend** — Python 3.13, FastAPI (SSE streaming), litellm, a custom quantitative signals engine
- **Frontend** — Next.js 15 (App Router), React 19, TypeScript (strict), Tailwind CSS v3, PWA
- **Storage** — PostgreSQL (primary store for all durable/shared state); Redis optional, only for
  multi-worker deployments
- **LLM providers** — Anthropic, OpenAI, Groq, Google, OpenRouter, or local Ollama (auto-detected,
  cross-provider failover)
- **Data sources** — Yahoo Finance, Screener.in, NSE/BSE, AMFI, Trendlyne, RBI, Google News/RSS

## Project structure

```text
stock-research/
├── backend/       FastAPI app, batch pipelines, signal engine, tests — see backend/CLAUDE.md
├── frontend/      Next.js app (App Router), Playwright E2E — see frontend/CLAUDE.md
├── docs/          setup, architecture, API reference, database schema, deployment, PRD
├── docker-compose.yml
└── CLAUDE.md
```

## Quickstart

```bash
git clone <repo> && cd stock-research
cp .env.example .env   # add at least one LLM provider key
docker compose up --build
docker compose exec backend alembic upgrade head   # first run only
```

Backend on `http://localhost:8000`, frontend on `http://localhost:3000`.

<details>
<summary>Manual setup (no Docker)</summary>

**Prerequisites:** Python 3.13, Node.js 18+, npm (not yarn/pnpm), one configured LLM provider key.
PostgreSQL is optional for single-stock analysis but required for every other mode.

```bash
# Backend
python3.13 -m venv .venv && source .venv/bin/activate
cd backend && pip install -r requirements.txt && cd ..
cp .env.example .env   # edit with your provider key + DATABASE_URL

# Database (once DATABASE_URL is set)
cd backend && alembic upgrade head && cd ..

# Frontend
cd frontend && npm install && cd ..
```

Run it:

```bash
# Terminal A
source .venv/bin/activate && cd backend && uvicorn api:app --reload --port 8000

# Terminal B
cd frontend && npm run dev
```

Single-stock CLI (no server): `cd backend && python main.py TCS`

See [docs/setup.md](docs/setup.md) for full env var reference and troubleshooting, and
`backend/CLAUDE.md`'s "Schema migrations" section for the existing-database (pre-Alembic) upgrade
path.

</details>

## Tests

```bash
cd backend && python -m pytest tests/       # no live network calls
cd frontend && npx tsc --noEmit && npm run test:e2e   # Playwright, backend fully mocked
```

## Documentation

| Doc | Covers |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Root overview + architectural constraints |
| [backend/CLAUDE.md](backend/CLAUDE.md) | Exhaustive backend reference — every feature, all data flows, code style |
| [frontend/CLAUDE.md](frontend/CLAUDE.md) | Frontend conventions, testing gate, code style |
| [docs/api-reference.md](docs/api-reference.md) | Every HTTP endpoint — auth, params, status codes, rate limits |
| [docs/database.md](docs/database.md) | Every table — columns, constraints, ownership model, migrations |
| [docs/architecture.md](docs/architecture.md) | System-level request flows and module boundaries |
| [docs/setup.md](docs/setup.md) | Full env var reference, local dev setup, troubleshooting |
| [docs/deployment.md](docs/deployment.md) | Docker Compose, manual deployment, scaling |
| [docs/backlog.md](docs/backlog.md) | The single "what's left" list |
| [docs/PRD.md](docs/PRD.md) | Product strategy — vision, goals, roadmap |
| [docs/feature-catalog.md](docs/feature-catalog.md) | Detailed feature inventory |
```

- [ ] **Step 3: Verify length**

Run: `wc -l README.md`
Expected: between 100 and 160 lines (target 120-150; some slack for exact wording).

- [ ] **Step 4: Verify every relative link resolves**

Run:
```bash
grep -oE '\]\(([^)]+\.md)\)' README.md | sed 's/[](]//g; s/)//g' | sort -u | while read f; do
  test -f "$f" && echo "OK  $f" || echo "MISSING  $f"
done
```
Expected: every line prints `OK`, no `MISSING` lines. (This checks relative `.md` links only —
the CI badge and any `http(s)://` links are external and not checked by this command.)

- [ ] **Step 5: Manual read-through against the spec's success criteria**

Confirm by inspection:
- Reads top-to-bottom in under a minute (no walls of table/tree beyond the trimmed structure block)
- No endpoint/table counts stated in prose (spec's staleness-bug avoidance)
- Every cut section (full tree, endpoint table, TTL table, migration walkthrough, analyst
  customization) has a working link to where it now lives

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: trim README to a GitHub-visitor-facing overview, link out for depth"
```
