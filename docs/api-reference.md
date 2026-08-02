# API Reference

The **request contract** for every FastAPI endpoint: method, path, auth, params, request body,
status codes, rate limits, and caching behaviour. **57 endpoints** — 29 in `backend/api.py`, 28
across `backend/routes/` (`watchlist.py` 5, `positions.py` 6, `portfolio_aggregator.py` 17).

**Division of responsibility with [`output-schema.md`](output-schema.md):**

| This doc (`api-reference.md`) | [`output-schema.md`](output-schema.md) |
|---|---|
| How to *call* an endpoint | What the response body *contains* |
| Path/query params, validation, enums, caps | Field-by-field JSON structure |
| Request bodies (Pydantic models) | Cache-file formats on disk |
| Auth, status codes, rate limits, cache TTLs | The merged `Report` shape, `MarketPick` shape |

Large response bodies are **not** duplicated here — each endpoint links out to its section in
`output-schema.md`. This doc is the contract; that doc is the payload.

Related: [`architecture.md`](architecture.md) (request flows), [`tools.md`](tools.md) (the
scrapers behind each endpoint), [`../backend/CLAUDE.md`](../backend/CLAUDE.md) (exhaustive
behavioural ground truth).

---

## Conventions

### Base URL

`http://localhost:8000` in development (`uvicorn api:app --port 8000` from `backend/`). In
normal operation the browser never calls this directly — it goes through the Next.js proxy
routes under `frontend/app/api/**/route.ts`, server-to-server. Those proxies are a passthrough
layer (plus cookie→bearer translation and client-IP forwarding); **the contract below is the
FastAPI one**.

### Authentication

Four distinct modes. Which one applies is stated per endpoint.

| Mode | Mechanism | Failure |
|---|---|---|
| **None** | — | n/a |
| **Session bearer** | `Authorization: Bearer <session_token>` (30-day token from `GET /api/auth/verify`; `auth.get_user_for_session`) | Hard `401` |
| **Owner-resolved** | Session bearer **or** `client_id` (anonymous per-browser UUID). Resolved by `routes/watchlist.py::resolve_owner()` | `422` if neither resolves |
| **API key** | `X-API-Key: <raw key>` (`auth.get_user_for_api_key`) | Hard `401` |

**Owner resolution** (`resolve_owner(token, client_id)`) — the subtlety worth internalising:

1. If an `Authorization: Bearer` header is present **and** the session is valid → owner is
   `("user", user_id)`.
2. Otherwise — including when a token was present but **expired or invalid** — fall through to
   `client_id`. An invalid token is **not** a 401 on these endpoints; they don't require being
   signed in.
3. If `client_id` is absent or doesn't match `^[a-zA-Z0-9-]{1,36}$` → `ValueError` → `422`.

A valid session always wins over a supplied `client_id`, even when both are sent (the frontend
always sends both). The two **claim** endpoints are the deliberate exception: they require a
session and return a real `401`.

`X-API-Key` is deliberately *not* `Authorization: Bearer` — reusing that header would let a
forwarded session token satisfy the API-key check.

### Symbol validation

Most path/query symbols are validated against `api._TICKER_RE`:

```text
^[A-Z0-9&\-]{1,20}$
```

Applied **after** `.upper().strip()`, so lowercase input is accepted. A non-match is `422
{"detail": "Invalid symbol."}`. Two documented exceptions:

- `GET /api/validate/{symbol}` deliberately skips `_TICKER_RE` — it legitimately accepts ISINs,
  numeric BSE scrip codes, and hyphenated Screener slugs. It enforces only non-empty and
  `len ≤ 40`.
- `GET /api/prices?symbols=` **silently drops** non-matching entries from the list rather than
  rejecting the request.

Other validators: `_DATE_RE` = `^\d{4}-\d{2}-\d{2}$`, `_EMAIL_RE` = `^[^@\s]+@[^@\s]+\.[^@\s]+$`
(plus `len ≤ 320`), `_CLIENT_ID_RE` = `^[a-zA-Z0-9-]{1,36}$`, `_is_isin` =
`^[A-Z]{2}[A-Z0-9]{9}[0-9]$`.

### Status codes

| Code | Meaning in this API |
|---|---|
| `200` | Success. Also the status of an SSE stream whose *content* is an error frame. |
| `201` | Created — `POST /api/api-keys`, `POST /api/portfolio/{profiles,accounts,assets}`, `POST /api/portfolio/assets/{id}/valuations` |
| `202` | Declared on both refresh endpoints (`status_code=202`); body is `{"started": true}` |
| `401` | No/invalid session bearer, no/invalid `X-API-Key`, or a consumed/expired magic link |
| `404` | Resource genuinely absent. Also used **instead of 403** for another user's API key id, so the endpoint never confirms whether that id exists |
| `409` | A single-flight lock is already held (refresh endpoints, `?force=true` picks); also a duplicate profile name |
| `422` | Validation — FastAPI's own param/body coercion, plus this codebase's explicit closed-enum and regex checks |
| `429` | Rate limit. Detail: `Rate limit exceeded: max N requests per Ss on this endpoint. Try again later.` |
| `503` | `DATABASE_URL` not configured, or a DB/downstream error — **always sanitized**, never raw exception text |

