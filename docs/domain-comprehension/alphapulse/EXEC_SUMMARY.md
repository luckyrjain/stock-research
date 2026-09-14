# Executive Summary

## Evidence summary

| Metric | Count | Notes |
|--------|------:|-------|
| Repositories scanned | 1 / 1 in scope | Single-repo layout (`backend/` + `frontend/` stacks within it), both walked thoroughly |
| Files inspected | ~190 (aggregate, cross-agent, approximate) | Sum across 6 dispatched research agents' reported file counts (22+35+64+21+23+14, allowing for some cross-agent overlap on shared files like `routes/watchlist.py`) plus ~7 in-repo docs read directly by the orchestrating session (`docs/database.md`, `docs/api-reference.md`, `docs/architecture.md`, `docs/tools.md`, `docs/PRD.md`, `docs/backlog.md`, plus auto-loaded `CLAUDE.md` files). This is an aggregate estimate, not a deduplicated exact count — disclosed as such. |
| Runtime edges confirmed | 0 / 0 total | P2b skipped — no Datadog/KubeSense MCP authorized this session |
| Events verified | 18 (12 Market Picks SSE + 6 Stock Analysis SSE event types, all `referenced` exercise status) | See `EVENT_CATALOG.md` |
| APIs verified | 63 (all `referenced` exercise status; 0 `runtime_confirmed` since P2b is skipped) | See `API_CATALOG.md` |
| Unknowns | 13 | See `UNKNOWNS.md` |
| Known omissions | 6 | See `KNOWN_OMISSIONS.md` |

## Time & Effort

**Model:** claude-sonnet-5

| Phase | Completed | Elapsed since previous phase (wall-clock, includes any RESUME gaps) |
|-------|-----------|------------------------------------------------------------------|
| session_0 | 2026-09-13T10:35:00Z | — |
| session_0b | 2026-09-13T10:36:00Z | ~1m |
| p0 | 2026-09-13T11:20:00Z | ~44m (parallel research agent dispatch + secondary-doc reading) |
| p0_25 | 2026-09-13T11:20:00Z | concurrent with p0 |
| p0_5 | 2026-09-13T11:20:00Z | concurrent with p0 (degraded to manual/grep) |
| p1 | 2026-09-13T11:25:00Z | ~5m (synthesis of 6 agent reports into DATA_OWNERSHIP.md/BOUNDED_CONTEXTS.md) |
| p2 | 2026-09-13T11:35:00Z | ~10m (BUSINESS_FLOWS.md, STATE_MACHINE.md, DEPENDENCY_GRAPH.md synthesis) |
| p2b | 2026-09-13T11:35:00Z | concurrent (skipped, recorded) |
| p3 | 2026-09-13T11:45:00Z | ~10m (Core Domain Deep Dive, implementation matrix) |
| p3b | 2026-09-13T11:45:00Z | concurrent with p3 (P3b agent report merged) |
| p4 | 2026-09-13T11:50:00Z | ~5m (RISK_MAP.md synthesis) |
| p5 | 2026-09-13T12:10:00Z | ~20m (PRD.md, machine YAML files, this document, ARCHITECTURE_DECISIONS.md, RUNBOOK.md, DOMAIN_GLOSSARY.md) |

**Size proxy:** 1 repo scanned (~98 backend .py files, ~157 frontend .ts/.tsx files per the
engagement brief), ~190 files inspected (aggregate) — see Evidence summary above.

## Overall confidence

**Overall:** MEDIUM *(weakest material items: (a) roughly half of `backend/api.py` — the
standalone enrichment/auth/API-key/SME/screener/consolidated route bodies — was not independently
re-read line-by-line this engagement, relying on the repo's own committed `docs/api-reference.md`
instead, capped at MEDIUM per evidence precedence; (b) P2b runtime validation is entirely skipped
(UNKNOWN) — no Datadog/KubeSense authorization in this environment; (c) HDFC Securities' and
Paytm Money's broker-sync REST shapes are disclosed-unverified against a live account by the
codebase's own admission. None of these gaps contradict any finding — they simply cap how high
this engagement can honestly rate its own completeness.)*

