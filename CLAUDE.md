# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

A full-stack Indian equity research platform. Given an NSE/BSE ticker (e.g. `TCS`, `RELIANCE`), it:

1. Validates the symbol across NSE autocomplete, BSE, and Screener.in
2. Fetches six data slices in parallel (price, fundamentals, news, shareholding, MF holdings, filings)
3. Runs a quantitative signal engine (valuation + growth + volume + filings signals)
4. Calls an LLM analyst to produce a structured `BUY`/`HOLD`/`SELL` recommendation
5. Streams progress and the final report to the browser via Server-Sent Events

A second mode — **Market Picks** — runs a multi-agent pipeline that scrapes 20 Indian and global financial sources, extracts stock recommendations with an LLM, validates symbols against the NSE equity master, runs due diligence on each, and returns a confidence-ranked watchlist with BUY / WATCHLIST / HOLD / SELL ratings.

A third mode — **SME Signals** — is a PostgreSQL-backed batch pipeline (`sme_ema_pipeline.py`) that screens all NSE Emerge + BSE SME stocks for EMA20/EMA50 **golden cross** and **death cross** events, served at `/sme-signals` via `GET /api/sme-signals`.

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
├── verdict_history.py      Daily verdict/price snapshots (PostgreSQL) — powers the hero's timeline strip
├── auth.py                 Magic-link auth: token/session issuance + validation (PostgreSQL)
├── email_sender.py         Sends the magic-link sign-in + watchlist-alert emails over generic SMTP
├── watchlist_alerts.py     Daily batch job: emails signed-in users on a watched stock's recommendation change
├── db/                     SQLAlchemy Core tables (models.py) + schema.sql reference
├── observability.py        Structured JSON logging via log_event()
├── requirements.txt
├── .env.example
├── config/
│   ├── analyst.json        Analyst role/goal/backstory + section labels (config.crew_tasks.ANALYST_SECTIONS)
│   └── crew_tasks.py       Builds the analyst prompt string from analyst.json
├── tools/
│   ├── market_picks_tools.py  RSS + GNews scrapers for 14 sources; exports SOURCES + SCRAPER_FNS
│   │                          (merges in hdfc_sec_agent.py + 4 others below → 20 sources total)
│   ├── sme_tools.py           NSE Emerge + BSE SME stock-list fetchers
│   ├── hdfc_sec_agent.py      HDFC Securities Fundamental + Technical scrapers (GNews-based)
│   └── ...                    Other data-fetching functions (yfinance, Screener.in, gnews, NSE API)
├── signals/                Quantitative signal engine (features → signal scores → verdict)
├── tests/                  unittest-based tests (no pytest plugins needed)
├── frontend/               Next.js 15 app (TypeScript, Tailwind CSS)
│   ├── app/page.tsx              Stock analysis page (supports ?symbol= deep links)
│   ├── app/market-picks/page.tsx Weekly picks page
│   ├── app/sme-signals/page.tsx  SME golden cross screener
│   ├── app/watchlist/page.tsx    Cross-mode watchlist page
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

`research` also carries a `quarterly_trend` (Sales/EPS mini-trend, oldest-first, from Screener's Quarterly Results table — the same company page `get_fundamentals` already fetches, so it's free) and `shareholding` carries `pledge_pct` (promoter pledge %, parsed from the same shareholding table `get_holdings` already fetches, as its own field rather than folded into `shareholding_pattern`). Both are absent/empty rather than guessed when Screener doesn't have a clean, fully-numeric window for them (e.g. a recent IPO with fewer quarters on record) — same "never invent" convention as everywhere else in this pipeline. `results-dashboard.tsx` renders them as a "Quarterly Trend" card (two `Sparkline`s) and a "Promoter Pledge" line atop the Shareholding Pattern card (warning-styled when > 0%).

These tool functions are decorated with `@tool` from `crewai.tools` purely for a consistent `.run(**kwargs)` calling convention (see `main._fetch_task`) — that's the only thing this codebase still uses CrewAI for. There used to be a second, parallel orchestration path (`build_crew()` in `crew.py`, wiring per-task `Agent`/`Task`/`Crew` objects from `config/agents.json` + `config/tasks.json`) but it had zero callers and zero test coverage — data collection has always gone through `_fetch_task()` in production — so it was removed rather than left as unverified dead code. If you're looking for `LLM_MODEL` / the "data-agent tier" model config from an older version of this doc: it only ever fed that removed path and has been dropped too — `ANALYST_MODEL` (below) is the only model-selection env var that does anything.

**Analyst (direct LLM call)**: `run_analysis_with_fallback()` in `crew.py` calls `litellm.completion` directly — no CrewAI involved. It receives all six data slices plus signal engine context, and must return a specific JSON schema defined in `config/analyst.json`. Guardrails in `_validate_analysis_payload()` enforce structural rules and grounded-claims checks; a guardrail failure triggers one corrective LLM retry with the validation error appended, and only if that also fails does it return a safe HOLD fallback via `_safe_analysis_fallback()`.

**Market picks pipeline** (`market_picks_pipeline.py`): Six sequential phases, all blocking work offloaded to `ThreadPoolExecutor`. Communicates back to the SSE stream via `on_event` callbacks bridged through `asyncio.Queue` with `loop.call_soon_threadsafe`.