**Error body** is FastAPI's standard `{"detail": "..."}` for every `HTTPException`. Per-section
degradation inside a `200` body (`unavailable` flags, `null` sections) is a separate, deliberate
mechanism — see [Response-contract inconsistencies](#response-contract-inconsistencies).

**Sanitized 503**: every DB-backed handler catches broad exceptions, logs the real message via
`log_event(..., level="error")`, and returns a fixed string (`"Database error. See server
logs."`, `"DATABASE_URL not configured."`, etc.). Raw `str(exc)` never reaches a client. The two
SSE endpoints use the same constant, `_SANITIZED_ERROR` = `"An internal error occurred. See
server logs."`

**409 takes priority over 429** on `POST /api/sme-signals/refresh`, `POST /api/screener/refresh`,
and `GET /api/market-picks?force=true`: the lock is acquired *first*, and a subsequent rate-limit
rejection releases it before returning 429. A rejected duplicate request therefore never steals
the lock from a real in-flight run.

### Rate limiting

Sliding window via `rate_limiter.is_allowed()` — Redis-shared across workers when `REDIS_URL` is
set, per-process in-memory otherwise. Keyed `"<bucket>:<client_ip>"`, where `client_ip` is
`api._client_ip(request)`, **not** `request.client.host`.

> **Disclosed limitation.** `_client_ip()` only trusts `X-Forwarded-For` when the request also
> carries a matching `X-Internal-Proxy-Secret` header (env `TRUSTED_PROXY_SECRET`, unset by
> default), and only when the forwarded chain has exactly one entry — an ambiguous multi-hop
> chain is refused rather than guessed at. Without the secret configured on both the backend and
> the Next.js frontend, **every per-IP bucket below collapses into one bucket for the whole
> site**, since every request arrives from the Next.js server's own IP. `api.py` logs
> `startup_trusted_proxy_secret_unset` at boot when `ALLOWED_ORIGINS` looks non-default and the
> secret is missing.

Two limits are keyed by something other than IP: `auth_request_link_email:<email>` (per target
address) and `api_v1:<user_id>` (per account).

### Caching

Endpoint-level caching uses `cache.py` (`backend/output/<SYMBOL>/<task>.json`, mirrored to Redis
when `REDIS_URL` is set). Freshness is always re-derived from `_meta.fetched_at` against the
current `TTL_HOURS` map, so a TTL change takes effect immediately on already-written entries.

| Cache task | TTL | Backs |
|---|---|---|
| `stock_info`, `news` | 1 h | `/api/analyse` (six-task pipeline) |
| `research`, `analysis` | 24 h | `/api/analyse`; `analysis` also read by `/api/consolidated` |
| `shareholding`, `mf_holdings` | 168 h | `/api/analyse` |
| `filings` | 1 h (default — not in `TTL_HOURS`) | `/api/analyse`, `/api/watchlist/calendar` |
| `price_history` | 6 h | `/api/prices/history/{symbol}` |
| `peers` | 24 h | `/api/peers/{symbol}` |
| `financials` | 24 h | `/api/financials/{symbol}` |
| `insider_activity` | 24 h | `/api/insider-activity/{symbol}` |
| `street_consensus` | 24 h | `/api/street-consensus/{symbol}` |
| `shareholding_detail` | 168 h | `/api/shareholding-detail/{symbol}` |
| `index_history` | 24 h | `?benchmark=true`, `/api/market-picks/history` (pseudo-symbol `NSEI`) |

Market Picks has its own file cache, `backend/output/_market_picks/picks.json`, at **192 h**
(7-day cron cadence + 24 h slack) — not part of `TTL_HOURS`.

**Failures are not cached** on `/api/peers`, `/api/financials`, `/api/shareholding-detail`,
`/api/insider-activity`, and `/api/street-consensus`. A transient scrape failure is retried on
the next request rather than locked in for the full TTL. The two multi-section endpoints cache
only when **every** section succeeded.

---

## All 57 endpoints

Auth column: `—` none · `session` session bearer · `owner` session-or-`client_id` · `key`
`X-API-Key`.

| # | Method | Path | Auth | Purpose |
|---|---|---|---|---|
| 1 | GET | `/` | — | Service banner |
| 2 | GET | `/health` | — | Liveness probe |
| 3 | GET | `/api/validate/{symbol}` | — | Resolve a ticker / ISIN / company name / BSE slug |
| 4 | GET | `/api/analyse/{symbol}` | — | **SSE** — full single-stock analysis pipeline |
| 5 | GET | `/api/market-picks` | — | **SSE** — weekly picks, cached or freshly scanned |
| 6 | GET | `/api/market-picks/status` | — | Picks cache metadata; no pipeline run |
| 7 | GET | `/api/market-picks/history` | — | Per-symbol track record, or one day's snapshot |
| 8 | GET | `/api/prices` | — | Bulk LTP + day change% (≤ 50 symbols) |
| 9 | GET | `/api/prices/history/{symbol}` | — | Daily-close series, optional Nifty benchmark |
| 10 | GET | `/api/peers/{symbol}` | — | Screener peer table + percentiles + P/E anchor |
| 11 | GET | `/api/financials/{symbol}` | — | Multi-year P&L / BS / CF + DCF + concalls |
| 12 | GET | `/api/shareholding-detail/{symbol}` | — | Individually-named shareholders (NSE XBRL) |
| 13 | GET | `/api/insider-activity/{symbol}` | — | Insider trades + bulk/block deals |
| 14 | GET | `/api/street-consensus/{symbol}` | — | Trendlyne articles + numeric consensus |
| 15 | GET | `/api/verdict-history/{symbol}` | — | Stored daily verdicts, scored vs. live price |
| 16 | GET | `/api/sme-signals` | — | SME golden/death crosses, or current regime |
| 17 | GET | `/api/sme-signals/{symbol}/history` | — | Stored EMA series + cross outcomes |
| 18 | POST | `/api/sme-signals/refresh` | — | Run the SME pipeline in the background |
| 19 | GET | `/api/screener` | — | Filter/sort the NIFTY 500 stored-metrics table |
| 20 | POST | `/api/screener/refresh` | — | Run the screener pipeline in the background |
| 21 | GET | `/api/consolidated/{symbol}` | — | Read-only roll-up across the three modes |
| 22 | POST | `/api/auth/request-link` | — | Email a magic sign-in link |
| 23 | GET | `/api/auth/verify` | — | Consume a magic link, issue a session |
| 24 | GET | `/api/auth/me` | session | Current user |
| 25 | POST | `/api/auth/logout` | session | Delete the session row (best-effort) |
| 26 | POST | `/api/api-keys` | session | Mint an API key (raw key returned **once**) |
| 27 | GET | `/api/api-keys` | session | List keys + tier + usage |
| 28 | DELETE | `/api/api-keys/{key_id}` | session | Revoke a key |
| 29 | GET | `/api/v1/consolidated/{symbol}` | key | Public v1 — same payload as #21 |
| 30 | GET | `/api/watchlist` | owner | List starred stocks |
| 31 | GET | `/api/watchlist/calendar` | — | Corporate-action + change roll-up for a symbol list |
| 32 | POST | `/api/watchlist` | owner | Star a stock |
| 33 | DELETE | `/api/watchlist/{symbol}` | owner | Unstar a stock |
| 34 | POST | `/api/watchlist/claim` | session | Claim anonymous rows onto the account |
| 35 | GET | `/api/positions` | owner | List "I bought this" positions |
| 36 | POST | `/api/positions` | owner | Mark a stock bought (upsert) |
| 37 | PATCH | `/api/positions/{symbol}` | owner | Set/clear the share count |
| 38 | DELETE | `/api/positions/{symbol}` | owner | Remove a position |
| 39 | POST | `/api/positions/claim` | session | Claim anonymous rows onto the account |
| 40 | GET | `/api/portfolio/concentration` | owner | Capital-weighted sector concentration |
| 41 | GET | `/api/portfolio/profiles` | — | List net-worth profiles |
| 42 | POST | `/api/portfolio/profiles` | — | Create a profile |
| 43 | GET | `/api/portfolio/accounts` | — | List a profile's accounts |
| 44 | POST | `/api/portfolio/accounts` | — | Create an account |
| 45 | PATCH | `/api/portfolio/accounts/{account_id}` | — | Update an account |
| 46 | DELETE | `/api/portfolio/accounts/{account_id}` | — | Delete an empty account |
| 47 | GET | `/api/portfolio/assets` | — | List an account's assets + latest valuation |
| 48 | POST | `/api/portfolio/assets` | — | Create an asset (+ initial valuation, + holding) |
| 49 | PATCH | `/api/portfolio/assets/{asset_id}` | — | Update asset and/or holding fields |
| 50 | DELETE | `/api/portfolio/assets/{asset_id}` | — | Delete an asset and its children |
| 51 | POST | `/api/portfolio/assets/{asset_id}/valuations` | — | Upsert a dated valuation |
| 52 | GET | `/api/portfolio/networth` | — | Net-worth summary for a profile |
| 53 | POST | `/api/portfolio/refresh-valuations` | — | Auto-value `mf`/`stock` assets |
| 54 | GET | `/api/portfolio/xirr` | — | Per-asset + pooled XIRR |
| 55 | POST | `/api/portfolio/import-cas` | — | Import a CAMS/KFintech detailed CAS PDF |
| 56 | POST | `/api/portfolio/import-csv/preview` | — | Preview + map a broker CSV/XLSX |
| 57 | POST | `/api/portfolio/import-csv` | — | Import mapped broker rows |

Endpoints 41–57 have **no authentication and no ownership scoping** — any caller may read or
mutate any profile's data by id. This is a disclosed, deliberate scope call (a personal
localhost/Tailscale tool), documented in `routes/portfolio_aggregator.py`'s own module docstring
and repeated at [that section](#portfolio-aggregator-4157) below.

---

## Service

### `GET /` — 1

No auth, no rate limit, no params. `200` with `{"service", "status", "message"}`.

### `GET /health` — 2

No auth, no rate limit, no params. `200` with `{"status": "ok"}`. Always succeeds if the process
is up; it touches no database, cache, or downstream service, so it is a liveness probe only, not
a readiness probe.

---

## Stock analysis

### `GET /api/validate/{symbol}` — 3

Resolves a user-typed string to a tradeable NSE/BSE ticker. Handles three input forms (ISIN,
BSE-forced slug, ticker/company name) — see backend/CLAUDE.md's "Symbol validation flow".

| | |
|---|---|
| **Auth** | None |
| **Path param** | `symbol` — **not** `_TICKER_RE`-validated. Uppercased and trimmed; only `1 ≤ len ≤ 40` is enforced, because ISINs, numeric BSE scrip codes, and hyphenated Screener slugs are all legitimate inputs here |
| **Query** | `exchange` — string, optional, default `""`. Case-insensitive; only the value `BSE` is meaningful (forces Screener-slug resolution). Any other value is ignored |
| **Rate limit** | `validate` — 30 / 60 s per IP |
| **Caching** | ISIN→symbol map from NSE `EQUITY_L.csv`, 1 h in-process (`_ISIN_CACHE`), not `cache.py`. No per-symbol caching |
| **Status** | `200` always on a resolved *or* unresolved lookup · `422` empty or > 40 chars · `429` |

A miss is **not** a 404 — it's `200` with `{"found": false, "valid": false, "symbol", "company":
"", "suggestions": []}`. A found result adds `exchange`, `isin`, `suspended`, and up to 6
`suggestions`; an NSE hit with a resolvable ISIN also gets `bse_symbol`. Every upstream lookup
(NSE autocomplete, BSE-by-ISIN, Screener search/company page, yfinance) is individually
try/excepted to `{}` or `[]`, so an upstream outage degrades to "not found", never a 5xx.

### `GET /api/analyse/{symbol}` — 4 · SSE

The main pipeline: cache check → parallel fetch of the six data slices → schema normalize →
signal engine → LLM analyst → merged report.

| | |
|---|---|
| **Auth** | None |
| **Path param** | `symbol` — `_TICKER_RE` |
| **Query** | `force` — bool, optional, default `false`. Marks all six tasks stale, bypassing their TTLs. Does **not** bypass the 6 h `price_history` cache the technical signal reads, so that signal can lag a forced refresh by up to 6 h |
| **Rate limit** | `analyse` — 20 / 300 s per IP |
| **Concurrency** | Takes one slot from the global LLM ceiling (`LLM_CONCURRENCY_LIMIT`, default 4) for the analyst call. Exhaustion is an in-stream error frame, not a 429 |
| **Caching** | Reads/writes the six `ALL_DATA_TASKS` caches plus `analysis` (24 h). The analyst re-runs only when at least one input task was stale or `analysis` itself is stale |
| **Response** | `text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no` |
| **Status** | `200` (stream) · `422` invalid symbol · `429` |

**Only `422` and `429` are real HTTP statuses.** They are raised before the `StreamingResponse`
is constructed. Everything after the stream opens — including total failure — arrives as an
in-stream frame on HTTP `200`.

Every frame is `data: <json>\n\n`. Heartbeats are `: heartbeat\n\n` (an SSE comment, no `data:`),
emitted every 15 s while the analyst runs.

| `event` | Payload | When |
|---|---|---|
| `start` | `stale: string[]`, `cached: string[]` | Always first — which of the six tasks will be fetched vs. served from cache |
| `task_done` | `task: string`, `ok: true` | One per stale task, as it completes |
| `task_done` | `task: string`, `ok: false`, `error: string` | Task fetch raised. `error` is always the sanitized constant |
| `analysing` | — | Analyst call started (omitted entirely on the cache-hit path) |
| `done` | `report: Report` | Terminal success. See [output-schema.md § Merged report shape](output-schema.md#merged-report-shape) |
| `error` | `message: string` | Terminal failure |

Three distinct `error` messages, all terminal:

- `"Symbol not valid: <schema error>"` — `stock_info` failed `schemas.validate()`.
- `"The AI analyst is at capacity right now — please try again in a moment."` — the LLM
  concurrency slot could not be acquired.
- `"An internal error occurred. See server logs."` — any unhandled exception.

A client disconnect mid-analysis does **not** cancel the LLM call: the background task keeps
running, holds its concurrency slot for the call's real duration, and still persists its result
to the `analysis` cache and `verdict_history`.

---

## Market picks

### `GET /api/market-picks` — 5 · SSE

Serves the cached weekly picks, or runs the full six-phase pipeline.

| | |
|---|---|
| **Auth** | None |
| **Query** | `force` — bool, optional, default `false`. Skips the cache and runs a fresh pipeline |
| **Rate limit** | `market_picks_force` — 3 / 3600 s per IP, **applied only when `force=true`**. Cached reads are unlimited |
| **Single-flight** | `market_picks_refresh` lock, 3600 s TTL. Held for `?force=true` *and* for a cold/stale-cache run — several concurrent visitors hitting an expired cache would otherwise each launch a full pipeline |
| **Caching** | `backend/output/_market_picks/picks.json`, 192 h. A run producing zero picks, or one `pipeline.healthy` flags as degraded, is **not** cached |
| **Status** | `200` (stream) · `409` `"A fresh market-picks scan is already running."` (force path only) · `429` |

`409` and `429` are the only real HTTP statuses, and only on the `?force=true` path. On the
non-force path a lost lock race is an in-stream `error` frame instead, since the stream has
already opened by then.

Frames, in pipeline order. Heartbeats (`: heartbeat\n\n`) every 20 s of queue silence.

| `event` | Payload | Phase |
|---|---|---|
| `picks_start` | `sources: {name, type}[]` | 1 — scrape begins |
| `source_done` | `source`, `articles: int`, `status: "ok" \| "empty"` | 1 — per source |
| `extracting` | `total_articles: int`, `total_batches: int` | 2 — LLM extraction begins |
| `extract_progress` | `batch: int`, `total_batches: int`, `found_so_far: int` | 2 — per batch (cache hit or fresh call) |
| `consolidating` | `total_raw: int`, `unique: int` | 3 |
| `validate_progress` | `symbol: string`, `ok: bool` | 3 — per candidate symbol |
| `researching` | `stocks: string[]`, `total: int` | 4 |
| `stock_researched` | `symbol: string`, `ok: bool` | 4 — per stock |
| `scoring` | — | 6 |
| `analysis_error` | `symbols: string[]`, `reason: string` (≤ 200 chars) | 5 — a batch's LLM call failed; other batches continue |
| `done` | `picks: MarketPick[]`, `generated_at`, `total_picks: int`, `from_cache: bool` | Terminal |
| `error` | `message: string` | Terminal |

`done.picks` — see [output-schema.md § `MarketPick` shape](output-schema.md#marketpick-shape). A
cache hit emits `done` with `from_cache: true` immediately and nothing else.

Terminal `error` messages: `"No valid stock picks found across all sources."`, `"A market-picks
scan is already in progress — please try again in a moment."`, `"The server is at capacity for
AI-driven pipelines right now — please try again in a moment."`, and the sanitized constant.

