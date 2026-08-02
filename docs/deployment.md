# Deployment

Two paths: Docker Compose (fastest to get running), or a manual deployment where you run the
backend and frontend yourself. Both need the same environment variables — see
[Setup & Configuration](setup.md) for the full list; this doc only calls out what's specific to
running in production rather than `--reload`/`npm run dev`.

## Docker Compose

```bash
cp .env.example .env   # add at least one LLM provider key
docker compose up --build
docker compose exec backend alembic upgrade head   # first run only — creates all 21 tables
```

This starts four services (`docker-compose.yml`):

- **postgres** — Postgres 16, with a named volume so data survives `docker compose down` (not `down -v`)
- **redis** — `redis:7-alpine`, persisted via a named volume; wired into the `backend` service's
  `REDIS_URL` automatically (see "Scaling" below)
- **backend** — `Dockerfile` at the repo root, single `uvicorn` worker (see "Scaling" below), reads `.env`
  for provider keys and gets `DATABASE_URL`/`REDIS_URL`/`ALLOWED_ORIGINS` set automatically by compose
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
`docker compose exec backend alembic stamp 0001_baseline_schema` followed by
`docker compose exec backend alembic upgrade head` instead — plain `upgrade head` will fail trying
to re-create the 11 tables that already exist, and a bare `stamp head` would skip creating the 10
newer tables for real. If the database is already fully caught up, neither is needed.

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

