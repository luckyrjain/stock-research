# Stock Research

[![CI](https://github.com/luckyrjain/stock-research/actions/workflows/ci.yml/badge.svg)](https://github.com/luckyrjain/stock-research/actions/workflows/ci.yml)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![Next.js 15](https://img.shields.io/badge/next.js-15-black)
![PostgreSQL](https://img.shields.io/badge/postgres-primary%20store-blue)

**AlphaPulse** is a full-stack Indian equity research platform: point it at an NSE/BSE ticker and
it validates the symbol, scrapes price/fundamentals/news/shareholding/filings in parallel, runs a
quantitative signal engine, and calls an LLM analyst for a structured BUY/HOLD/SELL
recommendation — streamed live over Server-Sent Events.

## What it does

- **Stock Analysis** — full single-stock report: quant signals (valuation, growth, technical,
  macro) + LLM verdict, peer comparison, DCF, verdict-history win-rate.
- **Market Picks** — multi-agent pipeline scraping 20 Indian/global sources into a
  confidence-ranked, sector-balanced weekly watchlist.
- **SME Signals** — batch screener for EMA golden-cross/death-cross events across NSE Emerge + BSE
  SME stocks.
- **Screener** — filterable NIFTY 500 screener (industry, P/E, market cap, RSI/EMA trend).
- **Watchlist & Portfolio** — cross-mode watchlist + "I bought this" position tracking, with daily
  email alerts on recommendation changes.
- **Portfolio Aggregator** — separate personal net-worth tracker (stocks, MFs, FDs, EPF/PPF) with
  an XIRR engine, fed by CAS PDF / broker CSV import.
- **Compare** and a shared **consolidated search** ("what does AlphaPulse think about X") round out
  the cross-mode UX.

## Tech stack

- **Backend** — Python 3.13, FastAPI (SSE streaming), litellm, a custom quantitative signals engine
- **Frontend** — Next.js 15 (App Router), React 19, TypeScript (strict), Tailwind CSS v3, PWA
- **Storage** — PostgreSQL (primary store for all durable/shared state); Redis optional, only for
  multi-worker deployments
- **LLM providers** — Anthropic, OpenAI, Groq, Google, OpenRouter, or local Ollama (auto-detected,
  cross-provider failover)
- **Data sources** — Yahoo Finance, Screener.in, NSE/BSE, AMFI, Trendlyne, RBI, Google News/RSS

## Project structure

```text
stock-research/
├── backend/       FastAPI app, batch pipelines, signal engine, tests — see backend/CLAUDE.md
├── frontend/      Next.js app (App Router), Playwright E2E — see frontend/CLAUDE.md
├── docs/          setup, architecture, API reference, database schema, deployment, PRD
├── docker-compose.yml
└── CLAUDE.md
```

## Quickstart

```bash
git clone <repo> && cd stock-research
cp .env.example .env   # add at least one LLM provider key
docker compose up --build
docker compose exec backend alembic upgrade head   # first run only
```

Backend on `http://localhost:8000`, frontend on `http://localhost:3000`.

<details>
<summary>Manual setup (no Docker)</summary>

**Prerequisites:** Python 3.13, Node.js 18+, npm (not yarn/pnpm), one configured LLM provider key.
PostgreSQL is optional for single-stock analysis but required for every other mode.

```bash
# Backend
python3.13 -m venv .venv && source .venv/bin/activate
cd backend && pip install -r requirements.txt && cd ..
cp .env.example .env   # edit with your provider key + DATABASE_URL

# Database (once DATABASE_URL is set)
cd backend && alembic upgrade head && cd ..

# Frontend
cd frontend && npm install && cd ..
```

Run it:

```bash
# Terminal A
source .venv/bin/activate && cd backend && uvicorn api:app --reload --port 8000

# Terminal B
cd frontend && npm run dev
```

Single-stock CLI (no server): `cd backend && python main.py TCS`

See [docs/setup.md](docs/setup.md) for full env var reference and troubleshooting, and
`backend/CLAUDE.md`'s "Schema migrations" section for the existing-database (pre-Alembic) upgrade
path.

</details>

## Tests

```bash
cd backend && python -m pytest tests/       # no live network calls
cd frontend && npx tsc --noEmit && npm run test:e2e   # Playwright, backend fully mocked
```

## Documentation

| Doc | Covers |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Root overview + architectural constraints |
| [backend/CLAUDE.md](backend/CLAUDE.md) | Exhaustive backend reference — every feature, all data flows, code style |
| [frontend/CLAUDE.md](frontend/CLAUDE.md) | Frontend conventions, testing gate, code style |
| [docs/api-reference.md](docs/api-reference.md) | Every HTTP endpoint — auth, params, status codes, rate limits |
| [docs/database.md](docs/database.md) | Every table — columns, constraints, ownership model, migrations |
| [docs/architecture.md](docs/architecture.md) | System-level request flows and module boundaries |
| [docs/setup.md](docs/setup.md) | Full env var reference, local dev setup, troubleshooting |
| [docs/deployment.md](docs/deployment.md) | Docker Compose, manual deployment, scaling |
| [docs/backlog.md](docs/backlog.md) | The single "what's left" list |
| [docs/PRD.md](docs/PRD.md) | Product strategy — vision, goals, roadmap |
| [docs/feature-catalog.md](docs/feature-catalog.md) | Detailed feature inventory |
