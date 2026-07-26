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

A minimal **account system** (magic-link email, no passwords) exists via `POST /api/auth/request-link` + `GET /api/auth/verify` — a `Sign in` link appears in every page's nav bar (`AuthWidget`). The watchlist (above) is account-aware; "I bought this" positions tracking (`frontend/lib/positions.ts`) remains purely anonymous/`localStorage`-only for now, with no backend of its own to link to an account.

A shared **search box** (`HeaderSearch`, in every page's nav bar) answers "what does AlphaPulse think about X" in one query: `GET /api/consolidated/{symbol}` is pure aggregation of what the three modes above have already cached/computed for that symbol — no new fetching, no LLM calls. Any section is `null` when that pipeline hasn't run for the symbol yet (the common case), not an error.

---

## Repo Structure

```
stock-research/
├── api.py                  FastAPI server — SSE endpoints and symbol validation
├── main.py                 CLI entry point; also contains _fetch_task, _build_report (shared with api.py)
├── crew.py                 Analyst guardrails, run_analysis_with_fallback (direct litellm call)
├── cache.py                File-based TTL cache (output/<SYMBOL>/<task>.json)
├── schemas.py              Normalization contracts: raw tool output → canonical dicts
├── market_picks_pipeline.py  Multi-agent weekly picks pipeline (6 phases)
├── sme_ema_pipeline.py     SME golden/death cross batch pipeline (PostgreSQL)
├── screener_pipeline.py    NIFTY 500 custom screener batch pipeline (PostgreSQL)
├── verdict_history.py      Daily verdict/price snapshots (PostgreSQL) — powers the hero's timeline strip
├── auth.py                 Magic-link auth: token/session issuance + validation (PostgreSQL)
├── email_sender.py         Sends the magic-link sign-in + watchlist-alert emails over generic SMTP
├── watchlist_alerts.py     Daily batch job: emails signed-in users on a watched stock's recommendation change
├── db/                     SQLAlchemy Core tables (models.py) + schema.sql reference
├── observability.py        Structured JSON logging via log_event()
├── error_tracking.py       Optional Sentry-style hook, wired into log_event()'s error-level path
├── schema_drift.py         Type-drift detection for the six scraped data slices
├── peer_analytics.py       Peer-percentile + absolute valuation-anchor math (api.py + market_picks_pipeline.py)
├── requirements.txt
├── .env.example
├── config/
│   ├── analyst.json        Analyst role/goal/backstory + section labels (config.crew_tasks.ANALYST_SECTIONS)
│   └── crew_tasks.py       Builds the analyst prompt string from analyst.json
├── tools/
│   ├── market_picks_tools.py  RSS + GNews scrapers for 14 sources; exports SOURCES + SCRAPER_FNS
│   │                          (merges in hdfc_sec_agent.py + 4 others below → 20 sources total)
│   ├── sme_tools.py           NSE Emerge + BSE SME stock-list fetchers
│   ├── nifty500_tools.py      NIFTY 500 constituent list fetcher (screener_pipeline.py's universe)
│   ├── hdfc_sec_agent.py      HDFC Securities Fundamental + Technical scrapers (GNews-based)
│   └── ...                    Other data-fetching functions (yfinance, Screener.in, gnews, NSE API)
├── signals/                Quantitative signal engine (features → signal scores → verdict)
├── tests/                  unittest-based tests (no pytest plugins needed)
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
│   ├── lib/positions.ts          usePositions() hook ("I bought this" — localStorage only, no backend)
│   ├── lib/auth.ts               useAuth() hook (session-cookie-backed; same shared-cache pattern as useWatchlist)
│   ├── lib/auth-cookie.ts        Server-only cookie helpers used by app/api/auth/* route handlers
│   ├── lib/useStockAnalysis.ts   Per-symbol SSE analysis hook, shared by the home page and /compare
│   ├── e2e/                      Playwright E2E specs — every backend response is mocked (see below)
│   ├── playwright.config.ts      webServer runs `npm run dev`; no real backend involved
│   ├── components/auth-widget.tsx "Sign in" link or email+logout dropdown, in every page's nav bar
│   └── types/index.ts            Canonical TS types for all SSE messages and reports
└── output/                 Cache files (gitignored); also where CLI saves report JSON
    ├── <SYMBOL>/           Per-symbol task caches
    ├── _extract_cache/     LLM extraction cache (6 h TTL) — avoids re-calling LLM on re-runs
    ├── _history/           Daily pick snapshots (YYYY-MM-DD.json) — powers both the in-pipeline
    │                       trend/trend_delta fields and GET /api/market-picks/history (/market-picks/history page)
    ├── _market_picks/      Market picks result cache (6 h TTL) for the SSE endpoint
    └── _nse_master.txt     NSE equity symbol master, refreshed every 24 h
```

---

## Backend (Python)

### Runtime & install

- **Python 3.13** (venv at `.venv/`)
- **pip** — no poetry/uv
- Install: `pip install -r requirements.txt`
- Always activate before running anything: `source .venv/bin/activate`

### Running the server

```bash
uvicorn api:app --reload --port 8000
```

### Running the CLI pipeline

```bash
python main.py TCS
python main.py RELIANCE --force   # bypass cache
```

### Running tests

```bash
python -m pytest tests/
python -m pytest tests/test_analysis_guardrails.py -v   # single file
```

Tests use `unittest` and are collected by pytest. They mock heavy dependencies (crewai, tool imports) via `sys.modules` patching — no external calls made.

### Key libraries

| Library | Purpose |
|---|---|
| `fastapi` + `uvicorn` | HTTP server and SSE streaming |
| `crewai` | Only its `@tool` decorator (`crewai.tools`) is used, for a stable `.run()` calling convention on the data-fetching functions in `tools/`. The Agent/Task/Crew orchestration layer was removed (see "Agent architecture" below) — it was never on the production path. |
| `litellm` | Provider-agnostic LLM calls (analyst step) |
| `yfinance` | NSE/BSE price quotes; also used for ISIN → symbol resolution |
| `requests` + `beautifulsoup4` | Screener.in scraping, NSE API calls |
| `gnews` + `feedparser` | News articles from Google News RSS; RSS feeds for 5 financial news sources |
| `rapidfuzz` | Fuzzy company-name matching in market picks consolidation phase |
| `python-dotenv` | `.env` loading |

### Agent architecture

**Data fetching**: the API and CLI call `_fetch_task()` directly using `ThreadPoolExecutor` for parallel fetching — no agent orchestration involved. Each task wraps exactly one tool function and returns its raw JSON output.

| Task name | Tool | Data source |
|---|---|---|
| `stock_info` | `get_stock_quote` | yfinance + NSE API |
| `research` | `get_fundamentals` | Screener.in |
| `news` | `get_latest_news` | gnews (Google News) |
| `shareholding` | `get_holdings` | Screener.in |
| `mf_holdings` | `get_mf_holdings` | NSE API |
| `filings` | `get_nse_filings` | NSE corporate announcements |

`research` also carries a `quarterly_trend` (Sales/EPS mini-trend, oldest-first, from Screener's Quarterly Results table — the same company page `get_fundamentals` already fetches, so it's free) and `shareholding` carries `pledge_pct` (promoter pledge %, parsed from the same shareholding table `get_holdings` already fetches, as its own field rather than folded into `shareholding_pattern`). Both are absent/empty rather than guessed when Screener doesn't have a clean, fully-numeric window for them (e.g. a recent IPO with fewer quarters on record) — same "never invent" convention as everywhere else in this pipeline. `quarterly_trend` also carries an independently-optional `operating_margin` (Screener's own OPM % row, never derived/computed here) — several sectors (banks, NBFCs) routinely omit that row even when Sales/EPS are present, so it's dropped from the payload entirely rather than backfilled, distinct from the whole-object-absent case above. `results-dashboard.tsx` renders them as a "Quarterly Trend" card (two or three `Sparkline`s depending on whether `operating_margin` is present) and a "Promoter Pledge" line atop the Shareholding Pattern card (warning-styled when > 0%).

These tool functions are decorated with `@tool` from `crewai.tools` purely for a consistent `.run(**kwargs)` calling convention (see `main._fetch_task`) — that's the only thing this codebase still uses CrewAI for. There used to be a second, parallel orchestration path (`build_crew()` in `crew.py`, wiring per-task `Agent`/`Task`/`Crew` objects from `config/agents.json` + `config/tasks.json`) but it had zero callers and zero test coverage — data collection has always gone through `_fetch_task()` in production — so it was removed rather than left as unverified dead code. If you're looking for `LLM_MODEL` / the "data-agent tier" model config from an older version of this doc: it only ever fed that removed path and has been dropped too — `ANALYST_MODEL` (below) is the only model-selection env var that does anything.

**Analyst (direct LLM call)**: `run_analysis_with_fallback()` in `crew.py` calls `litellm.completion` directly — no CrewAI involved. It receives all six data slices plus signal engine context, and must return a specific JSON schema defined in `config/analyst.json`. Guardrails in `_validate_analysis_payload()` enforce structural rules and grounded-claims checks; a guardrail failure triggers one corrective LLM retry with the validation error appended, and only if that also fails does it return a safe HOLD fallback via `_safe_analysis_fallback()`.

**Market picks pipeline** (`market_picks_pipeline.py`): Six sequential phases, all blocking work offloaded to `ThreadPoolExecutor`. Communicates back to the SSE stream via `on_event` callbacks bridged through `asyncio.Queue` with `loop.call_soon_threadsafe`.

| Phase | What it does |
|---|---|
| `_phase_scrape` | Parallel fetch from 20 sources (5 RSS + 12 GNews + 3 structured). 6 workers. |
| `_phase_extract` | One LLM call per source (parallel, up to 6 workers). Checks extraction cache first. Detects syndicated articles (Jaccard ≥ 0.60) across sources to down-weight them. |
| `_phase_consolidate` | Groups picks by ticker, validates against NSE equity master, confirms live price via yfinance (guards pre-IPO / unlisted names). Uses rapidfuzz for fuzzy company-name matching. |
| `_phase_research` | Fetches `stock_info` + `research` + signal engine + a valuation percentile per stock (4 workers, up to `_MAX_STOCKS` stocks). |
| `_phase_analyze` | Batched LLM calls (8 stocks/batch, parallel) for qualitative summary + bull/bear factors. Does NOT ask the LLM for prices. |
| `_phase_score` | Deterministic confidence scoring (`_compute_confidence`: 50% signal engine + 30% consensus + 20% recency, 0–100, plus a small ±3-point valuation nudge layered on top — see below). The 4-tier rec (BUY / WATCHLIST / HOLD / SELL) is a *separate* formula on top — `combined_dir = 0.55 × consensus + 0.45 × signal_score`, thresholded, with a quant-veto that demotes BUY → WATCHLIST on a strongly negative signal score. Entry/target/stop-loss computed from price and signal score — no LLM. Sector-balanced (`_apply_sector_balance()`): max 2 stocks per sector promoted to the primary list, excess deferred to the end — `sector` stays on every pick in the response (real, filterable data, not popped like the old internal-only `_sector`). Saves a daily snapshot to `output/_history/` for trend tracking. |

**Peer/valuation-anchor wired into scoring**: `GET /api/peers/{symbol}`'s `absolute_anchor` (where a
stock's current P/E sits within its own last 3-5 years of Screener-published P/E — see "Absolute
valuation anchor" below) previously only reached the single-stock analysis flow; Market Picks
scoring had no valuation-quality input at all. `peer_analytics.py` (repo root) holds the pure
percentile/anchor math (`compute_peer_percentiles`, `compute_valuation_anchor`) extracted out of
`api.py` — both `api.py`'s `GET /api/peers/{symbol}` and `market_picks_pipeline.py`'s
`_phase_research` now import from this one shared module rather than duplicating the math or
having one pipeline module reach into the other. `_phase_research`'s `_fetch_valuation_percentile()`
fetches `get_peer_comparison()` for each candidate stock (a third parallel fetch alongside
`stock_info`/`research`, `ThreadPoolExecutor(max_workers=3)`) and computes only the *absolute*
anchor (own P/E history), not the peer-relative percentile — it needs just that one stock's own
Screener page, not a second peer-group lookup, so it's cheap to add to every candidate's research
step. `None` (never guessed) when Screener didn't have a parseable current P/E or fewer than 3
years of valuation-band history for that stock. `_compute_confidence()` folds this in as a
confirmation signal, not a fourth primary component: ≤33rd percentile (cheap vs. own history) adds
+3, ≥67th percentile (expensive) subtracts 3, mid-range and `None` are both no-ops — bounded by the
existing final `min(100, max(0, ...))` clamp rather than reallocating weight from the 50/30/20
split. Surfaced on each pick as `valuation_percentile` (nullable) and rendered as a "Valuation"
key-metric row in `market-picks-dashboard.tsx`'s expanded row.

---

## Frontend (Next.js)

### Runtime & package manager

- **Node.js** — no `.nvmrc`; any Node 18+ works (tested on v25)
- **npm** (package-lock.json present; do not use yarn or pnpm)
- Install: `cd frontend && npm install`