`analysis_error.reason` is a truncated raw exception string — the **one place** an SSE stream
surfaces upstream exception text rather than the sanitized constant. It originates inside the
pipeline's own batch loop, not `api.py`'s sanitizing wrapper.

### `GET /api/market-picks/status` — 6

Cache metadata only; never runs the pipeline.

| | |
|---|---|
| **Auth** | None |
| **Params** | None |
| **Rate limit** | `market_picks_status` — 60 / 60 s per IP |
| **Status** | `200` always |

`{"last_run_at": string | null, "cache_fresh": bool, "next_scheduled_at": string}`.
`last_run_at` is present even once the cache is stale (unlike the picks-serving path, "stale"
and "absent" must be distinguishable here) and `null` only when the file is missing or
unparseable. `next_scheduled_at` is computed from constants mirroring
`.github/workflows/market-picks-cron.yml` (Monday 01:30 UTC), hand-synced — there is no shared
source of truth between a GitHub Actions cron expression and this computation.

### `GET /api/market-picks/history` — 7

Two code paths on one handler.

| | |
|---|---|
| **Auth** | None |
| **Query** | `date` — string, optional, default `null`. Must match `^\d{4}-\d{2}-\d{2}$` |
| **Rate limit** | `market_picks_history` — 60 / 60 s per IP |
| **Caching** | Reads `backend/output/_history/*.json` (permanent snapshots). The Nifty benchmark series is cached under the `NSEI` pseudo-symbol, `index_history`, 24 h, coverage-based (a request is served whenever the cached range already *contains* it) |
| **Status** | `200` · `404` `"No market-picks snapshot found for <date>"` · `422` malformed `date` · `429` |

**Without `date`** — aggregates every snapshot into `{symbols[], snapshot_count, win_rate,
tier_stats, avg_alpha_pct, available_dates[]}`. `win_rate`/`avg_alpha_pct` are `null` when no
symbol has both a `price_then` and `price_now` (older snapshots predate those fields, and are
never back-filled with a guess). A yfinance outage degrades every `nifty_change_pct`/`alpha_pct`
to `null` rather than failing the request.

**With `date`** — skips aggregation and returns `{"date", "picks": [...]}` verbatim in the shape
`_save_history()` wrote (six persisted fields per pick, not the full live `MarketPick`).
`available_dates` from the aggregated response bounds a date picker without a second round trip.

---

## Prices

### `GET /api/prices` — 8

| | |
|---|---|
| **Auth** | None |
| **Query** | `symbols` — string, **required**. Comma-separated. Each entry is matched against `_TICKER_RE` **before** being uppercased, then uppercased (`api.py:1328`); **non-matching entries are silently dropped**, and the surviving list is truncated to **50**. Note the ordering: `_TICKER_RE` is `^[A-Z0-9&\-]{1,20}$` and case-sensitive, so **lowercase input is silently dropped here**, unlike every other `_TICKER_RE` call site, which uppercases first (see `routes/watchlist.py:123-127`, which carries a comment about exactly this). `?symbols=tcs` returns nothing for that symbol |
| **Rate limit** | `prices` — 30 / 60 s per IP (tightest of the read endpoints: up to 50 yfinance calls per request) |
| **Caching** | None — live `yfinance.fast_info` per symbol, fanned out concurrently |
| **Status** | `200` · `422` `symbols` omitted entirely · `429` |

`{"prices": {"<SYMBOL>": {"price": float, "change_pct": float | null}}}`. A symbol that resolves
on neither `.NS` nor `.BO` maps to `{}` — present as a key, empty as a value. `change_pct` is
`null` (never a fabricated `0.0`) when the previous close is unavailable.

`symbols=ZZZ,!!!` returns `200` with an empty `prices` object, not a `422` — an entirely invalid
list is indistinguishable from an entirely unresolvable one.

### `GET /api/prices/history/{symbol}` — 9

| | |
|---|---|
| **Auth** | None |
| **Path param** | `symbol` — `_TICKER_RE` |
| **Query** | `days` — int, optional, default `180`, `7 ≤ days ≤ 365` (FastAPI-enforced → `422`)<br>`benchmark` — bool, optional, default `false` |
| **Rate limit** | `prices_history` — 60 / 60 s per IP |
| **Caching** | `price_history`, 6 h — shared with `signals/technical.py`'s RSI/EMA computation |
| **Status** | `200` · `422` invalid symbol or out-of-range `days` · `429` |

`{"symbol", "dates": string[], "closes": float[]}`, plus `benchmark` when opted in:
`{"stock_change_pct", "nifty_change_pct", "alpha_pct"}` or `null` when there are fewer than 2
closes or the Nifty fetch fails. Opt-in because most callers (the quarterly-trend sparklines)
aren't plotting a price series at all.

---

## Per-symbol research add-ons

