# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

A full-stack Indian equity research platform. Given an NSE/BSE ticker (e.g. `TCS`, `RELIANCE`), it:

1. Validates the symbol across NSE autocomplete, BSE, and Screener.in
2. Fetches six data slices in parallel (price, fundamentals, news, shareholding, MF holdings, filings)
3. Runs a quantitative signal engine (valuation + growth + volume + filings + technical + macro signals)
4. Calls an LLM analyst to produce a structured `BUY`/`HOLD`/`SELL` recommendation
5. Streams progress and the final report to the browser via Server-Sent Events

A second mode — **Market Picks** — runs a multi-agent pipeline that scrapes 20 Indian and global financial sources, extracts stock recommendations with an LLM, validates symbols against the NSE equity master, runs due diligence on each, and returns a confidence-ranked watchlist with BUY / WATCHLIST / HOLD / SELL ratings.

A third mode — **SME Signals** — is a PostgreSQL-backed batch pipeline (`sme_ema_pipeline.py`) that screens all NSE Emerge + BSE SME stocks for EMA20/EMA50 **golden cross** and **death cross** events, served at `/sme-signals` via `GET /api/sme-signals`.

A fourth mode — **Screener** — is a PostgreSQL-backed batch pipeline (`screener_pipeline.py`) over the NIFTY 500 universe, filterable/sortable by industry, P/E, market cap, and RSI/EMA trend, served at `/screener` via `GET /api/screener`.

A **Watchlist** ties the three modes together: a star button in each dashboard adds/removes a stock from a PostgreSQL-backed `watchlist_items` table, and `/watchlist` lists everything starred with live prices. Each row is owned by either an anonymous per-browser `client_id` (a UUID in `localStorage`) or, once signed in, an account (`user_id`) — see "Watchlist flow" below for how a request's identity is resolved, and "Account & magic-link auth flow" for the account system itself. Signing in never migrates an existing `client_id`'s rows onto the account. A daily batch job (`watchlist_alerts.py`, see "Watchlist alert emails" below) re-analyses every account-owned watchlist symbol and emails a digest to any user whose stock's recommendation changed since the prior stored verdict — anonymous `client_id` rows have no email to notify and are excluded.

A minimal **account system** (magic-link email, no passwords) exists via `POST /api/auth/request-link` + `GET /api/auth/verify` — a `Sign in` link appears in every page's nav bar (`AuthWidget`). Both the watchlist (above) and "I bought this" positions tracking (`frontend/lib/positions.ts`, backed by a `positions` table with the same anonymous-`client_id`-or-account-`user_id` ownership shape as `watchlist_items` — see "Positions" under "Market picks flow" below) are account-aware.