### Running dev server

```bash
cd frontend && npm run dev   # starts on port 3000
```

### Type-checking (no lint config exists)

```bash
cd frontend && npx tsc --noEmit
```

There is no ESLint config. TypeScript strict mode (`"strict": true`) is the primary code quality
gate alongside the E2E suite below.

### End-to-end tests (Playwright)

```bash
cd frontend && npx playwright install --with-deps chromium   # once, or in a fresh CI runner
cd frontend && npm run test:e2e
```

`frontend/e2e/*.spec.ts` covers core flows (home page search + a full mocked stock-analysis run,
watchlist, screener, portfolio) against `npm run dev`. **Every backend response is mocked at the
browser network layer** (`page.route()`, see `frontend/e2e/fixtures.ts` for the shared SSE/JSON
fixture builders) — this suite never talks to a real FastAPI backend, matching this repo's
existing "no live external calls in CI" convention (`tests/*.py`'s own docstring already states
this for the pytest suite; a live E2E run would mean real NSE/yfinance/Screener.in scraping on
every PR, exactly the flakiness that convention exists to avoid). Anything a test doesn't
explicitly mock falls through to the Next.js proxy routes' existing "backend unavailable" 503
handling (no FastAPI process runs in the E2E job at all), which the frontend already renders
gracefully — so an unmocked add-on fetch (e.g. peer comparison, insider activity) degrades to
"not available" instead of hanging or crashing the page.

### Design system

All UI work must follow `design.md` (AlphaPulse Design System) — the single source of truth for colors, typography, spacing, component patterns (cards, badges, buttons, tables, animations), and responsive strategy. Do not hard-code hex values or invent new patterns; always use the existing design tokens from `tailwind.config.ts`.

### Key libraries and patterns

- **Next.js 15** with App Router; all pages are `'use client'` components
- **React 19** — no React Query, no state management library; plain `useState`/`useRef`/`useCallback`
- **Tailwind CSS v3** with a custom dark-theme palette defined in `tailwind.config.ts` (key colors: `bg`, `surface`, `card`, `tx`, `muted`, `buy`, `sell`, `hold`, `accent`)
- **SSE via `EventSource`** — all streaming uses the browser's native EventSource API, not WebSockets
- **Proxy routes** — `frontend/app/api/*/route.ts` files proxy to `http://localhost:8000` (configurable via `API_URL` env var). They pipe the SSE stream directly; no buffering.
- **`@/` path alias** maps to `frontend/` root (set in `tsconfig.json`)
- All canonical TypeScript types live in `frontend/types/index.ts` — always update this when adding new SSE events or report fields

---

## Code Style & Conventions

### Python

- **No formatter configured** (no black/ruff/autopep8 in requirements or config). Match surrounding code style.
- **pylint** is referenced via `# pylint: disable=` comments in `crew.py` and `main.py` but is not enforced in CI.
- **Type hints** are used on public function signatures throughout (`-> dict`, `-> str | None`, `list[dict]`). Use Python 3.10+ union syntax (`X | Y`, not `Optional[X]`).
- Private helpers are prefixed with `_`. All internal functions in `api.py` are `_*_sync` to signal they are blocking.
- Return `dict` from tools and pipeline functions. Never raise exceptions from tool functions — return `{"error": "...", "symbol": sym}` instead.

### TypeScript / React

- **Strict TypeScript** — no `any`, no type assertions unless unavoidable.
- No Prettier config present — match surrounding formatting.
- Component files use `export default function`. Props interfaces are defined inline above the component.
- All SSE message types are discriminated unions in `frontend/types/index.ts`.

### Naming

- Python: `snake_case` everywhere; constants in `UPPER_SNAKE_CASE`; private helpers prefixed `_`
- TypeScript: `PascalCase` for types/interfaces/components; `camelCase` for variables and functions
- Task names (the six data slices) are always lowercase strings: `"stock_info"`, `"research"`, etc. These are used as dict keys, cache filenames, and SSE event fields — keep consistent

---

## Environment & Config

All configuration is via `.env` (copy from `.env.example`).

### Required — set exactly one API key

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `OPENAI_API_KEY` | OpenAI provider |
| `GROQ_API_KEY` | Groq provider |
| `GOOGLE_API_KEY` | Google Gemini provider |
| `OPENROUTER_API_KEY` | OpenRouter (access to 300+ models) |

Provider is auto-detected from whichever key is present (checked in the order above). Set `LLM_PROVIDER` explicitly to override.

