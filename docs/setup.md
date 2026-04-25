# Setup & Configuration

## What you need

- Python `3.10` to `3.13`
- Node.js `18+`
- `npm`
- Internet access for market/news data and, during Next.js builds, Google Fonts
- One LLM provider configured:
  - `ANTHROPIC_API_KEY`
  - `OPENAI_API_KEY`
  - `GROQ_API_KEY`
  - `GOOGLE_API_KEY`
  - or `LLM_PROVIDER=ollama` for a local model

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
|----------|----------|-------------|
| `LLM_PROVIDER` | No | `anthropic`, `openai`, `groq`, `google`, or `ollama` |
| `ANTHROPIC_API_KEY` | One provider required | Anthropic API key |
| `OPENAI_API_KEY` | One provider required | OpenAI API key |
| `GROQ_API_KEY` | One provider required | Groq API key |
| `GOOGLE_API_KEY` | One provider required | Gemini API key |
| `LLM_MODEL` | No | Model for the data/worker agents |
| `ANALYST_MODEL` | No | Model for the final analyst step |
| `OLLAMA_BASE_URL` | Only for Ollama | Base URL for local Ollama |

### Default models

| Provider | Data agents | Analyst agent |
|----------|-------------|---------------|
| `anthropic` | `claude-haiku-4-5-20251001` | `claude-sonnet-4-6` |
| `openai` | `gpt-4o-mini` | `gpt-4o` |
| `groq` | `groq/llama-3.1-8b-instant` | `groq/llama-3.3-70b-versatile` |
| `google` | `gemini/gemini-1.5-flash` | `gemini/gemini-1.5-flash` |
| `ollama` | `ollama/llama3.2` | `ollama/llama3.1:8b` |

If `LLM_PROVIDER` is not set, the backend auto-detects the first available API key in this order: `anthropic`, `openai`, `groq`, `google`.

## Frontend setup

From the `frontend/` directory:

```bash
cd frontend
npm install
```

The Next.js app proxies requests to the Python backend using `API_URL`.

### Frontend environment variables

Create `frontend/.env.local` if you want to override the default backend URL:

```env
API_URL=http://localhost:8000
```

If unset, the frontend already defaults to `http://localhost:8000`.

## Running locally

Run the backend and frontend in separate terminals.

### Terminal A: backend

```bash
cd /path/to/stock-research
source .venv/bin/activate
uvicorn api:app --reload --port 8000
```

Backend endpoints:

- `GET /api/validate/{symbol}`
- `GET /api/analyse/{symbol}`

### Terminal B: frontend

```bash
cd /path/to/stock-research/frontend
npm run dev
```

Frontend URL:

- [http://localhost:3000](http://localhost:3000)

## CLI mode

You can also run the pipeline without the frontend:

```bash
source .venv/bin/activate
python main.py TCS
python main.py RELIANCE --force
```

Use `--force` to bypass cache freshness and fetch all data again.

## Cache and output

Results are stored under `output/<SYMBOL>/`.

- `stock_info.json`
- `research.json`
- `news.json`
- `shareholding.json`
- `mf_holdings.json`
- `analysis.json`
- `report_<DATE>.json`

Freshness rules are enforced per task:

- `stock_info`: 1 hour
- `news`: 1 hour
- `research`: 24 hours
- `analysis`: 24 hours
- `shareholding`: 7 days
- `mf_holdings`: 7 days

## Useful commands

```bash
# Backend
source .venv/bin/activate
python main.py INFY
uvicorn api:app --reload --port 8000

# Frontend
cd frontend
npm run dev
npm run build
npm run start

# Frontend type-check
npx tsc --noEmit
```

## Troubleshooting

### No provider configured

If the backend exits with "No API key or local provider found", set one of the supported API keys in `.env`, or configure Ollama with:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
```

### Frontend shows backend unavailable

Make sure the Python backend is running on port `8000`, or update `frontend/.env.local`:

```env
API_URL=http://your-backend-host:8000
```

### Next.js build fails on Google Fonts

The frontend layout uses `next/font/google` for `Inter` and `JetBrains Mono`. A production build needs network access to `fonts.googleapis.com` unless you switch to local fonts.
