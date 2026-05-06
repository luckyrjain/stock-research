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

A second mode — **Market Picks** — runs a multi-agent pipeline that scrapes 13 Indian and global financial sources, extracts stock recommendations with an LLM, validates symbols against the NSE equity master, runs due diligence on each, and returns a confidence-ranked watchlist with BUY / WATCHLIST / HOLD / SELL ratings.

---

## Repo Structure

```
stock-research/
├── api.py                  FastAPI server — SSE endpoints and symbol validation
├── main.py                 CLI entry point; also contains _fetch_task, _build_report (shared with api.py)
├── crew.py                 LLM resolution, analyst guardrails, run_analysis_with_fallback, build_crew
├── cache.py                File-based TTL cache (output/<SYMBOL>/<task>.json)
├── schemas.py              Normalization contracts: raw tool output → canonical dicts
├── market_picks_pipeline.py  Multi-agent weekly picks pipeline (6 phases)
├── observability.py        Structured JSON logging via log_event()
├── requirements.txt
├── .env.example
├── config/
│   ├── agents.json         Agent roles, tools, backstories (data-fetching agents only)
│   ├── tasks.json          Task descriptions and expected outputs
│   ├── analyst.json        Analyst prompt template, output schema, guardrail instructions
│   ├── crew_agents.py      Thin loader: wires agents.json → CrewAI Agent objects
│   └── crew_tasks.py       Thin loader: builds task specs and analyst prompt string
├── tools/
│   ├── market_picks_tools.py  RSS + GNews scrapers for 11 sources; exports SOURCES + SCRAPER_FNS
│   ├── hdfc_sec_agent.py      HDFC Securities Fundamental + Technical scrapers (GNews-based)
│   └── ...                    Other data-fetching functions (yfinance, Screener.in, gnews, NSE API)
├── signals/                Quantitative signal engine (features → signal scores → verdict)
├── tests/                  unittest-based tests (no pytest plugins needed)
├── frontend/               Next.js 15 app (TypeScript, Tailwind CSS)
│   ├── app/page.tsx              Stock analysis page
│   ├── app/market-picks/page.tsx Weekly picks page
│   ├── components/               Dashboard, search, progress tracker, market picks dashboard
│   ├── app/api/                  Thin Next.js proxy routes → FastAPI backend
│   └── types/index.ts            Canonical TS types for all SSE messages and reports
└── output/                 Cache files (gitignored); also where CLI saves report JSON
    ├── <SYMBOL>/           Per-symbol task caches
    ├── _extract_cache/     LLM extraction cache (6 h TTL) — avoids re-calling LLM on re-runs
    ├── _history/           Daily pick snapshots for trend tracking (YYYY-MM-DD.json)
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
| `crewai` | Agent/Task/Crew abstractions for data-fetching agents |
| `litellm` | Provider-agnostic LLM calls (analyst step) |
| `yfinance` | NSE/BSE price quotes; also used for ISIN → symbol resolution |
| `requests` + `beautifulsoup4` | Screener.in scraping, NSE API calls |
| `gnews` + `feedparser` | News articles from Google News RSS; RSS feeds for 5 financial news sources |
| `rapidfuzz` | Fuzzy company-name matching in market picks consolidation phase |
| `python-dotenv` | `.env` loading |

### Agent architecture

The system has **two distinct agent layers**:

**Layer 1 — Data agents (CrewAI)**: Used in `build_crew()` in `crew.py`. Five agents, each wraps exactly one tool and returns its raw JSON output. In production the API and CLI call `_fetch_task()` directly (bypasses CrewAI) using `ThreadPoolExecutor` for parallel fetching. CrewAI is only exercised if `build_crew()` is called directly.

| Task name | Agent role | Tool | Data source |
|---|---|---|---|
| `stock_info` | Market Data Analyst | `get_stock_quote` | yfinance + NSE API |
| `research` | Fundamental Research Analyst | `get_fundamentals` | Screener.in |
| `news` | Financial News Aggregator | `get_latest_news` | gnews (Google News) |
| `shareholding` | Shareholding Pattern Analyst | `get_holdings` | Screener.in |
| `mf_holdings` | MF Holdings Analyst | `get_mf_holdings` | NSE API |
| `filings` | — (direct call, no CrewAI agent) | `get_nse_filings` | NSE corporate announcements |

**Layer 2 — Analyst (direct LLM call)**: `run_analysis_with_fallback()` in `crew.py` calls `litellm.completion` directly — no CrewAI involved. It receives all six data slices plus signal engine context, and must return a specific JSON schema defined in `config/analyst.json`. Guardrails in `_validate_analysis_payload()` enforce structural rules; failures return a safe HOLD fallback via `_safe_analysis_fallback()`.

**Layer 3 — Market picks pipeline** (`market_picks_pipeline.py`): Six sequential phases, all blocking work offloaded to `ThreadPoolExecutor`. Communicates back to the SSE stream via `on_event` callbacks bridged through `asyncio.Queue` with `loop.call_soon_threadsafe`.

| Phase | What it does |
|---|---|
| `_phase_scrape` | Parallel fetch from 13 sources (5 RSS + 8 GNews). 6 workers. |
| `_phase_extract` | One LLM call per source (parallel, up to 6 workers). Checks extraction cache first. Detects syndicated articles (Jaccard ≥ 0.60) across sources to down-weight them. |
| `_phase_consolidate` | Groups picks by ticker, validates against NSE equity master, confirms live price via yfinance (guards pre-IPO / unlisted names). Uses rapidfuzz for fuzzy company-name matching. |
| `_phase_research` | Fetches `stock_info` + `research` + signal engine per stock (4 workers, up to `_MAX_STOCKS` stocks). |
| `_phase_analyze` | Batched LLM calls (8 stocks/batch, parallel) for qualitative summary + bull/bear factors. Does NOT ask the LLM for prices. |
| `_phase_score` | Deterministic confidence scoring (50% signal engine + 30% consensus + 20% recency). 4-tier recs (BUY / WATCHLIST / HOLD / SELL). Entry/target/stop-loss computed from price and signal score — no LLM. Sector-balanced: max 2 stocks per sector in the primary list. Saves a daily snapshot to `output/_history/` for trend tracking. |

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

Provider is auto-detected from whichever key is present (checked in the order above). Set `LLM_PROVIDER` explicitly to override.

### Optional

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | auto | `anthropic` / `openai` / `groq` / `google` / `ollama` |
| `LLM_MODEL` | provider default | Model for data-fetching agents (fast/cheap tier) |
| `ANALYST_MODEL` | provider default | Model for analyst LLM call (stronger tier) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Only needed when `LLM_PROVIDER=ollama` |
| `LOG_LEVEL` | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`) |

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