A shared **search box** (`HeaderSearch`, in every page's nav bar) answers "what does AlphaPulse think about X" in one query: `GET /api/consolidated/{symbol}` is pure aggregation of what the three modes above have already cached/computed for that symbol — no new fetching, no LLM calls. Any section is `null` when that pipeline hasn't run for the symbol yet (the common case), not an error.

---

## Repo Structure

**All backend paths in this document — every bare filename or path like `crew.py`,
`tools/nse_tools.py`, `routes/positions.py` — are relative to `backend/`, not the repo root.**
`frontend/` stays a sibling of `backend/` at the repo root, unchanged. This split exists purely to
make the top-level repo listing readable (two clearly-separated stacks); it changes no import
paths or code behavior — every backend module still imports every other one exactly as it did
before (e.g. `from tools.nse_tools import get_stock_quote`, `from db.models import metadata`),
since nothing moved *relative to other backend files*, only the whole tree moved one level down.
Run every backend command from inside `backend/` (`cd backend && ...`) unless told otherwise.

```
stock-research/
├── backend/
│   ├── api.py                  FastAPI server — SSE endpoints and symbol validation
│   ├── main.py                 CLI entry point; also contains _fetch_task, _build_report (shared with api.py)
│   ├── crew.py                 Analyst guardrails, run_analysis_with_fallback (direct litellm call,
│   │                           cross-provider failover)
│   ├── llm_cost.py             Per-call LLM cost instrumentation + running daily total
│   ├── cache.py                File-based TTL cache (output/<SYMBOL>/<task>.json)
│   ├── schemas.py              Normalization contracts: raw tool output → canonical dicts
│   ├── market_picks_pipeline.py  Multi-agent weekly picks pipeline (6 phases)
│   ├── sme_ema_pipeline.py     SME golden/death cross batch pipeline (PostgreSQL)
│   ├── screener_pipeline.py    NIFTY 500 custom screener batch pipeline (PostgreSQL)
│   ├── eod_prices_pipeline.py  EOD bhavcopy + AMFI NAV ingestion (PostgreSQL) — see "EOD price
│   │                           store + corporate actions flow" below
│   ├── corporate_actions_pipeline.py  Split/bonus/dividend-adjusted close recompute
│   ├── portfolio_valuation.py  Portfolio Aggregator's nightly auto-valuation + XIRR engine
│   ├── cas_import.py           CAS PDF (CAMS/KFintech) mutual-fund statement import
│   ├── csv_import.py           Broker CSV/XLSX tradebook import (Zerodha preset)
│   ├── source_quality.py       Per-run Market Picks source-quality telemetry
│   ├── source_quality_report.py  Aggregation CLI for the above
│   ├── verdict_history.py      Daily verdict/price snapshots (PostgreSQL) — powers the hero's timeline strip
│   ├── mf_holdings_history.py  Quarterly MF stake snapshots (PostgreSQL) — powers the stake-delta badges
│   ├── auth.py                 Magic-link auth: token/session issuance + validation (PostgreSQL)
│   ├── email_sender.py         Sends the magic-link sign-in + watchlist-alert emails over generic SMTP
│   ├── watchlist_alerts.py     Daily batch job: emails signed-in users on a watched stock's recommendation change
│   ├── db/                     SQLAlchemy Core tables (models.py) + schema.sql reference
│   ├── routes/                 Per-domain FastAPI routers extracted out of api.py (see
│   │                           "Route module extraction" below) — watchlist.py, positions.py,
│   │                           portfolio_aggregator.py, _shared.py (the read/write wrapper all share)
│   ├── observability.py        Structured JSON logging via log_event()
│   ├── error_tracking.py       Optional Sentry-style hook, wired into log_event()'s error-level path
│   ├── schema_drift.py         Type-drift detection for the six scraped data slices
│   ├── peer_analytics.py       Peer-percentile + absolute valuation-anchor math (api.py + market_picks_pipeline.py)
│   ├── source_health.py        Freshness/volume monitoring for market-picks sources + macro overlay
│   ├── scraper_error_counters.py  Error (not empty-result) counters for the 4 standalone per-symbol scrapers
│   ├── requirements.txt
│   ├── alembic.ini             Schema-migration config (see "Schema migrations" below)
│   ├── migrations/             Alembic migration scripts — env.py + versions/*.py
│   ├── Dockerfile              Backend image (see docker-compose.yml at the repo root)
│   ├── config/
│   │   ├── analyst.json        Analyst role/goal/backstory + section labels (config.crew_tasks.ANALYST_SECTIONS)
│   │   └── crew_tasks.py       Builds the analyst prompt string from analyst.json
│   ├── tools/
│   │   ├── market_picks_tools.py  RSS + GNews scrapers for 14 sources; exports SOURCES + SCRAPER_FNS
│   │   │                          (merges in hdfc_sec_agent.py + 4 others below → 20 sources total)
│   │   ├── sme_tools.py           NSE Emerge + BSE SME stock-list fetchers
│   │   ├── nifty500_tools.py      NIFTY 500 constituent list fetcher (screener_pipeline.py's universe)
│   │   ├── hdfc_sec_agent.py      HDFC Securities Fundamental + Technical scrapers (GNews-based)
│   │   ├── securities_master.py   NSE/BSE main-board + SME symbol resolver (broker-code/ISIN/fuzzy-name)
│   │   ├── eod_sources.py         NSE bhavcopy + AMFI NAV fetch/parse
│   │   ├── corporate_actions.py   NSE corporate-actions fetch + purpose parser
│   │   ├── _nse_session.py        Shared NSE session-priming helper — every NSE-touching tools/*.py
│   │   │                          module delegates its own local session helper to this
│   │   └── ...                    Other data-fetching functions (yfinance, Screener.in, gnews, NSE API)
│   ├── signals/                Quantitative signal engine (features → signal scores → verdict)
│   ├── tests/                  unittest-based tests (no pytest plugins needed)
│   ├── tests_live/             Opt-in live-network scraper contract checks (see below)
│   └── output/                 Cache files (gitignored); also where the CLI saves report JSON
│       ├── <SYMBOL>/           Per-symbol task caches
│       ├── _extract_cache/     LLM extraction cache (6 h TTL) — avoids re-calling LLM on re-runs
│       ├── _history/           Daily pick snapshots (YYYY-MM-DD.json) — powers both the in-pipeline
│       │                       trend/trend_delta fields and GET /api/market-picks/history (/market-picks/history page)
│       ├── _market_picks/      Market picks result cache (192 h / 7-day TTL) for the SSE endpoint
│       ├── _bhavcopy/          Raw NSE bhavcopy archive (EOD price store replay)
│       ├── _cas/               Scrubbed CAS-import parse archive (PII stripped)
│       └── _nse_master.txt     NSE equity symbol master, refreshed every 24 h
├── .env.example                Shared by both stacks; stays at the repo root (python-dotenv's
│                               load_dotenv() walks up from backend/ and finds it there)
├── frontend/               Next.js 15 app (TypeScript, Tailwind CSS)
│   ├── app/page.tsx              Stock analysis page (supports ?symbol= deep links)
│   ├── app/market-picks/page.tsx Weekly picks page
│   ├── app/sme-signals/page.tsx  SME golden cross screener
│   ├── app/screener/page.tsx     NIFTY 500 custom screener
│   ├── app/watchlist/page.tsx    Cross-mode watchlist page
│   ├── app/portfolio/page.tsx    Aggregate return summary over tracked "I bought this" positions
│   ├── app/compare/page.tsx      Two stock analysis reports side by side (?symbols=TCS,INFY)
│   ├── components/               Dashboard, search, progress tracker, market picks dashboard
│   │   ├── header-search.tsx     Shared "what does AlphaPulse think about X" search box (every nav bar)
│   │   └── consolidated-card.tsx Modal rendering GET /api/consolidated/{symbol}'s three sections
│   ├── app/login/page.tsx        Magic-link sign-in form (email → "check your inbox")
│   ├── app/auth/verify/page.tsx  Consumes ?token=, calls /api/auth/verify, redirects home
│   ├── app/api/                  Thin Next.js proxy routes → FastAPI backend
│   │   └── app/api/auth/         request-link / verify / me / logout — verify + logout also
│   │                             set/clear the httpOnly session cookie (see auth-cookie.ts)
│   ├── lib/watchlist.ts          useWatchlist() hook (DB-backed via /api/watchlist, anonymous client_id)
│   ├── lib/positions.ts          usePositions() hook ("I bought this" — DB-backed via /api/positions,
│   │                             same client_id/account ownership shape as useWatchlist)
│   ├── lib/auth.ts               useAuth() hook (session-cookie-backed; same shared-cache pattern as useWatchlist)
│   ├── lib/auth-cookie.ts        Server-only cookie helpers used by app/api/auth/* route handlers
│   ├── lib/useStockAnalysis.ts   Per-symbol SSE analysis hook, shared by the home page and /compare
│   ├── e2e/                      Playwright E2E specs — every backend response is mocked (see below)
│   ├── playwright.config.ts      webServer runs `npm run dev`; no real backend involved
│   ├── components/auth-widget.tsx "Sign in" link or email+logout dropdown, in every page's nav bar
│   └── types/index.ts            Canonical TS types for all SSE messages and reports
├── docs/                   Setup/deployment/architecture/tools/output-schema/design/PRD docs
├── CLAUDE.md, README.md    Root-level docs (this file among them)
└── docker-compose.yml
```

---

## Where to look next

This file is deliberately a lean overview + pointer, not the exhaustive reference — the bulk of
this project's engineering detail lives in stack-specific `CLAUDE.md` files (Claude Code loads
whichever one is relevant to the directory you're working in):

| Doc | What it covers |
|---|---|
| [`backend/CLAUDE.md`](backend/CLAUDE.md) | Exhaustive backend engineering reference — every feature's exact behavior, agent architecture, all data flows, env config, code style, and the load-bearing "Important Rules for Claude" for Python code |
| [`frontend/CLAUDE.md`](frontend/CLAUDE.md) | Frontend conventions, testing gate (`npx tsc --noEmit`), env config, code style |
| [`docs/design.md`](docs/design.md) | AlphaPulse Design System — colors, typography, spacing, component patterns. All UI work must follow it |
| [`docs/PRD.md`](docs/PRD.md) | Product strategy — vision, goals, principles, personas, priority, roadmap, business context |
| [`docs/feature-catalog.md`](docs/feature-catalog.md) | Detailed "what's already built" feature inventory |
| [`docs/setup.md`](docs/setup.md) | Full environment variable reference, local dev setup, troubleshooting |
| [`docs/deployment.md`](docs/deployment.md) | Docker Compose, manual deployment, scaling guidance |
| [`docs/architecture.md`](docs/architecture.md) | System-level request flows and module boundaries |
| [`docs/tools.md`](docs/tools.md) | Reference for every data-fetching tool/scraper and its output shape |
| [`docs/output-schema.md`](docs/output-schema.md) | JSON schema reference for reports, cache files, standalone endpoint responses |
| [`README.md`](README.md) | Quickstart — install, run, top-level feature summary |

**Organizational, legal, and business decisions that no code change can close** (bus factor of
one, no legal/compliance review of the scraping surface, no real payment processing) are tracked
in [`docs/PRD.md`](docs/PRD.md)'s own "Explicitly Out of Scope" section, not duplicated here.
