# As-Built Product Requirements Document

> This PRD describes the **observed current-state behavior** of the in-scope service(s) and/or domain. It is reverse-engineered from implementation evidence. It does **not** claim undocumented product intent.

## Document status

| Field | Value |
|---|---|
| Scope | AlphaPulse — the full `stock-research` monolith (backend/ FastAPI + frontend/ Next.js), all five product modes plus accounts and the cross-mode watchlist/portfolio surfaces |
| PRD type | As-built / current-state |
| Generated from | This engagement's domain-comprehension evidence set: six parallel research agents (backend-core, backend-routes/pipelines, frontend, business-flow tracing, P3b adversarial security, P4 quality/ops), cross-checked against the repository's own extensive `CLAUDE.md`/`docs/*.md` documentation |
| Overall confidence | MEDIUM (weakest material: Q1/Q3 rely partly on doc-corroborated-not-independently-re-read evidence for ~half of `backend/api.py`; P2b runtime validation was skipped entirely — see `EXEC_SUMMARY.md`) |
| Product intent gaps | See `UNKNOWNS.md`; business/legal decisions in §17 of `docs/PRD.md` (SEBI registration, scraping ToS, bus factor) are recorded as-is, not re-verified against source |

## 1. Purpose and scope

AlphaPulse is a single-operator Indian equity research platform. Given an NSE/BSE ticker, it
validates the symbol, fetches six data slices in parallel, runs a deterministic quantitative
signal engine, and calls an LLM for a structured BUY/HOLD/SELL recommendation, streamed via
Server-Sent Events. A second mode (Market Picks) runs a multi-agent pipeline scraping 20 sources
weekly to produce a confidence-ranked watchlist. Two batch screeners (SME Signals, NIFTY 500
Screener) run daily crons over Postgres-stored technical signals. A cross-mode Watchlist/Positions
system and a separate Portfolio Aggregator (personal net-worth tracker with CAS/CSV import and
broker API sync) round out the product, tied together by an optional magic-link account system and
a Next.js BFF that never lets the browser call the backend directly.

**Boundary**: this PRD covers the demonstrable behavior of the code in this repository at the
commit recorded in `manifest.yaml`. It does not describe: real payment processing (explicitly
absent, `docs/PRD.md` §17.3), brokerage order execution (non-goal, `docs/PRD.md` §3), or any
non-Indian market (non-goal). Business/legal decisions recorded in `docs/PRD.md` §17
(SEBI-registration determination, scraping-ToS risk acceptance, bus-factor acceptance) are quoted
as decisions on record, not independently verified — this engagement has no authority to confirm
or dispute a legal/business judgment call.

## 2. Actors and consumers

| Actor / consumer | Interaction | Evidence | Confidence |
|---|---|---|---|
| Anonymous browser visitor | Analyzes stocks, browses Market Picks/SME/Screener, stars watchlist items, tracks positions — all via a `client_id` UUID, no account required | frontend agent: `lib/watchlist.ts:14,28-40` (client_id generation); docs/api-reference.md's "owner-resolved" auth mode | HIGH |
| Signed-in account holder | Same as above, plus API key minting, opt-in claim of anonymous rows, watchlist alert emails | backend-core agent (`auth.py`), frontend agent (`auth.ts`, `auth/verify/page.tsx`) | HIGH |
| Programmatic API consumer | Calls `GET /api/v1/consolidated/{symbol}` via `X-API-Key`, tier-scaled rate limit | docs/api-reference.md:746-770 (doc-sourced, not independently re-read this engagement) | MEDIUM |
| Household member (Portfolio Aggregator "profile") | Not this app's own account — a bare named picker under an owning `client_id`/`user_id`, tracks personal net worth | routes/pipelines agent (route inventory); docs/database.md:609-621 | MEDIUM |
| GitHub Actions cron | Triggers 5 scheduled batch pipelines + 1 weekly live-contract check | business-flow agent, opened `.github/workflows/*.yml` directly | HIGH |
| NSE, BSE, Screener.in, Trendlyne, AMFI, RBI, Google News (via `gnews`), yfinance | Scraped data sources — every scraper "never raises," degrades to `{"error":...}`/`null` on failure | backend-core, routes/pipelines agents | HIGH |
| Anthropic, OpenAI, Groq, Google Gemini, OpenRouter, Ollama | LLM providers for the analyst call and Market Picks extraction/analysis; auto-detected + one failover | backend-core agent, `analyst/crew.py` full verified read | HIGH |
| Zerodha Kite Connect, HDFC Securities, Paytm Money | Broker API sync sources for the Portfolio Aggregator (polling/pull-only, no inbound webhooks) | P3b, routes/pipelines agents | HIGH |
| SMTP server | Delivers magic-link and watchlist-alert emails, best-effort | backend-core agent, `core/email_sender.py` | HIGH |

## 3. Current capabilities