### Shared state and queues

- **No shared in-memory state** between requests. Each request runs its own pipeline instance.
- **Inter-phase communication** within the market picks pipeline uses direct function return values (not queues). The `asyncio.Queue` is only used to bridge the blocking thread back to the async SSE loop.
- **Cache** (`output/`) is the only persistent shared state; concurrent writes to different symbols are safe (each symbol has its own subdirectory).

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
- **Run `npx tsc --noEmit` in `frontend/`** before marking any frontend task done. There is no other automated check.
- **Cache TTLs are intentional.** `stock_info` and `news` are 1 h; `research` is 24 h; `shareholding`/`mf_holdings` are 168 h (7 days). Do not shorten these without understanding the NSE rate-limit implications.
- **The analyst step is expensive.** It only re-runs when at least one input task was stale. Do not add logic that forces it to re-run unconditionally.
- **Market picks pipeline max stocks = 35** (`_MAX_STOCKS` in `market_picks_pipeline.py`). Raising this significantly increases wall-clock time and LLM costs.
- **4-tier recommendation in market picks**: BUY / WATCHLIST / HOLD / SELL. Do not collapse these to 3-tier. `WATCHLIST` is a distinct lower-conviction tier between BUY and HOLD.
- **Trade levels are deterministic in market picks** (entry/target/stop computed from signal score and 52w range). Do not add LLM-driven price generation — it produces null values when context overflows.
- **Extraction cache** (`output/_extract_cache/`) avoids re-calling the LLM for the same source articles within 6 h. The cache key is content-aware (title + URL + summary hash), so edits or new articles get a fresh key automatically.
- **Source credibility weights** in `_SOURCE_CREDIBILITY` determine how much each source contributes to confidence scoring. Adding a new source requires adding a credibility entry; missing sources default to 0.50.
- **HDFC Securities sources** live in `tools/hdfc_sec_agent.py` and are merged into `SOURCES` / `SCRAPER_FNS` at import time in `tools/market_picks_tools.py`. Adding a new brokerage source follows the same pattern: define scrapers in a separate module, export `*_SOURCES` and `*_SCRAPERS`, merge in `market_picks_tools.py`.
