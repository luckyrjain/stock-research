# Architecture

## Overview

The project has two user-facing entrypoints and three backend flows:

| Flow | Entrypoint | Description |
|---|---|---|
| Stock analysis | `frontend/app/page.tsx` → `GET /api/analyse/{symbol}` | Validate → fetch → signal engine → LLM analyst → report |
| Market picks | `frontend/app/market-picks/page.tsx` → `GET /api/market-picks` | Scrape → extract → consolidate → research → analyze → score |
| CLI | `python main.py <SYMBOL>` | Same as stock analysis, no frontend |

---

## Stock analysis flow

```text
Browser (Next.js on :3000)
  └─ EventSource → /api/analyse/{symbol}
        └─ Next.js proxy → FastAPI :8000

FastAPI /api/analyse/{symbol}
  1. Check cache freshness for each of the 6 tasks
  2. Fetch stale tasks concurrently (ThreadPoolExecutor)
       task_done SSE event per task as each completes
  3. Normalize all outputs through schemas.normalize()
  4. Run signal engine (synchronous, fast)
  5. Run LLM analyst in a thread; send heartbeats every 15 s
  6. Emit done SSE event with merged report
```

### SSE events emitted

| Event | When |
|---|---|
| `start` | Immediately; lists stale vs cached tasks |
| `task_done` | Each time one of the 6 data tasks completes |
| `analysing` | When the LLM analyst call starts |
| `done` | Report is ready |
| `error` | Unrecoverable failure |

---

## Symbol validation flow (`GET /api/validate/{symbol}`)

The endpoint handles three input forms:

1. **ISIN** (e.g. `INE009A01021`) — checks NSE equity master CSV first; falls back to yfinance's native ISIN lookup; for BSE-only results returns directly, for NSE results falls through to full metadata fetch.
2. **BSE-forced** (`?exchange=BSE`) — resolves a Screener.in slug (e.g. `505685` or `TAPARIA-TOOLS`) to the proper NSE/BSE ticker via the Screener company page.
3. **Ticker / company name** — NSE autocomplete + BSE autocomplete (via Screener.in) run in parallel. NSE result is enriched with ISIN via `_quote_meta_sync`; BSE symbol is looked up by ISIN. Screener.in is used as a final fallback if both miss.

---

## Market picks flow

```text
Browser (Next.js on :3000)
  └─ EventSource → /api/market-picks[?force=true]
        └─ Next.js proxy → FastAPI :8000

FastAPI /api/market-picks
  1. Check output/_market_picks/picks.json (6 h TTL)
     → cache hit: emit done event immediately, return
  2. Run MarketPicksPipeline.run() in ThreadPoolExecutor
     → on_event() → loop.call_soon_threadsafe → asyncio.Queue → SSE
  3. Pipeline phases run synchronously inside the executor thread
  4. Final picks saved to output/_market_picks/picks.json
```

### Pipeline phases

| Phase | Workers | What it does |
|---|---|---|
| `_phase_scrape` | 6 | Parallel fetch from 13 sources (5 RSS + 8 GNews). Emits `source_done` per source. |
| `_phase_extract` | 6 | One LLM call per source in parallel. Checks `output/_extract_cache/` first (6 h, content-aware key). Detects syndicated articles across sources (Jaccard title similarity ≥ 0.60) and marks them for down-weighting. Emits `extracting` then `extract_progress` per batch. |
| `_phase_consolidate` | 8 | Groups picks by ticker; validates against NSE equity master (`output/_nse_master.txt`, refreshed every 24 h); confirms live price via yfinance (rejects pre-IPO / unlisted names); uses rapidfuzz for fuzzy company-name matching. Emits `consolidating` then `validate_progress` per symbol. |
| `_phase_research` | 4 | Fetches `stock_info` + `research` + signal engine per stock. Detects recent IPOs (< 8 months of monthly history). Emits `researching` then `stock_researched` per symbol. |
| `_phase_analyze` | 4 | Batched LLM calls (8 stocks per batch). Returns qualitative `summary`, `bull_factors`, `bear_factors`. Does **not** ask the LLM for prices. Emits `scoring` at start; `analysis_error` if a batch fails. |
| `_phase_score` | — | Deterministic. Computes confidence (50 % signal engine + 30 % consensus + 20 % recency). Assigns 4-tier recommendation. Computes entry/target/stop-loss from signal score and 52-week range. Sector-balances (max 2 per sector in primary list). Saves daily snapshot to `output/_history/`. Emits `done`. |

### Market picks SSE events

| Event | When |
|---|---|
| `picks_start` | Pipeline started; lists all sources |
| `source_done` | Each source fetch completes |
| `extracting` | Extraction phase begins |
| `extract_progress` | Each source LLM extraction completes |
| `consolidating` | Consolidation phase begins |
| `validate_progress` | Each symbol validated/rejected |
| `researching` | Research phase begins |
| `stock_researched` | Each stock researched |
| `scoring` | Scoring/analysis phase begins |
| `analysis_error` | A batch LLM call failed (non-fatal; fallback used) |
| `done` | Final ranked picks |
| `error` | Unrecoverable failure |

### Confidence scoring formula

```
confidence = 50 % × signal_score_component   (quant: valuation + growth + volume + filings)
           + 30 % × consensus_component       (log-scaled quality-weighted source signal)
           + 20 % × recency_component         (credibility-weighted mean exp(-age_days / 3))
```

### 4-tier recommendation logic