**Database schema**: run `alembic upgrade head` once against a fresh database, or `alembic stamp
0001_baseline_schema` followed by `alembic upgrade head` against a database that already has the
original 11 tables from before this app used Alembic — see
[Setup & Configuration](setup.md#database-schema-setup-alembic) for the full distinction. From
here on, schema changes ship as new Alembic revisions (`alembic revision --autogenerate`, then
`alembic upgrade head` on deploy) rather than hand-edited `db/schema.sql` guards or ad-hoc
`metadata.create_all()` calls. This replaces the old workflow documented in earlier versions of
this file; `sme_ema_pipeline.py --setup-db`/`--reset-db` and `screener_pipeline.py --setup-db`/
`--reset-db` also now stamp Alembic head automatically after their own `create_all()`/`drop_all()`
calls, so a deployment that provisions its schema through one of those CLI flags instead of
Alembic directly still ends up in a state `alembic upgrade head` can build on later without
conflict.

**Error tracking / APM (optional)**: set `SENTRY_DSN` (and optionally `SENTRY_ENVIRONMENT`,
default `production`) to forward every error-level `observability.log_event()` call to a
Sentry-compatible ingest endpoint (`error_tracking.py`). This is genuinely optional — `sentry-sdk`
is already in `requirements.txt`, but without `SENTRY_DSN` set the whole module is a no-op and
logging behaves exactly as it did before this existed. Worth setting in any production deployment
that doesn't already have another way to get paged on a backend error; there's no equivalent
signal on the frontend.

**Real client IPs for per-IP rate limiting**: the Next.js frontend talks to the FastAPI backend
server-to-server (see "Proxy routes" in CLAUDE.md) — without anything further, every one of
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

`rate_limiter.py` backs three kinds of guard state — the sliding-window rate limiter, the LLM
concurrency ceiling, and a set of single-run refresh locks (SME, Screener, Market Picks, each its
own named lock on the same primitive) — with Redis when `REDIS_URL` is set, falling back to
the same in-memory-per-process behavior this app had before Redis support existed when it's unset:

| State | What it does | Without `REDIS_URL` (>1 worker/replica) |
|---|---|---|
| Rate limiter | Per-IP sliding window (20 req/5min on `/api/analyse`, etc.) | Each worker gets its own counter — the documented limits become *per-worker*, silently multiplying the effective limit |
| LLM concurrency ceiling | Caps concurrent analyst/market-picks LLM pipelines across all callers | Each worker gets its own ceiling — `N` workers × the configured limit can run concurrently instead of the limit as a whole |
| Refresh locks (SME, Screener, Market Picks) | One `/api/sme-signals/refresh`, `/api/screener/refresh`, or force-refresh `/api/market-picks` run at a time, each independently | Two workers can both accept the same POST and run the pipeline concurrently — wasteful (duplicate NSE/yfinance/scraper calls), not corrupting (every underlying upsert is idempotent) |
| Cached DB engine (`_DB_ENGINE`) | Reused SQLAlchemy engine | Harmless either way — each worker just gets its own connection pool, not a bug, just not shared |
| `cache.py`'s six-task cache (`stock_info`, `research`, `news`, ...) | The persistent shared state behind every analysis/market-picks/peers/etc. cache TTL | **Only a problem across separate *hosts*/replicas without a shared disk volume** (same-host workers already share one local disk) — each host forks its own copy of every cache entry, multiplying scraper load on Screener.in/NSE/Trendlyne/RBI by however many hosts are running, since none of them see each other's writes |

**Set `REDIS_URL`** (Docker Compose does this automatically via its `redis` service) before scaling
the backend past one worker/replica — with it set, the three rate-limiter-backed guards above are
correctly shared across workers on one host, *and* `cache.py` becomes genuinely cross-host shared
state (see `cache.py`'s own module docstring) — the fix for the single biggest ceiling on running
this backend across more than one host, since every scraped data slice was previously only ever
cached per-instance. The default `CMD` in `Dockerfile` (which currently omits `--workers`) can safely
gain one once `REDIS_URL` is set. Without it, keep the backend at a single worker/replica — the gaps
in the table still apply, cache included. The frontend has no such constraint; scale it however you like.

A Redis outage mid-flight degrades gracefully, not fatally: every `rate_limiter.py` call falls back to
its in-memory equivalent for that one call, and `cache.py` falls back to its own local-disk read/write
for that one call, each logging a warning (`redis_rate_limit_failed`, `cache_redis_read_failed`, etc.)
— the backend keeps serving requests with per-worker/per-host-only guards until Redis is reachable
again, the same "missing optional infra degrades rather than breaks" convention as
`DATABASE_URL`/`SMTP_HOST`.

## Scheduled jobs (GitHub Actions crons)

Six workflows under `.github/workflows/` run on a schedule (all also support manual
`workflow_dispatch` from the Actions tab), each with its own repository-secret requirements:

| Workflow | Schedule | Requires | What it does |
|---|---|---|---|
| `sme-cron.yml` | Weekdays 13:00 UTC (18:30 IST) | `DATABASE_URL` secret | Runs `sme_ema_pipeline.py` directly on the GitHub-hosted runner — writes straight to Postgres, reachable from anywhere |
| `screener-cron.yml` | Weekdays 14:00 UTC (19:30 IST) | `DATABASE_URL` secret | Runs `screener_pipeline.py` directly on the runner, same shape as the SME cron. Scheduled an hour after `sme-cron.yml` and 30 min after `watchlist-alerts-cron.yml` so the three independent jobs don't contend for the same DB connection pool at once |
| `watchlist-alerts-cron.yml` | Weekdays 13:30 UTC (19:00 IST) | `DATABASE_URL` secret, an LLM provider key secret (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GROQ_API_KEY`/`GOOGLE_API_KEY`, plus optionally `LLM_PROVIDER`/`ANALYST_MODEL`), and the `SMTP_*` secrets if you want alert emails to actually send | Runs `watchlist_alerts.py` — re-analyses every account-owned watchlist symbol and emails a digest on a recommendation change or a large price move. Unattended, so it can't fall back to "no provider configured" the way the interactive CLI does — it fails the job loudly if no LLM key is set |
| `eod-prices-cron.yml` | Weekdays 14:15 UTC (19:45 IST) | `DATABASE_URL` secret | Runs `eod_prices_pipeline.py` directly on the runner (self-healing 5-day gap-fill) — ingests the NSE bhavcopy + AMFI NAVs, then corporate actions/adjusted prices and the Portfolio Aggregator's nightly valuation refresh as isolated final steps of the same run. Scheduled after the bhavcopy's ~19:00 IST publish and after `sme-cron.yml` |
| `market-picks-cron.yml` | Mondays 01:30 UTC (07:00 IST) | `MARKET_PICKS_API_URL` secret (your backend's public URL) | Does **not** run the pipeline on the runner — the picks cache is a local file on the backend host, not Postgres, so a GitHub-hosted run would compute picks nobody's live site would see. Instead it calls `GET {MARKET_PICKS_API_URL}/api/market-picks?force=true` on your already-deployed backend, exactly like a user clicking "Fresh scan" |
| `live-contract-check.yml` | Weekly, Mondays 06:00 UTC | None (no secrets — hits public third-party sites directly with `RUN_LIVE_TESTS=1`) | Runs `tests_live/test_scraper_contracts.py` against the 4 highest-blast-radius scrapers (Screener.in peer table, Trendlyne resolution, NSE FII/DII flow, RBI rates) as an early-warning signal for a layout/schema change on the live site — separate from and never run by the regular `ci.yml`/`pytest tests/` suite |

If you're self-hosting these instead of using GitHub Actions (e.g. because the picks cache or
other local-disk state needs to live next to the backend), each pipeline also has a direct CLI
entrypoint suitable for a crontab entry — see [Setup & Configuration](setup.md) for the
`sme_ema_pipeline.py`/`screener_pipeline.py`/`market_picks_pipeline.py`/`watchlist_alerts.py`/
`eod_prices_pipeline.py` crontab examples. Self-hosted crons don't need GitHub Actions repository
secrets at all — they read the same `.env`/environment the backend process already uses.

The Portfolio Aggregator's CAS PDF import and broker CSV import (`cas_import.py`, `csv_import.py`)
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

Also run `alembic upgrade head` (fresh DB) or the stamp-then-upgrade pair (existing pre-Alembic DB)
as part of your deploy process once `DATABASE_URL` is set — see "Database schema" above.

These aren't app env vars, but **GitHub Actions repository secrets** needed if you use the
scheduled-cron workflows above rather than self-hosting: `DATABASE_URL` (`sme-cron.yml`,
`screener-cron.yml`, `watchlist-alerts-cron.yml`, `eod-prices-cron.yml`), an LLM provider key +
`SMTP_*` secrets (`watchlist-alerts-cron.yml` only), and `MARKET_PICKS_API_URL`
(`market-picks-cron.yml`) — your backend's public URL, required for the weekly refresh to have
anywhere to send its `?force=true` request (see [Setup](setup.md#market-picks-pipeline)).
