# Stock Research Documentation

This project is a full-stack Indian equity research platform with three modes:

**Stock Analysis** — validates a ticker (NSE, BSE, or ISIN), fetches live market data, fundamentals, news, shareholding, MF holdings, and NSE filings in parallel, runs a quantitative signal engine, and calls an LLM analyst to produce a structured `BUY` / `SELL` / `HOLD` recommendation streamed to the browser via SSE.

**Market Picks** — a multi-agent pipeline that scrapes 16 Indian and global financial sources, extracts stock recommendations with an LLM, validates symbols against the NSE equity master, runs due diligence, and returns a confidence-ranked watchlist with `BUY` / `WATCHLIST` / `HOLD` / `SELL` ratings.

**SME Signals** — a PostgreSQL-backed batch pipeline that screens NSE Emerge + BSE SME stocks for EMA20/EMA50 golden/death cross events, with a screener page showing recent crosses and each stock's current regime.

## Documentation map

| Doc | What it covers |
|-----|----------------|
| [Setup & Configuration](setup.md) | Backend/frontend install, environment variables, local development |
| [Architecture](architecture.md) | Request flows, pipeline phases, caching, agent layers, file layout |
| [Tools Reference](tools.md) | Data-fetching tools, market picks scrapers, sources, and output shapes |
| [Output Schema](output-schema.md) | Report JSON structure, cache files, market picks pick schema |

## Quick start

```bash
# 1. Backend
/opt/homebrew/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your LLM provider key

# 2. Frontend
cd frontend && npm install

# 3. Run both in separate terminals
# Terminal A — backend
source .venv/bin/activate
uvicorn api:app --reload --port 8000

# Terminal B — frontend
cd frontend && npm run dev
```

Open [http://localhost:3000](http://localhost:3000) for stock analysis.
Open [http://localhost:3000/market-picks](http://localhost:3000/market-picks) for the weekly picks dashboard.
Open [http://localhost:3000/sme-signals](http://localhost:3000/sme-signals) for the SME golden cross screener (requires `DATABASE_URL` + one run of `python sme_ema_pipeline.py` — see [Setup](setup.md)).

## Output locations

| Path | Contents |
|---|---|
| `output/<SYMBOL>/` | Per-symbol task caches and report JSON |
| `output/_extract_cache/` | LLM extraction cache for market picks (6 h TTL) |
| `output/_history/` | Daily pick snapshots for trend tracking |
| `output/_market_picks/` | Market picks result cache (6 h TTL) |
| `output/_nse_master.txt` | NSE equity symbol master (refreshed every 24 h) |
| PostgreSQL (`DATABASE_URL`) | SME stock master + EMA cross signals (`sme_stocks`, `ema_signals`) |