Five on-demand endpoints outside `ALL_DATA_TASKS`, each independently cached and fetched by the
frontend *after* the main report loads, plus the verdict timeline. All are unauthenticated,
`_TICKER_RE`-validated, and return `200` with an empty-but-valid payload for the common
"nothing to report" case. Response shapes: [output-schema.md § Standalone endpoint response
shapes](output-schema.md#standalone-endpoint-response-shapes).

| Endpoint | Rate limit (per IP) | Cache task / TTL | Failure signalling |
|---|---|---|---|
| `GET /api/peers/{symbol}` — 10 | `peers` 30 / 60 s | `peers` / 24 h | **None** — empty result only |
| `GET /api/financials/{symbol}` — 11 | `financials` 30 / 60 s | `financials` / 24 h | **None** — `null` sections only |
| `GET /api/shareholding-detail/{symbol}` — 12 | `shareholding_detail` 30 / 60 s | `shareholding_detail` / 168 h | `unavailable: bool` |
| `GET /api/insider-activity/{symbol}` — 13 | `insider_activity` 30 / 60 s | `insider_activity` / 24 h | `insider_trades_unavailable`, `bulk_block_deals_unavailable` |
| `GET /api/street-consensus/{symbol}` — 14 | `street_consensus` 30 / 60 s | `street_consensus` / 24 h | `articles_unavailable`, `numeric_consensus_unavailable` |

Common contract for all five:

- **Params**: `symbol` path param only. No query params.
- **Status**: `200` · `422` invalid symbol · `429`. Never 404 — an unknown symbol yields an empty
  payload, not an error.
- **Failures are never cached.** A `{"error": ...}` from the underlying scraper returns the empty
  shape and increments a counter in `backend/output/_scraper_error_counters/<name>.json`
  (`scraper_error_counters.record_scraper_error`) with a `warning`-level log line. The next
  request retries.
- **Multi-section endpoints cache only on full success** (#13, #14) — caching a partial failure
  would lock a real outage in as a confident-looking empty answer for 24 h.
- **Cache-hit back-fill**: entries written before a field existed are back-filled with that
  field's default on read (`absolute_anchor: null` for #10; `profit_loss`/`balance_sheet`/
  `cash_flow`/`dcf: null` and `concalls: []` for #11; `unavailable: false` for #12; the
  `*_unavailable` flags for #13/#14), so every response has a self-describing shape regardless
  of when it was cached.

> **Contract gap.** #10 and #11 have no `unavailable` flag at all. A Screener outage and a
> company Screener genuinely has no data for produce byte-identical responses. Their siblings
> #12–#14 solve exactly this problem with an explicit boolean. See
> [Response-contract inconsistencies](#response-contract-inconsistencies).

### `GET /api/verdict-history/{symbol}` — 15

| | |
|---|---|
| **Auth** | None |
| **Path param** | `symbol` — `_TICKER_RE` |
| **Rate limit** | `verdict_history` — 60 / 60 s per IP |
| **Caching** | None. Reads the `verdict_history` Postgres table; one live yfinance call for scoring |
| **Status** | `200` · `422` invalid symbol · `429` |

**Never 503.** Unlike every other DB-backed endpoint, an unset `DATABASE_URL` or a failed query
degrades to `{"symbol", "history": [], "win_rate": null, "scored_count": 0}` on `200` — this is a
supplementary strip on top of an already-loaded report, so a DB hiccup must not read as "the
whole analysis failed".

The live-price fetch is skipped entirely below 2 stored entries (the frontend timeline needs 2 to
render), and its failure degrades `return_since_pct`/`outcome` to `null` per entry. Only `BUY`
and `SELL` verdicts are ever graded — a `HOLD` makes no directional claim.

---

## SME signals

### `GET /api/sme-signals` — 16

| | |
|---|---|
| **Auth** | None |
| **Query** | `lookback` — int, optional, default `5`, `1 ≤ n ≤ 30` (FastAPI-enforced)<br>`direction` — string, optional, default `"all"`. **Closed enum**: `all` \| `golden` \| `death`<br>`view` — string, optional, default `"crosses"`. **Closed enum**: `crosses` \| `regime` |
| **Rate limit** | `sme_signals` — 60 / 60 s per IP |
| **Caching** | None — direct Postgres query per request |
| **Status** | `200` · `422` out-of-enum `direction`/`view` or out-of-range `lookback` · `429` · `503` no `DATABASE_URL`, or a sanitized DB error |

`lookback` and `direction` are **accepted but ignored** when `view=regime` — there is no
cross-event window to filter in that view. `regime` returns the latest stored row for every
monitored stock, so `cross` is `null` for most rows; in `crosses` view `cross` is never null.

Response: `{signals[], total_monitored, golden_now, last_run, refreshing, golden_hit_rate_90d}`.
`refreshing` reflects the shared `sme_refresh` lock. `golden_hit_rate_90d.win_rate` is `null`
when `sample_size` is 0; a cross too recent to have resolved is excluded from the sample rather
than counted as a loss.

Both closed enums are validated *before* interpolation anywhere near SQL — `direction` is passed
as a bind parameter regardless.

### `GET /api/sme-signals/{symbol}/history` — 17

| | |
|---|---|
| **Auth** | None |
| **Path param** | `symbol` — `_TICKER_RE` |
| **Rate limit** | `sme_signal_history` — 60 / 60 s per IP |
| **Status** | `200` · `404` `"No stored EMA history for <SYM>."` · `422` invalid symbol · `429` · `503` no `DATABASE_URL` / DB error |

The **only** per-symbol endpoint that 404s on an unknown symbol — its siblings return an empty
payload. Justified here: an SME symbol with no stored series is genuinely not in this pipeline's
universe, not merely thin on data.

Returns `{symbol, name, exchange, series[], cross_events[]}`. `name`/`exchange` are `null` when
the symbol has an `ema_signals` series but no `sme_stocks` row. `cross_events[].ret_10d_pct` /
`ret_20d_pct` are `null` when fewer than N trading days have elapsed *within the stored ~3-month
window* — forward-return history is bounded by the same retention window as everything else in
the table.

### `POST /api/sme-signals/refresh` — 18

| | |
|---|---|
| **Auth** | None |
| **Body** | None |
| **Rate limit** | `sme_refresh` — 3 / 3600 s per IP |
| **Single-flight** | `sme_refresh` lock, 3600 s TTL |
| **Declared status** | `202` — body `{"started": true}` |
| **Status** | `202` · `409` `"A refresh is already running."` · `429` · `503` no `DATABASE_URL` |

Order is: `DATABASE_URL` check → lock acquire → rate limit. A rate-limit rejection **releases the
lock** before raising, so 409 takes priority over 429 when both would apply and a throttled
caller never strands the lock. Returns immediately; the pipeline runs in a background task. An
unhealthy run (empty stock list, > 50 % OHLCV error rate) is logged as
`sme_refresh_unhealthy` — the caller has already received `202` and is not told.

---

## Screener

### `GET /api/screener` — 19

| | |
|---|---|
| **Auth** | None |
| **Query** | `industry` — string, default `"all"`. Free text matched against `nse_industry`; `"all"` disables the filter. The valid set is the response's own `industries` field, not a hardcoded list<br>`ema_trend` — string, default `"all"`. **Closed enum**: `all` \| `bullish` \| `bearish`<br>`pe_max` — float, optional, `≥ 0`<br>`market_cap_min` — float, optional, `≥ 0` (₹ Cr)<br>`rsi_min` / `rsi_max` — float, optional, `0 ≤ n ≤ 100`<br>`sort` — string, default `"market_cap_cr"`. **Closed enum**: `symbol`, `current_price`, `pe_ratio`, `market_cap_cr`, `avg_volume_10d`, `rsi14`<br>`order` — string, default `"desc"`. **Closed enum**: `asc` \| `desc`<br>`limit` — int, default `100`, `1 ≤ n ≤ 500`<br>`offset` — int, default `0`, `≥ 0` |
| **Rate limit** | `screener` — 60 / 60 s per IP |
| **Status** | `200` · `422` out-of-enum `ema_trend`/`sort`/`order`, or out-of-range numerics · `429` · `503` no `DATABASE_URL` / DB error |

`sort` is interpolated into `ORDER BY` (column names can't be bind parameters) and is therefore
validated against `_SCREENER_SORT_COLUMNS` *first* — the same closed-enum-not-raw-text safety as
`direction`/`view` above. Every other filter is a bind parameter.

Numeric filters are optional and AND-ed. A `NULL` column value **excludes** that stock from that
filter rather than being treated as 0 or passing — each filter carries an explicit `IS NOT NULL`.
Ordering is always `<sort> <order> NULLS LAST, symbol ASC`.

Response: `{stocks[], total, total_monitored, industries[], last_run, refreshing}`. `total`
respects the active filters; `total_monitored` is the whole table.

### `POST /api/screener/refresh` — 20

Identical contract to #18 with its own lock and bucket: `screener_refresh` — 3 / 3600 s per IP,
`screener_refresh` lock (3600 s TTL), declared `202`, body `{"started": true}`, same
lock-then-rate-limit ordering and same `409`-over-`429` priority.

---

## Consolidated view

### `GET /api/consolidated/{symbol}` — 21

| | |
|---|---|
| **Auth** | None |
| **Path param** | `symbol` — `_TICKER_RE` |
| **Rate limit** | `consolidated` — 30 / 60 s per IP |
| **Caching** | Reads only. `analysis` cache (24 h), the picks cache (192 h), and the latest `ema_signals` row. **Never fetches, scrapes, or calls an LLM** |
| **Status** | `200` · `422` invalid symbol · `429` |

Three sections fetched concurrently, each independently `null`: never analysed / not on the
current picks list / not an SME stock. A DB error on the `sme` section alone is caught, logged,
and returns `null` for that section — it never fails the other two. No `DATABASE_URL` → `sme` is
`null`, not a 503.

Shape: [output-schema.md § `GET /api/consolidated/{symbol}`](output-schema.md#get-apiconsolidatedsymbol-and-the-api-key-gated-get-apiv1consolidatedsymbol).

---

## Accounts & magic-link auth

Passwordless, no OAuth, no separate signup — the first successful link click *is* account
creation. Only SHA-256 hashes of magic-link and session tokens are persisted.

### `POST /api/auth/request-link` — 22

| | |
|---|---|
| **Auth** | None |
| **Body** | `AuthRequestLinkRequest` — `email: str` (**required**). Lowercased and trimmed; must match `_EMAIL_RE` and be ≤ 320 chars |
| **Rate limits** | `auth_request_link` — 5 / 900 s **per IP**, *and* `auth_request_link_email:<email>` — 5 / 3600 s **per target address** |
| **Status** | `200` · `422` invalid email · `429` (either bucket) · `503` no `DATABASE_URL`, or `"Could not send sign-in link. See server logs."` |

**Always returns `{"sent": true}` on success — even when SMTP delivery failed.** The link is
created and stored regardless; a failed send is logged as `auth_link_email_not_delivered`
(`warning`) and the same link starts working once SMTP is fixed, with no re-request needed.
Reporting the failure would leak SMTP configuration state to an unauthenticated caller.

The per-address limit exists specifically because the per-IP limit alone doesn't stop an attacker
with rotating IPs from inbox-bombing one victim — each IP would otherwise get a fresh budget.

### `GET /api/auth/verify` — 23

| | |
|---|---|
| **Auth** | None (the token *is* the credential) |
| **Query** | `token` — string, **required** |
| **Rate limit** | `auth_verify` — 20 / 300 s per IP |
| **Status** | `200` · `401` `"This sign-in link is invalid, expired, or already used."` · `422` `token` omitted · `429` · `503` no `DATABASE_URL` / DB error |

`{"user": {...}, "session_token": "<raw>"}`. Token consumption is atomic (`UPDATE ... WHERE
used_at IS NULL AND expires_at > NOW() ... RETURNING`), so two concurrent clicks of the same link
cannot both win — the loser gets `401`.

`session_token` is returned exactly once here. The Next.js proxy at
`frontend/app/api/auth/verify/route.ts` is the only consumer: it strips the token into an
httpOnly `SameSite=Lax` cookie and forwards only `{user}` to page-level JS, so an XSS on the
frontend origin cannot read a live session token out of a fetch response.

### `GET /api/auth/me` — 24

| | |
|---|---|
| **Auth** | Session bearer, **required** |
| **Params** | None |
| **Rate limit** | **None** |
| **Status** | `200` `{"user": {...}}` · `401` `"Not signed in."` |

`401` — never `200` with a null user — so the frontend's `useAuth()` can distinguish "still
checking" from "confirmed signed out". An unset `DATABASE_URL` also yields `401`, not `503`: from
the caller's perspective there is no session either way.

### `POST /api/auth/logout` — 25

| | |
|---|---|
| **Auth** | Session bearer, optional |
| **Body** | None |
| **Rate limit** | **None** |
| **Status** | `200` `{"ok": true}` — unconditionally |

Best-effort: with no token, or no `DATABASE_URL`, it does nothing and still returns `{"ok":
true}`. A failed row delete is not surfaced — the Next.js route clears the cookie regardless, so
the browser is signed out either way.

---

## API key management

Session-authenticated management of long-lived keys for the `/api/v1/*` surface. A key has **no
expiry** (unlike a 30-day session) — a script can't redirect through a magic link — and is valid
until explicitly revoked.

### `POST /api/api-keys` — 26

| | |
|---|---|
| **Auth** | Session bearer, **required** |
| **Body** | `CreateApiKeyRequest` — `label: str \| None`, optional, default `null`. Trimmed and truncated to 120 chars; an empty/whitespace-only label becomes `null` |
| **Rate limit** | `api_keys_create` — 20 / 3600 s per IP |
| **Status** | `201` · `401` not signed in · `429` · `503` `"Could not create API key. See server logs."` |

The **only** response in this API that ever contains a raw API key. Only its SHA-256 hash is
persisted; the raw value is unrecoverable afterwards.

Ordering note: the rate limit is checked **before** the session, so an unauthenticated caller can
exhaust an IP's create budget and then receive `429` rather than `401` on subsequent attempts.

### `GET /api/api-keys` — 27

| | |
|---|---|
| **Auth** | Session bearer, **required** |
| **Params** | None |
| **Rate limit** | `api_keys_list` — 60 / 60 s per IP |
| **Status** | `200` · `401` · `429` · `503` `"Could not list API keys. See server logs."` |

`{"keys": [...], "tier": "free" | "pro", "usage": {"calls": int, "limit": int,
"window_seconds": 3600}}`. Key rows carry `key_prefix`, never the key or its hash, and include
revoked keys (badged in the UI).

Doubles as the usage dashboard for the `/api/v1/*` limit. `usage.calls` comes from
`rate_limiter.get_usage_count()`, a **non-mutating peek** at the same sliding window
`is_allowed()` maintains — checking usage never counts against the limit it reports on.

An unrecognised `tier` value falls back to `free` rather than being trusted.

### `DELETE /api/api-keys/{key_id}` — 28

| | |
|---|---|
| **Auth** | Session bearer, **required** |
| **Path param** | `key_id` — int (FastAPI coercion; non-numeric → `422`) |
| **Rate limit** | `api_keys_revoke` — 60 / 60 s per IP |
| **Status** | `200` `{"ok": true}` · `401` · `404` `"No such API key."` · `422` non-integer id · `429` · `503` |

**`404`, never `403`**, when the key exists but belongs to another user — indistinguishable from
a key that doesn't exist at all, so the endpoint never confirms or denies another account's key
ids. Revocation is idempotent in effect: re-revoking an already-revoked key returns `404`.

---

## Public v1 API

The only `/api/v1/*` route today — deliberately one real endpoint rather than a speculative
surface.

### `GET /api/v1/consolidated/{symbol}` — 29

| | |
|---|---|
| **Auth** | `X-API-Key: <raw key>`, **required**. Never `Authorization: Bearer` |
| **Path param** | `symbol` — `_TICKER_RE` |
| **Rate limit** | `api_v1:<user_id>` — **per account, not per IP**: 100 / 3600 s (`free`), 1000 / 3600 s (`pro`). A legitimate integration may run from a shared or rotating IP, so IP-keying would be the wrong bucket |
| **Status** | `200` · `401` `"Missing or invalid X-API-Key header."` · `422` invalid symbol · `429` |

Body is byte-identical to `GET /api/consolidated/{symbol}` — both call the same
`_consolidated_payload()` helper, extracted specifically so the two paths cannot drift. Only who
may ask, and how often, differs.

A missing `DATABASE_URL` yields `401`, not `503` — key validation is a DB read, and the handler
cannot distinguish "no key" from "can't check the key".

> **Disclosed gap.** This endpoint has **no IP-keyed rate limit at all**. The per-account limit
> only applies *after* a key validates, so failed authentication attempts are unbounded — each
> costing a DB lookup — and API-key guessing is throttled by nothing. Every other
> non-trivial endpoint in this API carries a `_rate_limit()` call before its auth check.

**Tier note.** `users.tier` exists as a column with no payment flow behind it. Every account is
`free` until an operator updates the column by hand; `/pricing` states this plainly rather than
advertising a checkout that doesn't exist.

---

## Watchlist

Owner-resolved (session **or** anonymous `client_id`). Capped at
`_MAX_WATCHLIST_ITEMS_PER_CLIENT` = **200** rows per identity.

Endpoints 30, 32, 33, 34 route through `routes/_shared.py::run_owned_db_call()`, which supplies
a uniform envelope:

1. Rate limit this call (bucket, cap, and window per endpoint).
2. `503 "DATABASE_URL not configured."` if the env var is unset.
3. Run the sync DB work in the executor.
4. Exception translation: `HTTPException` re-raised as-is · `ValueError` → **`422`** (cap
   exceeded, unresolvable owner) · `PermissionError` → **`401`** (expired session on a
   claim endpoint) · anything else → logged, then `503 "Database error. See server logs."`

`GET /api/watchlist/calendar` (31) deliberately bypasses this wrapper — it resolves no owner and
needs no `DATABASE_URL` gate.

### `GET /api/watchlist` — 30

| | |
|---|---|
| **Auth** | Owner-resolved. An invalid bearer falls through to `client_id` |
| **Query** | `client_id` — string, optional. Must match `_CLIENT_ID_RE` if it's the resolving identity |
| **Rate limit** | `watchlist_read` — 120 / 60 s per IP |
| **Status** | `200` `{"items": [...]}` · `422` no resolvable identity · `429` · `503` |

Items are `{symbol, company, exchange, addedAt}` (note the camelCase `addedAt` — the SQL aliases
it), newest first.

### `GET /api/watchlist/calendar` — 31

| | |
|---|---|
| **Auth** | **None** — takes the symbol list directly rather than querying ownership |
| **Query** | `symbols` — string, **required**. Comma-separated; each entry uppercased **then** matched against `_TICKER_RE`, truncated to 200 entries |
| **Rate limit** | `watchlist_calendar` — 30 / 60 s per IP |
| **Caching** | Reads each symbol's already-cached `filings` (1 h). **No new scraping** |
| **Status** | `200` `{"entries": [...]}` · `422` `symbols` omitted · `429` |

No `503`: an unset `DATABASE_URL` degrades `verdict_history.detect_recent_changes()` to both
flags `null`, and the filings half needs no DB at all. An all-invalid `symbols` list returns
`{"entries": []}` on `200`.

Two independent per-symbol sources, each optional: `classify_filings()` over cached filings, and
`detect_recent_changes()` (same-day recommendation flip or ≥ 10 % price move — the same two
conditions `watchlist_alerts.py`'s daily digest emails). A symbol contributing nothing from
either source is omitted entirely. Sorted: anything with a notable change first, then by
`next_results_date` ascending (entries without one sort last within their group).

### `POST /api/watchlist` — 32

| | |
|---|---|
| **Auth** | Owner-resolved |
| **Body** | `WatchlistAddRequest`:<br>`client_id: str \| None` = `null` — optional; ignored when a valid session is present<br>`symbol: str` — **required**, uppercased, `_TICKER_RE`<br>`company: str` = `""` — truncated to 200 chars<br>`exchange: str` = `"NSE"` — uppercased; **closed enum**: `NSE` \| `BSE` |
| **Rate limit** | `watchlist_write` — 60 / 60 s per IP |
| **Status** | `201`? No — **`200`** `{"items": [...]}` (the full refreshed list) · `422` invalid symbol / invalid exchange / no identity / cap exceeded · `429` · `503` |

Idempotent: `ON CONFLICT (owner_col, symbol) DO NOTHING`. A re-add of a symbol the owner already
holds is **exempt from the cap** — a double-click or retry at exactly 200 items is a harmless
no-op, not a `422`.

The count-then-insert runs under `pg_advisory_xact_lock(hashtext('watchlist:<owner_type>:<owner_value>'))`,
so concurrent adds for one owner cannot race past the cap. The owner-type segment keeps a
`client_id` string and a `user_id` integer from ever colliding on one lock.

### `DELETE /api/watchlist/{symbol}` — 33

| | |
|---|---|
| **Auth** | Owner-resolved |
| **Path param** | `symbol` — `_TICKER_RE` |
| **Query** | `client_id` — string, optional |
| **Rate limit** | `watchlist_write` — 60 / 60 s per IP (shared with #32) |
| **Status** | `200` `{"items": [...]}` · `422` · `429` · `503` |

Deleting a symbol the owner doesn't have is a silent no-op returning `200` — never `404`.

### `POST /api/watchlist/claim` — 34

Opt-in migration of an anonymous browser's rows onto the signed-in account. Nothing triggers this
automatically; sign-in alone never claims anything.

| | |
|---|---|
| **Auth** | Session bearer, **required** — a real `401`, not the usual fall-through |
| **Body** | `ClaimRequest` — `client_id: str`, **required**, `_CLIENT_ID_RE` |
| **Rate limit** | `watchlist_claim` — **5 / 3600 s** per IP (vs. 60/min for ordinary writes) |
| **Status** | `200` · `401` `"Sign in required to claim anonymous data."` (no header) or `"Your session has expired. Sign in again to claim this data."` (expired, via `PermissionError`) · `422` invalid `client_id` · `429` · `503` |

`{"claimed": int, "skipped_over_cap": int, "items": [...]}`. Rows are claimed oldest-first up to
the account's remaining room under the 200 cap; the remainder stays owned by `client_id` and is
reported as `skipped_over_cap` rather than silently dropped. A symbol the account already owns
keeps the account's row and discards the anonymous duplicate.

Takes **two** advisory locks in a fixed order: the account key (byte-identical to #32's, so a
concurrent claim and add serialise against each other) and the source `client_id` key (so two
accounts racing to claim the same anonymous identity serialise too). Logs a `watchlist_claimed`
audit event on success.

> **Disclosed residual risk.** `client_id` was never a secret — it appears in plaintext query
> strings on #30/#33 and any request knowing it can already read and write those rows. Claiming
> is more severe: it *exclusively* reassigns them, permanently cutting off the original browser.
> A signed-in attacker who obtains someone else's `client_id` (shared screenshot, browser
> history, access log) could claim their watchlist. The 5/hour limit and the audit event bound
> automated abuse but do not stop a single targeted guess; closing that would need proof of
> possession (a signed token minted into the anonymous browser), not attempted.

---

## Positions

"I bought this" tracking. Identical ownership shape to the watchlist — `routes/positions.py`
imports `resolve_owner()`/`owner_column()`/`_CLIENT_ID_RE`/`_VALID_EXCHANGES` from
`routes/watchlist.py` rather than redefining them. Cap `_MAX_POSITIONS_PER_CLIENT` = **200**. All
six endpoints use `run_owned_db_call()` (see [its envelope](#watchlist)).

### `GET /api/positions` — 35

| | |
|---|---|
| **Auth** | Owner-resolved |
| **Query** | `client_id` — string, optional |
| **Rate limit** | `positions_read` — 120 / 60 s per IP |
| **Status** | `200` `{"items": [...]}` · `422` · `429` · `503` |

Items: `{symbol, company, exchange, entry_price, target_price, stop_loss, shares, bought_at}`,
newest first. `shares` is `null` until the user fills it in — never `0` or an assumed `1`.

### `POST /api/positions` — 36

| | |
|---|---|
| **Auth** | Owner-resolved |
| **Body** | `PositionAddRequest`:<br>`client_id: str \| None` = `null`<br>`symbol: str` — **required**, `_TICKER_RE`<br>`company: str` = `""` — truncated to 200<br>`exchange: str` = `"NSE"` — **closed enum**: `NSE` \| `BSE`<br>`entry_price: float \| None` = `null`<br>`target_price: float \| None` = `null`<br>`stop_loss: float \| None` = `null` |
| **Rate limit** | `positions_write` — 60 / 60 s per IP |
| **Status** | `200` `{"items": [...]}` · `422` · `429` · `503` |

`ON CONFLICT DO UPDATE` refreshes `company`/`exchange`/`entry_price`/`target_price`/`stop_loss`
but deliberately **leaves `shares` and `bought_at` untouched** — re-marking a pick must not wipe a
user-entered share count or the original buy timestamp. Same advisory-lock-then-count pattern as
#32, in its own `positions:` lock-key namespace, with the same re-add cap exemption.

The three price fields are unvalidated beyond float coercion — no non-negativity check, unlike
`shares` on #37.

### `PATCH /api/positions/{symbol}` — 37

| | |
|---|---|
| **Auth** | Owner-resolved |
| **Path param** | `symbol` — `_TICKER_RE` |
| **Body** | `PositionSharesRequest`:<br>`client_id: str \| None` = `null`<br>`shares: float \| None` = `null` — `null` **clears** the count back to unknown; a negative value is `422` |
| **Rate limit** | `positions_write` — 60 / 60 s per IP |
| **Status** | `200` `{"items": [...]}` · `422` invalid symbol / negative `shares` / no identity · `429` · `503` |

Touches only `shares` — never company/exchange/entry/target/stop. Patching a symbol the owner
doesn't hold updates zero rows and returns `200`, not `404`.

### `DELETE /api/positions/{symbol}` — 38

Same contract as #33 with the `positions_write` bucket (60 / 60 s): `symbol` path param
(`_TICKER_RE`), optional `client_id` query param, `200 {"items": [...]}`, silent no-op on a
missing row.

### `POST /api/positions/claim` — 39

Same contract as #34, with `positions_claim` — 5 / 3600 s, the `positions` table/`bought_at`
ordering/`positions:` lock prefix, and a `positions_claimed` audit event. The same disclosed
residual risk applies.

### `GET /api/portfolio/concentration` — 40

Capital-weighted sector concentration over the caller's positions — an overlay Market Picks reads
to badge a new pick's sector. Unrelated to the Portfolio Aggregator below, despite the shared
`/api/portfolio` prefix (Next.js and FastAPI both resolve this exact path before the aggregator's
sub-paths).

| | |
|---|---|
| **Auth** | Owner-resolved |
| **Query** | `client_id` — string, optional |
| **Rate limit** | `portfolio_concentration` — **10 / 60 s** per IP — its own tighter bucket, not the shared `positions_read` one, because it fans out up to 200 live yfinance calls (4× `GET /api/prices`' own cap) |
| **Caching** | Sector comes **only** from an already-fresh 1 h `stock_info` cache entry — this read-only overlay never triggers a scrape. Prices are live |
| **Status** | `200` · `422` · `429` · `503` |

`{"by_sector": {"<sector>": pct}, "concentrated_sectors": [...]}` — sectors at or above 25 %. A
position missing `shares`, a resolvable live price, or a cached sector contributes to neither
field rather than being counted at an assumed weight; `{"by_sector": {}, "concentrated_sectors":
[]}` when nothing qualifies.

Note: the rate-limit bucket is `portfolio_concentration` but the log/event prefix is
`positions_read` — a failure here logs under the read endpoint's name.

---

## Portfolio Aggregator (41–57)

A **separate** personal net-worth tracker: profiles → accounts → assets → valuations, plus XIRR
and two import paths. Mounted under the same `/api/portfolio` prefix as #40 but otherwise
unrelated — different tables, different router, different frontend page
(`/portfolio-aggregator`, nav label "Net Worth").

> **No authentication, no ownership scoping — deliberate and disclosed.** Every endpoint here
> takes a bare `profile_id` / `account_id` / `asset_id` and never checks who is asking. Any
> caller can read or mutate any profile's data. `profiles` is a bare picker with no credentials,
> unconnected to this app's real `users`/`sessions` account system. This is the original design
> intent (a personal localhost/Tailscale tool for a household, not a multi-tenant product), not
> an oversight — see `routes/portfolio_aggregator.py`'s module docstring. **Do not expose this
> router on a public interface without adding auth first.**

All 17 use `run_owned_db_call()` (see [its envelope](#watchlist)), so all share: `503
"DATABASE_URL not configured."` when unset, sanitized `503` on any unexpected DB error, `429` on
their bucket, and `422` for a `ValueError`. Two buckets, both per IP:

- **`portfolio_agg_read` — 120 / 60 s**: #41, #43, #47, #52, #54
- **`portfolio_agg_write` — 60 / 60 s**: #42, #44, #45, #46, #48, #49, #50, #51, #53, #55, #56, #57

Closed enums used below:

- `_ACCOUNT_TYPES` = `bank`, `broker`, `amc`, `epfo`, `other`
- `_ASSET_TYPES` = `mf`, `stock`, `fd`, `epf`, `ppf`, `cash`, `manual`, `loan`

### Profiles

**`GET /api/portfolio/profiles`** — 41. No params. `200 {"profiles": [{"id", "name"}]}`.

**`POST /api/portfolio/profiles`** — 42. Body `ProfileIn`: `name: str`, **required**,
`1 ≤ len ≤ 60` (Pydantic-enforced → `422`). `201 {"id", "name"}` · `409 "profile name already
exists"` on the unique-name `IntegrityError`.

### Accounts

**`GET /api/portfolio/accounts`** — 43. Query `profile_id: int`, **required** (omitted →
`422`). `200 {"accounts": [...]}`. An unknown `profile_id` returns an empty list, not `404`.

**`POST /api/portfolio/accounts`** — 44. Body `AccountIn`: `profile_id: int` **required** ·
`name: str` **required**, `1 ≤ len ≤ 120` · `institution: str | None` = `null` · `type: str`
**required**, must be in `_ACCOUNT_TYPES`. `201 {"id"}` · `404 "profile not found"` ·
`422 "type must be one of: [...]"`.

**`PATCH /api/portfolio/accounts/{account_id}`** — 45. Path `account_id: int`. Body
`AccountPatch`: `name`, `institution`, `type`, all optional. **`None` means "don't change"** —
there is no way through this endpoint to null out `institution`. An all-`None` body is
`422 "no fields to update"`. `200 {"ok": true}` · `404 "account not found"` · `422` bad `type`.

**`DELETE /api/portfolio/accounts/{account_id}`** — 46. Path `account_id: int`.
`200 {"ok": true}` · `404 "account not found"` · **`422 "account still has assets; delete them
first"`** — a non-cascading delete guard expressed as 422 rather than 409, unlike #42's
duplicate-name conflict.

### Assets

**`GET /api/portfolio/assets`** — 47. Query `account_id: int`, **required**.
`200 {"assets": [...]}`, each row carrying `units`/`avg_cost` (from `holdings`, `null` for
non-`mf`/`stock`) and `value`/`valued_on` (the latest `valuations` row, `null` if never valued).

**`POST /api/portfolio/assets`** — 48. Body `AssetIn`: `account_id: int` **required** ·
`type: str` **required**, in `_ASSET_TYPES` · `name: str` **required**, `1 ≤ len ≤ 200` ·
`symbol: str | None` = `null` · `meta: dict` = `{}` (free-form per type) · `value: float`
**required**, `≥ 0` — written as today's valuation · `units: float | None` = `null` ·
`avg_cost: float | None` = `null`.

Three cross-field rules, all `422`: `units`/`avg_cost` only apply to `mf` and `stock`;
`avg_cost` cannot be set without `units`; `type` must be in the enum. `201 {"id"}` ·
`404 "account not found"`. One transaction inserts the asset, its initial valuation, and (when
`units` is given) its holding.

**`PATCH /api/portfolio/assets/{asset_id}`** — 49. Path `asset_id: int`. Body `AssetPatch`:
`name`, `symbol`, `meta`, `archived: bool`, `units`, `avg_cost` — all optional, all `None` =
"don't change". `200 {"ok": true}` · `404 "asset not found"` · `422` for an empty patch,
`units`/`avg_cost` on a non-`mf`/`stock` asset, or `avg_cost` with no existing holding and no
`units` supplied.

> **`archived: false` IS settable** — un-archiving works. The filter is `if v is not None`, and
> `False is not None`, so an explicit `archived: false` passes it; `archived: null`/omitted is the
> no-change case. Worth stating because the same filter applied to `name`/`symbol` genuinely does
> block clearing those to `null`.

**`DELETE /api/portfolio/assets/{asset_id}`** — 50. Path `asset_id: int`. Cascades in one
transaction: deletes the asset's `valuations`, `holdings`, and `transactions`, then the asset.
`200 {"ok": true}` · `404 "asset not found"`. Unlike #46 there is no "still has children" guard —
asset deletion is destructive by design.

**`POST /api/portfolio/assets/{asset_id}/valuations`** — 51. Path `asset_id: int`. Body
`ValuationIn`: `value: float` **required**, `≥ 0` · `as_of: date | None` = `null` → today.
`201 {"ok": true}` · `404 "asset not found"` · **`422 "as_of cannot be in the future"`** — a
future date is rejected outright, never silently clamped. Upserts on `(asset_id, as_of)`, so
same-day edits overwrite rather than accumulating; a different day inserts a new row, keeping
history from day one.

### Computed & import

**`GET /api/portfolio/networth`** — 52. Query `profile_id: int`, **required**. `200 {"total",
"by_type", "by_account"}`. Loans are stored positive and signed negative here — the only place
that flip happens. Archived assets and assets with no valuation row at all are excluded (the
latter contribute nothing rather than a `0`).

**`POST /api/portfolio/refresh-valuations`** — 53. No body. `200 {"valued", "skipped",
"details"}`. Auto-values every non-archived `mf`/`stock` asset with a `holdings` row from
`prices_daily`/`mf_nav_daily`, falling back to a live yfinance quote for a stock with no stored
price. A per-asset miss is skipped with a reason; the prior valuation stands rather than being
zeroed. Synchronous — the request blocks for the whole refresh, unlike the `202`-returning
pipeline refreshes (#18/#20).

**`GET /api/portfolio/xirr`** — 54. Query `profile_id: int`, **required**. `200
{"portfolio_xirr", "assets": [{"asset_id", "name", "xirr"}]}`. An asset with no transactions is
`null` and excluded from the pooled figure. Returns `null` rather than a guessed rate for fewer
than 2 flows, all-same-sign flows, or non-convergence.

**`POST /api/portfolio/import-cas`** — 55. **`multipart/form-data`**:

| Field | Type | Required |
|---|---|---|
| `file` | file — a CAMS/KFintech **detailed** CAS PDF | yes |
| `password` | form string | yes |
| `account_id` | form int | yes |

`200 {"schemes", "assets_created", "assets_matched", "transactions", "skipped_rows",
"warnings"}` · `422` wrong password, unparseable PDF, or a summary-only statement (rejected with
a message telling the caller to request the detailed statement) · `404` unknown `account_id`.

Re-import is **idempotent by replacement**: every existing `meta.source='cas'` row for the
matched asset is deleted before the statement's rows are inserted. Manually-entered and
CSV-sourced transactions are untouched. The password lives in memory only for the parse call —
never logged, never stored; the PDF bytes are never written to disk. On success the parse is
archived (PII-scrubbed) and `refresh_valuations()` runs before returning.

**`POST /api/portfolio/import-csv/preview`** — 56. **`multipart/form-data`**: `file` (required;
`.xlsx` via pandas/openpyxl, anything else via stdlib `csv` with delimiter sniffing).
`200 {"headers", "sample_rows", "suggested_mapping", "detected"}` · `422` empty or unreadable
file. Read-only — no DB write, despite using the `portfolio_agg_write` rate-limit bucket.

**`POST /api/portfolio/import-csv`** — 57. **`multipart/form-data`**:

| Field | Type | Required |
|---|---|---|
| `file` | file | yes |
| `mapping` | form string — a **JSON object**, parsed server-side | yes |
| `account_id` | form int | yes |
| `broker` | form string | yes |

`mapping` must be valid JSON and must supply all five required target fields — `date`, `symbol`,
`side`, `quantity`, `price` — or `422 "mapping missing required field(s): [...]"`. `amount` and
`isin` are optional.

> **Known gap:** the `422 "mapping must be a JSON object"` guard only catches
> `JSONDecodeError`/`TypeError` (`routes/portfolio_aggregator.py:465-468`), i.e. genuinely
> unparseable input. A *valid* JSON non-object — `mapping=5`, `mapping=[]` — parses cleanly, then
> `mapping_dict.get(f)` on the next line raises `AttributeError` **outside** the `_sync` wrapper,
> so it surfaces as an unhandled **500**, not the 422 the message implies. The fix is an
> `isinstance(mapping_dict, dict)` check alongside the existing except clause.

`200 {"rows", "imported", "duplicates", "skipped", "assets_created", "assets_matched",
"warnings"}` · `422` unparseable file · `404` unknown `account_id`.

**Appends, never replaces** (unlike #55): a broker tradebook is a date-ranged partial, not a full
restatement, so a row is counted a duplicate only when an existing `meta.source='csv'`
transaction for the same asset matches on `(date, type, units, amount)`. Re-uploading the same
file, or an overlapping export, is safe. An unparseable row is skipped with a 1-indexed warning
rather than aborting the file. New-asset symbols are resolved through
`tools/securities_master.py::resolve_symbol()`; an `isin`/`exact` hit substitutes the canonical
NSE/BSE symbol, while `fuzzy`/`unresolved` keeps the raw broker code and adds a warning — never a
silent guess. `refresh_valuations()` runs on success.

---

## Rate-limit bucket index

Every bucket, in one place. All are per-IP (`api._client_ip`) unless noted.

| Bucket | Limit | Window | Endpoints |
|---|---|---|---|
| `validate` | 30 | 60 s | 3 |
| `analyse` | 20 | 300 s | 4 |
| `market_picks_force` | 3 | 3600 s | 5 (`?force=true` only) |
| `market_picks_status` | 60 | 60 s | 6 |
| `market_picks_history` | 60 | 60 s | 7 |
| `prices` | 30 | 60 s | 8 |
| `prices_history` | 60 | 60 s | 9 |
| `peers` | 30 | 60 s | 10 |
| `financials` | 30 | 60 s | 11 |
| `shareholding_detail` | 30 | 60 s | 12 |
| `insider_activity` | 30 | 60 s | 13 |
| `street_consensus` | 30 | 60 s | 14 |
| `verdict_history` | 60 | 60 s | 15 |
| `sme_signals` | 60 | 60 s | 16 |
| `sme_signal_history` | 60 | 60 s | 17 |
| `sme_refresh` | 3 | 3600 s | 18 |
| `screener` | 60 | 60 s | 19 |
| `screener_refresh` | 3 | 3600 s | 20 |
| `consolidated` | 30 | 60 s | 21 |
| `auth_request_link` | 5 | 900 s | 22 |
| `auth_request_link_email:<email>` | 5 | 3600 s | 22 — **per target address** |
| `auth_verify` | 20 | 300 s | 23 |
| `api_keys_create` | 20 | 3600 s | 26 |
| `api_keys_list` | 60 | 60 s | 27 |
| `api_keys_revoke` | 60 | 60 s | 28 |
| `api_v1:<user_id>` | 100 / 1000 | 3600 s | 29 — **per account**, by tier |
| `watchlist_read` | 120 | 60 s | 30 |
| `watchlist_calendar` | 30 | 60 s | 31 |
| `watchlist_write` | 60 | 60 s | 32, 33 |
| `watchlist_claim` | 5 | 3600 s | 34 |
| `positions_read` | 120 | 60 s | 35 |
| `positions_write` | 60 | 60 s | 36, 37, 38 |
| `positions_claim` | 5 | 3600 s | 39 |
| `portfolio_concentration` | 10 | 60 s | 40 |
| `portfolio_agg_read` | 120 | 60 s | 41, 43, 47, 52, 54 |
| `portfolio_agg_write` | 60 | 60 s | 42, 44, 45, 46, 48, 49, 50, 51, 53, 55, 56, 57 |

**Unrated-limited**: `GET /` (1), `GET /health` (2), `GET /api/auth/me` (24), `POST
/api/auth/logout` (25), and — IP-wise — `GET /api/v1/consolidated/{symbol}` (29).

Two other global guards are not rate limits but reject requests:

- **LLM concurrency ceiling** — `LLM_CONCURRENCY_LIMIT` (default 4) slots shared by #4's analyst
  call and #5's whole pipeline run. Exhaustion is an in-stream `error` frame, never a 429 or 503.
  Redis-backed slots carry a 600 s TTL so a crashed worker doesn't strand one.
- **Single-flight locks** — `market_picks_refresh` (#5), `sme_refresh` (#18), `screener_refresh`
  (#20), each 3600 s TTL. Surfaced as `409` from #18/#20 and from #5's force path.

---

## Response-contract inconsistencies

Real, verified differences between sibling endpoints. Documented rather than smoothed over,
because a consumer will hit them.

1. **Scrape-failure signalling is not uniform across the five research add-ons.** `/api/peers`
   (10) and `/api/financials` (11) return an empty/`null`-filled payload for both a genuine "no
   data" and an upstream outage, with no flag distinguishing them.
   `/api/shareholding-detail` (12) uses a singular `unavailable`; `/api/insider-activity` (13)
   and `/api/street-consensus` (14) use two `*_unavailable` flags each. All five *do* increment a
   scraper error counter server-side — the signal exists, it just isn't in the response for the
   first two.

2. **Missing-`DATABASE_URL` handling splits three ways.** `/api/sme-signals` (16) and everything
   through `run_owned_db_call()` return `503`. `/api/verdict-history` (15) returns `200` with an
   empty history. `/api/auth/me` (24) and `/api/v1/consolidated` (29) return `401`, and
   `/api/consolidated` (21) returns `200` with a `null` `sme` section. Each is individually
   defensible (a supplementary strip must not read as total failure; an auth check can't
   distinguish "no key" from "can't check"), but a client cannot infer DB availability from any
   single status code.

3. **`GET /api/v1/consolidated/{symbol}` has no pre-auth rate limit.** Unbounded failed
   `X-API-Key` attempts, each costing a DB lookup. Every other non-trivial endpoint calls
   `_rate_limit()` before its auth check.

4. **`GET /api/auth/me` and `POST /api/auth/logout` are entirely unrated-limited**, despite
   `/api/auth/me` performing a DB session lookup on every anonymous call.

5. **Auth-vs-rate-limit ordering is inconsistent.** `POST /api/api-keys` (26), `GET
   /api/api-keys` (27), and `DELETE /api/api-keys/{key_id}` (28) rate-limit *before* checking the
   session, so an unauthenticated caller can exhaust an IP's budget and then see `429` where
   `401` would be more accurate.

6. **Conflict semantics differ between two "you can't do that yet" cases.** A duplicate profile
   name is `409` (#42); an account that still has assets is `422` (#46). Both are state
   conflicts rather than payload-validation failures.

7. **`GET /api/sme-signals/{symbol}/history` (17) is the only per-symbol endpoint that 404s.**
   Peers, financials, shareholding detail, insider activity, street consensus, verdict history,
   and consolidated all return `200` with an empty payload for an unknown symbol.

8. **`GET /api/prices` silently drops malformed symbols** rather than rejecting the request, the
   only symbol-taking endpoint that does. `symbols=!!!` is `200` with an empty `prices` map,
   indistinguishable from a valid-but-unresolvable list.

9. **`POST /api/positions` accepts negative prices.** `entry_price`/`target_price`/`stop_loss`
   get float coercion only, while `PATCH /api/positions/{symbol}` explicitly rejects a negative
   `shares` with `422`.

10. **`/api/watchlist/calendar` (31) is unauthenticated and takes an arbitrary symbol list**,
    unlike its four `/api/watchlist` siblings. It reads only already-cached, non-owner-scoped data
    (public filings, public verdict history), so it discloses nothing owner-specific — but it
    does not verify the caller actually watches those symbols.

11. **`GET /api/portfolio/concentration` (40) logs failures under the `positions_read` event
    prefix** while rate-limiting on `portfolio_concentration` — a log search by bucket name finds
    nothing.

12. **`POST /api/portfolio/import-csv/preview` (56) is a read-only preview on the write rate-limit
    bucket** (`portfolio_agg_write`, 60/min), so previewing a file consumes an import budget.

13. **Two endpoints declare `status_code=202` but complete synchronously enough to be
    indistinguishable from `200` to most clients** (#18, #20) — the actual work is a detached
    background task whose outcome (including an unhealthy run) is never reported back to the
    caller.

14. **`analysis_error.reason` in the Market Picks stream carries a truncated raw exception
    string**, the one exception to this codebase's otherwise-uniform sanitization of
    client-visible error text. It is emitted from inside the pipeline, below `api.py`'s
    sanitizing layer.