| Phase | What it does |
|---|---|
| `_phase_scrape` | Parallel fetch from 20 sources (5 RSS + 12 GNews + 3 structured). 6 workers. |
| `_phase_extract` | One LLM call per source (parallel, up to 6 workers). Checks extraction cache first. Detects syndicated articles (Jaccard ≥ 0.60) across sources to down-weight them. |
| `_phase_consolidate` | Groups picks by ticker, validates against NSE equity master, confirms live price via yfinance (guards pre-IPO / unlisted names). Uses rapidfuzz for fuzzy company-name matching. |
| `_phase_research` | Fetches `stock_info` + `research` + signal engine per stock (4 workers, up to `_MAX_STOCKS` stocks). |
| `_phase_analyze` | Batched LLM calls (8 stocks/batch, parallel) for qualitative summary + bull/bear factors. Does NOT ask the LLM for prices. |
| `_phase_score` | Deterministic confidence scoring (`_compute_confidence`: 50% signal engine + 30% consensus + 20% recency, 0–100). The 4-tier rec (BUY / WATCHLIST / HOLD / SELL) is a *separate* formula on top — `combined_dir = 0.55 × consensus + 0.45 × signal_score`, thresholded, with a quant-veto that demotes BUY → WATCHLIST on a strongly negative signal score. Entry/target/stop-loss computed from price and signal score — no LLM. Sector-balanced (`_apply_sector_balance()`): max 2 stocks per sector promoted to the primary list, excess deferred to the end — `sector` stays on every pick in the response (real, filterable data, not popped like the old internal-only `_sector`). Saves a daily snapshot to `output/_history/` for trend tracking. |

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

### Type-checking (no test suite or lint config exists)

```bash
cd frontend && npx tsc --noEmit
```

There is no ESLint config and no frontend test suite. TypeScript strict mode (`"strict": true`) is the primary code quality gate.

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

### Frontend

| Variable | Default | Purpose |
|---|---|---|
| `API_URL` | `http://localhost:8000` | FastAPI backend URL (set in Next.js env) |

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
session-cookie identity the frontend itself uses. Two independent pieces:

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
   `last_used_at`) and applies a per-*user* rate limit (`api_v1:{user_id}`, 100/hour) rather than
   per-IP like the internal endpoints — a legitimate integration may run from a shared or
   rotating IP, so IP-keying would be the wrong bucket here. More `/api/v1/*` routes can follow
   the same wrapper-around-an-existing-handler pattern later; this PR intentionally ships one
   real endpoint rather than a speculative surface no caller has asked for yet.

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
- **Cache** (`output/`) is the persistent shared state for stock analysis and market picks; concurrent writes to different symbols are safe (each symbol has its own subdirectory). SME signals persist to PostgreSQL instead (idempotent upserts keyed on symbol + trade_date).

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
- **Rate limiting** is a single-process, in-memory sliding window (`api.py`'s `_rate_limit()`), applied only to expensive/abusable routes: `/api/analyse/{symbol}` (20 req / 5 min per IP), `/api/market-picks?force=true` (3 req / hour per IP), `/api/sme-signals/refresh` (3 req / hour per IP, on top of the existing single-run guard). It does not survive a multi-worker deployment — that would need a shared store (e.g. Redis) instead.
- **`output/_history/<date>.json` snapshot schema** (`symbol`, `confidence`, `effective_signal`, `mention_count`, `current_price`, `recommendation`) is read by two independent consumers: the in-pipeline `_load_trend()` (confidence trend) and `GET /api/market-picks/history` (price track record, `/market-picks/history` page). Snapshots written before `current_price`/`recommendation` were added won't have them — the history endpoint handles this by returning `change_pct: null` rather than guessing. Keep both consumers in mind if the snapshot shape changes.
- **`GET /api/market-picks/history`** also computes an overall `win_rate` (share of tracked picks with `change_pct > 0`), a `tier_stats` breakdown keyed by `recommendation_then` (count/avg change/win rate per BUY/WATCHLIST/HOLD/SELL), and per-symbol `nifty_change_pct`/`alpha_pct` benchmarked against `^NSEI` over the same `first_seen` → `last_seen` window (`avg_alpha_pct` at the top level). The Nifty series is fetched once per request-range via `yfinance.Ticker("^NSEI").history()` — not once per snapshot date — and cached through `cache.py` using `"NSEI"` as a pseudo-symbol (`index_history`, 24 h TTL, re-fetched whenever a new snapshot date widens the needed range). A closed-market snapshot date (weekend/holiday) falls back to the nearest earlier trading day's close, never a later one. A yfinance outage degrades to `null` alpha fields, not a failed request.
- **CORS** is restricted via `CORSMiddleware` to origins in `ALLOWED_ORIGINS` (comma-separated env var, defaults to `http://localhost:3000`). This is defense in depth, not something normal operation relies on — the Next.js proxy routes talk to the backend server-to-server, which CORS doesn't apply to. Add your production frontend's origin to `ALLOWED_ORIGINS` before deploying, or direct browser calls to the backend will be rejected.
