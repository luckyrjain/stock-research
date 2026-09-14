# Runbook

*Code-derived from this engagement's evidence — validate with the operator before production use.
This is a solo-operator project with no on-call rotation; procedures below assume the operator is
the one running them.*

## Trace end-to-end

**Stock Analysis request**: every log line in the `/api/analyse/{symbol}` request lifecycle
carries a `run_id` (`uuid.uuid4().hex[:12]`, generated at `backend/api.py:581`) threaded through
`analyst/crew.py`, `analyst/llm_cost.py`, and `core/schema_drift.py`'s `log_event()` calls
(confirmed by the quality/ops research agent). Search structured logs for that `run_id` to see the
full six-task fetch → signal engine → analyst call → verdict-persist chain for one request.
**Limitation** (disclosed by this engagement): this correlation-ID pattern is scoped to the two
LLM-calling flows (`/api/analyse`, `/api/market-picks`) — a plain read endpoint (`GET
/api/watchlist`, `GET /api/screener`) has no per-request correlation ID to grep by.

**Batch pipeline run**: every pipeline (`sme_ema_pipeline.py`, `screener_pipeline.py`,
`eod_prices_pipeline.py`, `market_picks_pipeline.py`, `watchlist_alerts.py`) logs a start/end
summary via `log_event()`; cross-reference against the GitHub Actions run history for the
corresponding `.github/workflows/*-cron.yml` job.

## Replay/retry failed operation

- **Stock analysis**: re-request `GET /api/analyse/{symbol}?force=true` — bypasses all six task
  TTLs (but **not** the separately-TTL'd `technical`/`macro` signal caches, which can lag up to 6h
  — a disclosed, not-fixed characteristic per `docs/architecture.md`).
- **Market Picks**: `GET /api/market-picks?force=true` (rate-limited 3/hour per IP, single-flight
  locked) re-runs the full six-phase pipeline.
- **SME / Screener**: `POST /api/sme-signals/refresh` / `POST /api/screener/refresh` (3/hour per
  IP, single-flight locked, `202` + background run).
- **EOD price ingestion**: `python -m pipelines.eod_prices_pipeline --date YYYY-MM-DD` or
  `--backfill YYYY-MM-DD` for a specific gap; the default no-flag run is already self-healing over
  the last 5 weekdays.
- **Corporate actions**: `python -m pipelines.corporate_actions_pipeline --backfill` /
  `--recompute SYMBOL` / `--recompute-all`.
- **Watchlist alerts**: `python -m pipelines.watchlist_alerts --force` — respects the claim-before-
  send dedup (`_claim_alert_keys`), so re-running the same day will not double-send an already-
  claimed-and-sent alert; a failed SMTP send releases its claim automatically for the next attempt.
- **Broker sync**: `POST /api/portfolio/broker/{broker}/sync` — per-`(account,broker)` lock (300s
  TTL) and a 12/hour rate limit; each internal broker fetch already retries a transient failure
  (5xx/timeout) up to 3× with exponential backoff before surfacing `sync_status: "error"`.

## Reconcile mismatch

- **Verdict disagreement between Stock Analysis and Market Picks** for the same symbol: this is a
  known, *expected* possibility (3-tier vs. 4-tier formulas run independently) — surfaced only
  inside `ConsolidatedCard`'s `verdictsDisagree()` check (frontend/components/consolidated-card.tsx,
  confirmed by the frontend research agent). There is no backend reconciliation step; check
  `GET /api/consolidated/{symbol}` to see both verdicts side by side.
- **`prices_daily.close` vs. `adj_close` mismatch**: `adj_close` is written only by
  `corporate_actions_pipeline.py`'s recompute and — per this engagement's evidence — has **no
  confirmed production reader** (`portfolio_valuation._latest_close()` reads raw `close`). A
  perceived valuation discrepancy around a stock's split/bonus date is not a bug in the currently
  wired path; verify which column a new feature reads before assuming `adj_close` is live.
- **XIRR looks wrong for an asset**: check `transactions.meta.source` — three writers
  (`cas`/`csv`/broker-API) use three different idempotency models; a duplicate or missing
  transaction most likely traces to `csv_import`'s content-key dedup (no DB constraint — a known
  gap) rather than the broker-sync path (DB-constraint-backed).

