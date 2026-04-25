# Stock Research Documentation

This project is an Indian equity research app for NSE-listed stocks. It combines:

- A Python backend that fetches market data, fundamentals, news, and ownership data
- An analyst step that turns those inputs into a structured `BUY` / `SELL` / `HOLD` recommendation
- A Next.js frontend that validates symbols, streams task progress, and renders the final report

## Documentation map

| Doc | What it covers |
|-----|----------------|
| [Setup & Configuration](setup.md) | Backend/frontend install, environment variables, local development |
| [Architecture](architecture.md) | End-to-end request flow, config layer, caching, services, and file layout |
| [Tools Reference](tools.md) | Data-fetching tools, sources, and output shapes |
| [Output Schema](output-schema.md) | Final report JSON structure and cache files |

## Quick start

```bash
# 1. Backend
/opt/homebrew/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# 2. Frontend
cd frontend
npm install

# 3. Run both apps in separate terminals
# Terminal A
cd /path/to/stock-research
source .venv/bin/activate
uvicorn api:app --reload --port 8000

# Terminal B
cd /path/to/stock-research/frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Outputs

Reports and cache files are written under `output/<SYMBOL>/`, for example:

- `output/TCS/stock_info.json`
- `output/TCS/research.json`
- `output/TCS/news.json`
- `output/TCS/shareholding.json`
- `output/TCS/mf_holdings.json`
- `output/TCS/analysis.json`
- `output/TCS/report_2026-04-25.json`
