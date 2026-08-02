# Setup & Configuration

## What you need

- Python 3.13
- Node.js 18+
- `npm`
- Internet access (market/news data; Google Fonts during `npm run build`)
- One LLM provider configured (see below)
- PostgreSQL (optional — required for SME Signals, the custom Screener, the Watchlist,
  Positions/Portfolio, account/magic-link auth, and API keys; the core stock-analysis and
  Market Picks flows work without it)
- Redis (optional — only matters once you run more than one backend worker/replica; see
  [Deployment](deployment.md#scaling-read-this-before-adding-workers-or-replicas))

## Backend setup

From the repo root:

```bash
/opt/homebrew/bin/python3.13 -m venv .venv   # venv lives at the repo root, shared by the whole backend
source .venv/bin/activate
cd backend
pip install -r requirements.txt
cd ..
cp .env.example .env   # .env stays at the repo root — shared by both stacks
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
| `LLM_PROVIDER` | No | `anthropic` / `openai` / `groq` / `google` / `openrouter` / `ollama` — auto-detected if unset. If set explicitly, it also **disables cross-provider failover** even when a second provider's key is present (see `backend/CLAUDE.md`'s "LLM cost instrumentation + cross-provider failover") — an explicit pin is treated as deliberate, not incidental |
| `ANALYST_MODEL` | No | Model for the analyst step (stock analysis) and market picks' extraction/analysis LLM calls — the only model-selection env var that does anything (data fetching never calls an LLM) |
| `OLLAMA_BASE_URL` | No — **currently a no-op** | Present in `.env.example`, but **no backend code reads it**. litellm's Ollama provider reads `OLLAMA_API_BASE`, not this name, so setting it has no effect today; a non-default Ollama host needs `OLLAMA_API_BASE` instead. Flagged rather than silently removed — verify against your litellm version before relying on either |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` — default: `INFO` |
| `LLM_CONCURRENCY_LIMIT` | No | Max concurrent analyst/market-picks LLM pipelines across all callers — default `4` (`api.py`). Shared across workers only when `REDIS_URL` is set; per-worker otherwise |
| `EXECUTOR_MAX_WORKERS` | No | Size of the shared `ThreadPoolExecutor` backing every blocking call in the async request path — default `16` (`api.py`). Deliberately larger than `LLM_CONCURRENCY_LIMIT` so quick requests (validate, prices) aren't starved by in-flight analyses |
| `DATABASE_URL` | SME signals, Screener, Watchlist, Positions, auth, API keys, Alembic | PostgreSQL DSN, e.g. `postgresql://user:pass@localhost:5432/sme_research` |
| `REDIS_URL` | No (only past 1 backend worker/replica) | e.g. `redis://localhost:6379/0` — shares rate-limit/concurrency guards, and `cache.py`'s scraped-data cache, across workers/hosts; see [Deployment](deployment.md#scaling-read-this-before-adding-workers-or-replicas) |
| `FRONTEND_URL` | Account/magic-link auth | Canonical frontend origin embedded in the magic-link sign-in email (`{FRONTEND_URL}/auth/verify?token=...`) — that page has to run in the browser on the frontend's own origin to receive the session cookie. Defaults to `http://localhost:3000` |
| `SMTP_HOST` | No (auth still "works" without it) | SMTP server for sending magic-link and watchlist-alert emails. **Without it, sign-in links/tokens are still created and stored — they just never get emailed** (logged as a warning server-side); `POST /api/auth/request-link` still returns `{"sent": true}` either way, so there's no way for the caller to distinguish the two cases. See "Account & magic-link auth" below |
| `SMTP_PORT` | No | Default `587` |
| `SMTP_USER` / `SMTP_PASSWORD` | No | SMTP auth — skipped if either is unset |
| `SMTP_FROM` | No | Default: `SMTP_USER`, or `noreply@alphapulse.local` |
| `SMTP_USE_TLS` | No | Default `true` — set `false` only for a local/dev relay without STARTTLS |
| `SENTRY_DSN` | No | Forwards every error-level `observability.log_event()` call to a Sentry-compatible ingest endpoint (real Sentry, self-hosted Sentry, GlitchTip, ...). `sentry-sdk` is already in `requirements.txt`; without this set, `error_tracking.py` is a complete no-op |
| `SENTRY_ENVIRONMENT` | No | Default `production` — tag attached to every event sent to Sentry when `SENTRY_DSN` is set |
| `TRUSTED_PROXY_SECRET` | No | Shared secret (same value on backend + frontend) that lets `api.py` trust a real client IP forwarded by the Next.js proxy routes for per-IP rate limiting, instead of always seeing the Next.js server's own IP. Only matters once a reverse proxy/CDN sits in front of the frontend in production — see [Deployment](deployment.md) |
| `ALLOWED_ORIGINS` | No | Comma-separated list of origins allowed to call the backend directly (CORS). Defaults to `http://localhost:3000`. Only matters for genuine cross-origin browser calls — the Next.js proxy routes talk to the backend server-to-server, which CORS doesn't apply to |

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

Create `frontend/.env.local` only when overriding the defaults:

```env
API_URL=http://localhost:8000
# TRUSTED_PROXY_SECRET=              # same value as the backend's, only needed behind a reverse proxy
```

`API_URL` defaults to `http://localhost:8000`. `TRUSTED_PROXY_SECRET` is server-only (never
exposed to the browser) and, like the backend's copy, is optional — see the table above.

## Running locally

**Terminal A — backend:**

```bash
source .venv/bin/activate
cd backend
uvicorn api:app --reload --port 8000
```

The backend exposes **57 routes** — 29 declared in `api.py`, 28 across the three routers in
`routes/` (17 Portfolio Aggregator, 6 Positions, 5 Watchlist). The table below groups them by
feature area rather than listing every one; see `backend/CLAUDE.md`'s "Agent Orchestration"
section for the full narrative on each flow.

| Area | Endpoint(s) | Notes |
|---|---|---|
| Core analysis | `GET /api/validate/{symbol}`, `GET /api/analyse/{symbol}` | Symbol validation; the main SSE analysis stream |
| Prices | `GET /api/prices`, `GET /api/prices/history/{symbol}` | Live quotes; sparkline history (`?benchmark=true` for vs.-Nifty alpha) |
| Peers & valuation | `GET /api/peers/{symbol}`, `GET /api/financials/{symbol}` | Peer comparison + absolute P/E-history anchor; multi-year statements, concalls, and DCF estimate |
| Shareholding | `GET /api/shareholding-detail/{symbol}` | Named promoters + every other named-shareholder category from NSE's shareholding XBRL filing |
| Activity & consensus | `GET /api/insider-activity/{symbol}`, `GET /api/street-consensus/{symbol}` | Promoter/director trades + bulk/block deals; Trendlyne-cited articles + numeric consensus |
| Verdict history | `GET /api/verdict-history/{symbol}` | Daily verdict/price snapshots + win-rate scoring |
| Market picks | `GET /api/market-picks`, `GET /api/market-picks/status`, `GET /api/market-picks/history` | Weekly picks SSE stream (`?force=true`); cache metadata; per-symbol/per-day track record |
| SME signals | `GET /api/sme-signals`, `GET /api/sme-signals/{symbol}/history`, `POST /api/sme-signals/refresh` | Golden/death cross screener (PostgreSQL-backed) |
| Screener | `GET /api/screener`, `POST /api/screener/refresh` | NIFTY 500 custom screener (PostgreSQL-backed) |
| Watchlist | `GET/POST /api/watchlist`, `DELETE /api/watchlist/{symbol}`, `GET /api/watchlist/calendar`, `POST /api/watchlist/claim` | Cross-mode watchlist; corporate-action calendar roll-up; claim-anonymous-rows-onto-account |
| Positions | `GET/POST /api/positions`, `PATCH/DELETE /api/positions/{symbol}`, `POST /api/positions/claim`, `GET /api/portfolio/concentration` | "I bought this" tracking, same ownership shape as Watchlist; the concentration check lives in `routes/positions.py` despite its `/api/portfolio` prefix |
| Portfolio Aggregator | `GET/POST /api/portfolio/profiles`, `GET/POST/PATCH/DELETE /api/portfolio/accounts[/{id}]`, `.../assets[/{id}]`, `POST .../assets/{id}/valuations`, `GET .../networth`, `POST .../refresh-valuations`, `GET .../xirr`, `POST .../import-cas`, `POST .../import-csv[/preview]` | 17 routes in `routes/portfolio_aggregator.py` — the separate net-worth tracker, no auth. See "Portfolio Aggregator" below |
| Consolidated | `GET /api/consolidated/{symbol}` | Pure aggregation of the three modes' caches — no new fetching |
| Auth | `POST /api/auth/request-link`, `GET /api/auth/verify`, `GET /api/auth/me`, `POST /api/auth/logout` | Magic-link account system |
| API keys | `GET/POST /api/api-keys`, `DELETE /api/api-keys/{id}`, `GET /api/v1/consolidated/{symbol}` | Key management + usage dashboard; the one `/api/v1/*` externally-callable route |
| Misc | `GET /health`, `GET /` | Liveness / root |

**Terminal B — frontend:**

```bash
cd frontend
npm run dev
```

- Stock analysis: [http://localhost:3000](http://localhost:3000)
- Compare two stocks: [http://localhost:3000/compare?symbols=TCS,INFY](http://localhost:3000/compare?symbols=TCS,INFY)
- Market picks: [http://localhost:3000/market-picks](http://localhost:3000/market-picks)
- Market picks track record: [http://localhost:3000/market-picks/history](http://localhost:3000/market-picks/history)
- Portfolio (aggregate "I bought this" view): [http://localhost:3000/portfolio](http://localhost:3000/portfolio)
- Portfolio Aggregator (separate net-worth tracker — profiles/accounts/assets, CAS/CSV import,
  XIRR): [http://localhost:3000/portfolio-aggregator](http://localhost:3000/portfolio-aggregator)
- SME signals: [http://localhost:3000/sme-signals](http://localhost:3000/sme-signals)
- Screener (NIFTY 500): [http://localhost:3000/screener](http://localhost:3000/screener)
- Watchlist: [http://localhost:3000/watchlist](http://localhost:3000/watchlist)
- Sign in (magic link): [http://localhost:3000/login](http://localhost:3000/login)
- API keys + usage: [http://localhost:3000/api-keys](http://localhost:3000/api-keys)
- Pricing (informational only — no checkout): [http://localhost:3000/pricing](http://localhost:3000/pricing)

## CLI mode

```bash
source .venv/bin/activate
cd backend
python main.py TCS
python main.py RELIANCE --force   # bypass cache
```

## Database schema setup (Alembic)

Every PostgreSQL-backed feature (SME Signals, Screener, Watchlist, Positions, auth, API keys, the
EOD price store, corporate actions, and the Portfolio Aggregator — 22 tables total) shares one
SQLAlchemy Core `MetaData()` object (`db/models.py`). Schema changes
are now managed through **Alembic** rather than ad-hoc `create_all()` calls or hand-edited
`ALTER TABLE` statements in `db/schema.sql` (see backend/CLAUDE.md's "Schema migrations" section for the
full story — `db/schema.sql` is kept only as a frozen pre-Alembic reference).

There are two paths, depending on whether the database already has these tables:

**Fresh database (nothing created yet):**

```bash
source .venv/bin/activate
cd backend
createdb sme_research   # or whatever DATABASE_URL points at
alembic upgrade head
```

This runs all revisions in order — `0001_baseline_schema.py` (the original 11 tables),
`684c8a31e7e0_add_eod_price_store_and_corporate_.py` (EOD price store + corporate actions, 4
tables), `8613aafc2d9d_add_portfolio_aggregator_foundation_.py` (Portfolio Aggregator, 6
tables), and `a7f2c1d09b34_add_app_state_durable_json_state.py` (`app_state`, 1 table) — creating
all 22 tables, indexes, and constraints from scratch.

**Existing deployment with only the original 11 tables** (created by hand via `db/schema.sql`, or
via one of the pipelines' `--setup-db` flags before Alembic existed — i.e. predates the EOD price
store / corporate actions / Portfolio Aggregator tables):

```bash
cd backend
alembic stamp 0001
alembic upgrade head
```

The revision identifier is `0001`, not the filename stem `0001_baseline_schema` — Alembic
resolves by revision id and will fail with "Can't locate revision" on the longer form.

Stamp `0001` specifically here, **not** `alembic stamp head` — stamping straight to `head` would
mark the database as already having the EOD/portfolio tables too, when it doesn't, and Alembic
would then never create them. Stamping `0001` records "already at the baseline revision" without
executing DDL for those 11 tables (a plain `upgrade head` would fail — `0001`'s `CREATE TABLE`
statements collide with tables that already exist), then the subsequent `upgrade head` applies the
two later revisions normally, creating the 10 new tables for real.

**Existing deployment already fully caught up** (already ran the two commands above, or was
created after this session's work landed): nothing to do — already at `head`.

**From here on**, schema changes should be authored as new Alembic revisions:

```bash
cd backend
# after editing db/models.py
alembic revision --autogenerate -m "add some_column to some_table"
alembic upgrade head
```

**The `--setup-db`/`--reset-db` flags on `sme_ema_pipeline.py` and `screener_pipeline.py` call
`db.models.stamp_alembic_head()` automatically** right after their own `metadata.create_all()`,
so a database provisioned that way still gets a correct `alembic_version` row and a later
`alembic upgrade head` won't fail trying to re-create existing tables.

One caveat: that auto-stamp goes to the current `head`, including the EOD/portfolio revisions —
so if you use a bare `--setup-db` as your only setup step, run `alembic upgrade head` yourself
afterwards if you also need the EOD price store / Portfolio Aggregator tables. The
`alembic stamp 0001` + `alembic upgrade head` pair above is only for a database that predates
Alembic entirely and hasn't been touched by a pipeline's `--setup-db`/`--reset-db` since.

## SME signals pipeline

Requires `DATABASE_URL` in `.env` and a running PostgreSQL. Create the database once (`createdb
sme_research`), then either run `alembic upgrade head` (see above) or use the pipeline's own
`--setup-db` flag, which now also stamps Alembic head for you:

```bash
source .venv/bin/activate
cd backend
python sme_ema_pipeline.py --setup-db   # create tables (idempotent) + stamp alembic head
python sme_ema_pipeline.py              # fetch SME stocks, compute EMA20/EMA50 crosses, store
python sme_ema_pipeline.py --reset-db   # drop + recreate tables (after schema changes; data is regenerable)
python sme_ema_pipeline.py --force      # bypass the 24 h stock-list cache
python sme_ema_pipeline.py --lookback 10  # report window for the CLI summary
```

**`--reset-db` is scoped to this pipeline's own two tables** (`ema_signals`, `sme_stocks`) — it
does not touch the shared `MetaData()` as a whole. This used to be a real footgun (an early
version called `metadata.drop_all()` and wiped every table, including Watchlist/Positions/account
data); see backend/CLAUDE.md's "SME golden cross flow" section for the fix and the scoping
convention it established for every pipeline after it (`screener_pipeline.py`,
`eod_prices_pipeline.py`, `corporate_actions_pipeline.py`).

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

```cron
30 18 * * 1-5 cd /path/to/stock-research/backend && ../.venv/bin/python sme_ema_pipeline.py >> output/sme_cron.log 2>&1
```

## Market picks pipeline

No separate setup — it runs against whichever LLM provider key is already configured. Trigger a
run from the CLI, or via the **Fresh scan** / **See This Week's Picks** buttons on `/market-picks`:

```bash
source .venv/bin/activate
cd backend
python market_picks_pipeline.py
```

This saves straight to `backend/output/_market_picks/picks.json`, bypassing `api.py`'s SSE endpoint —
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

## Custom screener pipeline (NIFTY 500)

Same shape as the SME pipeline above, but over the NIFTY 500 universe instead of SME/Emerge
stocks — see backend/CLAUDE.md's "Custom screener flow" section. Requires `DATABASE_URL`:

```bash
source .venv/bin/activate
cd backend
python screener_pipeline.py --setup-db   # create the screener_stocks table + stamp alembic head
python screener_pipeline.py              # fetch NIFTY 500 constituents, quote + technical signal each, store
python screener_pipeline.py --reset-db   # drop + recreate ONLY screener_stocks (scoped, unlike sme_ema_pipeline.py's --reset-db)
python screener_pipeline.py --force      # bypass the 24 h constituent-list cache
```

The `/screener` page's **Refresh Data** button triggers the same pipeline via
`POST /api/screener/refresh` (same lock-then-rate-limit pattern as the SME refresh endpoint).

Daily automation runs via `.github/workflows/screener-cron.yml` — weekdays at 14:00 UTC
(19:30 IST), deliberately after `sme-cron.yml` (13:00 UTC) and 30 minutes after
`watchlist-alerts-cron.yml` (13:30 UTC) so the independent jobs don't contend for the same DB
connection pool at once. Same `DATABASE_URL`-secret-required, fail-fast pattern as `sme-cron.yml`.

## Watchlist & Positions

Requires `DATABASE_URL` in `.env` and a running PostgreSQL — the same database used for SME
signals/Screener works fine. `watchlist_items` and `positions` are both defined in the same
`db/models.py` metadata as every other table, so `alembic upgrade head` (fresh DB) or the
stamp-then-upgrade pair (existing DB) — see "Database schema setup" above — creates/recognizes both.

Each row is owned by either an anonymous per-browser `client_id` (a UUID generated client-side
and stored in `localStorage`) or, once a user signs in via the magic-link auth system below, the
account's `user_id`. Signing in does **not** automatically migrate existing `client_id` rows onto
the account — there's an explicit opt-in "claim my data" prompt on the `/auth/verify` page
instead (`POST /api/watchlist/claim` / `POST /api/positions/claim`). See backend/CLAUDE.md's "Watchlist
flow" section for the full identity-resolution story.

`GET /api/watchlist` / `GET /api/positions` return 503 if `DATABASE_URL` is unset or Postgres is
unreachable. `GET /api/portfolio/concentration` (also in `routes/positions.py`) flags a Market
Picks recommendation's sector when your tracked positions are already ≥25% concentrated in it —
same `DATABASE_URL` requirement, no separate setup.

## EOD price store + corporate actions pipeline

Requires `DATABASE_URL`. Ingests NSE's daily bhavcopy (OHLCV + delivery %) and AMFI mutual-fund
NAVs into `securities`/`prices_daily`/`mf_nav_daily`, then a second step ingests NSE corporate
actions (splits/bonuses/dividends) into `corporate_actions` and recomputes `prices_daily.adj_close`.
See backend/CLAUDE.md's "EOD price store + corporate actions flow" section for the full design.

```bash
source .venv/bin/activate
cd backend
python eod_prices_pipeline.py --setup-db     # create securities/prices_daily/mf_nav_daily
python eod_prices_pipeline.py                # self-healing: ingests any missing day in the last 5
python eod_prices_pipeline.py --date 2026-08-01
python eod_prices_pipeline.py --backfill 2024-08-01   # loop from that date to today

python corporate_actions_pipeline.py --setup-db   # create corporate_actions
python corporate_actions_pipeline.py --backfill 2024-08-01
python corporate_actions_pipeline.py --recompute-all   # rebuild adj_close for every symbol
```

`eod_prices_pipeline.py --setup-db`/`--reset-db` and `corporate_actions_pipeline.py
--setup-db`/`--reset-db` are each scoped to only their own tables, not the shared `MetaData()` —
same scoping discipline as `screener_pipeline.py --reset-db`.

Daily automation runs via `.github/workflows/eod-prices-cron.yml` — weekdays at 14:15 UTC
(19:45 IST), after the bhavcopy's ~19:00 IST publish and after `sme-cron.yml`. Same
`DATABASE_URL`-secret-required, fail-fast pattern as the other cron workflows. This same cron run
also triggers the Portfolio Aggregator's nightly valuation refresh (below) as an isolated final
step — no separate schedule needed for that.

## Portfolio Aggregator

A **separate** personal net-worth tracker at `/portfolio-aggregator` — not the same feature as the
`/portfolio` "I bought this" P&L page above; don't confuse the two when troubleshooting. No auth,
no `client_id` — a bare profile picker (deliberate, personal-scale-tool decision). Requires
`DATABASE_URL` for `profiles`/`accounts`/`assets`/`holdings`/`valuations`/`transactions` (created
by the same Alembic step as everything else above).

- Manual net-worth tracking works with no further setup: create a profile → account → assets on
  `/portfolio-aggregator`.
- **Auto-valuation** (`portfolio_valuation.py`) needs the EOD price store above populated —
  `mf`/`stock` assets are valued from `prices_daily`/`mf_nav_daily`; runs nightly as part of the
  EOD cron, or on-demand via the "Refresh valuations" button (`POST
  /api/portfolio/refresh-valuations`).
- **XIRR** (`GET /api/portfolio/xirr?profile_id=`) is `null` for every asset until real
  transaction history exists — either import path below populates `transactions`.
- **CAS import** (`POST /api/portfolio/import-cas`) — upload a CAMS/KFintech detailed CAS PDF +
  its password on `/portfolio-aggregator`. New dependency `casparser` (already in
  `requirements.txt`, pulls `pdfminer.six`). Parsed statements are archived (PII-scrubbed) under
  the `cas_archive` namespace in `app_state`, keyed by timestamp, for replay: from `backend/`,
  `python cas_import.py --replay <key> --account-id N`.
- **Broker CSV import** (`POST /api/portfolio/import-csv/preview` then `.../import-csv`) — upload
  a broker trade export (Zerodha tradebook auto-detected; any other broker via a column-mapping
  UI). New dependency `openpyxl` (already in `requirements.txt`, for `.xlsx` files — `pandas`
  already a dependency). New-asset broker codes are resolved to canonical NSE/BSE symbols via
  `tools/securities_master.py::resolve_symbol()`.

## Account & magic-link auth

A minimal, passwordless account system — no OAuth, no separate signup step; the first successful
magic-link click *is* account creation. Requires `DATABASE_URL` (the `users`/`magic_links`/
`sessions` tables, created by the same Alembic step as everything else above).

```env
# .env
FRONTEND_URL=http://localhost:3000   # default — only change if the frontend runs elsewhere

# Optional: without these, sign-in still "works" but no email is ever sent (see below)
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=apikey-or-username
SMTP_PASSWORD=...
SMTP_FROM=noreply@yourdomain.com
SMTP_USE_TLS=true
```

**`SMTP_HOST` is not required for sign-in requests to succeed** — `POST /api/auth/request-link`
always returns `{"sent": true}`, and the single-use token is always created and stored in the
`magic_links` table, regardless of whether SMTP is configured. Without `SMTP_HOST` set, the email
itself simply never goes out (logged as a warning server-side); there's no way for the caller to
tell the two cases apart from the response alone. See "Sign-in link never arrives" under
Troubleshooting below for how to work around this locally.

`FRONTEND_URL` matters because the magic link points at `{FRONTEND_URL}/auth/verify?token=...` —
that page has to load in the browser on the frontend's own origin to set the session cookie, so
it can't point directly at the FastAPI backend. The default (`http://localhost:3000`) is correct
for local dev; set it to your real frontend origin in production.

Try it locally: `POST http://localhost:8000/api/auth/request-link` with `{"email": "you@example.com"}`,
then check the backend log for the generated link (or your inbox, if SMTP is configured) and open
it — or drive the whole flow through `/login` in the browser.

## API keys & tiers

Signed-in users can mint long-lived API keys (`/api-keys` page) for scripting against the one
externally-callable route, `GET /api/v1/consolidated/{symbol}` (auth via an `X-API-Key` header,
not the internal `Authorization: Bearer` session convention). Each account has a `tier`
(`'free'` or `'pro'`) that gates the per-hour call limit (`_TIER_LIMITS` in `api.py`: 100/hour
free, 1000/hour pro).

**There is no real payment processing** — `/pricing` is purely informational, and nothing in this
codebase ever sets a `users.tier` row to `'pro'` automatically. If you need `'pro'` tier locally
or in a real deployment, set it by hand:

```sql
UPDATE users SET tier = 'pro' WHERE email = 'you@example.com';
```

This is a deliberate scope call, not an oversight — see backend/CLAUDE.md's "Explicitly out of scope"
section (point 3) for why. Don't spend time looking for a checkout flow; there isn't one yet.

## Cache and output

Per-symbol caches under `backend/output/<SYMBOL>/` with these TTLs (`cache.TTL_HOURS`):

| Task | TTL |
|---|---|
| `stock_info` | 1 hour |
| `news` | 1 hour |
| `filings` | 1 hour (not in `TTL_HOURS` — falls through to the 1 h default) |
| `research` | 24 hours |
| `analysis` | 24 hours |
| `shareholding` | 7 days |
| `mf_holdings` | 7 days |
| `shareholding_detail` | 7 days (standalone — same quarterly NSE XBRL filing as `mf_holdings`) |
| `peers`, `financials`, `insider_activity`, `street_consensus` | 24 hours (standalone, outside the six core tasks) |
| `price_history` | 6 hours |

`fii_dii_flow` and `macro_context` (24 h each) are cached under a fixed `"_MACRO"` pseudo-symbol
and `index_history` (24 h) under `"NSEI"` — market-wide, not under any real ticker's folder.

Market picks-specific caches:

| Path | TTL | Description |
|---|---|---|
| `backend/output/_market_picks/picks.json` | 7 days (192 h) | Full pipeline result |
| `backend/output/_extract_cache/` | 6 hours | Per-source LLM extraction |
| `backend/output/_nse_master.txt` | 24 hours | NSE equity symbol list |

Everything durable that used to live beside these — daily pick snapshots, LLM cost counters,
per-source health, scraper error counters, source-quality telemetry — is now in PostgreSQL, in
the `app_state` table under one namespace each. `backend/output/` holds only regenerable cache.
See [Database](database.md).

When `REDIS_URL` is set, `cache.py` writes through to Redis in addition to local disk — see
[Deployment](deployment.md#scaling-read-this-before-adding-workers-or-replicas).

## Useful commands

```bash
# Backend
source .venv/bin/activate
cd backend
python main.py INFY
uvicorn api:app --reload --port 8000

# SME signals pipeline
python sme_ema_pipeline.py

# Custom screener pipeline
python screener_pipeline.py

# EOD price store + corporate actions
python eod_prices_pipeline.py
python corporate_actions_pipeline.py --recompute-all

# Portfolio Aggregator valuation refresh (also runs nightly via the EOD cron)
python portfolio_valuation.py

# Watchlist alert emails (batch job, normally run daily via cron/GitHub Actions)
python watchlist_alerts.py
python watchlist_alerts.py --force   # bypass cache freshness

# Schema migrations
alembic upgrade head                        # fresh database
alembic stamp 0001 && alembic upgrade head  # existing DB with only the original 11 tables
alembic revision --autogenerate -m "..."    # after editing db/models.py

# Backend tests (1502 tests, no live network calls)
python -m pytest tests/

# Frontend
cd ../frontend
npm run dev
npm run build
npm run start
npx tsc --noEmit    # type-check (no ESLint config exists)
npm run test:e2e    # Playwright E2E — 44 tests across 8 spec files, every backend response mocked
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

# Or for Ollama (defaults to http://localhost:11434; see the OLLAMA_BASE_URL
# note in the env-var table above before pointing at a non-default host)
LLM_PROVIDER=ollama
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

The pipeline fails open when `backend/output/_nse_master.txt` cannot be downloaded — all tickers are allowed through. If validation is too permissive, delete the stale cache file and ensure `nsearchives.nseindia.com` is reachable.

### SME signals / Screener page returns 503

`GET /api/sme-signals` and `GET /api/screener` both return 503 when `DATABASE_URL` is not set or
PostgreSQL is unreachable. Set `DATABASE_URL` in `.env`, make sure the database exists and has
the tables (`alembic upgrade head`, or a pipeline's own `--setup-db` — see "Database schema
setup" above), then run the relevant pipeline once so the tables have data.

### Watchlist star button does nothing / /watchlist page is empty

`GET/POST/DELETE /api/watchlist` return 503 when `DATABASE_URL` is not set or PostgreSQL is unreachable, same as SME signals. Set `DATABASE_URL` and make sure the tables exist (see [Watchlist & Positions](#watchlist--positions) above). If the table exists but items still don't show up, check that the browser's `localStorage` still has an `alphapulse_client_id` entry — clearing site data resets it to a new anonymous ID with an empty watchlist.

### Sign-in link never arrives

`POST /api/auth/request-link` returns `{"sent": true}` even when `SMTP_HOST` is unset — the
magic-link token is still created and stored either way, so the response alone can't tell you
whether an email actually went out. If you don't receive anything:

- Check the backend log for a warning about SMTP not being configured (no `SMTP_HOST` set), or an
  actual send failure if it is set (bad credentials, wrong port, a provider blocking the connection).
- For local development, the simplest fix is to just read the generated token straight out of the
  `magic_links` table (`SELECT token_hash, expires_at FROM magic_links ORDER BY created_at DESC
  LIMIT 1` won't give you the raw token — it's only ever hashed at rest — so in practice, set
  `SMTP_HOST` to a local dev mail catcher like MailHog/Mailpit, or temporarily add a debug log
  line where `auth.create_magic_link()` builds the link, rather than trying to recover it from the DB).
- A token is single-use and expires after 15 minutes — request a fresh link rather than retrying
  a stale one.

### `alembic upgrade head` fails with "relation already exists"

This means the database already has these tables (created by hand via `db/schema.sql`, or by a
pipeline's `--setup-db` before it auto-stamped Alembic — see "Database schema setup" above). Which
stamp you need depends on **which** tables it already has:

- **Only the original 11 tables** (i.e. it predates the EOD price store / Portfolio Aggregator):
  ```bash
  cd backend
  alembic stamp 0001 && alembic upgrade head
  ```
  Stamp the baseline revision specifically, **not** `alembic stamp head`. There are three
  revisions now, and a bare `stamp head` marks the two later ones as applied when they aren't —
  Alembic then never creates the 10 tables they add (`securities`, `prices_daily`,
  `mf_nav_daily`, `corporate_actions`, `profiles`, `accounts`, `assets`, `holdings`,
  `valuations`, `transactions`), and the EOD price store and Portfolio Aggregator fail at
  runtime with nothing having gone wrong at migration time.
- **All 22 tables already present** (fully caught up — e.g. created by a recent pipeline
  `--setup-db`, which auto-stamps): `alembic stamp head` is the correct command here, and the
  only case where it is.

Not sure which you have? `\dt` in `psql` and count them, or run `alembic current` to see whether
the database is stamped at all. If you genuinely want a clean slate, drop the tables first (see
the `--reset-db` caveats above about scope) and re-run `alembic upgrade head`.

### LLM provider outage or rate limit mid-analysis

The analyst step never hangs or crashes an analysis run — `run_analysis_with_fallback()` in `crew.py` degrades gracefully:

- **Rate limit** (429 or similar from the provider): retried once after a computed backoff. If it's still rate-limited on the retry, or if any other exception occurs (connection refused, provider 5xx, invalid/expired API key, timeout), the analyst step returns a labeled fallback immediately — no further retries.
- **Guardrail validation failure** (the model returned malformed or ungrounded JSON): one corrective retry with the validation error appended to the prompt, then the same fallback if it still fails.

The fallback report is a `HOLD` recommendation with `LOW` confidence and a summary explicitly stating structured analysis was unavailable — the underlying market data (price, fundamentals, news, etc.) is still fetched and shown normally, only the LLM-generated verdict is degraded. `Report.degraded` is `true` in this case, rendered as a visible banner in the UI (see backend/CLAUDE.md's "LLM cost instrumentation + cross-provider failover" section, point 3), so this no longer requires grepping logs to notice from the frontend. To confirm which failure mode happened, check the server logs for an `analyst_llm_failed` event (set `LOG_LEVEL=DEBUG` for full detail) — its `failure_stage` field is `"exception"` (provider/network issue) or `"guardrail"` (formatting issue after a retry). Once the provider recovers, force a fresh analysis with `?force=true` on `/api/analyse/{symbol}` (subject to the 20-req/5-min rate limit) rather than waiting for the 24 h cache TTL.

### SME / Screener pipeline dies mid-run from a lost Postgres connection

Neither `sme_ema_pipeline.py` nor `screener_pipeline.py` is fully atomic across a whole run — the
initial stock/constituent-list upsert is one transaction (an early connection loss there rolls
back cleanly and nothing is written), but per-stock signal/metric writes commit in batches, each
its own transaction. A connection loss partway through means the batches that already committed
stay committed, and the run then crashes with a traceback — so the stored table can be left with
a mix of freshly-updated rows and stale rows from a previous run for that same day.

This is always safe to recover from by just re-running the pipeline once the database is
reachable again — every upsert is idempotent per `(symbol, trade_date)` (SME) or per `symbol`
(Screener) via `ON CONFLICT ... DO UPDATE`, so a re-run reconciles any partial state without
manual cleanup. To check whether a given day's SME data is actually complete, compare
`total_monitored` from `GET /api/sme-signals` against the row count you'd expect, or check the
GitHub Actions run log if using the scheduled workflow.

### Next.js build fails on Google Fonts

`npm run build` fetches `Inter` and `JetBrains Mono` from `fonts.googleapis.com`. To build offline, replace the font imports in `frontend/app/layout.tsx` with local font files.
