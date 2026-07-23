# Setup & Configuration

## What you need

- Python 3.13
- Node.js 18+
- `npm`
- Internet access (market/news data; Google Fonts during `npm run build`)
- One LLM provider configured (see below)
- PostgreSQL (optional — only for the SME Signals screener)

## Backend setup

From the repo root:

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env` and set the provider you want to use.

### Backend environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | One key required | Anthropic API key |
| `OPENAI_API_KEY` | One key required | OpenAI API key |
| `GROQ_API_KEY` | One key required | Groq API key |
| `GOOGLE_API_KEY` | One key required | Google Gemini API key |
| `OPENROUTER_API_KEY` | One key required | OpenRouter API key (access to 300+ models) |
| `LLM_PROVIDER` | No | `anthropic` / `openai` / `groq` / `google` / `openrouter` / `ollama` — auto-detected if unset |
| `LLM_MODEL` | No | Model for data/worker agents (fast/cheap tier) |
| `ANALYST_MODEL` | No | Model for the final analyst step (stronger tier) |
| `OLLAMA_BASE_URL` | Ollama only | Default: `http://localhost:11434` |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` — default: `INFO` |
| `DATABASE_URL` | SME signals only | PostgreSQL DSN, e.g. `postgresql://user:pass@localhost:5432/sme_research` |

If `LLM_PROVIDER` is unset, the backend auto-detects the first key present in this order: `anthropic`, `openai`, `groq`, `google`, `openrouter`.

### Default models per provider

| Provider | Data agents | Analyst / market picks |
|---|---|---|
| `anthropic` | `claude-haiku-4-5-20251001` | `claude-sonnet-4-6` |
| `openai` | `gpt-4o-mini` | `gpt-4o` |
| `groq` | `groq/llama-3.1-8b-instant` | `groq/llama-3.3-70b-versatile` |
| `google` | `gemini/gemini-2.5-flash` | `gemini/gemini-2.5-flash` |
| `openrouter` | `openrouter/meta-llama/llama-3.1-8b-instruct` | `openrouter/meta-llama/llama-3.3-70b-instruct` |
| `ollama` | `ollama/llama3.2` | `ollama/llama3.1:8b` |

## Frontend setup

```bash
cd frontend
npm install
```

### Frontend environment variables

Create `frontend/.env.local` only when overriding the default backend URL:

```env
API_URL=http://localhost:8000
```

The frontend already defaults to `http://localhost:8000`.

## Running locally

**Terminal A — backend:**

```bash
source .venv/bin/activate
uvicorn api:app --reload --port 8000
```

Backend endpoints:

| Endpoint | Description |
|---|---|
| `GET /api/validate/{symbol}` | Ticker / ISIN / company name lookup |
| `GET /api/analyse/{symbol}` | Stock analysis SSE stream |
| `GET /api/market-picks` | Market picks SSE stream (`?force=true` bypasses cache) |
| `GET /api/sme-signals` | SME golden/death cross events (`?lookback=1..30`, `?direction=all\|golden\|death`) |
| `POST /api/sme-signals/refresh` | Run the SME pipeline in the background (202; 409 if already running) |

**Terminal B — frontend:**

```bash
cd frontend
npm run dev
```

- Stock analysis: [http://localhost:3000](http://localhost:3000)
- Market picks: [http://localhost:3000/market-picks](http://localhost:3000/market-picks)
- SME signals: [http://localhost:3000/sme-signals](http://localhost:3000/sme-signals)

## CLI mode

```bash
source .venv/bin/activate
python main.py TCS
python main.py RELIANCE --force   # bypass cache
```

## SME signals pipeline

Requires `DATABASE_URL` in `.env` and a running PostgreSQL. Create the database once (`createdb sme_research`), then:

```bash
source .venv/bin/activate
python sme_ema_pipeline.py --setup-db   # create tables (idempotent)
python sme_ema_pipeline.py              # fetch SME stocks, compute EMA20/EMA50 crosses, store
python sme_ema_pipeline.py --reset-db   # drop + recreate tables (after schema changes; data is regenerable)
python sme_ema_pipeline.py --force      # bypass the 24 h stock-list cache
python sme_ema_pipeline.py --lookback 10  # report window for the CLI summary
```

The screener page's **Refresh Data** button triggers the same pipeline via `POST /api/sme-signals/refresh`. For daily automation after NSE close (assumes system TZ is IST):

```cron
30 18 * * 1-5 cd /path/to/stock-research && .venv/bin/python sme_ema_pipeline.py >> output/sme_cron.log 2>&1
```

## Cache and output

Per-symbol caches under `output/<SYMBOL>/` with these TTLs:

| Task | TTL |
|---|---|
| `stock_info` | 1 hour |
| `news` | 1 hour |
| `research` | 24 hours |
| `analysis` | 24 hours |
| `shareholding` | 7 days |
| `mf_holdings` | 7 days |

Market picks-specific caches:

| Path | TTL | Description |
|---|---|---|
| `output/_market_picks/picks.json` | 6 hours | Full pipeline result |
| `output/_extract_cache/` | 6 hours | Per-source LLM extraction |
| `output/_nse_master.txt` | 24 hours | NSE equity symbol list |
| `output/_history/` | Permanent | Daily snapshots for trend labels |

## Useful commands

```bash
# Backend
source .venv/bin/activate
python main.py INFY
uvicorn api:app --reload --port 8000

# SME signals pipeline
python sme_ema_pipeline.py

# Backend tests
python -m pytest tests/

# Frontend
cd frontend
npm run dev
npm run build
npm run start
npx tsc --noEmit   # type-check (the only automated frontend check)
```

## Troubleshooting

### No provider configured

If the backend exits with "No API key or local provider found":

```env
# Add one of these to .env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
GOOGLE_API_KEY=AIza...

# Or for Ollama
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
```

### Frontend shows backend unavailable

Make sure the backend is running on port 8000, or set:

```env
# frontend/.env.local
API_URL=http://your-backend-host:8000
```

### Market picks returns no results

The pipeline fetches from external RSS feeds and GNews. If all sources return empty:

- Check internet connectivity from the server
- Run with `?force=true` to bypass the result cache
- Set `LOG_LEVEL=DEBUG` in `.env` for verbose source-fetch logs

### NSE equity master download fails

The pipeline fails open when `output/_nse_master.txt` cannot be downloaded — all tickers are allowed through. If validation is too permissive, delete the stale cache file and ensure `nsearchives.nseindia.com` is reachable.

### SME signals page returns 503

`GET /api/sme-signals` returns 503 when `DATABASE_URL` is not set or PostgreSQL is unreachable. Set `DATABASE_URL` in `.env`, make sure the database exists, and run `python sme_ema_pipeline.py --setup-db` followed by a pipeline run so the tables have data.

### Next.js build fails on Google Fonts

`npm run build` fetches `Inter` and `JetBrains Mono` from `fonts.googleapis.com`. To build offline, replace the font imports in `frontend/app/layout.tsx` with local font files.
