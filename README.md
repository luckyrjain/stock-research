# Stock Research

[![CI](https://github.com/luckyrjain/stock-research/actions/workflows/ci.yml/badge.svg)](https://github.com/luckyrjain/stock-research/actions/workflows/ci.yml)

Stock Research (AlphaPulse) is a full-stack Indian equity research platform built around four
research modes, a cross-mode watchlist/positions layer, and a minimal account system:

- **Stock Analysis** — given a symbol like `TCS` or `RELIANCE`, validates the ticker across NSE
  autocomplete, BSE, and Screener.in, fetches price/fundamentals/news/shareholding/MF-holdings/
  filings in parallel, runs a quantitative signal engine (valuation, growth, volume, filings,
  technical RSI/EMA, and a macro FII/DII + RBI overlay), and calls an LLM analyst for a structured
  `BUY` / `HOLD` / `SELL` recommendation. Progress and the final report stream to the browser over
  Server-Sent Events. The report is further enriched with peer comparison, an absolute
  valuation-anchor badge, a DCF estimate, multi-year financial statements, concalls, insider &
  institutional activity, Trendlyne street consensus, MF-holdings stake deltas, filings
  classification, and a verdict-history timeline with win-rate scoring.
- **Market Picks** — a multi-agent pipeline that scrapes 20 Indian and global financial sources,
  extracts stock recommendations with an LLM, validates symbols against the NSE equity master, runs
  due diligence on each, and returns a confidence-ranked, sector-balanced watchlist
  (`BUY` / `WATCHLIST` / `HOLD` / `SELL`) with deterministic entry/target/stop-loss levels and a
  weekly auto-refresh.
- **SME Signals** — a PostgreSQL-backed batch pipeline screening every NSE Emerge + BSE SME stock
  for EMA20/EMA50 golden-cross / death-cross events, with RSI(14), volume-spike, liquidity, and
  aggregate hit-rate stats.
- **Screener** — a PostgreSQL-backed batch pipeline over the NIFTY 500 universe, filterable/sortable
  by industry, P/E, market cap, and RSI/EMA trend.
- **Watchlist** — a PostgreSQL-backed cross-mode watchlist (star a stock from any of the three
  screeners above); a **Portfolio** page tracks "I bought this" positions with entry/target/
  stop-loss and aggregate P&L, plus a sector-concentration badge on Market Picks rows when a
  pick's sector is already over-represented in your tracked positions. Both are owned by an
  anonymous per-browser `client_id` or, once signed in, an account — with an opt-in "claim my
  data" flow to move anonymous rows onto an account after sign-in. A daily batch job emails
  signed-in users when a watched stock's recommendation changes or its price moves sharply.
- **Portfolio Aggregator** — a separate, unauthenticated personal net-worth tracker (distinct
  from the "I bought this" Portfolio page above): profiles → accounts → assets (stocks, mutual
  funds, FDs, EPF/PPF, cash, loans), valued nightly from a PostgreSQL EOD price store (NSE
  bhavcopy + AMFI NAV, with a corporate-actions/adjusted-price pipeline) and an XIRR engine that
  lights up once real transaction history exists — imported from a CAMS/KFintech CAS PDF
  statement or a broker trade CSV (Zerodha preset), reconciled against a securities-master symbol
  resolver. Lives at `/portfolio-aggregator`.
- **Compare** — two full stock-analysis reports side by side, with a head-to-head diff table.
- **Consolidated search** — a shared search box in every page's nav bar that aggregates whatever
  the other modes have already cached/computed for a symbol, with zero new fetching or LLM calls.
- **Accounts & API access** — passwordless magic-link sign-in, plus long-lived API keys with
  tiered (free/pro) rate limiting for a small external `/api/v1/*` surface, and an informational
  pricing page (no real payment processing exists yet — see `backend/CLAUDE.md`).

## Tech stack

- **Backend**: Python 3.13, FastAPI (SSE streaming), the `crewai.tools` `@tool` decorator (no
  agent orchestration — see `backend/CLAUDE.md`), litellm, a custom quantitative signals engine
- **Frontend**: Next.js 15 (App Router), React 19, TypeScript (strict), Tailwind CSS v3, installable
  as a PWA
- **Data sources**: Yahoo Finance, Screener.in, stockanalysis.com (quote fallback), Google News
  (via `gnews`), RSS feeds, NSE API/archives (incl. daily bhavcopy + corporate actions), BSE API,
  AMFI (mutual fund NAVs), Trendlyne, RBI, plus user-supplied CAS PDF statements / broker CSVs
  (parsed via `casparser` / `openpyxl`)
- **LLM providers**: Anthropic, OpenAI, Groq, Google, OpenRouter, Ollama (auto-detected from env,
  with cross-provider failover and per-call cost instrumentation)
- **Storage**: PostgreSQL is now the primary store for shared/durable state — watchlist, positions,
  accounts/sessions, API keys, verdict history, MF-holdings history, SME signals, the NIFTY 500
  screener, the EOD price store (securities/daily prices/MF NAVs + corporate actions), and the
  Portfolio Aggregator (profiles/accounts/assets/holdings/valuations/transactions) all live there,
  managed with **Alembic** migrations. A file-based TTL cache under
  `backend/output/` still backs the six per-symbol scrape tasks and a handful of standalone endpoints
  (peers, financials, insider activity, street consensus). **Redis** is optional, opt-in via
  `REDIS_URL` — it backs shared rate limiting/concurrency guards and mirrors the file cache so both
  work correctly across multiple backend workers/hosts; everything degrades gracefully to
  single-process, on-disk behavior when it's unset.
- **Observability**: structured JSON logging (`observability.py`), optional Sentry-compatible error
  tracking (`error_tracking.py`, gated by `SENTRY_DSN`), schema-drift detection, source-health
  monitoring, and scraper-error counters for the scrapers outside the main six-task path

## Project structure

`backend/` and `frontend/` are two independent stacks living side by side in one repo — no import
paths changed when the backend moved into its own directory, only the top-level layout did.

```text
stock-research/
├── backend/
│   ├── api.py                    FastAPI server — SSE endpoints, symbol validation, most /api/* routes
│   ├── main.py                   CLI entry point; also contains _fetch_task, _build_report (shared with api.py)
│   ├── crew.py                   Analyst guardrails, run_analysis_with_fallback (cross-provider failover)
│   ├── llm_cost.py                Per-call LLM cost instrumentation + running daily total
│   ├── cache.py                   File-based TTL cache (output/<SYMBOL>/<task>.json), optionally Redis-mirrored
│   ├── rate_limiter.py             Shared sliding-window rate limits / concurrency slots / locks (Redis-backed, opt-in)
│   ├── error_tracking.py           Optional Sentry-compatible error hook, wired into observability.log_event()
│   ├── schema_drift.py             Type-drift detection for the six scraped data slices
│   ├── source_health.py            Freshness/volume monitoring for market-picks sources + macro overlay
│   ├── scraper_error_counters.py   Error counters for the standalone per-symbol scrapers (peers, financials, ...)
│   ├── schemas.py                  Normalization contracts: raw tool output → canonical dicts
│   ├── peer_analytics.py           Peer-percentile + absolute valuation-anchor math
│   ├── dcf_valuation.py             Deterministic two-stage DCF estimate off cash-flow history
│   ├── auth.py                     Magic-link auth: token/session/API-key issuance + validation (PostgreSQL)
│   ├── email_sender.py              Sends magic-link sign-in + watchlist-alert emails over generic SMTP
│   ├── verdict_history.py           Daily verdict/price snapshots (PostgreSQL) — powers the verdict timeline
│   ├── mf_holdings_history.py       Quarterly MF stake snapshots (PostgreSQL) — powers stake-delta badges
│   ├── watchlist_alerts.py          Daily batch job: emails users on a watched stock's rec/price change
│   ├── market_picks_pipeline.py    Multi-agent weekly picks pipeline (6 phases)
│   ├── source_quality.py            Per-run source-quality telemetry (market picks)
│   ├── source_quality_report.py     Aggregation CLI for source_quality.py's stored runs
│   ├── sme_ema_pipeline.py          SME golden/death cross batch pipeline (PostgreSQL)
│   ├── screener_pipeline.py         NIFTY 500 custom screener batch pipeline (PostgreSQL)
│   ├── eod_prices_pipeline.py       EOD price store: NSE bhavcopy + AMFI NAV ingestion (PostgreSQL)
│   ├── corporate_actions_pipeline.py  NSE corporate actions ingestion + adj_close recompute
│   ├── portfolio_valuation.py       Portfolio Aggregator nightly valuation + XIRR engine
│   ├── cas_import.py                CAMS/KFintech CAS PDF import → transactions/holdings
│   ├── csv_import.py                Broker trade CSV import (Zerodha preset) → transactions/holdings
│   ├── db/                          SQLAlchemy Core tables (models.py) + schema.sql reference
│   ├── routes/                      Per-domain FastAPI routers extracted from api.py
│   │   ├── watchlist.py             Watchlist CRUD, calendar, claim-anonymous-rows
│   │   ├── positions.py             Positions CRUD, claim-anonymous-rows, sector-concentration
│   │   ├── portfolio_aggregator.py  Portfolio Aggregator CRUD, net worth, XIRR, CAS/CSV import
│   │   └── _shared.py                Shared rate-limit/DB/executor wrapper all routers use
│   ├── alembic.ini / migrations/    Schema migrations (SQLAlchemy Core metadata is the single source of truth)
│   ├── observability.py             Structured JSON logging via log_event()
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── config/
│   │   ├── analyst.json             Analyst role/goal/backstory + section labels
│   │   └── crew_tasks.py             Builds the analyst prompt string from analyst.json
│   ├── tools/
│   │   ├── market_picks_tools.py     RSS + GNews scrapers for 20 sources; exports SOURCES + SCRAPER_FNS
│   │   ├── sme_tools.py               NSE Emerge + BSE SME stock-list fetchers
│   │   ├── nifty500_tools.py          NIFTY 500 constituent list fetcher (screener_pipeline.py's universe)
│   │   ├── screener_tools.py          Screener.in fundamentals, peers, financial statements, concalls
│   │   ├── trendlyne_scraper.py       Trendlyne numeric consensus (rating/analyst count/target price)
│   │   ├── nse_insider_trades.py / nse_bulk_block_deals.py   Insider & institutional activity
│   │   ├── nse_fii_dii_tools.py / macro_context_tools.py     FII/DII flow + RBI rate/inflation
│   │   ├── price_history_tools.py     Shared daily-close OHLCV fetch (sparklines, technical signal)
│   │   ├── eod_sources.py              NSE bhavcopy + equity master + AMFI NAV fetch/parse
│   │   ├── corporate_actions.py        NSE corporate-actions fetch + purpose-string parser
│   │   ├── securities_master.py        NSE+BSE main-board/SME merge + resolve_symbol() fuzzy resolver
│   │   ├── _nse_session.py             Shared NSE session-priming helper used by every NSE-touching module
│   │   └── ...                         Other data-fetching functions (yfinance, gnews, NSE filings)
│   ├── signals/                     Quantitative signal engine (features → signal scores → verdict)
│   │   ├── engine.py                  Weighted blend + sector-aware weight tilts
│   │   ├── technical.py                RSI(14) + EMA20/50 posture signal
│   │   ├── macro.py                    FII/DII flow + RBI rate/inflation overlay signal
│   │   └── filings_classifier.py       Corporate actions / rating actions / next-results-date extraction
│   ├── tests/                        unittest-based tests (no live network calls)
│   ├── tests_live/                   Opt-in (RUN_LIVE_TESTS=1) live scraper contract checks, run weekly
│   └── output/                       Cache files (gitignored); also where the CLI saves report JSON
│       ├── <SYMBOL>/                 Per-symbol task caches
│       ├── _extract_cache/           LLM extraction cache (6 h TTL)
│       ├── _history/                 Daily market-picks snapshots (trend tracking, win-rate history)
│       ├── _market_picks/            Market picks result cache (7-day TTL)
│       ├── _llm_cost/                Daily running LLM spend total
│       └── _nse_master.txt           NSE equity symbol master, refreshed every 24 h
├── .env.example                  Shared by both stacks; stays at the repo root
├── docker-compose.yml
├── frontend/                     Next.js 15 app (TypeScript, Tailwind CSS)
│   ├── app/page.tsx                    Stock analysis page (supports ?symbol= deep links)
│   ├── app/market-picks/page.tsx       Weekly picks page
│   ├── app/market-picks/history/page.tsx  Pick track record (per-symbol + per-day snapshots)
│   ├── app/sme-signals/page.tsx        SME golden cross screener
│   ├── app/screener/page.tsx           NIFTY 500 custom screener
│   ├── app/watchlist/page.tsx          Cross-mode watchlist page
│   ├── app/portfolio/page.tsx          Aggregate return summary over tracked positions
│   ├── app/portfolio-aggregator/page.tsx  Personal net-worth tracker (separate from app/portfolio)
│   ├── app/compare/page.tsx            Two stock analysis reports side by side (?symbols=TCS,INFY)
│   ├── app/login/ , app/auth/verify/   Magic-link sign-in + verification
│   ├── app/api-keys/page.tsx           API key management + usage dashboard
│   ├── app/pricing/page.tsx            Informational tier/pricing page
│   ├── app/manifest.ts, app/icon.tsx, ...   PWA manifest + generated icons
│   ├── components/                     Dashboard cards, search, progress tracker, nav, PWA service worker
│   │   ├── header-search.tsx           Shared "what does AlphaPulse think about X" search box
│   │   ├── consolidated-card.tsx        Modal rendering GET /api/consolidated/{symbol}
│   │   └── results-dashboard.tsx        Main report view, composed from the per-card components above
│   ├── app/api/                        Thin Next.js proxy routes → FastAPI backend
│   ├── lib/watchlist.ts / positions.ts / auth.ts   Shared-cache hooks (DB-backed)
│   ├── e2e/                             Playwright E2E specs — every backend response is mocked
│   └── types/index.ts                   Canonical TS types for all SSE messages and reports
├── docs/                         index, setup, deployment, architecture, tools, output-schema,
│                              design, PRD, feature-catalog
└── CLAUDE.md, README.md          Root-level docs (backend/CLAUDE.md and
                               frontend/CLAUDE.md hold the per-stack detail)
```

See `backend/CLAUDE.md` for the complete, exhaustive repo structure and per-feature design notes.

## Prerequisites

- Python 3.13
- Node.js 18+
- `npm` (do not use yarn or pnpm — `package-lock.json` is checked in)
- Internet access
- One configured LLM provider: Anthropic, OpenAI, Groq, Google, OpenRouter, or local Ollama
- PostgreSQL — optional for pure single-stock analysis, but required for SME Signals, Screener,
  Watchlist, Positions, accounts/API keys, verdict history, MF-holdings trend, the EOD price
  store, and the Portfolio Aggregator
- Redis — optional, only needed once you run more than one backend worker/replica

## Backend setup

```bash
python3.13 -m venv .venv          # venv lives at the repo root, shared by the whole backend
source .venv/bin/activate
cd backend
pip install -r requirements.txt
cd ..
cp .env.example .env               # .env stays at the repo root — shared by both stacks
```

Edit `.env` with your provider credentials — set exactly one LLM provider key (Anthropic, OpenAI,
Groq, Google, or OpenRouter), or configure Ollama locally. `.env.example` documents every other
optional variable (`DATABASE_URL`, `REDIS_URL`, `FRONTEND_URL` + `SMTP_*` for magic-link/alert
emails, `SENTRY_DSN`, `TRUSTED_PROXY_SECRET`, `ALLOWED_ORIGINS`) — see `backend/CLAUDE.md`'s "Environment &
Config" section for what each one gates.

### Database setup (PostgreSQL)

Once `DATABASE_URL` is set, create the schema via Alembic rather than any pipeline's own
`--setup-db` flag — this is now the single source of truth for schema changes:

```bash
cd backend
alembic upgrade head        # fresh database — creates all 21 tables

# For a database that predates Alembic and has only the original 11 tables, stamp the
# baseline first, then upgrade. NOT `alembic stamp head` — that would mark the EOD price
# store / Portfolio Aggregator tables as present when they aren't, and Alembic would then
# never create them. The revision id is `0001`, not the filename stem.
alembic stamp 0001 && alembic upgrade head
```

`alembic stamp head` on its own is correct only for a database already fully caught up. See
`backend/CLAUDE.md`'s "Schema migrations" section and [docs/setup.md](docs/setup.md) for the
full fresh-vs-existing walkthrough.

## Frontend setup

```bash
cd frontend
npm install
```

Optional — create `frontend/.env.local` only if your backend is not on the default port, or if
you're wiring up `TRUSTED_PROXY_SECRET`:

```env
API_URL=http://localhost:8000
```

## Run the app locally

**Terminal A — backend:**

```bash
source .venv/bin/activate
cd backend
uvicorn api:app --reload --port 8000
```

**Terminal B — frontend:**

```bash
cd frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The nav bar links to every mode: Market Picks,
SME Signals, Screener, Watchlist, Portfolio, Net Worth (Portfolio Aggregator), Compare, and API
Keys/Pricing.

### Or with Docker

```bash
cp .env.example .env   # add at least one LLM provider key — run from the repo root
docker compose up --build
docker compose exec backend alembic upgrade head   # first run only — creates all tables
```

Backend on `http://localhost:8000`, frontend on `http://localhost:3000`, with Postgres and Redis
included as their own services (`docker-compose.yml` sets `DATABASE_URL`/`REDIS_URL` to point at
them automatically — leave both unset in `.env`). See [docs/deployment.md](docs/deployment.md) for
production notes.

## Run the batch pipelines

Each of these is a standalone script with its own CLI, normally run on a schedule (see the
`.github/workflows/*-cron.yml` files) but runnable manually:

```bash
source .venv/bin/activate
cd backend

# SME Signals (golden/death cross)
python sme_ema_pipeline.py              # fetch, compute crosses, store
python sme_ema_pipeline.py --force      # bypass the stock-list cache
python sme_ema_pipeline.py --reset-db   # drop + recreate its tables (see backend/CLAUDE.md's disclosed
                                         # blast-radius caveat — this one uses the shared MetaData())

# NIFTY 500 custom screener
python screener_pipeline.py
python screener_pipeline.py --reset-db  # scoped to just the screener_stocks table

# Daily watchlist alert digest (account-owned watchlist rows only)
python watchlist_alerts.py
python watchlist_alerts.py --force

# EOD price store (bhavcopy + AMFI NAV; self-healing 5-day gap-fill)
python eod_prices_pipeline.py
python eod_prices_pipeline.py --date YYYY-MM-DD    # single day
python eod_prices_pipeline.py --backfill YYYY-MM-DD  # backfill from that date to today

# Corporate actions + adjusted prices (splits/bonuses/dividends, adj_close recompute)
python corporate_actions_pipeline.py
```

Each pipeline can also be triggered on-demand from its own page's "Refresh" button, subject to
rate limiting.

## Run the CLI pipeline (single stock)

```bash
source .venv/bin/activate
cd backend
python main.py TCS
python main.py RELIANCE --force   # bypass cache
```

## Tests

```bash
cd backend
python -m pytest tests/                                       # backend — no live network calls
python -m pytest tests/test_analysis_guardrails.py -v          # single file

cd ../frontend
npx tsc --noEmit                 # type-check (no lint config)
npm run build                    # also catches CSS issues tsc alone won't
npx playwright install --with-deps chromium   # once
npm run test:e2e                  # Playwright E2E — every backend response is mocked
```

`tests_live/` is a separate, opt-in test root (`RUN_LIVE_TESTS=1`) that makes real calls against a
handful of scraper targets on a weekly schedule — it is never part of the default test run.

## API endpoints

The backend exposes **57 routes** — 29 in `api.py`, 28 across `routes/` (17 Portfolio Aggregator,
6 Positions, 5 Watchlist). The table below covers the major ones per mode; **see
`backend/CLAUDE.md` for the complete, current list** (or, from `backend/`, run
`grep -c "@app\.\(get\|post\|delete\|patch\)" api.py` and
`grep -rc "@router\.\(get\|post\|delete\|patch\)" routes/`). The Next.js app proxies all of these
through `frontend/app/api/`.

| Endpoint | Description |
|---|---|
| `GET /api/validate/{symbol}` | Symbol lookup — ticker, ISIN, or company name; NSE/BSE metadata |
| `GET /api/analyse/{symbol}` | Stock analysis — SSE stream of task progress + final report |
| `GET /api/peers/{symbol}` | Peer comparison + absolute valuation-anchor percentile |
| `GET /api/financials/{symbol}` | Multi-year income statement/balance sheet/cash flow + DCF + concalls |
| `GET /api/shareholding-detail/{symbol}` | Named promoters + every other named-shareholder category from NSE's shareholding XBRL filing |
| `GET /api/insider-activity/{symbol}` | Promoter/director PIT trades + bulk/block deals |
| `GET /api/street-consensus/{symbol}` | Trendlyne-cited news + numeric analyst consensus |
| `GET /api/verdict-history/{symbol}` | Stored verdict timeline + BUY/SELL win-rate scoring |
| `GET /api/consolidated/{symbol}` | Cross-mode "what does AlphaPulse think about X" aggregation |
| `GET /api/prices`, `GET /api/prices/history/{symbol}` | Live quotes; price history (+`?benchmark=true`) |
| `GET /api/market-picks` | Market picks — SSE stream of pipeline progress + ranked watchlist |
| `GET /api/market-picks/status`, `GET /api/market-picks/history` | Cache metadata; track record / a single day's snapshot |
| `GET /api/sme-signals`, `GET /api/sme-signals/{symbol}/history` | SME golden/death cross events + regime; `POST .../refresh` |
| `GET /api/screener` | NIFTY 500 screener — industry/P-E/market-cap/RSI/EMA filters; `POST .../refresh` |
| `GET /api/watchlist`, `POST/DELETE`, `GET .../calendar`, `POST .../claim` | Cross-mode watchlist CRUD + corporate-action calendar + account-claim |
| `GET/POST/PATCH/DELETE /api/positions`, `POST .../claim` | "I bought this" positions CRUD + account-claim |
| `GET /api/portfolio/concentration` | Sector-concentration check against tracked positions (Market Picks badge) |
| `GET/POST/PATCH/DELETE /api/portfolio/profiles`, `.../accounts`, `.../assets`, `.../networth` | Portfolio Aggregator — separate net-worth tracker CRUD |
| `POST /api/portfolio/refresh-valuations`, `GET /api/portfolio/xirr` | Portfolio Aggregator valuation engine + XIRR |
| `POST /api/portfolio/import-cas`, `POST /api/portfolio/import-csv[/preview]` | Portfolio Aggregator CAS PDF / broker CSV import |
| `POST /api/auth/request-link`, `GET /api/auth/verify`, `GET /api/auth/me`, `POST /api/auth/logout` | Magic-link account auth |
| `POST/GET/DELETE /api/api-keys` | API key management + tier/usage dashboard |
| `GET /api/v1/consolidated/{symbol}` | Tier-rate-limited external API surface (`X-API-Key` header) |

`/api/market-picks` supports `?force=true` to bypass its result cache;
`/api/sme-signals`/`/api/screener` support their own `?refresh`-style endpoints — see `backend/CLAUDE.md`
for rate limits and query-parameter details on each.

## Cache behavior

Per-symbol task caches under `backend/output/<SYMBOL>/` (or Redis-mirrored, when `REDIS_URL` is set):

| Task | TTL |
|---|---|
| `stock_info` | 1 hour |
| `news` | 1 hour |
| `research` | 24 hours |
| `analysis` | 24 hours |
| `peers`, `financials`, `insider_activity`, `street_consensus` | 24 hours |
| `price_history` | 6 hours |
| `shareholding` | 7 days |
| `mf_holdings` | 7 days |

Market picks result cache: `backend/output/_market_picks/picks.json`, 7-day TTL (weekly refresh cadence).
Do not shorten these TTLs without understanding the NSE/Screener rate-limit implications — see
`backend/CLAUDE.md`.

## Customizing analyst behavior

| File | What to edit |
|---|---|
| `config/analyst.json` | Analyst persona, analysis rules, valuation guidance, output schema, section labels |

Adding a field to the analyst's JSON output requires updating `config/analyst.json`,
`crew._validate_analysis_payload()`, `main._build_report()`, and
`frontend/types/index.ts`'s `Analysis` interface together — see `backend/CLAUDE.md`'s "Important Rules for
Claude" section.

## Notes

- The backend emits structured JSON logs to stdout with a per-run `run_id`. Set `LOG_LEVEL=DEBUG`
  for verbose output; set `SENTRY_DSN` to also forward error-level events to a Sentry-compatible
  ingest endpoint.
- `npm run build` needs network access to Google Fonts (`fonts.googleapis.com`) for `Inter` and
  `JetBrains Mono`. Replace with local fonts to build offline.
- The validate endpoint accepts ISINs (e.g. `INE009A01021`) and resolves them to NSE/BSE tickers
  automatically.
- Signing in never automatically migrates an anonymous browser's watchlist/positions onto an
  account — use the explicit "claim my data" prompt shown right after sign-in.

## Documentation

- [CLAUDE.md](CLAUDE.md) — root-level overview + pointers
- [backend/CLAUDE.md](backend/CLAUDE.md) — exhaustive backend engineering reference
- [frontend/CLAUDE.md](frontend/CLAUDE.md) — frontend engineering conventions
- [docs/index.md](docs/index.md)
- [docs/setup.md](docs/setup.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/deployment.md](docs/deployment.md)
- [docs/tools.md](docs/tools.md)
- [docs/output-schema.md](docs/output-schema.md)
- [docs/design.md](docs/design.md) — AlphaPulse Design System
- [docs/PRD.md](docs/PRD.md) — product strategy (vision, goals, priority, roadmap)
- [docs/feature-catalog.md](docs/feature-catalog.md) — detailed feature inventory
