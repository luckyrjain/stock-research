# Stock Research Documentation

This project (product name **AlphaPulse**) is a full-stack Indian equity research platform.
See [`PRD.md`](PRD.md) for the product strategy view — vision, goals, principles, personas,
priority, and roadmap — and [`feature-catalog.md`](feature-catalog.md) for the detailed "what's
already built" inventory. This page is the doc-set index; [`../backend/CLAUDE.md`](../backend/CLAUDE.md)
and [`../frontend/CLAUDE.md`](../frontend/CLAUDE.md) are the exhaustive, always-current engineering
references for exactly how each feature behaves.

**Core modes**, at a glance:

**Stock Analysis** — validates a ticker (NSE, BSE, ISIN, or company name), fetches live market
data, fundamentals, news, shareholding, MF holdings, and NSE filings in parallel, runs a
quantitative signal engine (valuation, growth, volume, filings, technical, macro — sector-aware
weighted), and calls an LLM analyst to produce a structured `BUY` / `SELL` / `HOLD`
recommendation streamed to the browser via SSE. Layered on top: peer comparison, multi-year
financials + DCF valuation, insider/institutional activity, street consensus, and a verdict
timeline with win/loss scoring.

**Market Picks** — a multi-agent pipeline that scrapes ~28 Indian and global financial sources,
extracts stock recommendations with an LLM, validates symbols against the NSE equity master,
runs due diligence on each, and returns a confidence-ranked, sector-balanced watchlist with
`BUY` / `WATCHLIST` / `HOLD` / `SELL` ratings and deterministic entry/target/stop-loss levels.

**SME Signals** — a PostgreSQL-backed batch pipeline that screens NSE Emerge + BSE SME stocks
for EMA20/EMA50 golden/death cross events (plus RSI, volume-spike confirmation, and forward-
return outcomes), with a screener page showing recent crosses and each stock's current regime.

**Screener** — the same filter-chip screening pattern generalized to the NIFTY 500 universe
(industry, P/E, market cap, RSI/EMA trend).

**Watchlist, Portfolio, Compare, Consolidated Search** — cross-mode features tying the above
together: a shared star list with corporate-action alerts, "I bought this" position tracking
with aggregate P&L, side-by-side report comparison, and a single search box answering "what does
AlphaPulse already think about X."

**Portfolio Aggregator** — a separate, unauthenticated personal net-worth tracker (distinct from
the Portfolio/Positions page above): profiles → accounts → assets (stocks, mutual funds, FDs,
EPF/PPF, cash, loans), fed by an EOD price store (NSE bhavcopy + AMFI NAV), a corporate-actions/
adjusted-price pipeline, a nightly valuation + XIRR engine, and CAS PDF / broker CSV import
(reconciled via a securities-master symbol resolver). Reachable at `/portfolio-aggregator`.

**Accounts & API access** — passwordless magic-link sign-in, and API keys for programmatic
access to a public `/api/v1/*` surface.

## Documentation map

| Doc | What it covers |
|-----|----------------|
| [PRD](PRD.md) | Product vision, goals, principles, target users, priority, roadmap, business context |
| [Feature Catalog](feature-catalog.md) | Detailed inventory of every shipped feature area (the former PRD §3) |
| [Setup & Configuration](setup.md) | Backend/frontend install, environment variables, local development |
| [Deployment](deployment.md) | Docker Compose, manual production deployment, scaling caveats |
| [Architecture](architecture.md) | Request flows, pipeline phases, caching, agent layers, file layout |
| [Tools Reference](tools.md) | Data-fetching tools, market picks scrapers, sources, and output shapes |
| [Output Schema](output-schema.md) | Report JSON structure, cache files, and standalone endpoint response shapes |
| [Design System](design.md) | Colors, typography, spacing, component patterns |
| [CLAUDE.md](../CLAUDE.md) | Root-level overview + pointers |
| [backend/CLAUDE.md](../backend/CLAUDE.md) | Exhaustive backend engineering reference — the ground truth for exact behavior |
| [frontend/CLAUDE.md](../frontend/CLAUDE.md) | Frontend engineering conventions and testing |

## Quick start

```bash
# 1. Backend
/opt/homebrew/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your LLM provider key

# 2. Frontend
cd frontend && npm install

# 3. Database schema (PostgreSQL — required for Watchlist, Positions, Screener, SME Signals,
#    accounts, and verdict/MF-holdings history; optional for a bare stock-analysis-only setup)
alembic upgrade head    # fresh database
# or, for a database that already has these tables from before Alembic existed:
alembic stamp head

# 4. Run both in separate terminals
# Terminal A — backend
source .venv/bin/activate
uvicorn api:app --reload --port 8000

# Terminal B — frontend
cd frontend && npm run dev
```

Open [http://localhost:3000](http://localhost:3000) for stock analysis. See [Setup](setup.md)
for the full list of routes (`/market-picks`, `/sme-signals`, `/screener`, `/watchlist`,
`/portfolio`, `/portfolio-aggregator`, `/compare`, `/login`, `/api-keys`, `/pricing`) and which
env vars each needs.

## Output locations

| Path | Contents |
|---|---|
| `output/<SYMBOL>/` | Per-symbol task caches and report JSON |
| `output/_extract_cache/` | LLM extraction cache for market picks (6 h TTL) |
| `output/_history/` | Daily pick snapshots for trend tracking |
| `output/_market_picks/` | Market picks result cache (6 h TTL) |
| `output/_nse_master.txt` | NSE equity symbol master (refreshed every 24 h) |
| `output/_llm_cost/` | Daily LLM call-cost/token counters |
| `output/_source_health/`, `output/_scraper_error_counters/` | Scraper freshness/error monitoring |
| `output/_bhavcopy/` | Raw NSE bhavcopy CSV archive (EOD price store ingestion replay) |
| `output/_cas/` | Scrubbed CAS-import parse archive (portfolio aggregator, replay) |
| PostgreSQL (`DATABASE_URL`) | 21 tables — SME signals, screener, watchlist, positions, verdict history, MF-holdings history, accounts/sessions/API keys, EOD price store (securities/prices_daily/mf_nav_daily), corporate actions, and the portfolio aggregator (profiles/accounts/assets/holdings/valuations/transactions) (see [Architecture](architecture.md)) |
| Redis (`REDIS_URL`, optional) | Shared rate-limit/cache state across multiple backend workers/hosts |
