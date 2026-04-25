# Architecture

## Overview

The project has two user-facing entrypoints:

- The web app in `frontend/`
- The CLI entrypoint in `main.py`

Both flows use the same Python data-fetching and report-building logic.

## High-level flow

```text
Browser (Next.js on :3000)
  -> /api/validate/[symbol]
  -> /api/analyse/[symbol]
  -> proxies to Python backend on :8000

Python backend (FastAPI)
  -> checks cache freshness
  -> fetches stale data tasks in parallel
  -> validates normalized output
  -> runs analyst crew if needed
  -> builds merged report
  -> streams SSE progress + final report
```

## Components

### 1. Next.js frontend

Main files:

- `frontend/app/page.tsx`
- `frontend/components/ticker-search.tsx`
- `frontend/components/progress-tracker.tsx`
- `frontend/components/results-dashboard.tsx`
- `frontend/app/api/validate/[symbol]/route.ts`
- `frontend/app/api/analyse/[symbol]/route.ts`

Responsibilities:

- Validate ticker input before analysis
- Proxy browser requests to the backend using `API_URL`
- Stream progress events with Server-Sent Events
- Render recommendation, market data, holdings, and news

## 2. FastAPI backend

Main file:

- `api.py`

Responsibilities:

- Expose `/api/validate/{symbol}` for symbol lookup
- Expose `/api/analyse/{symbol}` for streaming analysis progress
- Read/write cache files
- Run stale fetch tasks concurrently
- Run the analyst step and return the merged report

## 3. CLI pipeline

Main file:

- `main.py`

Responsibilities:

- Support direct terminal usage: `python main.py TCS`
- Check cache freshness
- Fetch stale tasks in parallel
- Run the analyst step when inputs changed or analysis is stale
- Save the merged `report_<DATE>.json`

## 4. Crew / analyst layer

Main file:

- `crew.py`

The analyst is built with CrewAI. The current repo behavior is:

- Data tasks are fetched directly from Python in `main.py` / `api.py`
- The analyst step still uses CrewAI and the configured LLM provider
- Cached task data is inlined into the analyst prompt
- Fresh task data can also be passed through task context when needed

This means the pipeline is hybrid:

- deterministic data collection
- LLM-based synthesis for the final recommendation

## 5. Config layer

Directory: `config/`

All agent and task configuration is stored as JSON and loaded at startup. The Python files in `config/` are thin loaders that wire JSON definitions to live tool callables and CrewAI objects.

| File | Content |
|------|---------|
| `agents.json` | Per-task agent role, backstory, and tool name |
| `tasks.json` | Per-task description template, expected output, max retries |
| `analyst.json` | Analyst agent persona, section labels, analysis rules, valuation guidance, output schema |
| `crew_agents.py` | Reads `agents.json`, maps tool names to callables, exports `AGENTS_FOR_TASK` and `BACKSTORIES` |
| `crew_tasks.py` | Reads `tasks.json` + `analyst.json`, exports `build_task_specs`, `build_analysis_prompt`, `ANALYST_SECTIONS`, `ANALYST_DESCRIPTION_SUFFIX` |

To tune agent behaviour or the analyst prompt, edit the JSON files only — no Python changes are needed.

## Data tasks

Canonical task order:

1. `stock_info`
2. `research`
3. `news`
4. `shareholding`
5. `mf_holdings`

These tasks are fetched in parallel when stale. Their normalized outputs are stored in `output/<SYMBOL>/`.

## Analyst output

The analyst produces:

- `recommendation`
- `confidence`
- `summary`
- `valuation`
- `business_quality`
- `bull_factors`
- `bear_factors`
- `key_risks`
- `news_sentiment`
- `news_highlights`
- `institutional_trend`

## Validation and normalization

Main file:

- `schemas.py`

Responsibilities:

- Normalize raw tool output into a canonical shape
- Validate required fields before continuing
- Prevent invalid `stock_info` data from producing a bad report

## Cache layer

Main file:

- `cache.py`

Each task is cached separately with `_meta.fetched_at`.

TTL policy:

- `stock_info`: 1 hour
- `news`: 1 hour
- `research`: 24 hours
- `analysis`: 24 hours
- `shareholding`: 7 days
- `mf_holdings`: 7 days

This allows the app to re-fetch only stale sections instead of re-running the entire pipeline every time.

## Streaming flow

`GET /api/analyse/{symbol}` uses Server-Sent Events and emits:

- `start`
- `task_done`
- `analysing`
- `done`
- `error`

The frontend uses those events to update the progress tracker and render the final report when the `done` event arrives.

## File layout

```text
stock-research/
├── api.py
├── main.py
├── crew.py
├── cache.py
├── schemas.py
├── tools/
│   ├── nse_tools.py
│   ├── screener_tools.py
│   └── news_tools.py
├── config/
│   ├── agents.json         ← agent roles, backstories, tool mapping
│   ├── tasks.json          ← task descriptions, expected outputs, retry counts
│   ├── analyst.json        ← analyst persona, prompt rules, output schema
│   ├── crew_agents.py      ← loader: JSON → AGENTS_FOR_TASK, BACKSTORIES
│   └── crew_tasks.py       ← loader: JSON → task specs, analyst prompt builder
├── frontend/
│   ├── app/
│   ├── components/
│   ├── types/
│   └── package.json
├── docs/
├── output/
├── requirements.txt
└── .env.example
```
