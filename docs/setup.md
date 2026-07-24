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
| `ANALYST_MODEL` | No | Model for the analyst step (stock analysis) and market picks' extraction/analysis LLM calls |
| `OLLAMA_BASE_URL` | Ollama only | Default: `http://localhost:11434` |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` — default: `INFO` |
| `DATABASE_URL` | SME signals + watchlist | PostgreSQL DSN, e.g. `postgresql://user:pass@localhost:5432/sme_research` |

If `LLM_PROVIDER` is unset, the backend auto-detects the first key present in this order: `anthropic`, `openai`, `groq`, `google`, `openrouter`.

### Default models per provider

| Provider | Analyst / market picks (`ANALYST_MODEL`) |
|---|---|
| `anthropic` | `claude-sonnet-4-6` |
| `openai` | `gpt-4o` |
| `groq` | `groq/llama-3.3-70b-versatile` |
| `google` | `gemini/gemini-2.5-flash` |
| `openrouter` | `openrouter/meta-llama/llama-3.3-70b-instruct` |
| `ollama` | `ollama/llama3.1:8b` (stock analysis only — market picks doesn't support Ollama) |

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
| `GET /api/peers/{symbol}` | Sector peer comparison table + percentile badges, scraped from Screener.in (24 h cache) |
| `GET /api/market-picks` | Market picks SSE stream (`?force=true` bypasses cache) |
| `GET /api/sme-signals` | SME golden/death cross events (`?lookback=1..30`, `?direction=all\|golden\|death`) |
| `POST /api/sme-signals/refresh` | Run the SME pipeline in the background (202; 409 if already running) |
| `GET /api/consolidated/{symbol}` | Analysis verdict + market-pick membership + SME regime, aggregated from each mode's own cache — no new fetching |

**Terminal B — frontend:**

```bash
cd frontend
npm run dev
```

- Stock analysis: [http://localhost:3000](http://localhost:3000)
- Market picks: [http://localhost:3000/market-picks](http://localhost:3000/market-picks)
- SME signals: [http://localhost:3000/sme-signals](http://localhost:3000/sme-signals)
- Watchlist: [http://localhost:3000/watchlist](http://localhost:3000/watchlist)

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

The screener page's **Refresh Data** button triggers the same pipeline via `POST /api/sme-signals/refresh`.
The CLI exits non-zero (and the API logs an `sme_refresh_unhealthy` warning) when a run was
substantially unsuccessful — an empty stock list or too high an OHLCV fetch error rate — rather
than silently completing with mostly-empty data. See `_MAX_ACCEPTABLE_ERROR_RATE` in
`sme_ema_pipeline.py`.

Daily automation runs via `.github/workflows/sme-cron.yml` on GitHub Actions — weekdays at
13:00 UTC (18:30 IST), shortly after NSE close. Add a `DATABASE_URL` repository secret
(Settings > Secrets and variables > Actions) pointing at a network-reachable Postgres
instance; the workflow fails with a clear message rather than a Python traceback if it's
missing. Trigger a one-off run from the Actions tab ("Run workflow"). If you'd rather run
this locally/self-hosted instead of on GitHub Actions, a crontab entry works too:

## Market picks pipeline

No separate setup — it runs against whichever LLM provider key is already configured. Trigger a
run from the CLI, or via the **Fresh scan** / **See This Week's Picks** buttons on `/market-picks`:

```bash
source .venv/bin/activate
python market_picks_pipeline.py
```

This saves straight to `output/_market_picks/picks.json`, bypassing `api.py`'s SSE endpoint —
only useful when run on the same host/disk as the backend (e.g. a self-hosted crontab, same
caveat as the SME crontab alternative above).

Weekly automation runs via `.github/workflows/market-picks-cron.yml` on GitHub Actions — every
Monday at 01:30 UTC (07:00 IST), ahead of NSE's 9:15 IST open. Unlike the SME workflow, this one
doesn't run the pipeline itself on GitHub's runners — the picks cache is a local file on the
backend host, not Postgres, so a GitHub-hosted run would compute picks nobody's live site would
ever see. Instead it calls `GET /api/market-picks?force=true` on your *already-deployed* backend,
exactly like a user clicking "Fresh scan." Add a `MARKET_PICKS_API_URL` repository secret
(Settings > Secrets and variables > Actions) set to your backend's public URL (e.g.
`https://api.yourapp.com`) before this workflow can run.

## Watchlist

Requires `DATABASE_URL` in `.env` and a running PostgreSQL — the same database used for SME
signals works fine, or a separate one. `watchlist_items` is defined in the same
`db/models.py` metadata as the SME tables, so the usual setup command creates it too:

```bash
source .venv/bin/activate
python sme_ema_pipeline.py --setup-db   # creates all tables, including watchlist_items
```

Watchlist rows are keyed by an anonymous `client_id` (a UUID generated in the browser and
stored in `localStorage`) rather than a real user account — there's no login yet, so a
watchlist doesn't follow you across browsers or devices. `GET /api/watchlist` returns 503 if
`DATABASE_URL` is unset, matching the SME signals endpoints' behavior.

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

### Watchlist star button does nothing / /watchlist page is empty

`GET/POST/DELETE /api/watchlist` return 503 when `DATABASE_URL` is not set or PostgreSQL is unreachable, same as SME signals. Set `DATABASE_URL` and create the `watchlist_items` table (see [Watchlist](#watchlist) above). If the table exists but items still don't show up, check that the browser's `localStorage` still has an `alphapulse_client_id` entry — clearing site data resets it to a new anonymous ID with an empty watchlist.

### LLM provider outage or rate limit mid-analysis

The analyst step never hangs or crashes an analysis run — `run_analysis_with_fallback()` in `crew.py` degrades gracefully:

- **Rate limit** (429 or similar from the provider): retried once after a computed backoff. If it's still rate-limited on the retry, or if any other exception occurs (connection refused, provider 5xx, invalid/expired API key, timeout), the analyst step returns a labeled fallback immediately — no further retries.
- **Guardrail validation failure** (the model returned malformed or ungrounded JSON): one corrective retry with the validation error appended to the prompt, then the same fallback if it still fails.

The fallback report is a `HOLD` recommendation with `LOW` confidence and a summary explicitly stating structured analysis was unavailable — the underlying market data (price, fundamentals, news, etc.) is still fetched and shown normally, only the LLM-generated verdict is degraded. To confirm this happened rather than a genuine bearish HOLD call, check the server logs for an `analyst_llm_failed` event (set `LOG_LEVEL=DEBUG` for full detail) — its `failure_stage` field is `"exception"` (provider/network issue) or `"guardrail"` (formatting issue after a retry). Once the provider recovers, force a fresh analysis with `?force=true` on `/api/analyse/{symbol}` (subject to the 20-req/5-min rate limit) rather than waiting for the 24 h cache TTL.

### SME pipeline dies mid-run from a lost Postgres connection

`sme_ema_pipeline.py` isn't fully atomic across a whole run — `_upsert_stocks` is one transaction for the entire stock list (an early connection loss there rolls back cleanly and nothing is written), but `_upsert_signals` commits in 500-row batches, each its own transaction. A connection loss partway through signal writes means the batches that already committed stay committed, and the run then crashes with a traceback — so `ema_signals` can be left with a mix of freshly-updated rows and stale rows from a previous run for that same day.

This is always safe to recover from by just re-running the pipeline (`python sme_ema_pipeline.py`, or trigger `.github/workflows/sme-cron.yml` manually) once the database is reachable again — every upsert is idempotent per `(symbol, trade_date)` via `ON CONFLICT ... DO UPDATE`, so a re-run reconciles any partial state without manual cleanup. To check whether a given day's data is actually complete, compare `total_monitored` from `GET /api/sme-signals` against the row count you'd expect, or check the GitHub Actions run log if using the scheduled workflow.

### Next.js build fails on Google Fonts

`npm run build` fetches `Inter` and `JetBrains Mono` from `fonts.googleapis.com`. To build offline, replace the font imports in `frontend/app/layout.tsx` with local font files.
