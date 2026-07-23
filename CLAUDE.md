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

A **Watchlist** ties the three modes together: a star button in each dashboard adds/removes a stock from a PostgreSQL-backed `watchlist_items` table, and `/watchlist` lists everything starred with live prices. There's no login yet — rows are keyed by an anonymous per-browser `client_id` (a UUID in `localStorage`), not a real account, so a watchlist doesn't follow you across devices.

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
│   ├── components/               Dashboard, search, progress tracker, market picks dashboard
│   ├── app/api/                  Thin Next.js proxy routes → FastAPI backend
│   ├── lib/watchlist.ts          useWatchlist() hook (DB-backed via /api/watchlist, anonymous client_id)
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
| `_phase_score` | Deterministic confidence scoring (`_compute_confidence`: 50% signal engine + 30% consensus + 20% recency, 0–100). The 4-tier rec (BUY / WATCHLIST / HOLD / SELL) is a *separate* formula on top — `combined_dir = 0.55 × consensus + 0.45 × signal_score`, thresholded, with a quant-veto that demotes BUY → WATCHLIST on a strongly negative signal score. Entry/target/stop-loss computed from price and signal score — no LLM. Sector-balanced: max 2 stocks per sector in the primary list. Saves a daily snapshot to `output/_history/` for trend tracking. |

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
| `DATABASE_URL` | unset | PostgreSQL DSN — required for the SME signals pipeline (`/api/sme-signals`) and for the watchlist (`/api/watchlist`) |

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

### Symbol validation flow (`GET /api/validate/{symbol}`)

Handles three input forms:
1. **ISIN** (e.g. `INE009A01021`) — resolved via NSE equity master CSV first, then yfinance as fallback
2. **BSE-forced** (exchange query param = `BSE`) — resolves Screener.in slug → proper ticker via `_screener_company_page_sync`
3. **Ticker / name** — NSE autocomplete + BSE autocomplete (via Screener) run in parallel; BSE ISIN lookup enriches the NSE result; Screener.in fallback if both miss

### Market picks flow

1. Browser opens `EventSource` → `GET /api/market-picks` (optional `?force=true` bypasses cache)
2. `api.py` checks `output/_market_picks/picks.json` (6 h TTL); serves cached `done` event immediately if fresh
3. On cache miss: wraps `MarketPicksPipeline.run()` in `run_in_executor`; bridges events via `asyncio.Queue`
4. Pipeline calls `on_event(payload)` → `loop.call_soon_threadsafe(q.put_nowait, payload)` → SSE stream
5. The six pipeline phases run synchronously inside the executor thread; final result saved to cache

### SME golden cross flow

`sme_ema_pipeline.py` is a standalone batch job (PostgreSQL, `DATABASE_URL` env var):

1. Fetches all NSE Emerge + BSE SME stocks (`tools/sme_tools.py`, 24 h list cache)
2. Downloads 1 year of daily OHLCV per stock via yfinance
3. Computes EMA 20/50 over the full year; flags **golden crosses** (EMA20 crosses above
   EMA50) and **death crosses** (crosses below); stores only the last ~3 months of rows
4. `GET /api/sme-signals` serves cross events + current regime (`ema20 > ema50` on the
   latest row); `POST /api/sme-signals/refresh` runs the pipeline in the background
   (409 if already running; `refreshing` flag in the GET response)

CLI: `--setup-db` (create tables), `--reset-db` (drop + recreate — required after schema
changes; data is fully regenerable), `--force` (bypass list cache), `--lookback N`.

The DB column for the cross is named `cross_type` (`'golden'`/`'death'`/`NULL`) because
`CROSS` is a reserved SQL keyword; the API/TS field is `cross`.

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
connecting the three otherwise-independent modes:

1. `GET /api/watchlist?client_id=`, `POST /api/watchlist` (`{client_id, symbol, company, exchange}`),
   `DELETE /api/watchlist/{symbol}?client_id=` — all in `api.py`, using the same cached
   engine (`_get_db_engine()`) as the SME endpoints
2. No accounts: `client_id` is a UUID generated client-side (`crypto.randomUUID()`) and
   persisted in `localStorage` — it groups one browser's rows, nothing more
3. `frontend/lib/watchlist.ts`'s `useWatchlist()` hook holds a module-level shared cache +
   subscriber list so every mounted `WatchlistButton` (stock analysis, Market Picks rows,
   SME Signals rows) reads/writes the same in-memory state without each firing its own
   fetch or needing React Context
4. `/watchlist` fans out to `GET /api/prices` for live quotes on whatever's starred
5. Same defensive conventions as SME endpoints: 503 if `DATABASE_URL` unset/DB unreachable
   (sanitized — no raw exception text in the response), 422 on invalid `client_id`/`symbol`,
   rate-limited via `_rate_limit()`, capped at 200 items per `client_id`

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
- **CORS** is restricted via `CORSMiddleware` to origins in `ALLOWED_ORIGINS` (comma-separated env var, defaults to `http://localhost:3000`). This is defense in depth, not something normal operation relies on — the Next.js proxy routes talk to the backend server-to-server, which CORS doesn't apply to. Add your production frontend's origin to `ALLOWED_ORIGINS` before deploying, or direct browser calls to the backend will be rejected.
