# Stock Research

Stock Research is a full-stack Indian equity research app for NSE-listed stocks. It fetches live market data, fundamentals, news, shareholding patterns, and mutual fund holdings, then uses an LLM-powered analyst step to produce a structured `BUY`, `SELL`, or `HOLD` recommendation.

The project has two main parts:

- A Python backend for validation, data collection, caching, and final analysis
- A Next.js frontend for search, progress streaming, and report display

## What it does

Given a symbol like `TCS` or `RELIANCE`, the app:

1. Validates the ticker
2. Fetches fresh data only for stale sections
3. Reuses cached data when still within TTL
4. Runs an analyst step to synthesize the inputs
5. Shows the result in the UI and saves JSON outputs to `output/<SYMBOL>/`

## Tech stack

- Backend: Python, FastAPI, CrewAI
- Frontend: Next.js 15, React 19, TypeScript, Tailwind CSS
- Data sources: Yahoo Finance, Screener.in, Google News RSS, NSE filings
- Storage: file-based cache in `output/`

## Project structure

```text
stock-research/
├── api.py
├── main.py
├── crew.py
├── cache.py
├── schemas.py
├── tools/
├── config/
│   ├── agents.json       ← agent roles, backstories, tool mapping
│   ├── tasks.json        ← task descriptions, expected outputs, retry counts
│   ├── analyst.json      ← analyst agent, prompt instructions, output schema
│   ├── crew_agents.py    ← thin loader: wires JSON → CrewAI Agent objects
│   └── crew_tasks.py     ← thin loader: builds task specs and analyst prompt
├── frontend/
├── docs/
├── output/
├── requirements.txt
└── .env.example
```

## Prerequisites

- Python `3.10` to `3.13`
- Node.js `18+`
- `npm`
- Internet access
- One configured LLM provider:
  - Anthropic
  - OpenAI
  - Groq
  - Google
  - or local Ollama

## Backend setup

From the repo root:

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then update `.env` with your provider credentials.

Minimal example with OpenAI:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Minimal example with Anthropic:

```env
ANTHROPIC_API_KEY=sk-ant-...
```

Minimal example with Ollama:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
```

## Frontend setup

```bash
cd frontend
npm install
```

Optional: create `frontend/.env.local` if your backend is not running on the default URL.

```env
API_URL=http://localhost:8000
```

The frontend already defaults to `http://localhost:8000`, so this file is only needed when you want to override it.

## Run the app locally

Start backend and frontend in separate terminals.

### Terminal A: backend

```bash
cd /path/to/stock-research
source .venv/bin/activate
uvicorn api:app --reload --port 8000
```

### Terminal B: frontend

```bash
cd /path/to/stock-research/frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Run the pipeline from the CLI

If you just want the backend flow without the UI:

```bash
source .venv/bin/activate
python main.py TCS
python main.py RELIANCE --force
```

## Available scripts

### Frontend

```bash
npm run dev
npm run build
npm run start
npx tsc --noEmit
```

### Backend

```bash
uvicorn api:app --reload --port 8000
python main.py INFY
```

## Output files

Each run creates or refreshes files under `output/<SYMBOL>/`:

- `stock_info.json`
- `research.json`
- `news.json`
- `shareholding.json`
- `mf_holdings.json`
- `analysis.json`
- `report_<DATE>.json`

## Cache behavior

The app caches each section independently:

- `stock_info`: 1 hour
- `news`: 1 hour
- `research`: 24 hours
- `analysis`: 24 hours
- `shareholding`: 7 days
- `mf_holdings`: 7 days

This keeps the UI responsive on repeat searches while still refreshing volatile data.

## API endpoints

The Python backend exposes:

- `GET /api/validate/{symbol}`
- `GET /api/analyse/{symbol}`

The Next.js app proxies these routes through:

- `frontend/app/api/validate/[symbol]/route.ts`
- `frontend/app/api/analyse/[symbol]/route.ts`

## Customising agent behaviour

All agent definitions, task prompts, and analyst instructions live in the `config/` JSON files — no Python changes required for prompt tuning:

| File | What to edit |
|------|-------------|
| `config/agents.json` | Agent roles, backstories, tool assignment per task |
| `config/tasks.json` | Task descriptions, expected output shape, retry count |
| `config/analyst.json` | Analyst persona, analysis rules, valuation guidance, output schema |

## Notes

- The backend emits structured JSON logs to stdout/stderr with per-run `run_id`, task retries, failures, tool latency, and analyst LLM latency. Set `LOG_LEVEL=DEBUG` for more verbose logs.
- The frontend uses `next/font/google` for `Inter` and `JetBrains Mono`, so `npm run build` needs network access to Google Fonts unless the fonts are replaced with local assets.
- Reports are tailored for NSE-listed Indian equities.
- The final analyst output is structured JSON, but some nested fields can vary slightly in shape, so the frontend normalizes mixed response formats when rendering.

## Documentation

- [docs/index.md](/Users/luckyratanlaljain/project/stock-research/docs/index.md)
- [docs/setup.md](/Users/luckyratanlaljain/project/stock-research/docs/setup.md)
- [docs/architecture.md](/Users/luckyratanlaljain/project/stock-research/docs/architecture.md)
- [docs/tools.md](/Users/luckyratanlaljain/project/stock-research/docs/tools.md)
- [docs/output-schema.md](/Users/luckyratanlaljain/project/stock-research/docs/output-schema.md)
