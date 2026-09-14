# Event Catalog

AlphaPulse has no message broker (by architectural constraint — see root `CLAUDE.md`
"Architectural Constraints"). Its only asynchronous "events" are **Server-Sent Event (SSE) frames**
over the two long-lived HTTP streams, plus **scheduled cron triggers** (GitHub Actions) that behave
like fire-and-forget event sources for the six batch pipelines. Both are cataloged below since they
are the domain's real asynchronous-boundary mechanisms.

## SSE event streams

### `GET /api/analyse/{symbol}` — stock analysis stream

Producer: `backend/api.py` (verified `@app.get("/api/analyse/{symbol}")` at `api.py:575-576` by
the backend-core research agent). Consumer: `frontend/lib/useStockAnalysis.ts` via native
`EventSource` (verified `new EventSource(...)` at `useStockAnalysis.ts:133` by the frontend
research agent) — never consumed directly by the browser; the Next.js proxy
(`frontend/app/api/analyse/[symbol]/route.ts`) pipes the raw stream through unbuffered.

| Event / topic | Type | Producer | Consumer(s) | Schema location | Implementation | Exercise | Evidence |
|---|---|---|---|---|---|---|---|
| `start` | SSE frame | backend/api.py | frontend/lib/useStockAnalysis.ts (`reduceSSEMessage`) | `frontend/types/index.ts` `SSEMessage` union | Implemented | referenced | api.py:596-600 (backend-core agent); useStockAnalysis.ts:43-83 (frontend agent) |
| `task_done` (×6, one per data slice) | SSE frame | backend/api.py | frontend/lib/useStockAnalysis.ts | `frontend/types/index.ts` `SSEMessage` | Implemented | referenced | api.py:619-631 |
| `analysing` | SSE frame | backend/api.py | frontend/lib/useStockAnalysis.ts | `frontend/types/index.ts` `SSEMessage` | Implemented | referenced | docs/api-reference.md:298-305 (doc, MEDIUM alone) corroborated by api.py's `_run_and_signal` flow (business-flow agent, step 9) |
| `done` (terminal — carries `Report`) | SSE frame | backend/api.py | frontend/lib/useStockAnalysis.ts | `frontend/types/index.ts` `Report` | Implemented | referenced | api.py:783-786 (business-flow agent) |
| `error` (terminal, 3 sanitized message variants) | SSE frame | backend/api.py | frontend/lib/useStockAnalysis.ts | `frontend/types/index.ts` `SSEMessage` | Implemented | referenced | api.py:579-580 (invalid symbol), api.py:642-645 (stock_info validation), api.py:788-793 (catch-all); useStockAnalysis.ts:65-76 (foreground vs. background branch) |
| `: heartbeat` (SSE comment, no `data:`) | SSE keep-alive | backend/api.py | frontend EventSource transport layer (no app-level handler — comments are invisible to `onmessage`) | n/a | Implemented | referenced | api.py:762-767 (every 15s while analyst call in flight) |

**Confidence: HIGH** — both producer (backend-core agent) and consumer (frontend agent)
independently read the actual code for this stream; the two reports agree on every event name.

### `GET /api/market-picks` — Market Picks pipeline stream

Producer: `backend/api.py:802-803`. Consumer: `frontend/app/market-picks/page.tsx` — a
**second, independently-implemented** `EventSource`/switch (not shared with
`useStockAnalysis.ts` — flagged as an architecture-smell candidate in `RISK_MAP.md`).

