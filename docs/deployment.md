# Deployment

Two paths: Docker Compose (fastest to get running), or a manual deployment where you run the
backend and frontend yourself. Both need the same environment variables — see
[Setup & Configuration](setup.md) for the full list; this doc only calls out what's specific to
running in production rather than `--reload`/`npm run dev`.

## Docker Compose

```bash
cp .env.example .env   # add at least one LLM provider key
docker compose up --build
docker compose exec backend python sme_ema_pipeline.py --setup-db   # first run only, for SME signals
```

This starts three services (`docker-compose.yml`):

- **postgres** — Postgres 16, with a named volume so data survives `docker compose down` (not `down -v`)
- **backend** — `Dockerfile` at the repo root, single `uvicorn` worker (see "Scaling" below), reads `.env`
  for provider keys and gets `DATABASE_URL`/`ALLOWED_ORIGINS` set automatically by compose
- **frontend** — `frontend/Dockerfile`, a multi-stage build using Next.js's `output: 'standalone'`
  (`next.config.ts`), gets `API_URL` pointed at the `backend` service automatically

Rebuild after a dependency change with `docker compose up --build`. Logs: `docker compose logs -f backend`
(or `frontend`). The SME golden-cross screener needs its own scheduled refresh — see
[Setup & Configuration](setup.md#sme-signals-pipeline) for the cron/GitHub Actions options; the
compose setup doesn't run that automatically.

## Manual deployment (no Docker)

**Backend** — `uvicorn --reload` is a *development* flag (it watches files and restarts on every change,
which you don't want under real traffic). For production:

```bash
source .venv/bin/activate
uvicorn api:app --host 0.0.0.0 --port 8000
```

If you need process supervision (auto-restart on crash, log rotation), run it under `systemd`, `supervisor`,
or similar rather than backgrounding it directly. See "Scaling" below before adding `--workers`.

**Frontend** — build once, then run the production server (not `next dev`):

```bash
cd frontend
npm run build
npm run start   # or: node .next/standalone/server.js if you built with output: 'standalone'
```

Put both behind a reverse proxy (nginx, Caddy, a cloud load balancer) for TLS termination — neither
`uvicorn` nor `next start` handles HTTPS itself in this setup.

## Scaling: read this before adding workers or replicas

Several pieces of backend state are **single-process, in-memory, by design** (documented in `CLAUDE.md`):

| State | Where | What breaks with >1 worker/replica |
|---|---|---|
| Rate limiter (`_RATE_LIMIT_CALLS`) | `api.py` | Each worker gets its own counter — the documented per-IP limits (20 req/5min on `/api/analyse`, etc.) become *per-worker*, silently multiplying the effective limit |
| SME refresh guard (`_SME_REFRESHING`) | `api.py` | Two workers can both accept a `/api/sme-signals/refresh` POST and run the pipeline concurrently — wasteful (duplicate NSE/yfinance calls), not corrupting (upserts are idempotent) |
| Cached DB engine (`_SME_ENGINE`) | `api.py` | Harmless — each worker just gets its own connection pool, not a bug, just not shared |

If you need to scale past one backend process, migrate the rate limiter and refresh guard to a shared
store (Redis is the natural fit) first. Until then, keep the backend at a single worker/replica — the
default `CMD` in `Dockerfile` intentionally omits `--workers`. The frontend has no such constraint; scale
it however you like.

## Environment variable checklist

Beyond an LLM provider key (see [Setup](setup.md)), production deployments should set:

- `DATABASE_URL` — required for SME signals; Docker Compose sets this automatically
- `ALLOWED_ORIGINS` — add your real frontend origin (comma-separated for multiple), or direct browser
  calls to the backend get CORS-rejected; defaults to `http://localhost:3000` (see `api.py`)
- `API_URL` (frontend) — point at your backend's real address if not using Docker Compose's automatic wiring
- `LOG_LEVEL=INFO` (default) — bump to `DEBUG` temporarily when diagnosing an issue, not left on in steady state