| Question | Status | Confidence |
|----------|--------|------------|
| Q1 | COMPLETE | HIGH |
| Q2 | COMPLETE | HIGH |
| Q3 | COMPLETE | HIGH |
| Q4 | COMPLETE | HIGH |
| Q5 | COMPLETE | HIGH |

## Draft Five Questions

*(Retained under this heading per template; all five reached COMPLETE status by P3 and are
carried forward unchanged into Final Five Questions below — no question stalled at DRAFT/PARTIAL.)*

### Q1 — What component performs the core side effect of this domain (producing and persisting a BUY/HOLD/SELL verdict)?
Evidence: `backend/analyst/crew.py::run_analysis_with_fallback()` (`crew.py:840-925`, backend-core agent, full verified read) produces the guardrailed LLM verdict; `backend/analytics/verdict_history.py::save_snapshot()` (`:33-109`) is the sole persistence path, called from three independent entry points (SSE handler, CLI, daily watchlist-alert re-check) — confirmed identically by the backend-core and business-flow tracing agents.
Conclusion: The core side effect is the analyst's guardrailed LLM call plus its atomic upsert into `verdict_history`.
Confidence: HIGH

### Q2 — What prevents duplicate processing (idempotency / dedup)?
Evidence: `ON CONFLICT ... DO UPDATE/DO NOTHING` upserts across 10+ tables; atomic conditional `UPDATE ... WHERE used_at IS NULL RETURNING` for magic-link single-use consumption (`auth.py:94-101`); `pg_advisory_xact_lock` for watchlist/positions cap enforcement and the two-lock claim flow; a DB-enforced `UNIQUE` constraint (`uq_transactions_asset_external_ref`) for the broker-sync writer specifically; `state_store.mutate()`'s row-lock pattern for `app_state` counters and the watchlist-alert claim-before-send mechanism.
Conclusion: A deliberate, multi-layered idempotency strategy exists and is applied consistently, with one disclosed exception (`csv_import`/`cas_import`'s content-key dedup has no DB-level backstop, a genuine but low-severity concurrency gap under this app's single-operator/household-scale usage).
Confidence: HIGH

### Q3 — What is the source of truth for domain state?
Evidence: 23 PostgreSQL tables under one `SQLAlchemy Core MetaData()` (`db/models.py`) are authoritative for anything cross-session; the file cache under `backend/output/` is explicitly regenerable — `core/cache.py::_is_failed_payload()` refuses to persist a failed scrape, and freshness is always re-derived from `_meta.fetched_at`, never trusted from a store's own TTL. `DATABASE_URL` itself is optional; the core single-stock-analysis flow runs fully without Postgres (file-cache-backed only).
Conclusion: PostgreSQL is the source of truth wherever it is configured; the file cache is a pure, regenerable performance layer, never authoritative — and the product is explicitly designed to degrade gracefully when Postgres is absent.
Confidence: HIGH

### Q4 — How is reconciliation or consistency performed across systems?
Evidence: A daily cron (`pipelines/watchlist_alerts.py`) re-analyzes every account-owned watched symbol and diffs the two most recent `verdict_history` rows to detect a recommendation change or a ≥10% price move; `pipelines/corporate_actions_pipeline.py` recomputes `prices_daily.adj_close` after each new corporate action (though this engagement found no confirmed production reader of that column); `portfolio/portfolio_valuation.py::refresh_valuations()` runs nightly, chained after EOD price ingestion, to keep asset valuations current.
Conclusion: Reconciliation is scheduled and per-domain (verdict re-check, price adjustment, valuation refresh) rather than a single cross-cutting mechanism — consistent with this codebase's monolith-with-independent-batch-jobs architecture.
Confidence: HIGH