| Event / topic | Type | Producer | Consumer(s) | Schema location | Implementation | Exercise | Evidence |
|---|---|---|---|---|---|---|---|
| `picks_start` | SSE frame | backend/pipelines/market_picks_pipeline.py (phase 1) | frontend/app/market-picks/page.tsx | `frontend/types/index.ts` `MarketPicksSSEMessage` | Implemented | referenced | docs/api-reference.md:343 (doc); business-flow agent confirmed phase 1 exists at market_picks_pipeline.py:961-991 |
| `source_done` (×20, one per source) | SSE frame | same | same | same | Implemented | referenced | market_picks_pipeline.py:971-975,983 (business-flow agent) |
| `extracting` | SSE frame | same (phase 2) | same | same | Implemented | referenced | market_picks_pipeline.py:1001+ (business-flow agent, phase boundary) |
| `extract_progress` (per batch) | SSE frame | same | same | same | Implemented | referenced | docs/api-reference.md:346 (doc, MEDIUM) |
| `consolidating` | SSE frame | same (phase 3) | same | same | Implemented | referenced | market_picks_pipeline.py:1228 (business-flow agent) |
| `validate_progress` (per candidate symbol) | SSE frame | same | same | same | Implemented | referenced | docs/api-reference.md:348 (doc, MEDIUM) |
| `researching` | SSE frame | same (phase 4) | same | same | Implemented | referenced | market_picks_pipeline.py:1427 (business-flow agent) |
| `stock_researched` (per stock) | SSE frame | same | same | same | Implemented | referenced | docs/api-reference.md:350 (doc, MEDIUM) |
| `analysis_error` (per failed batch — **the one place a truncated raw exception string reaches the client**, disclosed gap) | SSE frame | same (phase 5) | same | same | Implemented | referenced | docs/api-reference.md:352,363-365; docs/backlog.md item 9 (disclosed, unfixed as of this pass) |
| `scoring` | SSE frame | same (phase 6) | same | same | Implemented | referenced | market_picks_pipeline.py:1663 (business-flow agent) |
| `done` (terminal — `picks[]`, `from_cache`) | SSE frame | same | same | same | Implemented | referenced | api.py:899-912 (business-flow agent — cache gate: only cached if non-empty AND `pipeline.healthy`) |
| `error` (terminal, 4 message variants) | SSE frame | same | same | same | Implemented | referenced | api.py:913-915; market_picks_pipeline.py:934-942 (empty-consolidation abort) |
| `: heartbeat` | SSE keep-alive | backend/api.py | transport layer | n/a | Implemented | referenced | docs/api-reference.md:339 (every 20s of queue silence) |

**Confidence: HIGH** for events independently confirmed by the business-flow tracing agent
(`picks_start`, `source_done`, `extracting`, `consolidating`, `researching`, `scoring`, `done`,
`error`); **MEDIUM** for `extract_progress`, `validate_progress`, `stock_researched` (doc-sourced
only — the exact per-item emission line wasn't independently re-read this engagement, though the
containing phase was).

## Scheduled triggers (cron — the closest thing to a "publish" event for batch pipelines)

Producer: GitHub Actions (`.github/workflows/*.yml`, external to the repo's own runtime).
Consumer: the named pipeline's `main()`. Confirmed directly by the business-flow tracing agent
opening the workflow YAML files.

| Trigger | Schedule (UTC) | Consumer pipeline | Writes | Evidence | Confidence |
|---|---|---|---|---|---|
| `sme-cron.yml` | `0 13 * * 1-5` (18:30 IST) | `backend/pipelines/sme_ema_pipeline.py::main()` | `sme_stocks`, `ema_signals` | `.github/workflows/sme-cron.yml:8` (business-flow agent, opened directly) | HIGH |
| `watchlist-alerts-cron.yml` | `30 13 * * 1-5` (19:00 IST) | `backend/pipelines/watchlist_alerts.py::main()` | `verdict_history`; SMTP emails; `app_state.watchlist_alerts_sent` | `.github/workflows/watchlist-alerts-cron.yml:9` | HIGH |
| `screener-cron.yml` | `0 14 * * 1-5` (19:30 IST) | `backend/pipelines/screener_pipeline.py::main()` | `screener_stocks` | `.github/workflows/screener-cron.yml:11` | HIGH |
| `eod-prices-cron.yml` | `15 14 * * 1-5` (19:45 IST) | `backend/pipelines/eod_prices_pipeline.py::main()` (chains corporate-actions + portfolio-valuation as isolated internal steps) | `securities`, `prices_daily`, `mf_nav_daily`, `corporate_actions`, `valuations` | `.github/workflows/eod-prices-cron.yml:9` | HIGH |
| `market-picks-cron.yml` | Monday `30 1 * * 1` (per docs/architecture.md — not independently re-opened by the dispatched agents, corroborated by docs) | `GET /api/market-picks?force=true` on the already-deployed backend (HTTP trigger, not a local pipeline run — see rationale in ALPHAPULSE_MAP.md § Flow) | `output/_market_picks/picks.json`, `app_state.market_picks_history` | docs/architecture.md:457-460 | MEDIUM |
| `live-contract-check` (weekly) | Monday `0 6 * * 1` (per docs/architecture.md) | `tests_live/` opt-in contract checks | nothing — early-warning only | docs/architecture.md:450 | MEDIUM |

## Non-events (explicitly out of scope)

No Kafka/RabbitMQ/SQS/SNS topics, no webhook receivers (confirmed by the P3b adversarial review —
`grep -rni "webhook|callback"` across routes/portfolio/api.py found nothing broker-related; broker
sync is polling/pull-only, never an inbound push). Per the architectural-constraints table (root
`CLAUDE.md`), a message broker is explicitly on the "rejected" list — this is a settled decision,
not a gap.