```
combined_dir = 0.55 × consensus_norm + 0.45 × signal_score

BUY       if combined_dir ≥ 0.35 and signal_score ≥ -0.3
WATCHLIST if combined_dir ≥ 0.15 (or BUY demoted by strong negative signal)
SELL      if combined_dir ≤ -0.30
HOLD      otherwise
```

---

## Agent layers

### Layer 1 — Data agents (CrewAI)

`build_crew()` in `crew.py` wires five agents, each wrapping exactly one tool. In production, the API and CLI call `_fetch_task()` directly using `ThreadPoolExecutor` — CrewAI is bypassed for performance.

| Task | Tool | Source |
|---|---|---|
| `stock_info` | `get_stock_quote` | yfinance + NSE API |
| `research` | `get_fundamentals` | Screener.in |
| `news` | `get_latest_news` | gnews (Google News) |
| `shareholding` | `get_holdings` | Screener.in |
| `mf_holdings` | `get_mf_holdings` | NSE API |
| `filings` | `get_nse_filings` (direct) | NSE corporate announcements |

### Layer 2 — Analyst (direct LLM call)

`run_analysis_with_fallback()` in `crew.py` calls `litellm.completion` directly — no CrewAI. It receives all six data slices plus signal engine context and must return the JSON schema defined in `config/analyst.json`. Guardrails in `_validate_analysis_payload()` enforce structure; failures return a safe HOLD via `_safe_analysis_fallback()`.

### Layer 3 — Market picks pipeline

`MarketPicksPipeline` in `market_picks_pipeline.py`. Six phases; all blocking work runs in `ThreadPoolExecutor`. Bridges back to the async SSE loop via `loop.call_soon_threadsafe(q.put_nowait, payload)`.

---

## Signal engine

`signals/engine.run_signal_engine(symbol, all_data)` returns a `SignalResult` with:

- `final_score` — float in –1..1
- `verdict` — `BUY` / `HOLD` / `SELL`
- `signals` — dict of named `SignalItem` objects (valuation, growth, volume, filings)

`signals/interpreter.interpret(signal_result)` returns a plain-English insight string.

Signal results are persisted by `signals/store.save_signal()` for the stock analysis flow. The market picks pipeline uses the signal engine's `final_score` and `verdict` directly as 50 % of the confidence score.

---

## Config layer

All agent and task definitions are JSON; Python files are thin loaders.

| File | Content |
|---|---|
| `config/agents.json` | Per-task agent role, backstory, and tool name |
| `config/tasks.json` | Per-task description template, expected output, max retries |
| `config/analyst.json` | Analyst persona, section labels, rules, valuation guidance, output schema |
| `config/crew_agents.py` | Reads `agents.json`, maps tool names to callables, exports `AGENTS_FOR_TASK` |
| `config/crew_tasks.py` | Reads `tasks.json` + `analyst.json`, exports task specs and analyst prompt builder |

---

## Cache layer

`cache.py` manages per-symbol task caches under `output/<SYMBOL>/`. Each file has a top-level `_meta.fetched_at` timestamp.

| Task | TTL |
|---|---|
| `stock_info` | 1 hour |
| `news` | 1 hour |
| `research` | 24 hours |
| `analysis` | 24 hours |
| `shareholding` | 7 days |
| `mf_holdings` | 7 days |

Market picks caches:

| Path | TTL | Purpose |
|---|---|---|
| `output/_market_picks/picks.json` | 6 hours | Full pipeline result |
| `output/_extract_cache/<hash>.json` | 6 hours | Per-source LLM extraction result |
| `output/_nse_master.txt` | 24 hours | NSE equity symbol master |
| `output/_history/<YYYY-MM-DD>.json` | Permanent | Daily pick snapshot for trend tracking |

---

## SSE bridge pattern

```python
# CORRECT — create_task requires a coroutine
async def _launch():
    await loop.run_in_executor(None, blocking_fn)

asyncio.create_task(_launch())
```

Never pass `loop.run_in_executor(...)` directly to `create_task` — it returns a `Future`, not a coroutine, and raises `TypeError` at runtime.

---

## File layout

```text
stock-research/
├── api.py                  FastAPI server
├── main.py                 CLI + shared _fetch_task, _build_report
├── crew.py                 LLM resolution, analyst, guardrails
├── cache.py                TTL cache
├── schemas.py              Normalisation contracts
├── market_picks_pipeline.py  6-phase picks pipeline
├── observability.py        Structured JSON logging
├── requirements.txt
├── .env.example
├── config/
│   ├── agents.json
│   ├── tasks.json
│   ├── analyst.json
│   ├── crew_agents.py
│   └── crew_tasks.py
├── tools/
│   ├── market_picks_tools.py   RSS + GNews scrapers (11 sources)
│   ├── hdfc_sec_agent.py       HDFC Securities scrapers (2 sources)
│   └── ...                     nse_tools, screener_tools, news_tools, etc.
├── signals/
│   ├── engine.py
│   ├── interpreter.py
│   └── store.py
├── tests/
├── frontend/
│   ├── app/
│   │   ├── page.tsx                Stock analysis page
│   │   ├── market-picks/page.tsx   Market picks page
│   │   └── api/                    Proxy routes
│   ├── components/
│   ├── types/index.ts
│   └── package.json
├── docs/
└── output/
    ├── <SYMBOL>/
    ├── _extract_cache/
    ├── _history/
    ├── _market_picks/
    └── _nse_master.txt
```