### Q5 — What happens when the primary operation fails?
Evidence: The LLM analyst call retries once on a guardrail failure, once on a rate-limit error, fails over to one other auto-detected provider (only if `LLM_PROVIDER` wasn't pinned), then falls back to a safe HOLD (`degraded:true`) — never raising to the caller. Every scraper "never raises," degrading to `{"error":...}`/`null`. Every batch pipeline isolates per-item failures and (in 4 of 6 confirmed cases) aborts with a non-zero exit if a health-gate threshold is crossed, so GitHub Actions fails the job loudly rather than silently succeeding with mostly-empty data.
Conclusion: Failure handling is deliberately layered and consistent: isolate the smallest failure unit, retry/failover where cheap, and fail loudly at the batch/job level rather than silently degrading data quality — with one disclosed exception (`corporate_actions_pipeline.py`'s health-gate completeness could not be confirmed this engagement).
Confidence: HIGH

## Final Synthesis

### Final Five Questions

All five questions reached COMPLETE status with HIGH confidence by P3 and remained unchanged
through P5 — see above. No question required revision during the P3b/P4/P5 passes.

### Executive Summary (narrative)

AlphaPulse is a mature, single-operator FastAPI + Next.js monolith that is unusually
well-documented and unusually well-disciplined for its scale: every one of this engagement's six
independent research agents found the codebase's own `CLAUDE.md`/`docs/*.md` claims to be accurate
against source wherever they were checked, with only a small number of genuine doc-drift findings
(a stale "22 tables" claim vs. the actual 23; one stale claim that a symbol resolver was "not yet
wired in" when it demonstrably is). The domain spans five loosely-coupled bounded contexts (Stock
Analysis, Market Picks, SME Signals, NIFTY 500 Screener, Portfolio Aggregator) plus three
cross-cutting contexts (Accounts & Auth, Watchlist & Positions, Consolidated Search), all sharing
one PostgreSQL instance and a small set of well-factored ownership-resolution and idempotency
primitives. Security posture, adversarially reviewed, is genuinely strong for the product's stated
stakes: encryption, rate limiting, SSRF defenses, and IDOR protections all held up under an attempt
to disprove them, with the two disclosed gaps (opt-in rather than framework-enforced rate limiting;
one low-severity PII-in-logs instance) both already low-to-medium severity. The most material risk
to future work is not a hidden defect but a structural one already named by the codebase's own
docs: bus factor of one, no legal review of the scraping surface, and no real payment processing —
none of which any code change can close.

### Engineering Leader Summary

#### Domain maturity

| Dimension | Rating | One-line evidence |
|-----------|--------|-------------------|
| Domain model clarity | 4/5 (Mature) | 5 cleanly-separated product-mode contexts + 3 cross-cutting contexts, confirmed by module boundaries and data-ownership lines; one naming inconsistency disclosed ("verdict" has 4+ names across the UI, `docs/backlog.md`) |
| Bounded context separation | 4/5 (Mature) | No cyclic dependency found; only 2 genuine multi-writer tables, both disclosed and mitigated |
| API/event contract discipline | 4/5 (Mature) | 63 endpoints with a consistent auth-mode taxonomy and sanitized-error convention; 14 disclosed response-contract inconsistencies (`docs/api-reference.md`), all individually minor |

#### Operational maturity

| Dimension | Rating | Evidence |
|-----------|--------|----------|
| Observability (logs/metrics/traces) | 3/5 (Emerging) | Structured logging + per-call LLM cost tracking exist; correlation IDs scoped to only 2 of many flows |
| Runbook coverage | 3/5 | This engagement's code-derived `RUNBOOK.md` covers all 6 required procedures, but several rely on inference (no dedicated kill-switch exists) |
| Failure handling / retry | 4/5 (Mature) | Consistent isolate-then-retry-then-fail-loudly pattern across 4/6 confirmed pipelines and the analyst call |
| Reconciliation | 3/5 | Per-domain scheduled reconciliation exists (Q4) but no cross-cutting mechanism; `adj_close` reconciliation appears unconsumed |

#### Architecture quality

| Dimension | Rating | Evidence |
|-----------|--------|----------|
| Coupling / cohesion | 4/5 (Mature) | Clean module DAG (no cycles found); shared helpers (`resolve_owner()`) are genuinely cross-cutting, not accidental duplication |
| Critical path clarity | 4/5 | `BUSINESS_FLOWS.md`'s 5 journeys are each fully traceable hop-by-hop in source |
| Runtime vs. code alignment (P2b) | UNKNOWN | P2b skipped entirely — no Datadog/KubeSense available this session |
| Smell count (Critical/High) | 0 Critical, 0 High, ~8 Medium | See `RISK_MAP.md` — no smell in this codebase reached Critical/High severity |

#### Ownership clarity

100% of the one in-scope repo has HIGH squad confidence (sole operator, `SQUAD_MAP.md`) — trivial
by construction for a single-operator project, 0 conflicts.

#### Documentation quality

Exceptionally high for a single-operator project — `backend/CLAUDE.md` alone runs to ~3100 lines
of feature-by-feature, code-cited narrative, and `docs/backlog.md`/`docs/api-reference.md`/
`docs/database.md` are unusually self-critical (documenting their own known gaps with the same
rigor as their happy-path claims). No formal ADRs exist (`ARCHITECTURE_DECISIONS.md` reconstructs
decisions from the `CLAUDE.md` constraints table instead). A small number of genuine doc-vs-code
and doc-vs-doc disagreements were found and are listed in `RISK_MAP.md`/`UNKNOWNS.md`.

#### Testing confidence

Strong on the backend (78 `unittest`-based test files across routes/pipelines/portfolio/analyst/
signals, plus 1 opt-in live-contract-check file); the critical Stock-Analysis path is tested. Weak
on the frontend: zero unit tests exist for `frontend/lib/*.ts` hook logic — only mocked Playwright
E2E specs exercise it indirectly, a disclosed gap in `docs/backlog.md`.

#### Deployment risk

From `RISK_MAP.md` § Change risk: **3 High** (Stock Analysis/analyst path, Accounts & Auth,
Portfolio Aggregator + broker sync — all high-stakes and/or high-fan-out but also the most
heavily tested), **3 Moderate** (Watchlist & Positions, Market Picks, EOD Price Store), **2 Safe**
(SME/Screener leaf pipelines, the frontend BFF proxy layer), **0 Unknown**.

#### Recommended investments

1. Close the `csv_import`/`cas_import` DB-level idempotency gap (`RISK_MAP.md` Top Smell #5) —
   the cheapest fix with the clearest correctness payoff, given the broker-sync writer already
   proves the pattern.
2. Confirm (or fix) `corporate_actions_pipeline.py`'s health-gate completeness (`RISK_MAP.md` Top
   Smell #10) — the one pipeline this engagement could not confirm fails loudly on near-total
   ingest failure.
3. Add a frontend unit-test suite for `lib/*.ts` (SSE parsing especially) — the current
   mocked-E2E-only coverage cannot catch a regression Playwright's happy-path specs would miss.
4. Extend the `run_id` correlation-ID pattern beyond the two LLM-calling flows to every HTTP
   request, closing the observability gap named in `RISK_MAP.md` and this document's Operational
   maturity table.
5. Resolve the two doc-drift findings (22 vs. 23 tables; the stale "resolver not wired in" claim)
   — trivial fixes that reduce future onboarding friction for a bus-factor-of-one project.

#### Top 5 technical debt items

| # | Item | Impact | Evidence | Confidence |
|---|------|--------|----------|------------|
| 1 | Rate limiting is opt-in per-route, not framework-enforced | A future unguarded route gets zero abuse/cost protection | P3b agent | HIGH |
| 2 | `csv_import`/`cas_import` lack a DB-level idempotency constraint | Concurrent uploads could corrupt XIRR math | routes/pipelines agent, `docs/backlog.md` #2 | HIGH |
| 3 | `corporate_actions_pipeline.py`'s health-gate completeness is unconfirmed | A near-total silent ingest failure could go undetected | `docs/backlog.md` (self-disclosed), not independently confirmed this engagement | MEDIUM |
| 4 | No frontend unit tests | Regressions in SSE parsing / shared-cache hooks caught only by mocked E2E | quality/ops, frontend agents | HIGH |
| 5 | Correlation IDs scoped to 2 of many flows | Harder to trace a non-LLM request across logs | quality/ops agent | HIGH |

### Section confidences

| Section | Confidence | Weakest evidence |
|---------|------------|-------------------|
| Inventory (P0) | HIGH | Direct code + doc corroboration |
| Contracts / API_CATALOG.md | MEDIUM | ~half of `api.py`'s route bodies not independently re-read this engagement |
| Mechanical Insights (P0.5) | MEDIUM (degraded) | No static-analysis tool available — manual/grep discovery only |
| Bounded Contexts / Data Ownership (P1) | HIGH | 5 of 6 agents independently verified table writers against source |
| Business Flows / State Machine (P2) | HIGH | Full hop-by-hop trace with file:line citations for all 5 journeys |
| Runtime validation (P2b) | UNKNOWN | Skipped — no Datadog/KubeSense authorization |
| Core Domain Deep Dive (P3) | HIGH | Idempotency/routing/failure/concurrency/PII all independently confirmed |
| Fraud & Compliance (P3b) | HIGH | Adversarial review attempted a bypass for every control |
| Quality & Ops (P4) | HIGH | Direct test-file inventory, grep-based debt/smell scan |
| PRD / traceability (P5) | MEDIUM | Inherits the Contracts section's MEDIUM cap for doc-sourced-only requirement rows |

### Repo Map Table

| Repo | Classification | Tier | Squad | Branch | SHA | Inventory |
|------|----------------|------|-------|--------|-----|-----------|
| stock-research | application | tier_0 (spans all tiers via internal modules) | Sole operator | claude/codebase-comprehensive-review-0rjms1 | 1f9b6cd | complete |

### Implementation Status Matrix

See `PRD.md` § 3 Current capabilities for the full 24-capability matrix (Implementation +
Exercise + Evidence + Notes columns) — not duplicated here.

### Critical-Path Tiering (refined)

| Tier | Definition | Confirmed members |
|------|------------|--------------------|
| Tier 0 — side-effect executor | LLM call + Postgres writes | `backend/analyst/crew.py`, `backend/analytics/verdict_history.py`, `backend/core/state_store.py`, `backend/core/crypto.py` |
| Tier 1 — orchestration | SSE endpoints, batch pipeline entry points | `backend/api.py`, `backend/main.py`, all 6 `pipelines/*.py` |
| Tier 2 — recon/ops | Reconciliation, telemetry | `backend/analytics/*.py`, `backend/telemetry/*.py`, `pipelines/watchlist_alerts.py`, `pipelines/corporate_actions_pipeline.py` |
| Tier 3 — BFF/gate | Presentation, proxy | `frontend/app/api/**`, `frontend/app/**`, `frontend/components/**` |

### Risk Flags (Critical / High / Medium)

0 Critical, 0 High, ~8 Medium architectural smells — see `RISK_MAP.md` § Top smells for the full
ranked list with remediation hints.

### Mechanical + Runtime Summary

Mechanical pass: degraded to manual/grep discovery (no `understand-anything`/`madge`/
`dependency-cruiser`/`pydeps` available) — see `ALPHAPULSE_MAP.md` § Mechanical Insights.
Runtime pass: skipped entirely (P2b) — no Datadog/KubeSense authorization this session. Both
degradations are disclosed per this engagement's explicit adaptation instructions, not silent
gaps.