## Clear stuck / in-flight

- **Single-flight lock stuck** (`market_picks_refresh`, `sme_refresh`, `screener_refresh`,
  `broker_sync:{account}:{broker}`): these are Redis-backed with a TTL when `REDIS_URL` is set
  (600s for the LLM concurrency ceiling, 3600s for the pipeline refresh locks, 300s for broker
  sync) — a crashed worker's lock self-expires; there is no manual "clear lock" endpoint.  Confirm
  via the relevant `POST .../refresh` or `.../sync` endpoint returning `409` past the TTL window
  before assuming a genuine hang.
- **`broker_connections.sync_status` stuck at `"syncing"`**: the frontend polls
  `GET /broker/connections` every 2s while `"syncing"`; if the background thread crashed without
  updating the row, the TTL'd lock (300s) will still expire and allow a fresh sync attempt, but the
  row itself has no automatic timeout-to-`"error"` transition confirmed by this engagement — treat
  a `"syncing"` status older than the lock TTL as stale.
- **`hdfc_securities` `pending_token_id` stuck** (an interrupted OTP flow): `verify-otp` clears it
  unconditionally in its own committed transaction before running the three dependent HDFC calls,
  so a stuck value only persists if `login-start` succeeded but `verify-otp` was never called —
  a fresh `login-start` call overwrites it.

## Investigate effect not received

- **No email arrived** (magic link or watchlist alert): `core/email_sender.py::_send_via_smtp()`
  is best-effort — returns `True`/`False`, never raises; a missing `SMTP_HOST` env var means
  nothing is ever sent, silently. Check for `auth_link_email_not_delivered` (magic link) or the
  per-run watchlist-alert log lines. **Magic-link requests always return `{"sent": true}`
  regardless of actual delivery** (deliberate, to avoid leaking SMTP configuration state to an
  unauthenticated caller) — a "successful" API response does not confirm the email was sent.
- **A watched stock's recommendation changed but no alert arrived**: confirm the watching row is
  account-owned (`user_id IS NOT NULL`) — anonymous `client_id` watchlist rows are structurally
  excluded from `pipelines/watchlist_alerts.py`'s query and can never alert.
- **A pipeline "succeeded" with suspiciously little data**: check its health gate
  (`_MAX_ACCEPTABLE_ERROR_RATE` / empty-source-rate) — every pipeline in this repo is designed to
  fail loudly (non-zero exit) rather than silently succeed with mostly-empty data, **except**
  `corporate_actions_pipeline.py`, whose health-gate completeness is UNKNOWN per this engagement
  (see `UNKNOWNS.md`) — a silent near-total ingest failure there is the one disclosed blind spot.

## Emergency stop / kill-switch

No dedicated kill-switch endpoint or feature-flag system exists in this codebase (confirmed —
`grep` for `FeatureFlag`/`feature.toggle`/`FEATURE_` returned zero matches repo-wide). To stop a
specific capability:

- **Stop LLM spend**: unset all LLM provider API key env vars — `run_analysis_with_fallback()`
  degrades to `_safe_analysis_fallback()` (a safe HOLD, no LLM call) when no provider is
  configured, rather than erroring.
- **Stop a scraper source objecting to load** (per `docs/PRD.md` §17.2's stated compliance
  posture — "comply immediately... if any site operator objects"): each scraper module is
  independent (`tools/*.py`); removing its entry from `SOURCES` in `tools/market_picks_tools.py`
  (for Market Picks) or commenting out its call site in the relevant pipeline is the only
  mechanism found — there is no runtime toggle.
- **Stop all cron pipelines**: disable the relevant `.github/workflows/*-cron.yml` file (GitHub
  Actions' own workflow-disable mechanism) — no in-app equivalent exists.
- **Revoke a compromised API key**: `DELETE /api/api-keys/{key_id}` (idempotent, badges as
  revoked rather than deleting the row).
- **Revoke a compromised session**: no admin-side session-revocation endpoint exists; only the
  session owner's own `POST /api/auth/logout` deletes their session row. A full user lockout would
  require a direct DB `DELETE FROM sessions WHERE user_id = :id`.
