# Stock Research

[![CI](https://github.com/luckyrjain/stock-research/actions/workflows/ci.yml/badge.svg)](https://github.com/luckyrjain/stock-research/actions/workflows/ci.yml)

Stock Research is a full-stack Indian equity research platform with two modes:

**Stock Analysis** — given a symbol like `TCS` or `RELIANCE`, the app validates the ticker (NSE, BSE, or ISIN), fetches live market data, fundamentals, news, shareholding, MF holdings, and NSE filings in parallel, runs a quantitative signal engine, then calls an LLM analyst to produce a structured `BUY`, `SELL`, or `HOLD` recommendation. Progress and the final report are streamed to the browser via Server-Sent Events.

**Market Picks** — a multi-agent pipeline that scrapes 16 Indian and global financial sources (RSS feeds + GNews), extracts stock recommendations with an LLM, validates symbols against the NSE equity master, runs due diligence on each, and returns a confidence-ranked watchlist with `BUY` / `WATCHLIST` / `HOLD` / `SELL` ratings and deterministic entry/target/stop-loss levels.

**SME Signals** — a PostgreSQL-backed batch pipeline that screens all NSE Emerge + BSE SME stocks for EMA20/EMA50 **golden cross** and **death cross** events. It downloads a year of daily prices per stock, detects crosses, and serves recent events plus each stock's current regime (in or out of golden cross) on a dedicated screener page with an on-demand refresh button.

## Tech stack

- **Backend**: Python 3.13, FastAPI, CrewAI, litellm, signals engine
- **Frontend**: Next.js 15, React 19, TypeScript, Tailwind CSS
- **Data sources**: Yahoo Finance, Screener.in, Google News, NSE API, BSE API, RSS feeds
- **LLM providers**: Anthropic, OpenAI, Groq, Google, OpenRouter, Ollama (auto-detected from env)
- **Storage**: file-based cache in `output/`; PostgreSQL for SME signals

## Project structure

```text
stock-research/
├── api.py                  FastAPI server — SSE endpoints, symbol validation, market picks
├── main.py                 CLI entry point; _fetch_task, _build_report shared with api.py
├── crew.py                 LLM resolution, analyst guardrails, run_analysis_with_fallback
├── cache.py                File-based TTL cache (output/<SYMBOL>/<task>.json)
├── schemas.py              Normalisation contracts: raw tool output → canonical dicts
├── market_picks_pipeline.py  6-phase market picks pipeline
├── sme_ema_pipeline.py     SME golden/death cross batch pipeline (PostgreSQL)
├── db/                     SQLAlchemy Core tables (models.py) + schema.sql reference
├── observability.py        Structured JSON logging via log_event()
├── requirements.txt
├── .env.example
├── config/
│   ├── agents.json
│   ├── tasks.json
│   ├── analyst.json
│   ├── crew_agents.py
│   └── crew_tasks.py
├── tools/
│   ├── market_picks_tools.py       RSS + GNews scrapers; source registry
│   ├── hdfc_sec_agent.py           HDFC Securities Fundamental + Technical (GNews)
│   ├── nse_bulk_block_deals.py     NSE bulk/block deal institutional activity
│   ├── screener_scanner.py         Screener.in public fundamental screener
│   ├── trendlyne_agent.py          Trendlyne analyst consensus + upgrade alerts
│   ├── sme_tools.py                NSE Emerge + BSE SME stock-list fetchers
│   └── ...                         yfinance, Screener.in, gnews, NSE filings tools
├── signals/                Quantitative signal engine
├── tests/
├── frontend/
│   ├── app/page.tsx              Stock analysis page (supports ?symbol= deep links)
│   ├── app/market-picks/page.tsx Market Picks page
│   ├── app/sme-signals/page.tsx  SME golden cross screener
│   ├── components/               Dashboard, search, progress tracker, market picks dashboard
│   ├── app/api/                  Next.js proxy routes → FastAPI backend
│   └── types/index.ts            Canonical TS types for all SSE messages and reports
└── output/
    ├── <SYMBOL>/           Per-symbol task caches
    ├── _extract_cache/     LLM extraction cache (6 h TTL)
    ├── _history/           Daily pick snapshots for trend tracking
    ├── _market_picks/      Market picks result cache (6 h TTL)
    └── _nse_master.txt     NSE equity symbol master (refreshed every 24 h)
```

## Prerequisites

- Python 3.13
- Node.js 18+
- `npm`
- Internet access
- One configured LLM provider: Anthropic, OpenAI, Groq, Google, or local Ollama
- PostgreSQL (optional — only for the SME Signals screener)

## Backend setup

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your provider credentials. Minimal examples:

```env
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...

# Ollama
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434

# Optional — only for the SME Signals screener
DATABASE_URL=postgresql://user:password@localhost:5432/sme_research
```

## Frontend setup

```bash
cd frontend
npm install
```

Optional — create `frontend/.env.local` only if your backend is not on the default port:

```env
API_URL=http://localhost:8000
```

## Run the app locally

**Terminal A — backend:**

```bash
source .venv/bin/activate
uvicorn api:app --reload --port 8000
```

**Terminal B — frontend:**

```bash
cd frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

The Market Picks page is at [http://localhost:3000/market-picks](http://localhost:3000/market-picks).
The SME Signals screener is at [http://localhost:3000/sme-signals](http://localhost:3000/sme-signals) (needs `DATABASE_URL` and one pipeline run — see below).

## Run the SME signals pipeline

```bash
source .venv/bin/activate
python sme_ema_pipeline.py --setup-db   # create tables (first run only)
python sme_ema_pipeline.py              # fetch, compute crosses, store
python sme_ema_pipeline.py --reset-db   # drop + recreate tables (after schema changes)
```

Data can also be refreshed from the SME Signals page (Refresh Data button), or it runs
automatically on a schedule via `.github/workflows/sme-cron.yml` (weekdays, shortly after
NSE close — add a `DATABASE_URL` repository secret for it to work). For a local/self-hosted
alternative, a crontab entry works too:

```cron
30 18 * * 1-5 cd /path/to/stock-research && .venv/bin/python sme_ema_pipeline.py >> output/sme_cron.log 2>&1
```

## Run the CLI pipeline

```bash
source .venv/bin/activate
python main.py TCS
python main.py RELIANCE --force   # bypass cache
```

## Available scripts

```bash
# Backend
uvicorn api:app --reload --port 8000
python main.py INFY

# SME signals pipeline
python sme_ema_pipeline.py --setup-db
python sme_ema_pipeline.py

# Tests
python -m pytest tests/

# Frontend
cd frontend
npm run dev
npm run build
npm run start
npx tsc --noEmit    # type-check (no lint config, no frontend test suite)
```

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/validate/{symbol}` | Symbol lookup — accepts ticker, ISIN, or company name; returns NSE/BSE metadata |
| `GET /api/analyse/{symbol}` | Stock analysis — SSE stream of task progress + final report |
| `GET /api/market-picks` | Market picks — SSE stream of pipeline progress + ranked watchlist |
| `GET /api/sme-signals` | SME golden/death cross events + current regime (`?lookback=`, `?direction=all\|golden\|death`) |
| `POST /api/sme-signals/refresh` | Run the SME pipeline in the background (202; 409 if already running) |

The Next.js app proxies all of these through `frontend/app/api/`.

`/api/market-picks` supports `?force=true` to bypass the 6 h result cache.

## Cache behaviour

Per-symbol task caches under `output/<SYMBOL>/`:

| Task | TTL |
|---|---|
| `stock_info` | 1 hour |
| `news` | 1 hour |
| `research` | 24 hours |
| `analysis` | 24 hours |
| `shareholding` | 7 days |
| `mf_holdings` | 7 days |

Market picks result cache: `output/_market_picks/picks.json`, 6 h TTL.

## Customising agent behaviour

All agent and task configuration is in `config/` JSON files — no Python changes needed for prompt tuning:

| File | What to edit |
|---|---|
| `config/agents.json` | Agent roles, backstories, tool assignment per task |
| `config/tasks.json` | Task descriptions, expected output shape, retry count |
| `config/analyst.json` | Analyst persona, analysis rules, valuation guidance, output schema |

## Notes

- The backend emits structured JSON logs to stdout with per-run `run_id`. Set `LOG_LEVEL=DEBUG` for verbose output.
- `npm run build` needs network access to Google Fonts (`fonts.googleapis.com`) for `Inter` and `JetBrains Mono`. Replace with local fonts to build offline.
- The validate endpoint accepts ISINs (e.g. `INE009A01021`) and resolves them to NSE/BSE tickers automatically.
- Market Picks uses a daily snapshot history (`output/_history/`) to compute `rising` / `falling` / `stable` trend labels.

## Documentation

- [docs/index.md](docs/index.md)
- [docs/setup.md](docs/setup.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/tools.md](docs/tools.md)
- [docs/output-schema.md](docs/output-schema.md)