### Optional

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | auto | `anthropic` / `openai` / `groq` / `google` / `openrouter` / `ollama` |
| `ANALYST_MODEL` | provider default | Model for the analyst LLM call — the only model-selection env var that does anything; data fetching doesn't call an LLM (see "Agent architecture") |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Only needed when `LLM_PROVIDER=ollama` |
| `LOG_LEVEL` | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`) |
| `DATABASE_URL` | unset | PostgreSQL DSN — required for the SME signals pipeline (`/api/sme-signals`), the watchlist (`/api/watchlist`), the verdict timeline (`/api/verdict-history/{symbol}`), and account/magic-link auth (`/api/auth/*`) |
| `FRONTEND_URL` | `http://localhost:3000` | Canonical frontend origin embedded in magic-link sign-in emails (`/auth/verify?token=...` must run in the browser to receive the session cookie, so it can't point at the FastAPI backend directly) |
| `SMTP_HOST` | unset | SMTP server for magic-link emails. Without it, sign-in links are created and stored but never emailed (logged as a warning; the request still returns success) |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` / `SMTP_PASSWORD` | unset | SMTP auth — skipped if either is unset |
| `SMTP_FROM` | `SMTP_USER` or `noreply@alphapulse.local` | From address on the sign-in email |
| `SMTP_USE_TLS` | `true` | Set to `false` only for a local/dev relay that doesn't speak STARTTLS |
| `SENTRY_DSN` | unset | Forwards every error-level `observability.log_event()` call to a Sentry-compatible ingest endpoint (see "Error tracking / APM hook" below). No-op without it — `sentry-sdk` is a hard dependency but does nothing until this is set |
| `SENTRY_ENVIRONMENT` | `production` | Tag attached to every event sent to Sentry when `SENTRY_DSN` is set (e.g. `staging`) |
| `TRUSTED_PROXY_SECRET` | unset | Shared secret proving a request's `X-Forwarded-For` header genuinely came through the Next.js proxy routes (see "Trusted client IP for per-IP rate limiting" below) — set to the same value on both this backend and the frontend process. Without it, every per-IP rate limiter keys off `request.client.host`, which is always the Next.js server's own IP |

### Frontend

| Variable | Default | Purpose |
|---|---|---|
| `API_URL` | `http://localhost:8000` | FastAPI backend URL (set in Next.js env) |
| `TRUSTED_PROXY_SECRET` | unset | Same value as the backend's env var of the same name — see "Trusted client IP for per-IP rate limiting" below. Server-only (never exposed to the browser) |

---

## Agent Orchestration

### Stock analysis flow

1. Browser opens `EventSource` → Next.js proxy route → FastAPI `GET /api/analyse/{symbol}`
2. `api.py` checks `cache.is_fresh()` for each of the six tasks
3. Stale tasks are dispatched concurrently via `asyncio`/`ThreadPoolExecutor` calling `main._fetch_task()`
4. Each completed task emits a `task_done` SSE event; the browser updates its progress tracker
5. All six raw outputs are normalized through `schemas.normalize()` → canonical dicts
6. `signals.engine.run_signal_engine()` scores the canonical data and produces a verdict
7. `crew.run_analysis_with_fallback()` calls `litellm.completion` in a thread; the SSE loop sends heartbeats (`: heartbeat`) every 15s while waiting
8. Final `done` event carries the merged report dict

### Technical signal (RSI14 + EMA20/50 posture)

Extends the momentum-screener math `sme_ema_pipeline.py` already computes for SME stocks
(golden/death cross, RSI(14)) to every symbol the main stock-analysis flow scores — previously
that confirmation signal only existed for SME/Emerge stocks, not the primary NSE/BSE large-cap
flow this whole product is centered on.

1. `tools/price_history_tools.py::get_price_series(symbol, days=180)` is the shared daily-close
   OHLCV fetch — extracted out of `GET /api/prices/history/{symbol}` (the sparkline endpoint)
   rather than duplicated, so both call sites share one yfinance `.NS`/`.BO` fallback and one
   `price_history` cache (6 h TTL, same as before this extraction).
2. `signals/technical.py::technical_signal(symbol)` is the one signal in `signals/engine.py`
   that does its own I/O — every other signal (`volume`, `valuation`, `growth`, `filings`) reads
   from `features`, already-fetched data with no network calls of its own. It computes RSI(14)
   (same Wilder-style `ewm` formula as `sme_ema_pipeline._compute_rsi`) and EMA20/EMA50 trend
   posture over the cached close series, returning `UNKNOWN` (score 0, never guessed) when fewer
   than `_MIN_CLOSES` (75, same value as `sme_ema_pipeline._MIN_HISTORY_DAYS`) closes are
   available — not enough history for EMA50 to have meaningfully converged (e.g. a recent IPO).
   The `price_history` cache this reads (see point 1) is on its own 6 h TTL, independent of the
   six-task caches — a `?force=true` re-analysis bypasses `ALL_DATA_TASKS` but not this cache, so
   the technical signal can lag up to 6 h behind a forced refresh of everything else. Acceptable
   for a momentum-confirmation signal on daily-close data (a 6 h-old RSI/EMA reading rarely
   flips), but worth knowing if it's ever surprising in a support ticket.
3. `run_signal_engine(symbol, all_data)` calls `technical_signal(symbol)` directly (it already
   received `symbol`, no signature change needed) and blends it in at weight 0.2 — the same tier
   as `volume`/`filings` (confirmation signals), below `valuation`/`growth` (0.4, the primary
   fundamental drivers).
4. **Blocking-I/O consequence**: `run_signal_engine()` was previously pure CPU (dict lookups +
   arithmetic over already-fetched data) and so was called directly inside `api.py`'s
   `/api/analyse/{symbol}` async SSE generator, unwrapped. Adding a (cached, but still
   potentially network-hitting) call inside it means that call site now must run through
   `loop.run_in_executor()` like every other blocking call in the SSE path — the same "never
   block the event loop" rule the "SSE bridge pattern" section below already documents. The
   other three call sites (`main.py`'s CLI, `watchlist_alerts.py`'s batch loop,
   `market_picks_pipeline.py`'s `_phase_research`) were already running inside a synchronous
   script or a `ThreadPoolExecutor` worker, so they needed no change.
5. `results-dashboard.tsx`'s existing "Quant Signals" card renders every entry in
   `signal_context.signals` generically (`Object.entries(...).map(...)`), so the new
   `technical` entry appears automatically with no frontend code change — only its tooltip copy
   was updated to mention it.

### Macro overlay signal (FII/DII flow + RBI rate/inflation)

A market-wide overlay on top of the per-stock signals above — "is the broader institutional/rate
backdrop a tailwind or a headwind right now" — blended into every symbol's signal score at a low
weight (0.15), since it says nothing about the specific company.

1. `tools/nse_fii_dii_tools.py::get_fii_dii_flow()` — NSE's own daily provisional FII/DII net
   equity-flow report (₹ Cr). `tools/macro_context_tools.py::get_macro_context()` — RBI's policy
   repo rate and CPI inflation, scraped from RBI's own "Current Rates" table. Both follow the
   same never-raise, `{"error": ...}`-on-failure convention as every other `tools/*.py` module,
   and never guess a missing field (e.g. a DII row NSE didn't return, or a CPI figure RBI's
   homepage doesn't currently carry) — that field comes back `None`, never invented.
   **Disclosed limitation**: neither scrape target could be verified against a live response in
   this sandbox (no outbound internet — see the repeated disclosure elsewhere in this doc). Both
   parsers are written defensively so a real-world layout/schema drift degrades to an error dict
   rather than crashing the signal engine, but the actual selectors should be spot-checked against
   live NSE/RBI responses before this ships to a real deployment.
2. Unlike every other signal, this one is identical for every stock analysed on a given day, so
   `signals/macro.py` caches both fetches under a fixed pseudo-symbol (`"_MACRO"`) rather than
   fetching fresh per symbol — the same pattern `GET /api/market-picks/history` already uses to
   cache the Nifty benchmark series under a `"NSEI"` pseudo-symbol. `cache.TTL_HOURS` gained
   `fii_dii_flow` (24h — NSE publishes once per trading day) and `macro_context` (24h — RBI's repo
   rate changes at most every MPC meeting and CPI is a monthly release, so daily refresh is purely
   a ceiling, not a real cadence match).
3. `signals/macro.py::macro_signal()` combines both inputs into one `Signal`: net FII+DII flow
   (₹ Cr) hits a raw component capped at ±0.6 at ±3000 Cr thresholds, sub-weighted ×0.6 → up to
   ±0.36 contribution to the signal's own score; CPI above 6% (above RBI's inflation-target upper
   bound) or below 4% hits a raw component of ∓0.4/+0.2, sub-weighted ×0.4 → up to ∓0.16/+0.08 —
   repo rate is carried in `meta` for context but doesn't independently move the score (CPI
   already captures the same tightening/easing direction more directly). `UNKNOWN` (score 0) only
   when every one of the four underlying fields is `None`.
4. `run_signal_engine()` calls `macro_signal()` unconditionally alongside `technical_signal()` —
   both are the signals in this package that do their own I/O, so both are subject to the same
   "callers on an asyncio event loop must invoke this via an executor" rule `api.py`'s
   `/api/analyse/{symbol}` SSE endpoint already satisfies (see the "Technical signal" section
   above — no further change to that call site was needed for `macro`).

### Sector-aware signal weights

Previously the same `.4/.4/.2/.2/.2/.15` weight split applied to every stock regardless of
sector — a capital-intensive bank and an asset-light IT services company got identical
valuation/growth logic. `signals/engine.py::_weights_for_sector()` now layers a documented tilt
on top of `_DEFAULT_WEIGHTS` for three economically-similar sector groups, keyed off yfinance's
own `sector` field (`tools/nse_tools.py::get_stock_quote` → `info.get("sector")`, assumed to be a
GICS-like taxonomy — see the disclosed limitation below):

- **Rate-sensitive** (`Financial Services`, `Real Estate`, `Utilities`): valuation and the macro
  overlay (FII/DII flow + RBI rate/inflation, see above) weighted up; growth weighted down —
  these are typically mature, income-oriented businesses, not high-growth compounders.
- **Growth** (`Technology`, `Communication Services`, `Healthcare`): growth weighted up; the
  macro overlay weighted down — export-oriented, globally-priced businesses are less exposed to
  domestic rate/inflation than the FII/DII-heavy sectors above.
- **Cyclical** (`Basic Materials`, `Energy`, `Industrials`, `Consumer Cyclical`): technical and
  volume weighted up, offset by valuation and growth weighted down — price/volume momentum is
  more informative for a cyclical business than for a steady compounder, and a cyclical's
  steady-state fundamentals matter less than a compounder's.
- Any sector outside those three groups (including `None` when yfinance didn't report one) uses
  the unchanged default weights this engine always used.

Every override reallocates weight from other signals rather than just adding to the total, so
each group's weights sum to the same 1.55 baseline as `_DEFAULT_WEIGHTS` — a pure add-without-
offset would otherwise inflate that sector's `final_score` magnitude against the shared,
sector-independent verdict thresholds.

**Explicitly not a back-tested calibration** — three grouped buckets rather than one override
per individual GICS sector, since with only six signals and no realized-return backtest behind
any of this, splitting further would read as more empirical precision than the underlying
judgment actually has. This closes the "identical weights regardless of sector" gap; it does not
claim the specific override numbers are empirically optimal.

**Disclosed limitation**: whether yfinance actually reports GICS-like sector names (e.g.
`"Technology"`, `"Financial Services"`) for NSE/BSE symbols, rather than a different taxonomy,
was not verified against a live response in this sandbox (no outbound internet — same disclosure
as the FII/DII and macro-context scrapers above). There's real in-repo counter-evidence worth
noting: other pre-existing test fixtures in this codebase (`tests/test_signal_engine.py`'s own
`ExtractFeaturesTest`, `tests/test_market_picks_scoring.py`) use short Indian-market-style labels
like `"IT"`/`"Banking"` for this same field, rather than GICS names. If the real taxonomy differs,
every sector silently falls through to `_DEFAULT_WEIGHTS` — safe (identical to this engine's
pre-existing behavior) but a no-op for NSE/BSE stocks in production. `_log_unmatched_sector_once()`
logs a one-time-per-process debug event (`sector_weight_override_unmatched`) for each distinct
non-matching sector value seen, so this can be validated against real production traffic
post-merge without adding a new metrics dependency.

### Peer comparison flow (`GET /api/peers/{symbol}`)

Answers "is this ratio actually cheap/expensive for its sector" — something the
analyst prompt explicitly won't do (`config/analyst.json`: "Never invent
benchmarks or sector averages that are not in the data"). Real peer data closes
that gap without touching the analyst step at all:

1. `tools/screener_tools.py::get_peer_comparison()` scrapes Screener.in's own
   Peer comparison table (`section#peers`) — the company's row, up to 5 sector
   peers, and Screener's own sector-median row when present. Column parsing is
   driven entirely by the table's own headers (`_parse_peer_table()`), not a
   hardcoded schema, since the ratio set varies by sector (a bank's peer table
   looks nothing like an IT company's).
2. `api.py`'s `_compute_peer_percentiles()` ranks the company against its peers
   for every column both sides report (mean-rank percentile, 0-100). A ratio
   Screener doesn't expose for that sector (or that no peer reports) is simply
   absent from `percentiles` — never guessed or backfilled.
3. Cached like the six data slices (24 h TTL) but intentionally outside
   `ALL_DATA_TASKS` — a standalone, on-demand comparison fetched by the frontend
   after the main report loads, same pattern as `price_history` for sparklines.
4. `results-dashboard.tsx`'s `usePeerComparison()` hook fetches once and feeds
   both the dedicated "Peer Comparison" table and small percentile badges next
   to matching rows in the existing "Fundamentals" card — `normalizeRatioKey()`
   bridges the two independent label sets (the research task's own ratio names
   vs. Screener's peer-table column headers, e.g. "ROCE" vs "ROCE %").

**Absolute valuation anchor**: peer percentile only ever answers "cheap/expensive
*vs. peers*" — it says nothing about whether the stock is cheap/expensive *vs. its
own history*, which was the analyst-lens gap this closes. `_compute_valuation_anchor()`
(`api.py`) is folded into the same `/api/peers/{symbol}` response as a sibling
`absolute_anchor` field, not a new endpoint:

1. `tools/screener_tools.py::_extract_valuation_band()` parses Screener's own
   yearly Ratios table (`section#ratios`) for a "Price to Earning" row — the
   same company page `get_peer_comparison()` already fetches, so this is free
   (no extra network round trip), same pattern as `_extract_quarterly_trend`.
   Returns `{}` (never guessed) when the row is absent or fewer than 3 years
   are available — too thin a sample for a meaningful band. **Disclosed
   limitation**: whether Screener actually renders a yearly "Price to Earning"
   row under `section#ratios` (vs. only exposing historical P/E through its
   separate interactive chart, which this scraper does not call) was not
   verified against a live response in this sandbox — same disclosure as the
   FII/DII/macro scrapers and the sector-taxonomy assumption above. If the row
   isn't there under this id/label, this just returns `{}`, same as "Screener
   doesn't have this data" elsewhere in this module.
2. `_compute_valuation_anchor()` finds the current P/E in `self`'s own peer-row
   values (whichever column key contains "P/E", case-insensitive) and ranks it
   against `valuation_band.pe` using the same mean-rank percentile formula as
   `_compute_peer_percentiles` — but, unlike that function (which folds `self`
   into the ranked population), `current_pe` is ranked against `pe_values`
   alone: it's today's live snapshot, not itself one of the historical yearly
   observations. Returns `None` (not a guessed number) when there's no
   parseable current P/E or fewer than 3 years of band history.
3. `results-dashboard.tsx`'s `ValuationAnchorBadge` renders inside the existing
   "Peer Comparison" card, right below the table — buy/hold/sell-toned by
   percentile (≤33rd cheap, ≥67th expensive vs. its own range), showing the
   current P/E, its percentile, and the raw low/median/high band. Renders
   nothing when `absolute_anchor` is `null`.

### Insider & institutional activity flow (`GET /api/insider-activity/{symbol}`)

`tools/nse_insider_trades.py` and `tools/nse_bulk_block_deals.py` already scrape NSE's
promoter/director PIT disclosures and bulk/block deal feeds — but only as input to the
Market Picks discovery pipeline, where each qualifying trade is formatted as a
plain-language "article" for LLM extraction and then discarded. A researcher looking up
one specific stock had no way to see this activity unless that stock happened to make the
weekly picks list. This endpoint surfaces the same underlying data directly, per symbol:

1. Both tool modules gained a `_parse_pit_row()`/`_parse_deal_row()` shared parse step
   (returning a plain dict, not an LLM article) that the existing market-wide
   `_trade_to_article()`/`_deal_to_article()` functions now build on top of — so the
   market-wide and per-symbol paths can't drift on what counts as a "real" trade (same
   category/mode/value-threshold filters either way). `fetch_insider_trades_for_symbol()`
   and `fetch_bulk_block_deals_for_symbol()` are the new per-symbol entry points, returning
   `{"symbol", "trades": [...]}` / `{"symbol", "deals": [...]}` — structured records, not
   article text. Both sort on a separately-parsed `date_iso` field, never NSE's own raw date
   string (month abbreviations like "Jan"/"Apr" don't sort lexically in calendar order).
2. `fetch_insider_trades_for_symbol()` requests a 90-day window from NSE's PIT endpoint
   (vs. the market-wide scraper's 14-day window) — a single stock's insider activity is
   comparatively sparse, so a short window would too often show nothing. Bulk/block deals
   have no equivalent widening: NSE's `bulk-deals`/`block-deals` endpoints only ever return
   "recent trading days" with no date-range parameter to request more.
3. `GET /api/insider-activity/{symbol}` fetches both sources concurrently
   (`asyncio.gather`, same spirit as `_consolidated_payload`'s parallel lookups), combines
   them, and caches the combined result (24 h TTL) — standalone and on-demand, intentionally
   outside `ALL_DATA_TASKS`, same pattern as `peers`/`price_history`. Absent rather than
   guessed: most stocks simply have no recent insider/bulk activity in the window, which
   returns empty lists (never null), not an error.
4. `results-dashboard.tsx`'s `InsiderActivityCard` (via `useInsiderActivity()`) renders
   nothing when both lists are empty, and otherwise lists each trade/deal with a BUY/SELL
   badge, counterparty name, value, and date — right after the Peer Comparison card.

### Street consensus flow (`GET /api/street-consensus/{symbol}`)

`tools/trendlyne_agent.py::fetch_trendlyne_consensus()` already searches GNews for
Trendlyne-cited analyst commentary — but only market-wide, as Market Picks scoring input.
A researcher looking up one specific stock had no "N analysts rate this BUY" anchor
anywhere in the single-stock report. This surfaces the same search, scoped per symbol —
the same per-stock-endpoint pattern as insider activity above, but with one important
difference in what it can honestly return:

1. `fetch_trendlyne_consensus_for_symbol(symbol)` runs one GNews query ANDing the exact
   ticker, `"Trendlyne"`, and a buy/upgrade/target-price phrase (vs. the market-wide
   function's three broader queries), returning `{"symbol", "articles": [...]}` — real
   article title/summary/url/published_at, deduped by URL the same way
   `fetch_trendlyne_consensus()` already dedupes. The bare ticker is what `get_latest_news`
   already searches by elsewhere in this codebase, but stacked under three more required
   terms here recall is lower still — many tickers (`HDFCBANK`, `M&M`) rarely appear
   literally in prose the way journalists write company names, so this returns real
   coverage when Trendlyne got cited by name, not a guarantee of finding every article a
   human researcher would.
2. **Deliberately never a numeric consensus rating or target price.** This module has never
   scraped trendlyne.com's own aggregated numbers — only GNews articles that happen to
   mention Trendlyne — so a "12 analysts rate BUY, target ₹X" figure isn't data this module
   actually has. Returning one would violate this codebase's "never invent" convention the
   same way guessing a missing scraped field would; the UI surfaces real article headlines
   ("TCS gets Trendlyne buy upgrade") instead of a synthesized number.
3. `GET /api/street-consensus/{symbol}` is cached (24 h TTL) but intentionally outside
   `ALL_DATA_TASKS` — standalone and on-demand, same pattern as `peers`/`insider_activity`.
   An empty `articles` list (never an error) is the expected common case for most stocks on
   most days — both because most companies simply don't have recent Trendlyne-cited
   coverage, and because of the query's own recall limits noted in point 1.
4. `results-dashboard.tsx`'s `StreetConsensusCard` (via `useStreetConsensus()`) renders
   nothing when `articles` is empty, and otherwise lists up to 6 recent article
   titles/dates as external links — placed after `InsiderActivityCard` in the card grid.

### Symbol validation flow (`GET /api/validate/{symbol}`)

Handles three input forms:
1. **ISIN** (e.g. `INE009A01021`) — resolved via NSE equity master CSV first, then yfinance as fallback
2. **BSE-forced** (exchange query param = `BSE`) — resolves Screener.in slug → proper ticker via `_screener_company_page_sync`
3. **Ticker / name** — NSE autocomplete + BSE autocomplete (via Screener) run in parallel; BSE ISIN lookup enriches the NSE result; Screener.in fallback if both miss

### Market picks flow

1. Browser opens `EventSource` → `GET /api/market-picks` (optional `?force=true` bypasses cache)
2. `api.py` checks `output/_market_picks/picks.json` (192 h / 7-day TTL — sized to the weekly cron
   cadence below plus a day of slack, not the old "no scheduled job" 6 h bound); serves cached `done`
   event immediately if fresh
3. On cache miss: wraps `MarketPicksPipeline.run()` in `run_in_executor`; bridges events via `asyncio.Queue`
4. Pipeline calls `on_event(payload)` → `loop.call_soon_threadsafe(q.put_nowait, payload)` → SSE stream
5. The six pipeline phases run synchronously inside the executor thread; final result saved to cache
   via `market_picks_pipeline.save_picks_cache()` (also re-exported into `api.py` as `_save_picks_cache`
   for the existing call sites/test patches)

**Weekly auto-refresh**: `.github/workflows/market-picks-cron.yml` fires every Monday at 01:30 UTC
(07:00 IST, ahead of NSE's 9:15 IST open) — weekly, not daily like `sme-cron.yml`, to match the
product's own "Top Indian Stocks This Week" framing. Unlike SME (which persists to Postgres, reachable
from anywhere), the picks cache is a local file on whatever host runs the backend — a GitHub Actions
runner can't compute picks and expect them to reach the live site. So this workflow instead calls
`GET {MARKET_PICKS_API_URL}/api/market-picks?force=true` on the already-deployed backend (same effect
as a user clicking "Fresh scan," just on a timer) and requires a `MARKET_PICKS_API_URL` repository
secret pointing at that backend's public address. `market_picks_pipeline.py` also has a `main()` CLI
entrypoint for a self-hosted crontab that runs on the *same* host as the backend (mirrors the
crontab alternative documented for `sme_ema_pipeline.py` below) — GitHub's own workflow does not call it.

**`GET /api/market-picks/status`** is cache metadata only (no pipeline run): `last_run_at` (present even
once the cache has gone stale — unlike the picks-serving path, "stale" and "absent" must be
distinguishable here), `cache_fresh`, and `next_scheduled_at` (computed in `api.py` from constants that
mirror the cron schedule above — kept in sync by hand, there's no way to share one source of truth
between a GitHub Actions cron expression and this Python computation). Powers the idle `/market-picks`
hero's true "Last scan" / "Next scheduled scan" line, replacing an unverifiable "every week" claim.

**Browsing a specific day's picks**: `GET /api/market-picks/history` normally aggregates every
`output/_history/<date>.json` snapshot into a per-symbol first/last-seen roll-up (see "Market picks
track record" below) — it never surfaces one day's actual full list. `?date=YYYY-MM-DD` is a second
code path on the same handler that skips aggregation entirely and returns that single day's snapshot
verbatim (`{"date": ..., "picks": [...]}`, the same shape `_save_history()` wrote it in — just the six
fields persisted there, not the full live `MarketPick` shape); 404 if no snapshot exists for that date
(weekend, holiday, or before this feature existed), 422 if `date` isn't `YYYY-MM-DD`. The no-`date`
aggregated response also grew an `available_dates` field (every date with a stored snapshot) so the
frontend's date picker (`/market-picks/history`) can bound its `<input type="date">` and step
Prev/Next through actual snapshot days without a second round trip.

**Positions ("I bought this")**: purely client-side — no backend endpoint, no DB table.
`frontend/lib/positions.ts`'s `usePositions()` hook persists a `Position[]` (symbol, company,
exchange, `entry_price`/`target_price`/`stop_loss` captured at mark-time from the live `MarketPick`,
and a `bought_at` timestamp) straight to `localStorage` under `alphapulse_positions`, using the same
module-level shared-cache-plus-listener-set pattern `useWatchlist()` uses for its Postgres-backed
data — here there's simply no fetch step, since localStorage reads/writes are synchronous.
`PositionButton` (next to `TradeBox`'s entry/target/stop-loss in each pick's expanded row) toggles a
pick in/out of this list; `PositionsStrip` (rendered above the phase content on `/market-picks`, so it
shows regardless of whether a fresh scan has run) polls the *existing* `GET /api/prices` endpoint every
30 s for the tracked symbols' live price — no new backend work — and computes P&L client-side against
each position's stored entry, flagging "At target" / "At stop-loss" when the live price clears either
level.

**Portfolio summary** (`/portfolio`): an aggregate view over every tracked position, addressing the
Product-lens gap "positions aren't aggregated into a portfolio" — `PositionsStrip` only ever showed
one card per position, with no roll-up. Purely client-side, same as the rest of this feature: no new
backend endpoint, reuses the exact same `GET /api/prices` poll `PositionsStrip` already makes (30 s
interval), just computed over the full `positions` array instead of rendered per-card. `Position`
carries no share-count/quantity field (only `entry_price`/`target_price`/`stop_loss` per symbol), so a
real capital-weighted portfolio value (₹ invested, ₹ current) isn't data this page actually has —
computing one would mean silently assuming "1 share per position," which would violate this
codebase's "never invent" convention the same way guessing a missing scraped field would. The
aggregate stats shown are therefore explicitly equal-weighted across positions and labeled as such:
win rate (share of priced positions currently above entry — the adjacent "W/L" breakdown also
surfaces a "flat" count for exactly-0%-P&L positions, so the two numbers always reconcile), average
P&L% (a plain mean of each position's own % move, not a capital-weighted return), best/worst
performer, and counts at target/stop-loss. `PositionsStrip` gained a "View full portfolio →" link;
`/market-picks`'s nav bar gained a "Portfolio" link alongside "Watchlist"; `/portfolio`'s own nav bar
links to every sibling section (same full set every other page's nav bar carries), even though no
*other* page links back to it — positions are only ever created from the Market Picks flow, so that
one entry point (plus `PositionsStrip`'s link) is enough for discoverability without adding a seventh
item to every other page's already-long nav bar.

### Shared-state rate limiting (`rate_limiter.py`)

Three pieces of backend guard state were previously **single-process, in-memory, by design** —
each backend worker/replica held its own counter, so the documented per-IP rate limits, the LLM
concurrency ceiling, and the SME refresh guard all silently became *per-worker* the moment the
backend ran with more than one worker (see `docs/deployment.md`'s "Scaling" section, which
flagged this as the blocker to scaling past a single process).

1. `rate_limiter.py` (repo root, alongside `cache.py`) is a small shared module with three
   primitives — `is_allowed(key, max_calls, window_seconds)` (sliding-window rate limit),
   `try_acquire_slot(name, limit)` / `release_slot(name)` (named concurrency ceiling), and
   `try_acquire_lock(name, ttl_seconds)` / `release_lock(name)` / `is_locked(name)` (single-run
   lock) — each backed by a small Lua script (rate limit, slot) or `SET NX EX` (lock) for atomic
   check-and-set against Redis, so two workers hitting the same key at the same instant can't
   both succeed.
2. **Graceful degradation, not a hard dependency**: every primitive falls back to the exact same
   in-memory implementation this app had before Redis support existed whenever `REDIS_URL` is
   unset, or a Redis call raises (network blip, Redis down) — logged as a warning
   (`redis_rate_limit_failed` etc.) and swallowed, the same "missing optional infra degrades
   rather than breaks" convention as `DATABASE_URL`/`SMTP_HOST` elsewhere in this codebase. A
   single-process deployment behaves identically with or without `REDIS_URL` set.
3. `api.py`'s `_check_rate_limit()`, `_acquire_llm_slot()`/`_release_llm_slot()`, and the
   `/api/sme-signals/refresh` endpoint's run-guard are now thin wrappers over these three
   primitives — same call sites, same `429`/capacity-rejection/`409` response shapes as before,
   just backed by shared state instead of a module-level dict/counter/bool. The SME refresh
   endpoint's lock-then-rate-limit ordering was preserved exactly (a `try_acquire_lock()` success
   followed by a rate-limit rejection releases the lock before returning 429), matching the
   original code's "409 takes priority over 429 when both would apply" behavior.
4. **Crash recovery**: a Redis-held slot or lock carries a TTL (`_SLOT_TTL_SECONDS` = 600s for
   slots; the SME refresh lock uses its own 3600s, matching how long one pipeline run can
   reasonably take) so a worker that crashes mid-hold — skipping its `release_*()` call — doesn't
   permanently strand that slot/lock. The in-memory fallback has no TTL, since a process crash
   there already resets all in-memory state, making one redundant.
5. Docker Compose gained a `redis` service (`redis:7-alpine`, persisted via a named volume) and
   wires `REDIS_URL` into the `backend` service automatically — a manual/non-Compose deployment
   only needs to set `REDIS_URL` once it scales past one worker (see `docs/deployment.md`).

### Trusted client IP for per-IP rate limiting

The Redis-shared limiter above fixed rate-limit state being *per-worker* — but every one of
`api.py`'s per-IP buckets was still being keyed off `request.client.host`, and every request
reaches this backend via the Next.js proxy routes, server-to-server (see "Proxy routes" below).
That means `request.client.host` is always the Next.js server's own IP, never the real visitor's
— collapsing every per-IP limiter (`/api/analyse`'s 20/5min, `/api/auth/request-link`'s 5/15min,
etc.) into one shared bucket for the entire site regardless of how many distinct visitors are
actually calling it, the opposite of what a *per-IP* limit is for.

1. `api.py::_client_ip(request)` only trusts a caller-supplied client IP when the request also
   presents a matching shared secret — `TRUSTED_PROXY_SECRET` (env var, unset by default) —  via
   the `X-Internal-Proxy-Secret` header. When it matches, the first address in `X-Forwarded-For`
   is used as the client IP; otherwise (no secret configured, or a mismatch) it falls straight
   back to `request.client.host`, i.e. today's behavior. `_rate_limit()` now calls this instead of
   reading `request.client.host` directly — its only call site.
2. `frontend/lib/proxy-headers.ts::clientIpHeaders(req)` is the frontend half: every one of the
   ~25 Next.js proxy routes under `frontend/app/api/*` now merges this into the headers on its
   `fetch()` call to the backend. It reads the real client IP off whatever's in front of the
   Next.js server in production (a reverse proxy/CDN/load balancer — see `docs/deployment.md`)
   via the standard `X-Forwarded-For` header the request arrived with, and forwards it — plus
   `TRUSTED_PROXY_SECRET` from `process.env` — to the backend. Both env var and header are
   optional; a route with neither set sends no extra headers and the backend behaves exactly as
   before.
3. **Why a shared secret, not just trusting `X-Forwarded-For` outright**: the backend's port isn't
   inherently unreachable except through the Next.js proxy — without the secret check, any direct
   caller could set an arbitrary `X-Forwarded-For` value to dodge its own rate limit, or to frame
   an innocent IP into being blocked. The secret proves the forwarded value really came from this
   deployment's own Next.js server, which is the only thing that knows it.
4. Deliberately scoped to rate limiting only — this does *not* change what IP address ends up in
   any log line or stored record; `observability.log_event()` call sites are unaffected.
5. Local Docker Compose exposes the frontend container directly (no reverse proxy in front of it),
   so `TRUSTED_PROXY_SECRET` is a documented no-op there by default — both the `backend` and
   `frontend` services pass it through from the host's `.env` (`${TRUSTED_PROXY_SECRET:-}`) so a
   self-hosted deployment that does add a reverse proxy in front of the frontend container can set
   one value in `.env` and have it reach both services unchanged.

### Error tracking / APM hook (`error_tracking.py`)

Every error-level `observability.log_event()` call already carries a structured JSON payload
(`event`, and whatever `**fields` the call site attached — `symbol`, `run_id`, `error`, etc.), but
until now it only ever reached stdout/the process log. There was no way to get paged, deduped,
or grouped-by-stack-trace on a production error without grepping logs after the fact.

1. `error_tracking.py` (repo root, alongside `cache.py`/`rate_limiter.py`) is a small pluggable
   hook gated behind the optional `SENTRY_DSN` env var — unset by default, so `log_event()`
   behaves exactly as before with zero behavior change out of the box. "Pluggable" here means
   swappable ingest endpoint (real Sentry, self-hosted Sentry, GlitchTip — anything that speaks
   the same DSN/`init()` protocol), not a plugin registry of multiple simultaneous backends; this
   codebase has exactly one thing that consumes errors today (`log_event`'s error-level path), so
   a heavier abstraction on top of that would be speculative.
2. `init_error_tracking()` is called once per process at every entry point that can emit an
   error-level `log_event()` — `api.py` (module-level, right after `LOGGER = get_logger("api")`,
   so it runs once per worker process) and the CLI `main()` of `main.py`, `sme_ema_pipeline.py`,
   `market_picks_pipeline.py`, `watchlist_alerts.py`, and `screener_pipeline.py`. It's idempotent
   (a second call is a harmless no-op, guarded by a module-level `_initialized` flag) since
   `sentry_sdk.init()` itself isn't safe to call twice with different configs — this matters
   because e.g. `watchlist_alerts.py` imports `main.py` (for `_fetch_task`), and `api.py`'s
   background SME/screener refresh endpoints run those pipelines' `run()` functions in-process
   inside the already-initialized API server, not through their CLI `main()` at all.
3. **Same graceful-degradation convention as `DATABASE_URL`/`SMTP_HOST`/`REDIS_URL`** elsewhere in
   this codebase: unset `SENTRY_DSN`, a missing `sentry-sdk` package (logged once via stdlib
   `logging`, not `observability.log_event` — this module is a dependency *of* observability.py,
   so routing its own diagnostics back through `log_event` would be circular), or a failed
   `sentry_sdk.init()`/capture call all degrade to a silent no-op rather than breaking the
   request/batch job that triggered the error in the first place. `sentry-sdk` is still a hard
   `requirements.txt` dependency (same pattern as `redis` — always installed, behavior gated by
   the env var) rather than conditionally installed, so there's no separate install step once a
   deployment is ready to set `SENTRY_DSN`.
4. `observability.log_event()`'s error-level path (`level="error"`, the existing convention every
   call site already uses) forwards `(event, fields, exc)` to `error_tracking.capture_error()`,
   wrapped in its own try/except so a broken/unreachable Sentry backend can never break the
   primary structured-logging path `log_event` exists for. `log_event()` gained a new optional
   `exc: BaseException | None` keyword — existing call sites are unchanged (still passing
   `error=str(exc)` as a field, which is what the log line itself shows); passing the actual
   exception object too is opt-in and only worth doing at a handful of the most valuable
   top-level `except Exception as exc:` sites, since it's what gives Sentry a real grouped stack
   trace instead of just a message string.
5. `capture_error()` tags the Sentry event with the `event` name, attaches every other field as
   Sentry "extra" context (skipping `error` itself — that string just duplicates what
   `capture_exception`'s own stack trace already conveys), and calls `capture_exception(exc)` when
   an exception object was passed, or `capture_message(event, level="error")` otherwise.
6. `sentry_sdk.init()` is called with an explicit `integrations=[LoggingIntegration(event_level=None)]`
   override — without it, the SDK's own default `LoggingIntegration` auto-captures *any*
   `logger.error()`/`.critical()` call as its own event, including the plain log line
   `log_event()` already emits immediately before it calls `capture_error()` — so every
   error would otherwise ship as two separate, differently-shaped Sentry events (one
   well-tagged, one a raw JSON-message duplicate with no `event` tag or exception attached).
   `capture_error()` also uses `sentry_sdk.new_scope()`, not the older `push_scope()` — the
   latter is deprecated as of `sentry-sdk` 2.x and logs a `DeprecationWarning` on every call.
   Both were caught (and are regression-tested) by actually initializing the real, installed
   `sentry-sdk` package against a custom in-memory `Transport` subclass and asserting exactly
   one envelope is captured per error — most of `tests/test_error_tracking.py` mocks
   `sentry_sdk` at the `sys.modules` level (the same crewai-mocking pattern `tests/conftest.py`
   already documents), which verifies this module's own call shapes but can't catch a real SDK
   behavior mismatch like these two; `RealSdkRegressionTest` exists specifically to close that
   gap by running against the real package instead.
7. **Disclosed limitation**: `sentry_sdk.init()`'s actual behavior against a live Sentry
   project — DSN parsing, event delivery, what a captured event looks like once ingested —
   was not verified against a real Sentry account in this sandbox (no outbound internet to
   sentry.io; same disclosure as the FII/DII/RBI scrapers and the sector-taxonomy assumption
   elsewhere in this doc). `RealSdkRegressionTest` (point 6 above) verifies the real SDK's
   *client-side* behavior — what gets handed to its transport layer — not that a live ingest
   endpoint actually accepts and stores it.

### Schema-drift detection (`schema_drift.py`)

The six data slices (`stock_info`, `research`, `news`, `shareholding`, `mf_holdings`,
`filings`) are all scraped, and tools never raise (see "Important Rules for Claude" below) —
a scraped source restructuring its HTML/JSON (Screener.in renaming a table, NSE changing a
field) doesn't crash the fetch, it just silently returns something under the expected key
that's no longer the expected *shape*. `schemas.CONTRACTS`'s existing `"required"` list only
checks presence, and most other fields are legitimately absent per-symbol by this codebase's
own "never invent" convention — so a naive "did the key set change" check would be constant
false-positive noise on exactly the symbols/fields this convention already expects to be thin.

1. `schemas.CONTRACTS` gained an optional `"types"` entry per task — a `{field: type}` map for
   *container-shaped* fields only (`dict`/`list`), e.g. `research: {"ratios": dict,
   "quarterly_trend": dict}`. This is the single source of truth `schema_drift.py` reads from —
   no second hand-maintained field list to drift out of sync with `schemas.py` itself.
2. `schema_drift.check_drift(task_name, raw_data)` is a pure function: for each field in that
   task's `"types"` map that's *present* in `raw_data`, checks its Python type matches. A field
   that's simply absent (the common, legitimate "never invent" case) is skipped, not flagged —
   this only fires when a field is present but has changed shape (e.g. `ratios` coming back as a
   `list` instead of a `dict`), which is never a legitimate per-symbol variation and is exactly
   the case that breaks every downstream `.get()`/iteration call written for the declared shape,
   often silently (many call sites are themselves defensively wrapped, so a shape flip can
   degrade a section to "missing" several layers away from where the drift actually happened).
3. `schema_drift.log_drift_if_any(task_name, raw_data, **context)` wraps `check_drift()` in a
   try/except that never raises — matching the "tools must not raise" convention even though
   this isn't a tool itself, since it's called from the same fetch loop a real tool failure
   already can't be allowed to break. When drift is found it calls `observability.log_event()`
   at `level="warning"` (not `"error"` — this needs a human to look at the scraper, not an
   on-call page through the Phase 15 Sentry hook) with the field-level problem descriptions plus
   whatever `run_id`/`symbol` context the caller passed through.
4. Wired into `main._fetch_task()` — the single choke point all six data-slice fetches already
   go through for both the CLI and `api.py`'s SSE endpoint (`main.py`'s own module docstring:
   "also contains `_fetch_task`... shared with `api.py`") — right after a successful
   `tool_attempt_succeeded` log, on both the raw-dict and parsed-JSON-text success paths. No
   other call site needed changing to get coverage across every entry point that fetches these
   six slices.
5. Deliberately scoped to only these six "data slices" (the term CLAUDE.md's own "Project
   Overview" section already uses) — not the growing set of standalone scrapers outside
   `ALL_DATA_TASKS` (peers, insider activity, street consensus, FII/DII flow, macro context,
   valuation band, NIFTY 500 constituents, SME stock lists). Those already carry their own
   disclosed-limitation notes elsewhere in this doc about being unverified against live
   responses in this sandbox; extending drift detection to them is future work, not silently
   assumed to already be covered by this pass.

### SME golden cross flow

`sme_ema_pipeline.py` is a standalone batch job (PostgreSQL, `DATABASE_URL` env var):

1. Fetches all NSE Emerge + BSE SME stocks (`tools/sme_tools.py`, 24 h list cache)
2. Downloads 1 year of daily OHLCV per stock via yfinance
3. Computes EMA 20/50 over the full year; flags **golden crosses** (EMA20 crosses above
   EMA50) and **death crosses** (crosses below); stores only the last ~3 months of rows.
   Also computes **RSI(14)** (`_compute_rsi()`, Wilder's smoothing) and a **volume-spike**
   flag (`_compute_volume_spike()`: today's volume > 2x its trailing 20-day average) per
   day, stored alongside `ema20`/`ema50` on `ema_signals` — momentum-screener confirmation
   signals a bare EMA cross doesn't provide on its own. Also computes avg daily
   volume/turnover over the last 20 trading days (`_compute_liquidity()`) and market cap
   via yfinance `fast_info` (`_safe_market_cap_cr()`, one extra lightweight request per
   stock — trailing P/E deliberately isn't fetched, since it needs the much heavier full
   `.info` scrape, which across potentially hundreds of SME stocks per run would meaningfully
   add to this pipeline's already rate-limit-sensitive runtime for one inline column) — no
   OHLCV network calls beyond that. Both stored on `sme_stocks` (plain `UPDATE`s via
   `_upsert_liquidity()`/`_upsert_market_cap()`, run after `_upsert_signals()` since neither
   is known until this phase, unlike the stock-list metadata `_upsert_stocks()` writes
   before OHLCV is even fetched)
4. `GET /api/sme-signals` serves cross events + current regime (`ema20 > ema50` on the
   latest row) + each stock's `avg_volume_20d`/`avg_turnover_20d`/`market_cap_cr`/`rsi14`/
   `volume_spike` + a 90-day golden-cross follow-through hit rate; `POST /api/sme-signals/refresh`
   runs the pipeline in the background (409 if already running; `refreshing` flag in the
   GET response)

CLI: `--setup-db` (create tables), `--reset-db` (drop + recreate — required after schema
changes; data is fully regenerable), `--force` (bypass list cache), `--lookback N`.

**Disclosed limitation**: `--reset-db` calls `metadata.drop_all(engine)`/`create_all(engine)`
against the single shared `MetaData()` in `db/models.py` — the same object every other table
in this app (`users`, `sessions`, `watchlist_items`, `verdict_history`, `api_keys`, and
`screener_stocks`) is registered on. Running `sme_ema_pipeline.py --reset-db` therefore drops
and recreates every table in the database, not just `sme_stocks`/`ema_signals` — "data is
fully regenerable" above is only true for this pipeline's own tables, not for accounts/
sessions/watchlist rows belonging to other features. `screener_pipeline.py --reset-db` (see
"Custom screener flow" below) was scoped to its own table specifically to avoid repeating this;
this script predates that fix and hasn't been changed to match, since a blast-radius change to
an existing operational command is riskier to make speculatively than to flag here for whoever
touches this next.

The DB column for the cross is named `cross_type` (`'golden'`/`'death'`/`NULL`) because
`CROSS` is a reserved SQL keyword; the API/TS field is `cross`.

**Liquidity + illiquid badge**: `avg_volume_20d`/`avg_turnover_20d` on `sme_stocks` are NULL
until the first pipeline run after this feature shipped (never invented for older data). The
`_ILLIQUID_TURNOVER_THRESHOLD` (₹5L avg daily turnover) is a frontend-only constant in
`frontend/app/sme-signals/page.tsx` — a stock below it gets an amber "⚠ Illiquid" badge next
to its Turnover cell (reusing the `hold` design token; there's no separate `warning` token in
this codebase). The threshold decision lives client-side rather than as a stored/computed
backend field, matching how other purely-presentational thresholds (e.g. market-picks'
large/mid/small cap buckets) are handled in this repo.

**Cross outcome (forward returns)**: `GET /api/sme-signals/{symbol}/history` also returns
`cross_events` — every cross in the stored ~3-month window, most recent first, with `ret_10d_pct`/
`ret_20d_pct` (close price N trading days after the cross, as a % change from the close at the
cross). Computed in Python post-fetch by `_compute_cross_events()` in `api.py`, not stored — the
series it operates on is already small (≤ `_STORE_DAYS`) and already fetched for the EMA chart, so
no new query or schema change was needed. A return is `null` (not a guess) if fewer than N trading
days have elapsed since the cross within the stored window — this also means "last 3 golden
crosses" can genuinely return fewer than 3 (or zero) for an infrequently-crossing stock, since
`ema_signals` only retains ~100 calendar days (`_RETENTION_DAYS`) — forward-return history is
bounded by the same retention window as everything else in this table, not a separate archive.
`frontend/app/sme-signals/page.tsx`'s expanded row renders this as "Last N golden/death crosses
(20d): +12%, −4%, +22%" above the EMA chart, using the same fetch the chart already makes.

**Aggregate golden hit-rate**: the single strongest trust-building number a raw technical
screener can show — "golden crosses in the last 90d: X% follow-through" — is computed as
part of `GET /api/sme-signals` in one SQL pass: a `LEAD(close_price, 20) OVER (PARTITION BY
symbol ORDER BY trade_date)` window function finds each golden cross's close price 20 trading
days later, aggregated across every stock at once (the same trading-day-offset approach
`_compute_cross_events` uses per-symbol, just as one query instead of N). Returned as
`golden_hit_rate_90d: {sample_size, win_rate, lookback_days, forward_days}` — `win_rate` is
`null` when `sample_size` is 0 (never guessed at); a cross too recent to have resolved yet
(`LEAD` returns `NULL`) is excluded from the sample rather than counted as a loss. Surfaced as
a 5th stat tile on `/sme-signals`.

**RSI(14) + volume spike**: standard momentum-screener confirmation signals alongside the EMA
cross — a cross with no volume confirmation behind it is a weak signal on its own. Both are
per-day columns on `ema_signals` (`rsi14`, `volume_spike`), computed once per pipeline run
from the same OHLCV fetch (see step 3 above), and filtered **client-side** in
`frontend/app/sme-signals/page.tsx` (RSI oversold ≤30 / overbought ≥70 chips, a
"Volume-confirmed only" toggle) — the API already returns every row for the selected
period/direction/view, so no new query params were needed for this, matching how the
existing Exchange filter already works.

**Regime view**: `GET /api/sme-signals?view=regime` (default `view=crosses`) drops the
`cross_type IS NOT NULL` filter and returns the latest stored row for **every** monitored
stock via `DISTINCT ON (s.symbol) ... ORDER BY s.symbol, e.trade_date DESC` — the "golden-now"
stat in the default view has no way to say which specific stocks make up that number without
this. `lookback`/`direction` are accepted but ignored in this view (no cross-event window to
filter by). Since most stocks' latest row isn't a cross day, `cross` is `null` for most rows
in this view — `SmeSignal.cross` and `CrossBadge` both accept `null` (rendered as "—") to
support this; in the default crosses view `cross` is never null (guaranteed by the SQL's own
`WHERE e.cross_type IS NOT NULL`). The frontend's Period/Direction filter chips are hidden in
regime view since they don't apply.

**BSE deep-link resolution**: an NSE row's `symbol` is already a directly analyzable ticker,
so it deep-links straight to `/?symbol=<symbol>`. A BSE SME row's `symbol` is BSE's own numeric
scrip code, which isn't — `/api/analyse/{symbol}` passes its input straight through to
yfinance/Screener.in/NSE-API calls with no resolution step, so it needs the same ISIN-based
resolution `/api/validate/{symbol}` already does for a user-typed ISIN (see "Symbol validation
flow" above). `GET /api/sme-signals` now selects `s.isin` in both views (`null` for NSE rows —
`tools/sme_tools.py`'s NSE fetch never populates it; present for BSE rows when BSE's own list
API reported one). The frontend deep-links a BSE row via `/?symbol=<isin>` when `isin` is set
(plain, unclickable text otherwise), and the home page's deep-link handler
(`frontend/app/page.tsx`) detects an ISIN-shaped `?symbol=` value and resolves it through
`GET /api/validate/{isin}` first — same resolution `ticker-search.tsx` already does for
user-typed ISINs — before starting the actual analysis SSE stream, showing a brief "Resolving
listing…" state and a dedicated error message if resolution fails (never silently retrying the
raw ISIN as if it were a ticker). This only applies to genuinely ISIN-shaped deep links — every
other existing deep link (NSE rows, market-picks, consolidated card) is already a resolved
ticker and skips this extra round trip entirely, unchanged.

Daily auto-run: `.github/workflows/sme-cron.yml` runs the pipeline on GitHub Actions at
13:00 UTC (18:30 IST) on weekdays — NSE closes 15:30 IST, so this leaves a ~3h buffer for
end-of-day data to settle. Requires a `DATABASE_URL` repository secret pointing at a
network-reachable Postgres instance (Settings > Secrets and variables > Actions); the
workflow fails fast with a clear message if it's missing rather than a raw Python
traceback. Trigger a one-off run manually via the Actions tab's "Run workflow" button
(`workflow_dispatch`). `sme_ema_pipeline.run()` returns `False` (and the CLI exits non-zero)
when the run was substantially unsuccessful — an empty stock list, or an OHLCV fetch error
rate above `_MAX_ACCEPTABLE_ERROR_RATE` (50%, almost always NSE/yfinance rate-limiting rather
than genuinely bad symbols) — so a bad run fails the GitHub Actions job instead of silently
"succeeding" with mostly-empty data, and GitHub's built-in run-failure notification fires.
For a local/self-hosted alternative, a crontab entry works too:

    30 18 * * 1-5 cd /path/to/stock-research && .venv/bin/python sme_ema_pipeline.py >> output/sme_cron.log 2>&1

### Custom screener flow

Generalizes SME Signals' filter-chip pattern to the main NSE/BSE market (the gap the
Product-lens gap analysis called out: "no custom screener" for the primary large/mid-cap
flow this product is centered on) — a stored-metrics batch pipeline, `screener_pipeline.py`,
mirroring `sme_ema_pipeline.py`'s shape, served at `/screener` via `GET /api/screener`.

1. **Universe**: NIFTY 500 (NSE's own published index membership,
   `tools/nifty500_tools.py::get_nifty500_constituents()`, 24 h cache) rather than the full
   NSE equity master (`_nse_master.txt`, ~2000 symbols) — a daily per-stock yfinance `.info`
   scrape (this codebase's heaviest documented per-symbol call; see `sme_ema_pipeline.py`'s
   own note on why it deliberately avoids that call for "hundreds of SME stocks") is only
   reasonable at a bounded, curated scale, and NIFTY 500 already covers the vast majority of
   stocks anyone would realistically screen for. **Disclosed limitation**: the exact NSE
   archive URL and CSV column layout for the NIFTY 500 list was not verified against a live
   response in this sandbox (no outbound internet — same disclosure pattern as the other
   NSE/BSE scrapers in this codebase) — defensive parsing degrades to an empty list (never a
   partial/guessed universe) rather than raising.
2. **No new scraping/OHLCV logic** — `screener_pipeline.py` reuses the exact same
   already-cached fetch functions the rest of this codebase already has for each stock:
   `tools.nse_tools.get_stock_quote` (price, P/E, market cap, sector/industry — one yfinance
   `.info` call, same as the main analysis flow) and `signals.technical.technical_signal`
   (RSI14 + EMA20/EMA50 trend posture, off the already-6h-cached `price_history` series — see
   "Technical signal" above). Both results are upserted into a new stored-metrics table,
   `screener_stocks`, so `GET /api/screener` never needs a live fetch per request — the same
   "fetch once, filter/sort many" shape `sme_stocks`/`ema_signals` already established. The two
   fetches are isolated in their own try/except inside `_fetch_one()` — a `technical_signal`
   failure (a transient pandas/price-history hiccup) must not discard an otherwise-good quote
   (price/P/E/market cap/sector); `rsi14`/`ema_trend` simply stay `null` in that case, same as
   when `technical_signal` legitimately returns `UNKNOWN` for too little price history.
3. **Industry vs. sector**: `nse_industry` (from the NIFTY 500 list itself, a real
   NSE-published classification) is the primary filter-chip dimension, in preference to
   `sector` (yfinance's own field, kept on the table for reference) — `sector`'s GICS-vs-
   Indian-market taxonomy for NSE/BSE symbols is an explicitly disclosed unverified assumption
   elsewhere in this codebase (see "Sector-aware signal weights" above), so this screener
   doesn't lean on it as the primary, user-facing filter.
4. `GET /api/screener` (rate-limited 60/min) accepts `industry`, `ema_trend`
   (`all`/`bullish`/`bearish`), `pe_max`, `market_cap_min`, `rsi_min`/`rsi_max`, `sort`
   (whitelisted column set — interpolated into `ORDER BY` since column names can't be bind
   parameters, validated against the whitelist first, same "closed enum, not raw user text"
   safety as `/api/sme-signals`'s `direction`/`view`), `order`, and `limit`/`offset`. Every
   numeric filter is optional and AND-ed together; a `NULL` value for a stock (yfinance/
   Screener didn't have it) excludes that stock from that filter rather than guessing a value
   for it. The response's `industries` field is the real, currently-populated set of
   `nse_industry` values in the table — the frontend's filter chips are built from this, not a
   hardcoded/guessed list, the same "no static list, ask the data" instinct as
   `GET /api/market-picks/history`'s `available_dates`.
5. `POST /api/screener/refresh` runs the pipeline in the background — same
   lock-then-rate-limit pattern (409 takes priority over 429) as `/api/sme-signals/refresh`.
6. `frontend/app/screener/page.tsx` renders a filterable/sortable table (trend/RSI/industry
   filter chips, P/E and market-cap numeric inputs, click-to-sort column headers) — a "Screener"
   nav link was added alongside "SME Signals" across every page's nav bar. Each row carries a
   `WatchlistButton`, tying this mode into the same cross-mode watchlist as the other three.
7. **Daily auto-run**: `.github/workflows/screener-cron.yml` runs at 14:00 UTC (19:30 IST) on
   weekdays — after `sme-cron.yml` (13:00 UTC) so that pipeline's own writes have settled, after
   NSE's 15:30 IST close, and 30 minutes after `watchlist-alerts-cron.yml` (also 13:30 UTC) so
   the two independent jobs don't hit the same DB connection pool / Actions minute at once. Same
   `DATABASE_URL`-secret-required, fail-fast-with-a-clear-message pattern as `sme-cron.yml`.
   `screener_pipeline.py` also has a `--setup-db`/`--reset-db`/`--force` CLI, same shape as
   `sme_ema_pipeline.py` — except `--reset-db` here is scoped to dropping/recreating only the
   `screener_stocks` table (`screener_stocks.drop()`/`.create()`, not `metadata.drop_all()`),
   unlike `sme_ema_pipeline.py --reset-db`, which operates on the shared `MetaData()` and so
   drops every table in the app — see that command's own disclosed limitation above.

### Watchlist flow

The `watchlist_items` table (PostgreSQL, `DATABASE_URL`) is the one piece of shared state
connecting the three otherwise-independent modes. Each row is owned by exactly one
identity — the anonymous per-browser `client_id`, or, once signed in, the account's
`user_id` — enforced by `ck_watchlist_exactly_one_owner` (`CHECK ((client_id IS NULL) <>
(user_id IS NULL))`) plus two separate `UNIQUE` constraints (`(client_id, symbol)` and
`(user_id, symbol)` — a single combined constraint wouldn't work, since Postgres treats
every row's `NULL` as distinct from every other `NULL`, so it wouldn't actually cap either
identity to one row per symbol).

1. `GET /api/watchlist?client_id=`, `POST /api/watchlist` (`{client_id, symbol, company, exchange}`,
   `client_id` optional), `DELETE /api/watchlist/{symbol}?client_id=` — all in `api.py`, using
   the same cached engine (`_get_db_engine()`) as the SME endpoints
2. **Identity resolution** (`api._resolve_watchlist_owner()`): a valid session (the
   `Authorization: Bearer <token>` header — see "Account & magic-link auth flow" below) always
   wins over `client_id` when both are present in a request, since the whole point of an
   account is that it doesn't depend on which browser sent the request. An expired/invalid
   token isn't a 401 here — this endpoint doesn't require being signed in, so it just falls
   through to the `client_id` path, same as no token at all. A request with neither a valid
   session nor a well-formed `client_id` gets 422.
3. **No migration on sign-in**: an anonymous `client_id`'s existing rows are never
   claimed/merged onto an account when a user signs in — a freshly-signed-in user simply
   starts seeing whatever rows their account already owns (possibly none), and their old
   anonymous rows remain reachable only by that same browser's `client_id` while logged out.
   This mirrors the same deliberate scope call `db/models.py`'s `users` table comment
   documents for the auth system as a whole.
4. `client_id` is a UUID generated client-side (`crypto.randomUUID()`) and persisted in
   `localStorage` — it groups one browser's anonymous rows, nothing more
5. `frontend/lib/watchlist.ts`'s `useWatchlist()` hook holds a module-level shared cache +
   subscriber list so every mounted `WatchlistButton` (stock analysis, Market Picks rows,
   SME Signals rows) reads/writes the same in-memory state without each firing its own
   fetch or needing React Context. It always sends `client_id` regardless of auth state — the
   backend transparently decides which identity actually owns the request, so the hook itself
   doesn't need to know. `refreshWatchlist()` clears that cache and re-fetches; it's called
   from `/auth/verify`'s success path and from `useAuth()`'s `logout()`, since neither a
   sign-in nor a sign-out otherwise gives the watchlist's independent module-level cache any
   signal that the caller's identity just changed.
6. The Next.js proxy routes (`app/api/watchlist/route.ts`, `app/api/watchlist/[symbol]/route.ts`)
   forward the session cookie as `Authorization: Bearer <token>` alongside the existing
   `client_id` passthrough — same pattern as the `/api/auth/*` proxy routes — so `api.py`
   never sees a cookie, only that header.
7. `/watchlist` fans out to `GET /api/prices` for live quotes on whatever's starred
8. Same defensive conventions as SME endpoints: 503 if `DATABASE_URL` unset/DB unreachable
   (sanitized — no raw exception text in the response), 422 on invalid `client_id`/`symbol`/
   missing identity, rate-limited via `_rate_limit()`, capped at 200 items per identity
   (`_MAX_WATCHLIST_ITEMS_PER_CLIENT`, same cap for both client_id- and user_id-owned rows)

### Watchlist alert emails

A standalone daily batch job, `watchlist_alerts.py` (repo root) — same standalone-script shape
as `sme_ema_pipeline.py` (PostgreSQL, a `run()`/`main()` split, `--force` CLI flag, a
`_MAX_ACCEPTABLE_ERROR_RATE`-style health gate so a bad run fails its GitHub Actions job loudly
instead of "succeeding" silently) — but wired to the existing single-stock analysis pipeline
(`main._fetch_task` + `signals.engine` + `crew.run_analysis_with_fallback`) instead of the SME
OHLCV fetch. Only **account-owned** (`user_id`) watchlist rows are ever considered — an
anonymous `client_id` row has no email to notify and is excluded at the query level.

1. `_get_watched_symbols()` runs one query joining `watchlist_items` to `users` (`WHERE
   user_id IS NOT NULL`) and groups the rows by symbol, since several users can watch the same
   stock and each should only trigger one re-analysis of it, not one per watcher.
2. `_analyze_symbol(symbol, run_id, force=False)` re-runs the same fetch → signal-engine →
   analyst flow `main.py`'s CLI path runs for one symbol — respecting the existing per-task
   cache TTLs (so a symbol some other visitor already refreshed today via the website isn't
   double-fetched or double-billed) — and calls `verdict_history.save_snapshot()` on every path,
   including the "everything was already fresh" cache-hit path, mirroring `main.py`'s own
   early-return branch so a day is never silently missing a snapshot just because nobody
   re-triggered the LLM that day. Any exception is caught and logged per-symbol (returns `None`)
   so one bad fetch can't sink the whole run, the same isolation convention
   `_consolidated_payload()` and `get_insider_activity()` use for their independent sub-fetches.
3. `_detect_change(symbol)` compares `verdict_history.load_history(symbol, limit=2)`'s two most
   recent rows — today's just-saved snapshot against the one immediately before it — and returns
   `{"symbol", "old_recommendation", "new_recommendation", "confidence"}` only when the
   recommendation actually differs and both rows exist (a symbol analysed for the first time
   today, like `VerdictTimeline`'s own 2-day minimum, has nothing to compare against yet).
4. This job runs the full paid LLM analyst call per distinct watched symbol, so an unbounded
   watchlist fan-in would mean an unbounded daily bill — the same cost-control instinct as
   `market_picks_pipeline.py`'s `_MAX_STOCKS`. `_MAX_ALERT_SYMBOLS` (50) caps how many distinct
   symbols one run analyses; symbols beyond the cap are skipped for that day (logged, not
   silently dropped — no-silent-caps convention) rather than letting the bound grow unbounded.
5. `email_sender.py` gained a second message builder/sender pair —
   `send_watchlist_alert_email(to_email, alerts)` — alongside the existing magic-link one; both
   now share one `_send_via_smtp()` helper (extracted, not duplicated) for the connect/STARTTLS/
   login/send sequence. One digest email per user per run lists every changed symbol, not one
   email per symbol, so a user watching several stocks that all moved the same day gets a single
   message. Same best-effort convention as `send_magic_link_email`: returns `True`/`False`,
   never raises, and a missing `SMTP_HOST` just means the email never arrives.
6. **Daily auto-run**: `.github/workflows/watchlist-alerts-cron.yml` runs at 13:30 UTC (19:00
   IST) on weekdays — after `sme-cron.yml` (13:00 UTC) so that pipeline's own writes have
   settled, and well after NSE's 15:30 IST close. Requires the same `DATABASE_URL` secret as
   `sme-cron.yml`, plus whichever LLM provider key and `SMTP_*` secrets the deployment already
   uses for the live site (the batch job is unattended, so it can't fall back to "no key
   configured" the way the interactive CLI does — `run()` returns `False` immediately if neither
   is set, failing the job loudly). `python watchlist_alerts.py --force` is available for a
   manual re-run that bypasses cache freshness entirely.

### Account & magic-link auth flow

Minimal, passwordless auth — no OAuth, no separate signup step. Additive on top of the
anonymous `client_id` identity above: `watchlist_items` rows an anonymous browser already
had stay exactly as they were and keyed by `client_id` — signing in doesn't claim or merge
them onto the account (a deliberate scope call — see the Tier 2 product-queue discussion
this shipped from, and "Watchlist flow" above for how a signed-in request's identity is
resolved). "I bought this" positions tracking has no backend at all yet (see the Market
Picks pipeline docs), so there's nothing for an account to link there.

1. **Request a link** — `POST /api/auth/request-link` (`{email}`), rate-limited both per-IP
   (5/15 min) and per-target-address (5/hour) — the address-keyed limit exists because an
   attacker with rotating IPs would otherwise get a fresh 5/15min budget per IP and could
   email-bomb one victim's inbox indefinitely. `auth.create_magic_link(email)` opportunistically
   prunes expired `magic_links`/`sessions` rows (same "delete stale entries on the next write"
   convention as `_prune_extract_cache()` — these tables only grow from auth traffic, so a
   request-link call is a natural trigger) before storing a single-use token (only its SHA-256
   hash is persisted — the raw token exists only in the outbound email and the process memory
   that generated it) with a 15-minute expiry, then `email_sender.send_magic_link_email()` emails
   a link pointing at `{FRONTEND_URL}/auth/verify?token=...`. The response is always
   `{"sent": true}` regardless of whether SMTP delivery actually succeeded (logged server-side
   as a warning) — this avoids leaking SMTP configuration state, and a link that failed to send
   once still works if the caller re-requests after SMTP is fixed.
2. **Verify** — the browser opens `/auth/verify?token=...` (a Next.js page, not the FastAPI
   endpoint directly — the cookie has to be set on the frontend's own origin), which shows a
   "Complete sign-in" button rather than firing the verify call automatically on page load —
   corporate email "safe link" pre-fetchers (Outlook Safe Links, Proofpoint, etc.) crawl links
   in emails before a human opens them, and an auto-firing `GET` would let the scanner consume
   the single-use token first and lock the real user out. Clicking the button calls
   `GET /api/auth/verify?token=`. `auth.verify_magic_link()` atomically consumes the token
   (`UPDATE ... WHERE used_at IS NULL AND expires_at > NOW() ... RETURNING`, so two
   concurrent clicks of the same link can't both win) and get-or-creates the `users` row for
   its email — there's no separate signup; the first successful link click *is* account
   creation. `auth.create_session()` then issues a session token (30-day expiry, same
   hash-only-storage convention as magic links) tied to that user. The response body never
   echoes the raw session token back to the caller past the one proxy hop that sets the
   cookie (see step 3) — only `{user}` reaches page-level JS, so an XSS on this origin can't
   read a live session token out of a fetch response.
3. **Cookie handoff** — `frontend/app/api/auth/verify/route.ts` is the one proxy route that
   isn't a pure passthrough: on a successful backend response it also sets the raw session
   token as an httpOnly, `SameSite=Lax` cookie (`alphapulse_session`) on the Next.js origin,
   since the browser only ever talks to that origin, never to FastAPI directly. Every other
   authenticated proxy route (`/api/auth/me`, `/api/auth/logout`, and any future
   account-gated endpoint) reads that cookie server-side (`lib/auth-cookie.ts`) and forwards
   it to the backend as `Authorization: Bearer <token>` — `api.py` never sees a cookie, only
   that header.
4. **Session state in the UI** — `frontend/lib/auth.ts`'s `useAuth()` hook holds a
   module-level shared cache + subscriber list (same pattern as `useWatchlist()`): every
   mounted `AuthWidget` (dropped into every page's nav bar next to `HeaderSearch`) reads/
   subscribes to one in-memory fetch of `GET /api/auth/me` instead of each firing its own.
   Shows a "Sign in" link when logged out, or the user's email with a sign-out dropdown when
   logged in. `refreshAuth()` re-fetches after `/auth/verify` succeeds so the nav updates
   without a full page reload.
5. **Logout** — `POST /api/auth/logout` best-effort deletes the session row
   (`auth.delete_session()`, swallow-and-log like `verdict_history.py`'s read path); the
   Next.js route clears the cookie regardless of whether that delete succeeded, so the
   browser is signed out either way.
6. `GET /api/auth/me` is the one endpoint every authenticated page implicitly depends on —
   401 (not 200 with a null user) when there's no session, so `useAuth()`'s `loading` state
   distinguishes "still checking" from "confirmed signed out."

### Programmatic API access flow

A signed-in user can mint long-lived API keys for scripts/integrations, separate from the
session-cookie identity the frontend itself uses. Three independent pieces:

1. **Key management** (session-authenticated, same identity as everything else under "Account
   & magic-link auth flow" above) — `POST /api/api-keys` (`{label?}`, 201, returns the row
   *including the raw key* — the only response that ever does, since `auth.create_api_key()`
   never persists it, only its SHA-256 hash, the same convention as `magic_links`/`sessions`),
   `GET /api/api-keys` (list metadata only — `key_prefix`, not the key or its hash), `DELETE
   /api/api-keys/{id}` (revoke; 404 if the id doesn't exist or isn't owned by the caller — never
   a 403, so the endpoint doesn't confirm/deny another user's key IDs exist). A key has no fixed
   TTL, unlike a session — it's valid until explicitly revoked, since a script can't "re-sign-in"
   through a magic link the way a browser redirects through one. `frontend/app/api-keys/page.tsx`
   (linked from `AuthWidget`'s dropdown) is the management UI: the create form shows the raw key
   exactly once, in a copy-to-clipboard box, with an explicit "won't be shown again" warning;
   the list table shows every key including revoked ones (badged), never re-displaying the secret.
2. **The gated surface itself** — `GET /api/v1/consolidated/{symbol}`, deliberately the *only*
   `/api/v1/*` route today: a thin auth/rate-limit wrapper around the exact same
   `_consolidated_payload()` helper `GET /api/consolidated/{symbol}` already calls (extracted out
   of that handler specifically so the two paths can't drift), so "what does AlphaPulse think
   about X" is available to external callers with zero duplicated aggregation logic. Auth here is
   a raw key in the `X-API-Key` header — deliberately **not** `Authorization: Bearer`, which is
   reserved for the internal session-token convention above; reusing that header would let a
   forwarded session token accidentally satisfy this check. `_require_api_key_user()` validates
   the key via `auth.get_user_for_api_key()` (which also opportunistically stamps
   `last_used_at`) and applies a per-*user*, **tier-scaled** rate limit (`api_v1:{user_id}`, a
   sliding one-hour window) rather than per-IP like the internal endpoints — a legitimate
   integration may run from a shared or rotating IP, so IP-keying would be the wrong bucket here.
   More `/api/v1/*` routes can follow the same wrapper-around-an-existing-handler pattern later;
   this PR intentionally ships one real endpoint rather than a speculative surface no caller has
   asked for yet.
3. **Tiers + usage dashboard** — `users.tier` (`'free'` | `'pro'`, `db/models.py`) gates
   `api._TIER_LIMITS` (`{"free": 100, "pro": 1000}` calls/hour). **No real payment processing
   exists** — there is no signup/checkout flow that ever sets a row to `'pro'`; every account is
   `'free'` until an operator updates the column by hand (`server_default 'free'` on the column
   itself, so this is a safe no-op for every pre-existing row, not a breaking migration). An
   unrecognized tier value falls back to `'free'` (`_DEFAULT_TIER`) rather than trusting a stored
   value blindly. `rate_limiter.get_usage_count(key, window_seconds)` is a **non-mutating peek**
   at the same sliding-window state `is_allowed()` already maintains (`ZCARD` after
   `ZREMRANGEBYSCORE`, or the in-memory equivalent) — checking current usage never itself counts
   as a call against the limit it's reporting on, unlike `is_allowed()`. `GET /api/api-keys`
   (already session-authenticated for key management) now also returns `tier` and
   `usage: {calls, limit, window_seconds}` in the same response — a usage dashboard alongside key
   management, not a separate endpoint, since a user managing their keys is exactly who wants to
   see this. `frontend/app/api-keys/page.tsx` renders it as a tier badge + a progress bar (red
   past the limit) above the key-creation form.

### Consolidated view flow

`GET /api/consolidated/{symbol}` answers "what does AlphaPulse think about X" without
visiting three pages. It is pure read-aggregation — no LLM calls, no scraping, no SME
pipeline run:

1. **Analysis** — `cache.load(symbol, "analysis")`, the same 24 h cache the stock analysis
   flow writes to. `None` if never analyzed for this symbol, or the cache has gone stale.
2. **Market pick** — `_load_picks_cache()` (the same 6 h `output/_market_picks/picks.json`
   cache market picks serves from), matched by symbol. `None` if the symbol isn't on the
   current picks list, or the cache itself is stale/missing.
3. **SME regime** — one indexed query against `ema_signals`/`sme_stocks` for the latest
   stored row, via the same cached engine (`_get_db_engine()`) as the SME and watchlist
   endpoints. `None` if `DATABASE_URL` is unset, the symbol isn't an SME/Emerge stock, or
   the query fails — a DB hiccup on this section must not fail the other two, so it's
   caught and logged rather than raising.

The three lookups run concurrently via `asyncio.gather` over `run_in_executor`. The
frontend's `HeaderSearch` component (embedded in every page's nav bar) opens
`ConsolidatedCard` on submit, which fetches this endpoint and renders each section
independently — a `null` section shows "not yet analyzed" / "not on the picks list" /
"no SME data" rather than an error, since that's the expected common case.

### Compare flow (`/compare?symbols=TCS,INFY`)

Two full stock analysis reports side by side. No new backend — each column runs the exact
same `GET /api/analyse/{symbol}` SSE pipeline the home page uses, via a shared
`useStockAnalysis()` hook (`frontend/lib/useStockAnalysis.ts`, extracted from the home page
so both call sites stay in sync) — one independent `EventSource` per symbol, so the two
columns fetch/progress/error independently of each other.

Capped at 2 symbols: `ResultsDashboard`'s internal grid breakpoints (`lg:`, `md:`, `sm:`)
are viewport-relative, not container-relative (no container-query plugin installed), so a
column narrower than the component's own breakpoint would render its internal two-block
layout compressed rather than actually reflowing. `/compare`'s own column layout only
switches from stacked to side-by-side at `2xl:` (1536px) specifically so that by the time
two columns sit side by side, each is wide enough for `ResultsDashboard`'s own layout to
still look right — below that, the two reports stack full-width instead of squeezing.

### Verdict history flow

"How does today's call compare to a past one for this stock?" was previously unanswerable
in the web app — the CLI wrote a dated `report_<date>.json` per run (`main.py`), but that
file never left disk, and `api.py`'s SSE endpoint didn't write anything comparable at all.

1. `verdict_history.py` (repo root, alongside `cache.py`) is a small persistence module with
   two functions: `save_snapshot(symbol, analysis, signal_context, stock_info)` upserts one
   row per `(symbol, verdict_date)` into the `verdict_history` Postgres table (recommendation,
   confidence, current_price, signal_score); `load_history(symbol, limit=60)` reads them back
   oldest-first. Both are best-effort — a missing `DATABASE_URL` or a DB hiccup is logged and
   swallowed, never raised, the same convention `signals/store.py` uses for its own audit trail.
2. `save_snapshot()` is called from **both** entry points that produce a report — `main.py`'s
   CLI pipeline (all three exit paths: cache-hit early return and the normal run) and `api.py`'s
   `/api/analyse/{symbol}` SSE stream, right after `_build_report()` — so the timeline reflects
   web usage and CLI usage identically, the same lockstep `main._build_report()` already
   enforces between the two entry points. A same-day re-run (cache hit, force refresh) upserts
   the existing row instead of adding a duplicate, so "one row per day" holds regardless of how
   many times the pipeline actually ran that day.
3. `GET /api/verdict-history/{symbol}` is pure read-aggregation over `load_history()` — no LLM
   calls, no scraping. Degrades to `{"symbol": ..., "history": [], "win_rate": null,
   "scored_count": 0}` (200, not 503) when `DATABASE_URL` is unset or the query fails, matching
   `/api/consolidated`'s "a missing section isn't an error" philosophy, since this is a
   supplementary strip on top of a report that has already loaded successfully — a DB hiccup
   here must not look like the whole analysis failed.
4. **Outcome scoring** — the single-stock analogue of the win-rate `GET /api/market-picks/history`
   already tracks for the weekly picks list: each stored verdict is additionally scored against
   *today's* live price (one extra `yfinance` call via `_fetch_live_price_sync()`, the same
   helper `GET /api/prices` uses, extracted out so both endpoints share it). `_score_verdict_history()`
   computes `return_since_pct` (an observed fact, populated whenever both the stored and live
   price are known) and `outcome` (`'win' | 'loss' | null`) per entry — but only for `BUY`/`SELL`
   calls; a `HOLD` makes no directional claim, so grading it against a price move would be
   inventing a judgment the verdict itself never made, the same "never invent" instinct applied
   to a derived field instead of a scraped one. A live-price fetch failure (including this
   sandbox's lack of outbound internet) degrades `return_since_pct`/`outcome` to `null` on every
   entry rather than failing the whole response. The response also carries a per-symbol
   `win_rate` (% of scored BUY/SELL entries that were a win) and `scored_count`.
5. `ResultsDashboard`'s hero renders a `VerdictTimeline` strip (fetched independently, same
   pattern as `PriceSparkline`/`usePeerComparison`) showing each stored day as a small
   recommendation badge with its date and a ✓/✗ win/loss mark (green/red, independent of the
   badge's own BUY/HOLD/SELL color), chained left-to-right, latest one ring-highlighted, with a
   "`X`% right so far (`N` scored)" summary next to the strip's label when at least one entry has
   been scored. Needs at least 2 stored days to render at all — a symbol analysed for the first
   time today has nothing to compare against yet.

### PWA installability

The frontend is installable (Chrome "Add to Home Screen" / desktop install prompt) and previously-
visited pages/static assets keep working offline. No new npm dependency — everything is built on
Next.js App Router's own metadata file conventions plus `next/og`'s `ImageResponse` (already bundled
with `next`, normally used for Open Graph images):

1. `frontend/app/manifest.ts` — the App Router manifest file convention; Next.js serves it at
   `/manifest.webmanifest` and auto-injects the `<link rel="manifest">` tag, no manual wiring needed.
2. Icons are generated at request time via `ImageResponse` (JSX → PNG), not static files, so there
   was no need to hand-produce or check in binary image assets:
   - `frontend/app/icon.tsx` (32×32) and `frontend/app/apple-icon.tsx` (180×180) are Next's own
     favicon/apple-touch-icon file conventions — Next auto-generates the `<link>` tags for both.
   - `frontend/app/manifest-icons/[size]/route.tsx` is a plain Route Handler (not a Next metadata
     convention file — those only support one fixed size each) serving the 192×192 and 512×512 PNGs
     `manifest.ts`'s `icons` array points at; any other `size` param 404s.
   - All three render the same navy-background/blue-"AP" mark inline via `ImageResponse`'s
     satori-backed CSS subset (flexbox required explicitly) — no external image tooling.
3. `frontend/public/sw.js` is a minimal hand-written service worker (no Workbox/next-pwa) registered
   from `frontend/components/service-worker-registration.tsx` (mounted once in `app/layout.tsx`,
   renders nothing, and **only in production** — registering in `next dev` would install a real,
   persisted service worker in every engineer's dev browser profile that then keeps intercepting
   static assets across future dev sessions): cache-first for same-origin static assets,
   network-first-with-cache-fallback for navigations (so a previously-visited page still loads
   offline, falling back to a plain "You are offline." response if nothing at all is cached yet), and
   **`/api/*` is never intercepted** — this is a live stock-data tool, and serving a cached quote/
   verdict while offline would be actively misleading rather than a helpful fallback, unlike a typical
   content-site PWA. Navigations are also never cached when the URL carries a query string — the app
   has at least one route (`/auth/verify?token=...`) where the query string IS a sensitive, single-use
   credential, and the Cache API keys entries by full URL, so caching it would persist that secret in
   Cache Storage indefinitely; skipping *every* query string (not just that one route) is the safe
   default for a general-purpose service worker that shouldn't need route-specific knowledge of which
   params are sensitive. Only successful (`response.ok`) responses are ever cached, on both the
   navigation and static-asset paths, so a transient 5xx never gets served as the offline fallback.
4. `app/layout.tsx` also exports `viewport.themeColor` (`#0b1120`, matching `bg` in
   `tailwind.config.ts`) and `metadata.appleWebApp` for the iOS status-bar/home-screen title.

### Shared state and queues

- **No shared in-memory state** between requests. Each request runs its own pipeline instance.
- **Inter-phase communication** within the market picks pipeline uses direct function return values (not queues). The `asyncio.Queue` is only used to bridge the blocking thread back to the async SSE loop.
- **Cache** (`output/`) is the persistent shared state for stock analysis and market picks; concurrent writes to different symbols are safe (each symbol has its own subdirectory) — the one exception is the `"_MACRO"` pseudo-symbol (see "Macro overlay signal" above), where several `market_picks_pipeline.py` worker threads researching different real stocks can all miss the same `fii_dii_flow`/`macro_context` cache entry at once and race to fill it; `cache.save()` writes atomically (tempfile + `os.replace`) so a race there produces at most a few redundant NSE/RBI fetches, never a corrupt cache file. SME signals persist to PostgreSQL instead (idempotent upserts keyed on symbol + trade_date).

### SSE bridge pattern (critical)

```python
async def _launch():
    await loop.run_in_executor(None, blocking_fn)

asyncio.create_task(_launch())   # create_task needs a coroutine, not a Future
```

Never pass `loop.run_in_executor(...)` directly to `create_task` — it returns a `Future`, not a coroutine, and will raise `TypeError` at runtime.

---

## Important Rules for Claude

- **Schema boundary is sacred.** Raw tool output must be normalized through `schemas.normalize()` before being passed to cache, guardrails, signal engine, or analyst prompt. If a tool changes its output shape, only `schemas.py` needs updating.
- **Never add fields to the analyst JSON output schema** without also updating `config/analyst.json` (`output_schema`), `crew._validate_analysis_payload()`, `main._build_report()`, and `frontend/types/index.ts` (`Analysis` interface). These four are in lockstep.
- **Tools must not raise.** All functions in `tools/` must return `{"error": "...", ...}` on failure. The cache layer silently discards error payloads; guardrails detect them and trigger retries.
- **Run `npx tsc --noEmit` in `frontend/`** before marking any frontend task done. This does NOT catch everything — a CSS syntax error, for example, only surfaces under the production minifier (`npm run build`), not `tsc` or `next dev`. CI runs both; when in doubt, especially after touching `globals.css` or raw CSS, run `npm run build` locally too.
- **Cache TTLs are intentional.** `stock_info` and `news` are 1 h; `research` is 24 h; `shareholding`/`mf_holdings` are 168 h (7 days). Do not shorten these without understanding the NSE rate-limit implications.
- **The analyst step is expensive.** It only re-runs when at least one input task was stale. Do not add logic that forces it to re-run unconditionally.
- **Market picks pipeline max stocks = 35** (`_MAX_STOCKS` in `market_picks_pipeline.py`). Raising this significantly increases wall-clock time and LLM costs.
- **4-tier recommendation in market picks**: BUY / WATCHLIST / HOLD / SELL. Do not collapse these to 3-tier. `WATCHLIST` is a distinct lower-conviction tier between BUY and HOLD.
- **Trade levels are deterministic in market picks** (entry/target/stop computed from signal score and 52w range). Do not add LLM-driven price generation — it produces null values when context overflows.
- **Extraction cache** (`output/_extract_cache/`) avoids re-calling the LLM for the same source articles within 6 h. The cache key is content-aware (title + URL + summary hash), so edits or new articles get a fresh key automatically. Expired files aren't just ignored on read — `_prune_extract_cache()` deletes them once per pipeline run, or this directory grows by one file per (source, article-batch) forever.
- **`signals_data/<SYMBOL>/<date>.json`** (written by `signals/store.save_signal`) is a write-only audit trail — nothing reads it back. Pruned to a 90-day retention window per symbol on every write (`signals/store._prune_old_signals`).
- **Source credibility weights** in `_SOURCE_CREDIBILITY` determine how much each source contributes to confidence scoring. Adding a new source requires adding a credibility entry; missing sources default to 0.50.
- **HDFC Securities sources** live in `tools/hdfc_sec_agent.py` and are merged into `SOURCES` / `SCRAPER_FNS` at import time in `tools/market_picks_tools.py`. Adding a new brokerage source follows the same pattern: define scrapers in a separate module, export `*_SOURCES` and `*_SCRAPERS`, merge in `market_picks_tools.py`.
- **Rate limiting** is a sliding window (`api.py`'s `_rate_limit()` → `rate_limiter.is_allowed()`), applied only to expensive/abusable routes: `/api/analyse/{symbol}` (20 req / 5 min per IP), `/api/market-picks?force=true` (3 req / hour per IP), `/api/sme-signals/refresh` (3 req / hour per IP, on top of the existing single-run guard). Backed by Redis (shared across workers) when `REDIS_URL` is set, an in-memory per-process counter otherwise — see "Shared-state rate limiting" below. The "per IP" is `api.py::_client_ip()`, not raw `request.client.host` — see "Trusted client IP for per-IP rate limiting" below for why that distinction matters given every request arrives via the Next.js proxy routes.
- **`output/_history/<date>.json` snapshot schema** (`symbol`, `confidence`, `effective_signal`, `mention_count`, `current_price`, `recommendation`) is read by two independent consumers: the in-pipeline `_load_trend()` (confidence trend) and `GET /api/market-picks/history` (price track record, `/market-picks/history` page). Snapshots written before `current_price`/`recommendation` were added won't have them — the history endpoint handles this by returning `change_pct: null` rather than guessing. Keep both consumers in mind if the snapshot shape changes.
- **`GET /api/market-picks/history`** also computes an overall `win_rate` (share of tracked picks with `change_pct > 0`), a `tier_stats` breakdown keyed by `recommendation_then` (count/avg change/win rate per BUY/WATCHLIST/HOLD/SELL), and per-symbol `nifty_change_pct`/`alpha_pct` benchmarked against `^NSEI` over the same `first_seen` → `last_seen` window (`avg_alpha_pct` at the top level). The Nifty series is fetched once per request-range via `yfinance.Ticker("^NSEI").history()` — not once per snapshot date — and cached through `cache.py` using `"NSEI"` as a pseudo-symbol (`index_history`, 24 h TTL, re-fetched whenever a new snapshot date widens the needed range). A closed-market snapshot date (weekend/holiday) falls back to the nearest earlier trading day's close, never a later one. A yfinance outage degrades to `null` alpha fields, not a failed request.
- **CORS** is restricted via `CORSMiddleware` to origins in `ALLOWED_ORIGINS` (comma-separated env var, defaults to `http://localhost:3000`). This is defense in depth, not something normal operation relies on — the Next.js proxy routes talk to the backend server-to-server, which CORS doesn't apply to. Add your production frontend's origin to `ALLOWED_ORIGINS` before deploying, or direct browser calls to the backend will be rejected.
