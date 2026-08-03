# Deployment

Two paths: Docker Compose (fastest to get running), or a manual deployment where you run the
backend and frontend yourself. Both need the same environment variables — see
[Setup & Configuration](setup.md) for the full list; this doc only calls out what's specific to
running in production rather than `--reload`/`npm run dev`.

## Docker Compose

```bash
cp .env.example .env   # add at least one LLM provider key
docker compose up --build
docker compose exec backend alembic upgrade head   # first run only — creates all 23 tables
```

This starts four services (`docker-compose.yml`):

- **postgres** — Postgres 16, with a named volume so data survives `docker compose down` (not `down -v`)
- **redis** — `redis:7-alpine`, persisted via a named volume; wired into the `backend` service's
  `REDIS_URL` automatically (see "Scaling" below)
- **backend** — `backend/Dockerfile` (build context `./backend` in `docker-compose.yml`), single
  `uvicorn` worker (see "Scaling" below), reads `.env` (repo root) for provider keys and gets
  `DATABASE_URL`/`REDIS_URL`/`ALLOWED_ORIGINS` set automatically by compose
- **frontend** — `frontend/Dockerfile`, a multi-stage build using Next.js's `output: 'standalone'`
  (`next.config.ts`), gets `API_URL` pointed at the `backend` service automatically

Rebuild after a dependency change with `docker compose up --build`. Logs: `docker compose logs -f backend`
(or `frontend`). The SME golden-cross screener, the NIFTY 500 custom screener, and the daily
watchlist-alerts job all need their own scheduled refresh — see "Scheduled jobs (GitHub Actions
crons)" below; the compose setup doesn't run any of them automatically.