| Capability ID | Capability | Service / bounded context | Evidence | Implementation status | Exercise status | Confidence |
|---|---|---|---|---|---|---|
| CAP-001 | Single-stock analysis (SSE) | Stock Analysis | `CAPABILITY_TRACEABILITY.yaml` cap #1 | Implemented | referenced | HIGH |
| CAP-002 | LLM guardrails + cross-provider failover | Stock Analysis | `CAPABILITY_TRACEABILITY.yaml` cap #2 | Implemented | referenced | HIGH |
| CAP-003 | Market Picks weekly pipeline | Market Picks | `CAPABILITY_TRACEABILITY.yaml` cap #3 | Implemented | referenced | HIGH |
| CAP-004 | SME Signals screener | SME Signals | `CAPABILITY_TRACEABILITY.yaml` cap #4 | Implemented | referenced | HIGH |
| CAP-005 | NIFTY 500 Screener | Screener | `CAPABILITY_TRACEABILITY.yaml` cap #5 | Implemented | referenced | HIGH |
| CAP-006 | EOD price store ingestion | EOD Price Store | `CAPABILITY_TRACEABILITY.yaml` cap #6 | Implemented | referenced | MEDIUM |
| CAP-007 | Corporate actions ingestion + adj_close | EOD Price Store | `CAPABILITY_TRACEABILITY.yaml` cap #7 | Partially implemented — write path live, `adj_close` has no confirmed production reader | referenced (write side); dead_code candidate (read side) | MEDIUM |
| CAP-008 | Watchlist star/unstar, dual ownership | Watchlist & Positions | `CAPABILITY_TRACEABILITY.yaml` cap #8 | Implemented | referenced | HIGH |
| CAP-009 | Positions ("I bought this") tracking | Watchlist & Positions | `CAPABILITY_TRACEABILITY.yaml` cap #9 | Implemented | referenced | HIGH |
| CAP-010 | Opt-in claim-to-account migration | Watchlist & Positions | `CAPABILITY_TRACEABILITY.yaml` cap #10 | Implemented | referenced | HIGH |
| CAP-011 | Daily watchlist alert emails | Watchlist & Positions | `CAPABILITY_TRACEABILITY.yaml` cap #11 | Implemented | referenced | HIGH |
| CAP-012 | Magic-link passwordless auth | Accounts & Auth | `CAPABILITY_TRACEABILITY.yaml` cap #12 | Implemented | referenced | HIGH |
| CAP-013 | Programmatic API keys, tiered limits | Accounts & Auth | `CAPABILITY_TRACEABILITY.yaml` cap #13 | Implemented; billing/checkout absent (disclosed non-goal-for-now) | referenced | MEDIUM |
| CAP-014 | Consolidated cross-mode search | Consolidated Search | `CAPABILITY_TRACEABILITY.yaml` cap #14 | Implemented | referenced | MEDIUM |
| CAP-015 | Portfolio Aggregator CRUD | Portfolio Aggregator | `CAPABILITY_TRACEABILITY.yaml` cap #15 | Implemented | referenced | HIGH |
| CAP-016 | Nightly auto-valuation + XIRR | Portfolio Aggregator | `CAPABILITY_TRACEABILITY.yaml` cap #16 | Implemented | referenced | HIGH |
| CAP-017 | CAS PDF import (PII-scrubbed) | Portfolio Aggregator | `CAPABILITY_TRACEABILITY.yaml` cap #17 | Implemented; untested against a real CAS PDF per `docs/backlog.md` | referenced | HIGH (mechanism), MEDIUM (real-file fidelity) |
| CAP-018 | Broker CSV/XLSX import | Portfolio Aggregator | `CAPABILITY_TRACEABILITY.yaml` cap #18 | Implemented; no DB-level idempotency constraint (disclosed gap) | referenced | HIGH |
| CAP-019 | Broker API sync (Zerodha/HDFC/Paytm) | Portfolio Aggregator | `CAPABILITY_TRACEABILITY.yaml` cap #19 | Implemented for Zerodha (verified against real SDK); HDFC/Paytm shape unverified against a live account | referenced | HIGH (mechanism), MEDIUM (HDFC/Paytm fidelity) |
| CAP-020 | Rate limiting (sliding window, Redis-optional) | Cross-cutting | `CAPABILITY_TRACEABILITY.yaml` cap #20 | Implemented; opt-in per-route pattern, not framework-enforced (disclosed design risk) | referenced | HIGH (mechanism), MEDIUM (coverage completeness) |
| CAP-021 | File + Redis caching layer | Cross-cutting | `CAPABILITY_TRACEABILITY.yaml` cap #21 | Implemented | referenced | HIGH |
| CAP-022 | Broker credential encryption (Fernet) | Cross-cutting | `CAPABILITY_TRACEABILITY.yaml` cap #22 | Implemented | referenced | HIGH |
| CAP-023 | Structured observability + optional Sentry | Cross-cutting | `CAPABILITY_TRACEABILITY.yaml` cap #23 | Partially implemented — correlation ID scoped to 2 of many flows | referenced | HIGH |
| CAP-024 | Next.js BFF proxy layer | Cross-cutting | `CAPABILITY_TRACEABILITY.yaml` cap #24 | Implemented | referenced | HIGH |

## 4. Functional requirements