**Schema setup uses Alembic, not `metadata.create_all()`** — see [Setup & Configuration](setup.md#database-schema-setup-alembic)
for the full fresh-DB-vs-existing-DB story. `alembic upgrade head` above is for a genuinely empty
database (which is what a fresh `docker compose up` gives you). If you're pointing this compose
setup at a database that already has the original 11 tables from before Alembic was introduced
(and predates the EOD price store / Portfolio Aggregator tables), run
`docker compose exec backend alembic stamp 0001` followed by
`docker compose exec backend alembic upgrade head` instead — plain `upgrade head` will fail trying
to re-create the 11 tables that already exist, and a bare `stamp head` would skip creating the 10
newer tables for real. If the database is already fully caught up, neither is needed.

## Manual deployment (no Docker)

**Backend** — `uvicorn --reload` is a *development* flag (it watches files and restarts on every change,
which you don't want under real traffic). For production:

```bash
source .venv/bin/activate
cd backend
uvicorn api:app --host 0.0.0.0 --port 8000
```

If you need process supervision (auto-restart on crash, log rotation), run it under `systemd`, `supervisor`,
or similar rather than backgrounding it directly. See "Scaling" below before adding `--workers`.

> **The working directory must be `<repo>/backend`, not the repo root.** This is the one thing the
> backend-directory move can break silently. A dozen-odd paths — `core/cache.py`'s
> `CACHE_DIR = Path("output")`, the market-picks and extraction caches, the bhavcopy archive,
> the NSE/SME/NIFTY-500 master caches — are resolved relative
> to the current working directory, not to `__file__`.
>
> Start the process from the repo root and it comes up cleanly, serves cleanly, and quietly writes
> to a brand-new empty `<repo>/output/` that `.gitignore` hides from `git status`. Nothing crashes
> and nothing is logged. What you get is a permanent total cache miss: every scraper re-fetches
> against the rate-limit-sensitive NSE and Screener.in endpoints this codebase is otherwise careful
> with, `/api/market-picks` re-runs the full paid LLM pipeline on every request because
> `output/_market_picks/picks.json` always reads empty, and the NSE symbol master re-downloads.
>
> For a `systemd` unit that means:
>
> ```ini
> [Service]
> WorkingDirectory=/path/to/stock-research/backend
> ExecStart=/path/to/stock-research/.venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000
> ```
>
> Docker and CI are both immune — the image sets `WORKDIR /app` and the workflows set
> `working-directory: backend`. This applies only to a manual/self-hosted deploy.

**Frontend** — build once, then run the production server (not `next dev`):

```bash
cd frontend
npm run build
npm run start   # or: node .next/standalone/server.js if you built with output: 'standalone'
```

Put both behind a reverse proxy (nginx, Caddy, a cloud load balancer) for TLS termination — neither
`uvicorn` nor `next start` handles HTTPS itself in this setup.

**Database schema** (all Alembic commands run from `backend/`): `alembic upgrade head` once
against a fresh database, or `alembic stamp 0001` followed by `alembic upgrade head` against a
database that already has the original 11 tables from before this app used Alembic — see
[Setup & Configuration](setup.md#database-schema-setup-alembic) for the full distinction. The
revision identifier is `0001`, not the filename stem `0001_baseline_schema`; Alembic resolves by
revision id and fails on the longer form.

From here on, schema changes ship as new Alembic revisions (`alembic revision --autogenerate`,
then `alembic upgrade head` on deploy) rather than hand-edited `db/schema.sql` guards or ad-hoc
`metadata.create_all()` calls. `pipelines/sme_ema_pipeline.py` and `pipelines/screener_pipeline.py`'s
`--setup-db`/`--reset-db` flags also stamp Alembic head automatically after their own
`create_all()`/`drop_all()`, so a deployment provisioned through one of those flags still ends up
in a state `alembic upgrade head` can build on later without conflict.

**Error tracking / APM (optional)**: set `SENTRY_DSN` (and optionally `SENTRY_ENVIRONMENT`,
default `production`) to forward every error-level `observability.log_event()` call to a
Sentry-compatible ingest endpoint (`core/error_tracking.py`). This is genuinely optional — `sentry-sdk`
is already in `requirements.txt`, but without `SENTRY_DSN` set the whole module is a no-op and
logging behaves exactly as it did before this existed. Worth setting in any production deployment
that doesn't already have another way to get paged on a backend error; there's no equivalent
signal on the frontend.

**Real client IPs for per-IP rate limiting**: the Next.js frontend talks to the FastAPI backend
server-to-server (see "Proxy routes" in backend/CLAUDE.md) — without anything further, every one of
`api.py`'s per-IP rate limiters sees only the Next.js server's own IP for every request, collapsing
them into one shared bucket for the whole site. Once a reverse proxy sits in front of the frontend,
two things need to line up:
1. The reverse proxy must **replace** `X-Forwarded-For` with the real client IP on requests it
   forwards to Next.js — use `proxy_set_header X-Forwarded-For $remote_addr;` on nginx. Caddy does
   this by default. **Do not** use nginx's more commonly copy-pasted
   `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` — that *appends* to whatever
   `X-Forwarded-For` value the request already carried instead of replacing it, and since
   `X-Forwarded-For` isn't a browser-forbidden header, any caller can set it directly on a request
   to your reverse proxy. Under append mode, a spoofed value from the caller ends up as the
   *leftmost* entry ahead of the proxy's own real observation — exactly what `clientIpHeaders()`
   (`frontend/lib/proxy-headers.ts`) and `api.py::_client_ip()` are watching for: both refuse to
   trust the header at all once it contains more than one entry, falling back to the same
   pre-this-feature behavior rather than guessing which entry is real. Getting the proxy directive
   right is still what makes the real client IP actually reach the rate limiter, though — the
   multi-value refusal is a safety net, not a substitute for the correct config.
2. Set `TRUSTED_PROXY_SECRET` to the same random value on **both** the backend and frontend
   processes (see `.env.example`) — the frontend's proxy routes forward the client IP they read off
   that header alongside this shared secret, and `api.py` only trusts the forwarded IP when the
   secret matches, so a caller that reaches the backend directly (bypassing the reverse proxy and
   Next.js) can't spoof `X-Forwarded-For` to dodge its own rate limit or frame another IP.

Leaving `TRUSTED_PROXY_SECRET` unset is safe — every per-IP limiter just keys off whatever IP the
backend actually sees, exactly as before this existed.

## Scaling: read this before adding workers or replicas

`core/rate_limiter.py` backs three kinds of guard state — the sliding-window rate limiter, the LLM
concurrency ceiling, and a set of single-run refresh locks (SME, Screener, Market Picks, each its
own named lock on the same primitive) — with Redis when `REDIS_URL` is set, falling back to
the same in-memory-per-process behavior this app had before Redis support existed when it's unset:

| State | What it does | Without `REDIS_URL` (>1 worker/replica) |
|---|---|---|
| Rate limiter | Per-IP sliding window (20 req/5min on `/api/analyse`, etc.) | Each worker gets its own counter — the documented limits become *per-worker*, silently multiplying the effective limit |
| LLM concurrency ceiling | Caps concurrent analyst/market-picks LLM pipelines across all callers (`LLM_CONCURRENCY_LIMIT`, default 4) | Each worker gets its own ceiling — `N` workers × the configured limit can run concurrently instead of the limit as a whole |
| Refresh locks (SME, Screener, Market Picks) | One `/api/sme-signals/refresh`, `/api/screener/refresh`, or force-refresh `/api/market-picks` run at a time, each independently | Two workers can both accept the same POST and run the pipeline concurrently — wasteful (duplicate NSE/yfinance/scraper calls), not corrupting (every underlying upsert is idempotent) |
| Cached DB engine (`_DB_ENGINE`) | Reused SQLAlchemy engine | Harmless either way — each worker just gets its own connection pool, not a bug, just not shared |
| `core/cache.py`'s six-task cache (`stock_info`, `research`, `news`, ...) | The persistent shared state behind every analysis/market-picks/peers/etc. cache TTL | **Only a problem across separate *hosts*/replicas without a shared disk volume** (same-host workers already share one local disk) — each host forks its own copy of every cache entry, multiplying scraper load on Screener.in/NSE/Trendlyne/RBI by however many hosts are running, since none of them see each other's writes |

**Set `REDIS_URL`** (Docker Compose does this automatically via its `redis` service) before scaling
the backend past one worker/replica — with it set, the three rate-limiter-backed guards above are
correctly shared across workers on one host, *and* `core/cache.py` becomes genuinely cross-host shared
state (see `core/cache.py`'s own module docstring) — the fix for the single biggest ceiling on running
this backend across more than one host, since every scraped data slice was previously only ever
cached per-instance. The default `CMD` in `Dockerfile` (which currently omits `--workers`) can safely
gain one once `REDIS_URL` is set. Without it, keep the backend at a single worker/replica — the gaps
in the table still apply, cache included. The frontend has no such constraint; scale it however you like.

A Redis outage mid-flight degrades gracefully, not fatally: every `core/rate_limiter.py` call falls back to
its in-memory equivalent for that one call, and `core/cache.py` falls back to its own local-disk read/write
for that one call, each logging a warning (`redis_rate_limit_failed`, `cache_redis_read_failed`, etc.)
— the backend keeps serving requests with per-worker/per-host-only guards until Redis is reachable
again, the same "missing optional infra degrades rather than breaks" convention as
`DATABASE_URL`/`SMTP_HOST`.

## Scheduled jobs (GitHub Actions crons)

Six workflows under `.github/workflows/` run on a schedule (all also support manual
`workflow_dispatch` from the Actions tab), each with its own repository-secret requirements:

| Workflow | Schedule | Requires | What it does |
|---|---|---|---|
| `sme-cron.yml` | Weekdays 13:00 UTC (18:30 IST) | `DATABASE_URL` secret | Runs `pipelines/sme_ema_pipeline.py` directly on the GitHub-hosted runner — writes straight to Postgres, reachable from anywhere |
| `screener-cron.yml` | Weekdays 14:00 UTC (19:30 IST) | `DATABASE_URL` secret | Runs `pipelines/screener_pipeline.py` directly on the runner, same shape as the SME cron. Scheduled an hour after `sme-cron.yml` and 30 min after `watchlist-alerts-cron.yml` so the three independent jobs don't contend for the same DB connection pool at once |
| `watchlist-alerts-cron.yml` | Weekdays 13:30 UTC (19:00 IST) | `DATABASE_URL` secret, an LLM provider key secret (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GROQ_API_KEY`/`GOOGLE_API_KEY`, plus optionally `LLM_PROVIDER`/`ANALYST_MODEL`), and the `SMTP_*` secrets if you want alert emails to actually send | Runs `pipelines/watchlist_alerts.py` — re-analyses every account-owned watchlist symbol and emails a digest on a recommendation change or a large price move. Unattended, so it can't fall back to "no provider configured" the way the interactive CLI does — it fails the job loudly if no LLM key is set |
| `eod-prices-cron.yml` | Weekdays 14:15 UTC (19:45 IST) | `DATABASE_URL` secret | Runs `pipelines/eod_prices_pipeline.py` directly on the runner (self-healing 5-day gap-fill) — ingests the NSE bhavcopy + AMFI NAVs, then corporate actions/adjusted prices and the Portfolio Aggregator's nightly valuation refresh as isolated final steps of the same run. Scheduled after the bhavcopy's ~19:00 IST publish and after `sme-cron.yml` |
| `market-picks-cron.yml` | Mondays 01:30 UTC (07:00 IST) | `MARKET_PICKS_API_URL` secret (your backend's public URL) | Does **not** run the pipeline on the runner — the picks cache is a local file on the backend host, not Postgres, so a GitHub-hosted run would compute picks nobody's live site would see. Instead it calls `GET {MARKET_PICKS_API_URL}/api/market-picks?force=true` on your already-deployed backend, exactly like a user clicking "Fresh scan" |
| `live-contract-check.yml` | Weekly, Mondays 06:00 UTC | None (no secrets — hits public third-party sites directly with `RUN_LIVE_TESTS=1`) | Runs `tests_live/test_scraper_contracts.py` against the 4 highest-blast-radius scrapers (Screener.in peer table, Trendlyne resolution, NSE FII/DII flow, RBI rates) as an early-warning signal for a layout/schema change on the live site — separate from and never run by the regular `ci.yml`/`pytest tests/` suite |

If you're self-hosting these instead of using GitHub Actions (e.g. because the picks cache or
other local-disk state needs to live next to the backend), each pipeline also has a direct CLI
entrypoint suitable for a crontab entry — see [Setup & Configuration](setup.md) for the
`pipelines/sme_ema_pipeline.py`/`pipelines/screener_pipeline.py`/`pipelines/market_picks_pipeline.py`/`pipelines/watchlist_alerts.py`/
`pipelines/eod_prices_pipeline.py` crontab examples. Self-hosted crons don't need GitHub Actions repository
secrets at all — they read the same `.env`/environment the backend process already uses.

The Portfolio Aggregator's CAS PDF import and broker CSV import (`portfolio/cas_import.py`, `portfolio/csv_import.py`)
have no scheduled job — they're interactive, upload-triggered flows only (`/portfolio-aggregator`
in the browser), not something a cron re-runs.

## Environment variable checklist

Beyond an LLM provider key (see [Setup](setup.md)), production deployments should set:

- `DATABASE_URL` — required for SME signals, the Screener, Watchlist, Positions, account/API-key
  auth, the EOD price store + corporate actions, and the Portfolio Aggregator (including CAS/CSV
  import — both write into the same tables); Docker Compose sets this automatically
- `REDIS_URL` — required only if you scale the backend past one worker/replica, or across more than
  one host (see "Scaling" above); Docker Compose sets this automatically
- `ALLOWED_ORIGINS` — add your real frontend origin (comma-separated for multiple), or direct browser
  calls to the backend get CORS-rejected; defaults to `http://localhost:3000` (see `api.py`)
- `API_URL` (frontend) — point at your backend's real address if not using Docker Compose's automatic wiring
- `FRONTEND_URL` (backend) — your real frontend origin, embedded in magic-link sign-in emails; the
  `/auth/verify` page has to load on this origin to set the session cookie
- `SMTP_HOST` + `SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_FROM`/`SMTP_USE_TLS` — set these in
  any real deployment that wants magic-link and watchlist-alert emails to actually be delivered;
  without `SMTP_HOST`, sign-in tokens are still created but never emailed (see [Setup](setup.md#account--magic-link-auth))
- `SENTRY_DSN` (+ optionally `SENTRY_ENVIRONMENT`) — optional but recommended in production for
  error visibility; see the "Error tracking / APM" note above
- `TRUSTED_PROXY_SECRET` — set on both backend and frontend once a reverse proxy/CDN sits in front
  of the frontend (see "Real client IPs for per-IP rate limiting" above); safe to leave unset otherwise
- `LOG_LEVEL=INFO` (default) — bump to `DEBUG` temporarily when diagnosing an issue, not left on in steady state
- `LLM_CONCURRENCY_LIMIT` (default `4`) / `EXECUTOR_MAX_WORKERS` (default `16`) — throughput knobs,
  fine to leave at their defaults. Raise the first only if your LLM provider's own concurrency
  allowance is higher than 4 and analyses are queueing; keep it below `EXECUTOR_MAX_WORKERS` so
  quick requests (validate, prices) aren't starved by in-flight analyses. Both are per-worker
  unless `REDIS_URL` is set (see "Scaling" above)

Also run `alembic upgrade head` (fresh DB) or the stamp-then-upgrade pair (existing pre-Alembic DB)
as part of your deploy process once `DATABASE_URL` is set — see "Database schema" above.

These aren't app env vars, but **GitHub Actions repository secrets** needed if you use the
scheduled-cron workflows above rather than self-hosting: `DATABASE_URL` (`sme-cron.yml`,
`screener-cron.yml`, `watchlist-alerts-cron.yml`, `eod-prices-cron.yml`), an LLM provider key +
`SMTP_*` secrets (`watchlist-alerts-cron.yml` only), and `MARKET_PICKS_API_URL`
(`market-picks-cron.yml`) — your backend's public URL, required for the weekly refresh to have
anywhere to send its `?force=true` request (see [Setup](setup.md#market-picks-pipeline)).