| ID | Requirement | Scope | Evidence | Status | Confidence |
|---|---|---|---|---|---|
| FR-001 | The system streams a symbol's analysis via SSE, emitting `start`, `task_done` (×6), `analysing`, and a terminal `done`/`error` event. | Stock Analysis | backend/api.py:575-793 (business-flow agent, full trace) | Observed | HIGH |
| FR-002 | Symbol input is validated against `^[A-Z0-9&\-]{1,20}$` (after uppercasing/trimming) before the SSE stream opens; an invalid symbol returns `422`, not a stream frame. | Stock Analysis | backend/api.py:579-580 | Observed | HIGH |
| FR-003 | `GET /api/validate/{symbol}` resolves a ticker, ISIN, BSE-forced slug, or company name to a tradeable symbol, returning `found:false` (not a 404) on a miss. | Stock Analysis | docs/api-reference.md:255-273 (doc-sourced, cross-checked structurally, not independently re-read line-by-line this engagement) | Observed | MEDIUM |
| FR-004 | The analyst call is retried once on a guardrail failure, once on a rate-limit error, then fails over to one other auto-detected provider (only when `LLM_PROVIDER` was not explicitly pinned), before falling back to a safe HOLD. | Stock Analysis | backend/analyst/crew.py:745-925 (backend-core agent, full verified read) | Observed | HIGH |
| FR-005 | Every completed analysis (SSE path, CLI, or watchlist-alert re-check) upserts one `verdict_history` row per `(symbol, day)`. | Stock Analysis, Watchlist & Positions | backend/analytics/verdict_history.py:33-109 (backend-core + business-flow agents) | Observed | HIGH |
| FR-006 | The Market Picks pipeline scrapes 20 sources across 6 phases, aborting early only if zero picks survive consolidation. | Market Picks | backend/pipelines/market_picks_pipeline.py (business-flow agent, full trace) | Observed | HIGH |
| FR-007 | A Market Picks run is cached (192h TTL) only when the picks list is non-empty **and** the pipeline reports itself healthy. | Market Picks | backend/api.py:899-905 (business-flow agent) | Observed | HIGH |
| FR-008 | SME Signals computes EMA20/EMA50 golden/death crosses plus RSI14/volume-spike/liquidity for NSE Emerge + BSE SME stocks daily, retaining ~100 days. | SME Signals | backend/pipelines/sme_ema_pipeline.py (business-flow agent) | Observed | HIGH |
| FR-009 | The NIFTY 500 Screener filters/sorts on a whitelisted set of columns; `sort` is validated against a closed enum before interpolation into `ORDER BY`. | Screener | backend/pipelines/screener_pipeline.py, docs/api-reference.md:578-580 | Observed | HIGH |
| FR-010 | A signed-in-or-anonymous caller can star a stock to their Watchlist; the write is idempotent (`ON CONFLICT DO NOTHING`) and capped at 200 items per identity, enforced under an advisory lock. | Watchlist & Positions | backend/routes/watchlist.py:222-229 (P3b agent) | Observed | HIGH |
| FR-011 | A caller can mark a Market Pick as "bought" (Positions); re-marking refreshes price levels but never overwrites a user-entered `shares`/`bought_at`. | Watchlist & Positions | backend/routes/positions.py:117-130 (P3b agent) | Observed | HIGH |
| FR-012 | A broker sync mirrors each synced holding into `positions` as well as the Portfolio Aggregator's own tables, under whichever owner triggered the sync. | Watchlist & Positions, Portfolio Aggregator | backend/portfolio/positions_mirror.py (routes/pipelines + quality/ops agents) | Observed | HIGH |
| FR-013 | Sign-in never automatically migrates an anonymous browser's watchlist/positions rows onto the account; migration requires an explicit, session-authenticated claim call. | Watchlist & Positions | frontend/app/auth/verify/page.tsx:68-93 (frontend agent, verified no auto-migration) | Observed | HIGH |
| FR-014 | A daily cron re-analyzes every account-owned watched symbol and emails one digest per affected account when the recommendation changed or price moved ≥10%; anonymous rows are excluded at the query level. | Watchlist & Positions | backend/pipelines/watchlist_alerts.py:118-191 (business-flow agent, full trace) | Observed | HIGH |
| FR-015 | A magic-link email round trip creates the `users` row on first successful verification (no separate signup); the token is single-use, consumed atomically. | Accounts & Auth | backend/auth.py:68-101 (backend-core agent) | Observed | HIGH |
| FR-016 | A signed-in user can mint, list, and revoke long-lived API keys; the raw key is shown exactly once. | Accounts & Auth | docs/api-reference.md:692-738 (doc-sourced; not independently re-read this engagement) | Observed | MEDIUM |
| FR-017 | `GET /api/consolidated/{symbol}` aggregates the Stock Analysis, Market Picks, and SME sections for one symbol with zero new fetching; each section is independently `null` when not yet available. | Consolidated Search | frontend/components/consolidated-card.tsx (frontend agent, verified null-handling) | Observed | HIGH |
| FR-018 | A Portfolio Aggregator profile/account/asset/valuation is fully owner-scoped (`client_id` XOR `user_id`); a mismatched id returns 404, never 403. | Portfolio Aggregator | backend/routes/portfolio_aggregator.py, routes/pipelines agent (route inventory + ownership check confirmed) | Observed | HIGH |
| FR-019 | A CAS PDF import replaces (deletes then reinserts) only the previously-CAS-sourced transactions for the matched asset, leaving CSV/broker-sourced rows untouched; PII (PAN/KYC) is scrubbed before archival. | Portfolio Aggregator | backend/portfolio/cas_import.py:55-62,208-212 (P3b + routes/pipelines agents) | Observed | HIGH |
| FR-020 | A broker CSV import appends and dedupes by content key (`date,type,units,amount`) rather than replacing, since a tradebook export is a date-ranged partial. | Portfolio Aggregator | backend/portfolio/csv_import.py:166-287 (routes/pipelines agent) | Observed | HIGH |
| FR-021 | A broker API sync (Zerodha/HDFC/Paytm) runs on a dedicated background executor, writes deduped by a DB-enforced `external_ref` unique constraint, and retries a transient per-fetch failure up to 3× with backoff. | Portfolio Aggregator | backend/portfolio/broker_sync_common.py (routes/pipelines + P3b agents) | Observed | HIGH |
| FR-022 | `POST /api/portfolio/refresh-valuations` is the one deliberately unscoped, global endpoint in the Portfolio Aggregator surface — it recomputes valuations for every non-archived priceable asset system-wide, not just the caller's own. | Portfolio Aggregator | docs/api-reference.md:1082-1087; docs/database.md:604-621 (doc-sourced, disclosed exception) | Observed | MEDIUM |

## 5. Business rules and invariants

| ID | Rule / invariant | Scope | Trigger / precondition | Outcome | Evidence | Status | Confidence |
|---|---|---|---|---|---|---|---|
| BR-001 | A `watchlist_items`/`positions` row belongs to exactly one identity — a `client_id` XOR a `user_id`, never both, never neither. | Watchlist & Positions | Any insert/upsert | `CHECK` constraint rejects a row violating this; two separate `UNIQUE` constraints (not one combined) cap each identity independently since Postgres treats NULLs as distinct | backend/db/models.py (backend-core agent); docs/database.md:242-289 | Observed | HIGH |
| BR-002 | A watchlist/positions identity is capped at 200 rows. | Watchlist & Positions | Add beyond the cap | `422`, except a re-add of an already-held symbol (idempotent no-op, exempt from the cap) | backend/routes/watchlist.py (P3b agent) | Observed | HIGH |
| BR-003 | A valid session bearer always wins over a supplied `client_id` for identity resolution; an invalid/expired token falls through to `client_id` rather than 401ing (these endpoints don't require sign-in). | Watchlist & Positions, Portfolio Aggregator | Any owner-resolved request | Owner determined per `resolve_owner()` | backend/routes/watchlist.py:55-69 (P3b agent) | Observed | HIGH |
| BR-004 | The claim flow (migrating anonymous rows to an account) requires a real, valid session — an expired/missing token here is a genuine `401`, the one exception to BR-003's fall-through rule. | Watchlist & Positions | `POST /api/watchlist/claim`, `POST /api/positions/claim` | `401` on missing/expired session | P3b agent, docs/api-reference.md:852-881 | Observed | HIGH |
| BR-005 | A magic-link token is single-use and expires after 15 minutes; a session token expires after 30 days; an API key never expires until explicitly revoked. | Accounts & Auth | Token issuance/consumption | Enforced via atomic conditional UPDATE (magic link) and expiry-checked-on-read (session) | backend/auth.py:33-34,94-101 (backend-core agent) | Observed | HIGH |
| BR-006 | Only SHA-256 hashes of magic-link tokens, session tokens, and API keys are ever persisted — the raw value exists only in the outbound channel (email, cookie, one-time response) that issued it. | Accounts & Auth | Every token issuance | A full DB leak alone cannot let an attacker sign in as anyone or call `/api/v1/*` | backend/auth.py:47-48 (backend-core agent); P3b agent (adversarial confirmation) | Observed | HIGH |
| BR-007 | A rejected LLM analysis output must not contradict the deterministic signal engine: `final_score > 0.5` blocks a SELL recommendation; `final_score <= -0.6` blocks a BUY; `abs(final_score) < 0.15` blocks a HIGH-confidence call. | Stock Analysis | Every analyst guardrail check with a `signal_context` present | Guardrail rejection triggers one corrective retry | backend/analyst/crew.py:570-655 (backend-core agent) | Observed | HIGH |
| BR-008 | A cited numeric claim in the analyst's prose (9 named metrics: dividend yield, P/E, ROE, ROCE, book value, sales/profit growth, EBITDA margin, market cap) more than 2× off the true source value is rejected. | Stock Analysis | Analyst guardrail check | Guardrail rejection, one corrective retry | backend/analyst/crew.py (backend-core agent); docs/backlog.md's own disclosure that only these 9 metrics are covered | Observed | HIGH |
| BR-009 | A pipeline's `--reset-db` command is scoped to only the tables it owns — no pipeline calls `metadata.drop_all()`. | Cross-cutting | Any `--reset-db` CLI invocation | Only the pipeline's own tables are dropped/recreated | routes/pipelines agent, `grep -n "drop_all"` returned nothing across `pipelines/*.py`; root `CLAUDE.md`'s architectural constraint | Observed | HIGH |
| BR-010 | `prices_daily.adj_close` is written only by the corporate-actions recompute step; the ordinary bhavcopy upsert's `ON CONFLICT` clause deliberately excludes it, so a re-ingest cannot clobber an already-adjusted value. | EOD Price Store | Bhavcopy re-ingestion | `adj_close` survives a re-ingest of the same trading day | docs/database.md:524-529; routes/pipelines agent corroborated the column-level split ownership | Observed | MEDIUM |
| BR-011 | A CAS import's PII (PAN, KYC fields, investor info) is stripped before the parsed statement is archived for replay; the raw PDF bytes are never persisted. | Portfolio Aggregator | Every `POST /api/portfolio/import-cas` | Archived payload contains no PAN/KYC | backend/portfolio/cas_import.py:55-62 (P3b agent, verified) | Observed | HIGH |
| BR-012 | A broker's `api_secret`/`access_token` are Fernet-encrypted before storage; the encryption key's absence is a hard failure at the call site, never a silent plaintext fallback. | Portfolio Aggregator | Every broker credential write | `EncryptionNotConfigured` raised, `503` surfaced to the caller | backend/core/crypto.py:23-53 (backend-core + P3b agents) | Observed | HIGH |

## 6. Journeys and workflows

See `BUSINESS_FLOWS.md` for the full 5-journey evidence-backed trace (Stock Analysis, Market
Picks, Watchlist alert email, Portfolio Aggregator nightly valuation, SME/Screener batch
pipelines), including sync/async boundaries, state transitions, and failure-point tables. Not
duplicated here per this engagement's cross-linking convention.

| Journey | Entry point | Key states / steps | Failure behavior | Evidence | Confidence |
|---|---|---|---|---|---|
| Stock Analysis | `GET /api/analyse/{symbol}` | 10 steps, `BUSINESS_FLOWS.md` Journey 1 | Isolated per-task failure; whole-stream abort only on `stock_info` validation failure; safe-HOLD fallback on total LLM failure | BUSINESS_FLOWS.md | HIGH |
| Market Picks | `GET /api/market-picks` | 10 steps across 6 phases, Journey 2 | Isolated per-source failure; whole-run abort on zero consolidated picks or an uncaught phase exception | BUSINESS_FLOWS.md | HIGH |
| Watchlist alert email | Daily cron | 8 steps, Journey 3 | Isolated per-symbol failure; claim-before-send dedup; SMTP failure releases the claim | BUSINESS_FLOWS.md | HIGH |
| Portfolio nightly valuation | Daily cron (chained) | 7 steps, Journey 4 | Isolated valuation-step failure (never affects EOD pipeline's own exit code); per-asset price miss skipped, prior value retained | BUSINESS_FLOWS.md | HIGH |
| SME/Screener batch | Daily cron | 4-5 steps each, Journey 5 | Isolated per-stock failure; whole-run health gate at >50% error rate | BUSINESS_FLOWS.md | HIGH |

## 7. State and lifecycle model

See `STATE_MACHINE.md` for the full evidence-backed state model (`verdict_history` verdict
lifecycle, watchlist-alert claim-key lifecycle, `broker_connections` connection lifecycle,
`valuations` freshness lifecycle, batch-pipeline health-gate outcome). One contradiction-worth-
noting: `docs/backlog.md` records that concurrent same-day re-analyses of the same symbol can
produce different LLM outputs, with the last write silently winning over the `verdict_history`
row's `ON CONFLICT` upsert — a disclosed, unfixed race, not a hidden one.

## 8. Interfaces and contracts

### APIs

63 HTTP endpoints — see `API_CATALOG.md` for the full producer/consumer/evidence table. Auth
modes: none, session bearer, owner-resolved (session-or-`client_id`), and API key
(`X-API-Key`, deliberately distinct from `Authorization: Bearer` to prevent a forwarded session
from satisfying the API-key check).

### Events and asynchronous contracts

No message broker exists (architectural constraint). The only asynchronous contracts are two SSE
streams (12+6 distinct event types, `EVENT_CATALOG.md`) and 6 scheduled cron triggers. No inbound
webhooks exist anywhere — broker sync is polling/pull-only (P3b agent, adversarially confirmed).

### Scheduled / batch / CLI / file interfaces

6 batch pipelines, each with a `run() -> bool` + `main()` CLI wrapper that exits non-zero on a
health-gate failure — see `EVENT_CATALOG.md` § Scheduled triggers and `BUSINESS_FLOWS.md`.
`main.py`'s CLI (`python main.py SYMBOL`) is a third entry point into the same Stock Analysis
pipeline the SSE endpoint uses, sharing `_fetch_task`/`_build_report`.

## 9. Data model and ownership

23 PostgreSQL tables under one `SQLAlchemy Core MetaData()` — see `DATA_OWNERSHIP.md` for the
full per-table authoritative-source/replica/cache/consumer table, and `DATA_OWNERSHIP_GRAPH.yaml`
for the machine-readable projection. Source of truth is PostgreSQL for anything cross-session;
the file cache under `backend/output/` is explicitly regenerable, never authoritative (confirmed:
`core/cache.py::_is_failed_payload` refuses to persist a failed scrape as if it were data, and
freshness is always re-derived from `_meta.fetched_at`, never trusted from a store's own TTL
mechanism). `DATABASE_URL` itself is optional — the core single-stock-analysis flow runs fully
without Postgres at all (file-cache-backed); see `ALPHAPULSE_MAP.md` § Core Domain Deep Dive for
the full per-feature degradation table when it's unset.

## 10. Dependencies and integrations

| Dependency | Direction | Contract / purpose | Failure behavior | Evidence | Confidence |
|---|---|---|---|---|---|
| PostgreSQL | Outbound (backend depends on it) | Sole persistent datastore | Optional overall; individual features 503/degrade per `ALPHAPULSE_MAP.md`'s degradation table | backend-core agent | HIGH |
| Redis | Outbound (optional) | Shared rate-limit/cache state across workers | Every call site falls back to in-memory/local-disk on absence or error | quality/ops agent (verified fallback logic) | HIGH |
| NSE/BSE/Screener.in/Trendlyne/AMFI/RBI/GNews/yfinance | Outbound | Scraped data sources | Every scraper "never raises" — degrades to `{"error":...}`/`null`; SSRF host-checks guard any URL derived from scraped content | backend-core, routes/pipelines, P3b agents | HIGH |
| LLM providers (6, one primary + one failover) | Outbound | Analyst synthesis + Market Picks extraction/analysis | Cross-provider failover, then safe-HOLD fallback; never raises to the caller | backend-core agent | HIGH |
| Zerodha/HDFC Securities/Paytm Money | Outbound | Broker holdings/trades sync | Retries transient failures 3× with backoff; per-connection lock + 12/hr rate limit; sync errors surface as row state, not a request-time error | routes/pipelines, P3b agents | HIGH (mechanism); MEDIUM (HDFC/Paytm live-response fidelity, disclosed unverified) |
| SMTP | Outbound | Magic-link + watchlist-alert email delivery | Best-effort, never raises; missing `SMTP_HOST` is a silent no-op | backend-core agent | HIGH |
| Sentry (optional) | Outbound | Error tracking | Genuinely inert when `SENTRY_DSN` unset | backend-core agent | HIGH |
| GitHub Actions | Inbound trigger | Cron scheduling for 6 batch jobs | A pipeline's own health gate causes the job to fail loudly on substantial failure (4 of 6 pipelines confirmed; `corporate_actions_pipeline.py`'s gate is UNKNOWN) | business-flow agent | HIGH (schedule), MEDIUM (universal health-gate coverage) |

## 11. Authorization, security, fraud, and compliance controls

See `ALPHAPULSE_MAP.md` § Fraud & Compliance for the full adversarially-reviewed control table
(11 controls: replay/dedup protection, webhook spoofing [N/A], encryption at rest, magic-link
security, rate-limiting coverage, SSRF mitigation, PII handling, IDOR/privilege escalation,
hardcoded secrets [none found], maker-checker [correctly absent], audit trail). Summary verdict:
every control that should exist for a system of this shape and stakes does exist, verified by
attempted bypass rather than presence alone; the two disclosed gaps (rate-limiting is opt-in by
convention rather than framework-enforced; a plaintext email is logged at warning level on one
failure path) are both low-to-medium severity and already tracked in `RISK_MAP.md`.

## 12. Non-functional requirements

| ID | Area | Requirement / observed constraint | Scope | Evidence | Status | Confidence |
|---|---|---|---|---|---|---|
| NFR-001 | Idempotency | Every retriable write path uses an explicit idempotency mechanism (`ON CONFLICT` upsert, atomic conditional UPDATE, DB unique constraint, or advisory lock) — with one disclosed exception (`csv_import`/`cas_import`'s content-key dedup has no DB-level constraint, a read-then-write race under true concurrency). | Cross-cutting | `ALPHAPULSE_MAP.md` § Core Domain Deep Dive; `docs/backlog.md` item #2 | Observed | HIGH |
| NFR-002 | Concurrency / rate limiting | A global LLM concurrency ceiling (default 4 slots, Redis-backed with a 600s TTL when available) is shared across the two LLM-calling flows; most HTTP routes have a per-IP or per-account sliding-window rate limit, applied via an opt-in helper rather than framework middleware. | Cross-cutting | backend-core, P3b, quality/ops agents | Observed | MEDIUM |
| NFR-003 | Availability under missing optional infra | The system runs correctly (core Stock Analysis flow fully functional) with `DATABASE_URL`, `REDIS_URL`, `SMTP_HOST`, and `SENTRY_DSN` all unset — each degrades independently and predictably rather than crashing. | Cross-cutting | backend-core, quality/ops agents (verified each fallback path) | Observed | HIGH |
| NFR-004 | Consistency (single-writer discipline) | Two tables have more than one write path (`transactions`: 3 writers; `positions`: 2 writers); both are mitigated by a `meta.source`/column-partition discriminator rather than left as an unmanaged race, except that `csv_import`'s dedup lacks a DB-level guard (see NFR-001). | Portfolio Aggregator, Watchlist & Positions | `RISK_MAP.md` "Multiple writers" entries | Observed | HIGH |
| NFR-005 | Security — credential storage | No hardcoded secret literals exist anywhere in `backend/config`/`backend/` source; every credential-shaped value is read from an environment variable. | Cross-cutting | P3b agent, `rg` scan returned zero matches | Observed | HIGH |
| NFR-006 | Security — encryption | Broker `api_secret`/`access_token` are Fernet-encrypted at rest; `api_key` is deliberately left plaintext (treated as a public client id, matching the broker's own security model, not a gap). | Portfolio Aggregator | backend/core/crypto.py (backend-core, P3b agents) | Observed | HIGH |
| NFR-007 | Security — magic-link entropy | Magic-link/session/API-key tokens are generated via `secrets.token_urlsafe(32)` (256-bit CSPRNG entropy) and persisted only as SHA-256 hashes. | Accounts & Auth | backend/auth.py:58 (backend-core, P3b agents) | Observed | HIGH |
| NFR-008 | Recovery — SSRF defense | Every URL derived from scraped/parsed content (not a hardcoded constant) is host-checked before being fetched (`_is_nse_host`, `_is_trendlyne_host`). | Stock Analysis, Market Picks (scrapers) | P3b, routes/pipelines agents | Observed | HIGH |
| NFR-009 | Data quality — "never invent" | A missing scraped or computed value is represented as `null`/empty, never a fabricated plausible-looking value, across the scraper layer, the signal engine's own convention, and the DCF valuation module. | Cross-cutting | docs/PRD.md §4 (design principle); backend-core agent (multiple independent code confirmations) | Observed | HIGH |
| NFR-010 | Observability — correlation | Every request in the two LLM-calling flows carries a `run_id` threaded through every `log_event()` call in its lifecycle; no equivalent correlation ID exists for the majority of read-only HTTP routes. | Cross-cutting | quality/ops agent (verified via `grep`) | Observed | HIGH |
| NFR-011 | Batch job failure semantics | 4 of 6 batch pipelines confirm a `>50%`-error-rate (or empty-input) health gate that causes a non-zero exit rather than a silent partial success; `corporate_actions_pipeline.py`'s equivalent gate is UNKNOWN (not confirmed present or absent by direct full-file reading this engagement). | Cross-cutting | business-flow, routes/pipelines agents | Inferred | MEDIUM |
| NFR-012 | Scalability constraint (by design) | The architecture is a single-process monolith sized for "a single operator and tens of users" (root `CLAUDE.md`'s own stated constraint) — no message broker, no Kubernetes, no service split; `ThreadPoolExecutor` and GitHub Actions cron are the only concurrency/scheduling primitives. | Cross-cutting | root CLAUDE.md (committed design doc); confirmed by absence of any broker/orchestrator dependency in `requirements.txt`/imports across all six agents' reports | Observed | HIGH |

## 13. Configuration and deployment behavior

Single Docker Compose deployment (frontend :3000, backend :8000, Postgres, optional Redis) —
topology not independently re-verified this engagement (`DEPENDENCY_GRAPH.md` § Deployment graph
is doc-sourced, MEDIUM confidence). Base URL resolution: every frontend proxy route uses
`process.env.API_URL ?? 'http://localhost:8000'` (confirmed zero hardcoded exceptions across all
30 files by the frontend agent). No feature-flag framework exists (confirmed zero matches by the
quality/ops agent's repo-wide grep) — every optional-infrastructure behavior is gated by its own
single-purpose env var (`DATABASE_URL`, `REDIS_URL`, `SMTP_HOST`, `SENTRY_DSN`,
`PORTFOLIO_AGGREGATOR_ENABLED`). Six GitHub Actions cron workflows drive the batch pipelines
(schedules confirmed by direct file read — see `EVENT_CATALOG.md`).

## 14. Observability and operations

See `ALPHAPULSE_MAP.md` § Quality & Ops and `RUNBOOK.md` for the full detail. Summary: structured
JSON logging via `log_event()`; correlation IDs scoped to the two LLM-calling flows only (a
disclosed gap); per-call LLM cost tracked and accumulated per UTC day; optional, genuinely inert
Sentry integration; no dedicated kill-switch/feature-toggle mechanism; no formal audit-log table
(two `log_event()` calls function as an ad-hoc audit trail for the claim flow specifically).

## 15. Error and failure semantics

| Failure / error | Trigger | User/system-visible behavior | Retry / compensation | Evidence | Confidence |
|---|---|---|---|---|---|
| One of six Stock-Analysis data-slice fetches fails | Scraper exception, exhausted internal retries | `task_done{ok:false}` for that task alone; report still assembles with the other five | Internal retry ≤3× per task before surfacing failure; caller can `?force=true` on the next request | api.py:619-637 (business-flow agent) | Observed | HIGH |
| `stock_info` task fails validation | Malformed/missing required field | Whole SSE stream emits terminal `error` | Caller re-requests | api.py:642-645 | Observed | HIGH |
| Both LLM providers fail | Guardrail/rate-limit/exception on primary and failover | Safe HOLD returned, `degraded:true` on the report | None automatic — a later manual re-request may succeed if the outage clears | analyst/crew.py:658-693,925 | Observed | HIGH |
| A batch pipeline's per-item error rate exceeds its threshold | Widespread scrape/fetch failure | `run()` returns `False`; `main()` exits non-zero; GitHub Actions job fails loudly | None automatic — requires operator intervention/re-run | 4 of 6 pipelines confirmed directly | Observed | HIGH |
| SMTP send fails (magic link) | SMTP misconfiguration/outage | `POST /api/auth/request-link` still returns `{"sent": true}` (deliberate, avoids leaking SMTP state); logged as a warning | The same link works once SMTP is fixed — no re-request needed | docs/api-reference.md:632-638 (doc-sourced) | Observed | MEDIUM |
| SMTP send fails (watchlist alert) | Same | Claimed alert keys are released so the alert isn't permanently marked sent | Next day's run retries naturally; no immediate automatic retry | backend/pipelines/watchlist_alerts.py:319 (business-flow agent) | Observed | HIGH |
| A broker sync fetch fails transiently | 5xx/timeout from the broker's API | Retried up to 3× with exponential backoff before `sync_status: "error"` | Operator/user re-triggers `POST .../sync` | routes/pipelines agent | Observed | HIGH |
| Unset `DATABASE_URL` | Missing env var | Splits three ways by design: `503` for DB-backed endpoints (watchlist/positions/SME/screener/portfolio-aggregator/auth-request-link/verify); `200` with an empty/degraded payload for supplementary endpoints (verdict-history, consolidated); `401` for `/api/auth/me`, `/api/v1/consolidated` | N/A — configuration fix | docs/database.md "DATABASE_URL is optional" table (doc-sourced, structurally consistent with every agent's independent confirmation of the optional-infra pattern) | Observed | MEDIUM |

## 16. Constraints and architectural decisions

See `ARCHITECTURE_DECISIONS.md` for the full list. The binding architectural constraint (monolith,
PostgreSQL-only, Redis-optional, `ThreadPoolExecutor` not a broker, GitHub Actions cron not an
orchestrator, file cache regenerable/Postgres durable, plain deploys not Kubernetes) is recorded
in root `CLAUDE.md` and independently confirmed against the actual dependency set and code
structure by all six of this engagement's dispatched research agents — no contradicting evidence
was found anywhere.

## 17. Known gaps, contradictions, and risks

See `RISK_MAP.md` (ranked top-10 + full smell inventory + change-risk table) and `UNKNOWNS.md`
(15 open questions this engagement could not close within its discovery budget or read-only
boundary). Headline items: (1) rate limiting's opt-in-by-convention design pattern; (2) a doc-drift
finding (22 vs. 23 Postgres tables — code says 23, trusted per evidence precedence); (3)
`corporate_actions_pipeline.py`'s health-gate completeness is unconfirmed; (4) `csv_import`/
`cas_import` lack a DB-level idempotency constraint under true concurrency; (5) `prices_daily
.adj_close` has no confirmed production reader despite being actively written every cron run.
Business/legal risk items (SEBI registration determination, scraping ToS risk acceptance, bus
factor of one, no real payments) are recorded in `docs/PRD.md` §16-17 as decisions already made by
the operator — this engagement reports them as-is per evidence precedence, taking no position on
their correctness.

## 18. Success measures and analytics

**UNKNOWN — product intent not recoverable from implementation evidence, beyond what is explicitly
computed in code.** Two real, computed-today metrics exist: Market Picks' per-tier win rate/alpha-
vs.-Nifty track record, and per-stock verdict win/loss scoring against subsequent price moves
(both confirmed by direct code reading across multiple agents' reports). `docs/PRD.md` §12 itself
states plainly that every other candidate metric (weekly active investors, analyses/user, retention,
etc.) is "not instrumented" — this PRD does not manufacture targets for them.

## 19. Requirement traceability

| Requirement ID | Requirement status | Evidence source(s) | Evidence type | Confidence | Notes / contradiction |
|---|---|---|---|---|---|
| FR-001 | Observed | backend/api.py:575-793 | Code | HIGH | |
| FR-002 | Observed | backend/api.py:579-580 | Code | HIGH | |
| FR-003 | Observed | docs/api-reference.md:255-273 | Authoritative doc | MEDIUM | Not independently re-read this engagement, cited as MEDIUM per evidence precedence |
| FR-004 | Observed | backend/analyst/crew.py:745-925 | Code | HIGH | |
| FR-005 | Observed | backend/analytics/verdict_history.py:33-109 | Code | HIGH | |
| FR-006 | Observed | backend/pipelines/market_picks_pipeline.py | Code | HIGH | |
| FR-007 | Observed | backend/api.py:899-905 | Code | HIGH | |
| FR-008 | Observed | backend/pipelines/sme_ema_pipeline.py | Code | HIGH | |
| FR-009 | Observed | backend/pipelines/screener_pipeline.py | Code | HIGH | |
| FR-010 | Observed | backend/routes/watchlist.py:222-229 | Code | HIGH | |
| FR-011 | Observed | backend/routes/positions.py:117-130 | Code | HIGH | |
| FR-012 | Observed | backend/portfolio/positions_mirror.py | Code | HIGH | |
| FR-013 | Observed | frontend/app/auth/verify/page.tsx:68-93 | Code | HIGH | |
| FR-014 | Observed | backend/pipelines/watchlist_alerts.py:118-191 | Code | HIGH | |
| FR-015 | Observed | backend/auth.py:68-101 | Code | HIGH | |
| FR-016 | Observed | docs/api-reference.md:692-738 | Authoritative doc | MEDIUM | Not independently re-read this engagement |
| FR-017 | Observed | frontend/components/consolidated-card.tsx | Code | HIGH | |
| FR-018 | Observed | backend/routes/portfolio_aggregator.py | Code | HIGH | |
| FR-019 | Observed | backend/portfolio/cas_import.py:55-62,208-212 | Code | HIGH | |
| FR-020 | Observed | backend/portfolio/csv_import.py:166-287 | Code | HIGH | |
| FR-021 | Observed | backend/portfolio/broker_sync_common.py | Code | HIGH | |
| FR-022 | Observed | docs/api-reference.md:1082-1087 | Authoritative doc | MEDIUM | Not independently re-read this engagement |
| BR-001 | Observed | backend/db/models.py | Code | HIGH | |
| BR-002 | Observed | backend/routes/watchlist.py | Code | HIGH | |
| BR-003 | Observed | backend/routes/watchlist.py:55-69 | Code | HIGH | |
| BR-004 | Observed | backend/routes/watchlist.py (P3b agent) | Code | HIGH | |
| BR-005 | Observed | backend/auth.py:33-34,94-101 | Code | HIGH | |
| BR-006 | Observed | backend/auth.py:47-48 | Code | HIGH | |
| BR-007 | Observed | backend/analyst/crew.py:570-655 | Code | HIGH | |
| BR-008 | Observed | backend/analyst/crew.py | Code | HIGH | |
| BR-009 | Observed | backend/pipelines/*.py (grep, no drop_all) | Code | HIGH | |
| BR-010 | Observed | docs/database.md:524-529 | Authoritative doc | MEDIUM | Column-level ownership corroborated but not independently re-read line-by-line |
| BR-011 | Observed | backend/portfolio/cas_import.py:55-62 | Code | HIGH | |
| BR-012 | Observed | backend/core/crypto.py:23-53 | Code | HIGH | |
| NFR-001 | Observed | RISK_MAP.md, docs/backlog.md item #2 | Code + authoritative doc | HIGH | |
| NFR-002 | Observed | backend/core/rate_limiter.py | Code | MEDIUM | Mechanism verified HIGH; universal per-route coverage not exhaustively confirmed |
| NFR-003 | Observed | backend-core, quality/ops agent reports | Code | HIGH | |
| NFR-004 | Observed | RISK_MAP.md | Code | HIGH | |
| NFR-005 | Observed | P3b agent, rg scan | Code | HIGH | |
| NFR-006 | Observed | backend/core/crypto.py | Code | HIGH | |
| NFR-007 | Observed | backend/auth.py:58 | Code | HIGH | |
| NFR-008 | Observed | P3b, routes/pipelines agents | Code | HIGH | |
| NFR-009 | Observed | docs/PRD.md §4; backend-core agent | Code + authoritative doc | HIGH | |
| NFR-010 | Observed | quality/ops agent, grep | Code | HIGH | |
| NFR-011 | Inferred | business-flow, routes/pipelines agents (4/6 pipelines confirmed, 1 UNKNOWN) | Code | MEDIUM | corporate_actions_pipeline.py's gate not directly confirmed present or absent |
| NFR-012 | Observed | root CLAUDE.md; requirements.txt / import graph across all agent reports | Code + authoritative doc | HIGH | |

## 20. Open product-intent questions

- Is the intended long-term positioning of Watchlist alert emails (currently a plain digest) meant
  to expand into the "What changed since yesterday" daily-companion digest named as a roadmap
  candidate in `docs/PRD.md` §15? Code shows only the current, narrower implementation — no
  evidence of the larger vision being under active construction. See `UNKNOWNS.md`.
- Is `POST /api/portfolio/refresh-valuations`'s global, unscoped design (FR-022) intended to
  remain permanent, or is it a known-temporary simplification for the current single-operator
  scale? No code or doc evidence resolves this either way.
- Whether `corporate_actions_pipeline.py` is intended to eventually gain a production reader for
  `adj_close`, or whether the entire adjustment pipeline is deliberately pre-built infrastructure
  for a future migration that has not yet been prioritized — `docs/backlog.md` frames it as the
  latter, but this is the operator's own prose, not independently verifiable intent.
- The exact criteria for when `docs/PRD.md` §17.4's SEBI-registration determination or §17.2's
  scraping-risk acceptance would be re-opened are stated qualitatively ("the circle grows,"
  "materially higher volume") but have no quantitative trigger defined anywhere in code or docs —
  this is a genuine open business question, not a comprehension gap.
- Whether Paytm Money's and HDFC Securities' broker-sync integrations are intended to reach the
  same "verified against a live account" status as Zerodha's before being promoted out of "beta"
  in the UI, or whether they will remain permanently best-effort given the sandbox's inability to
  reach those developer portals — `docs/backlog.md` frames this as still open.
