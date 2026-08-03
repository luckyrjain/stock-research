# AlphaPulse — Gmail-Based Portfolio Intelligence (Proposal)

**Status:** Draft proposal — reconciled against AlphaPulse's existing architecture and binding
constraints. **Not yet on the committed roadmap.** [`docs/PRD.md` §15](PRD.md#15-roadmap)
deliberately leaves "Next/Later" as an open prioritization question rather than a fabricated
sequence — this document does not change that. If a phase below is greenlit, it belongs in that
roadmap explicitly at that time, not by implication from this doc existing.

**Origin:** started as a standalone, self-contained PRD for a privacy-first personal-portfolio
tool. Reconciled here against `../CLAUDE.md`'s binding "Architectural Constraints" and the
already-shipped Portfolio Aggregator, since building this as originally scoped would mean
overriding roughly half of that constraints list. Every place this document diverges from the
original pitch is called out explicitly below, not silently smoothed over.

---

## 1. Reconciliation decisions — binding for everything below

These are the load-bearing calls that make this buildable as an AlphaPulse feature instead of a
parallel product. Each one traded something away from the original pitch; each tradeoff is stated
plainly rather than assumed.

| # | Original ask | Conflict | Reconciled decision |
|---|---|---|---|
| 1 | Dedicated vector database | `../CLAUDE.md`: "PostgreSQL as the only datastore" — Cassandra/ClickHouse/Mongo/a data lake/a feature store are explicitly rejected | **pgvector** — a Postgres extension, not a second datastore. Embeddings live in a normal table with a `vector` column, in the same database as everything else. |
| 2 | "Portfolio Knowledge Graph" | Implies a graph database, same constraint | Model relationally: companies/holdings/transactions/documents as ordinary FK'd Postgres tables. Nothing in FR-10/FR-11 actually needs graph-traversal semantics — it needs joins and vector similarity search, both of which Postgres does natively. |
| 3 | "100% local inference, no cloud AI" (whole product) | AlphaPulse's existing analyst LLM layer (`analyst/crew.py::run_analysis_with_fallback`) is cloud-first via `litellm`, auto-detecting whichever of five cloud providers is configured, with cross-provider failover — Ollama is one optional provider among six, not the only one | **Scoped, not product-wide** (per your call): local-only applies *specifically* to Gmail content — classification, entity extraction, and anything else that reads raw email/attachment text (FR-2, FR-3, FR-5). Every other AI capability in this doc (Phase 3 research Q&A, Phase 4 portfolio chat) uses AlphaPulse's existing multi-provider `litellm` layer exactly as today, since by that point the LLM is reasoning over already-extracted, already-reviewed structured data and public research documents, not raw private email content. |
| 4 | Architecture diagram's "engines"/"processors" | Reads like separate long-running services if taken literally | Built the same way the Market Picks six-phase pipeline already is: sequential Python functions in one process under `backend/pipelines/`, `ThreadPoolExecutor` for parallelism, no message broker between stages. |
| 5 | New "Unified Order Book" / "Portfolio Engine" / "Corporate Action Processor" | AlphaPulse already has this — `profiles → accounts → assets → holdings → valuations → transactions` (Portfolio Aggregator, `backend/db/models.py`), fed today by CAS PDF import and broker CSV/XLSX import, with a working XIRR engine (`portfolio/portfolio_valuation.py`) and corporate-actions pipeline (`pipelines/corporate_actions_pipeline.py`) | Gmail becomes a **third writer into the existing schema**, not a parallel ledger. Reuses the exact parse → reconcile-against-existing-assets → dedupe → write shape `portfolio/cas_import.py` and `portfolio/csv_import.py` already established, and the same `tools/securities_master.py::resolve_symbol()` broker-code resolver `csv_import.py` already wires up. |
| 6 | Implicit "just works for any user" | Portfolio Aggregator is explicitly documented as **no-auth, localhost/Tailscale-only, not a multi-tenant product** — a deliberate scope call, not an oversight. Gmail OAuth inherently requires a real signed-in identity (Google ties the grant to an account), which that no-auth model can't express | A Gmail connection — and every row it writes — must be tied to AlphaPulse's **existing** magic-link account system (`users`/`sessions`, already shipped for Watchlist/Positions/API keys), not the bare-name `profiles` picker. This is a real, disruptive-enough decision that it needs explicit sign-off before Phase 1 starts, not a schema afterthought — see §8. |
| 7 | "Encrypted database... encrypted embeddings... encrypted document storage" | Stated as an NFR with no design behind it; AlphaPulse's Postgres data isn't encrypted at rest today (auth tokens are hashed, not the database itself) | Flagged as a **real, unaddressed gap**, not silently dropped or silently assumed solved. No phase below should store real Gmail content in a shared/production deployment until this has its own design pass. Fine for local-only/single-user use in the meantime, which is this feature's realistic Phase 1 audience anyway. |
| 8 | (implicit) reading a user's actual Gmail | Raises the compliance/privacy stakes well past anything AlphaPulse handles today — [`docs/PRD.md` §17](PRD.md#17-explicitly-out-of-scope-organizational-legal--business) already discloses **no legal review of the scraping surface** and an **unassessed SEBI registration question**, both about *public* data and read-only research | Called out here as its own risk, not inherited quietly. Reading private correspondence + PII + real financial holdings is a different order of exposure than scraping Screener.in. See §8. |

---

## 2. Vision

Extend AlphaPulse with a privacy-conscious personal-portfolio layer: automatically construct and
maintain a complete investment portfolio by reading brokerage emails, with the parts that touch
raw email content processed by a **local** LLM only. The rest of the product — synthesis over
already-extracted structured data, research Q&A — uses AlphaPulse's existing LLM infrastructure
unchanged.

The end state, across all four phases: a single source of truth inside the existing Portfolio
Aggregator for portfolio across brokers, order history, holdings, P&L, dividends, IPO allocations,
mutual funds, and a searchable research knowledge base, queryable in natural language.

## 3. Problem statement

Unchanged from the original pitch — this is a real, common pain point independent of any
architectural reconciliation:

Retail investors typically use multiple brokers over several years (Zerodha, Groww, Angel One,
Upstox, ICICI Direct, HDFC Sky, Kotak Securities, INDmoney, Smallcase, MF Central, ...), and the
result is fragmented data: holdings spread across brokers, no unified order book, contract notes
scattered in Gmail, manual average-cost calculation, missing dividend history, manual IPO
tracking, research scattered everywhere, forgotten investment theses, and manual reconciliation at
tax time.

AlphaPulse's existing Portfolio Aggregator already addresses part of this (net worth across
manually-entered or CAS/CSV-imported assets) but requires the user to go find and upload each
statement by hand. This proposal automates the sourcing.

## 4. Goals

### Primary
- Automatically build a unified portfolio from Gmail, writing into the existing Portfolio
  Aggregator schema.
- Extend the existing consolidated order book across every broker (not build a new one).
- Build a searchable investment research knowledge base (Phase 3).
- Enable AI-powered portfolio analysis (Phase 4), reusing AlphaPulse's existing LLM layer.
- Keep raw email/attachment content on-device — see Decision 3.

### Secondary
- Reduce manual portfolio tracking (upload → connect, for the Gmail path).
- Simplify tax preparation.
- Preserve investment history.
- Extend AlphaPulse's existing single-stock research depth (financials, DCF, peer comparison,
  street consensus, insider activity) into a genuine document-level research memory.

### Non-goals (explicit)
- **Not a new ledger, portfolio engine, or corporate-actions system.** All exist; see Decision 5.
- **Not a vector database or graph database as separate infrastructure.** See Decisions 1–2.
- **Not a product-wide "local LLM only" mandate.** See Decision 3.
- **Not a redesign of Portfolio Aggregator's core no-auth model** for its existing manual/CAS/CSV
  users — only Gmail-connected data requires a signed-in account.

## 5. Design principles

- **Private content stays local.** No raw Gmail/attachment content is ever sent to a cloud LLM
  provider. This is a hard constraint on the Phase 1 extraction path specifically (Decision 3),
  not a slogan for the whole feature.
- **AI-native, not regex-brittle**, for document understanding — matches AlphaPulse's existing
  instinct (e.g. `signals/filings_classifier.py`'s keyword-based approach is the *cheap* case;
  this is the LLM-native case for messier, less-structured broker email formats).
- **Explainable.** Every extracted value traces back to its source email/attachment/page/paragraph
  — the same instinct as AlphaPulse's existing `_meta`/`data_freshness` provenance fields, applied
  to a new data source.
- **Incremental.** Only process new emails (Gmail History API), matching the existing "only
  re-fetch what's stale" convention throughout AlphaPulse's cache layer.
- **Human-verifiable.** Users can review and correct extracted values before they're committed to
  `transactions` — this is the single most important trust mechanism in the whole feature, and
  should ship in Phase 1, not deferred. Never auto-commit a low-confidence extraction silently;
  this mirrors AlphaPulse's own "never invent" convention applied to LLM output instead of a
  scraped field.

## 6. Users

Same as AlphaPulse's existing personas (`docs/PRD.md` §5) — this feature is aimed squarely at the
existing **"Personal finance tracker"** and **"Active portfolio tracker"** personas already
documented there, not a new audience.

## 7. Success metrics

| Metric | Target | Status |
|---|---|---|
| Portfolio accuracy | >99% | Aspirational — needs a real eval set before it's a commitment |
| Transaction extraction precision | >99% | Same |
| Duplicate detection | >99.9% | Achievable — same dedup patterns `cas_import.py`/`csv_import.py` already prove out |
| Initial Gmail sync (10,000 emails) | <30 min | **Flagged, not committed** — a local small model (8B-class) doing classification + structured extraction per email, on realistic consumer hardware, may not hit this. Needs a real benchmark against actual hardware before this number ships anywhere user-facing. |
| Incremental sync | <2 min | Plausible given Gmail History API's delta-only design, but untested |
| Manual corrections needed | <1% | Depends entirely on the extraction accuracy above |
| AI query response | <3 sec | Achievable for Phase 4 (cloud-capable path); not applicable to the local extraction path, which has no interactive latency requirement |

---

## 8. Open decisions requiring human sign-off before Phase 1 starts

Not engineering calls — the same "flag it, don't silently assume it" instinct
[`docs/PRD.md` §17](PRD.md#17-explicitly-out-of-scope-organizational-legal--business) already
uses for AlphaPulse's other unresolved organizational/legal questions.

1. **Auth model for Gmail-connected data** (Decision 6). Tying Gmail-sourced rows to the existing
   `users`/`sessions` system is the plan, but the exact mechanism — does a `profiles` row gain an
   optional `user_id`? does Gmail-connected use bypass `profiles` entirely? — is a real schema
   decision, not filled in here. Needs its own design pass at Phase 1 kickoff.
2. **Encryption-at-rest** (Decision 7). No design exists yet. Acceptable to defer for a genuinely
   single-user/local deployment; not acceptable before this is used by more than the operator.
3. **Legal/privacy review.** Reading a user's actual Gmail — not public NSE/Screener.in data — is
   a materially bigger trust and compliance surface than anything AlphaPulse does today. This
   compounds the *already-disclosed, already-unassessed* gaps in `docs/PRD.md` §17.2/§17.4
   (no scraping-surface legal review, unassessed SEBI-registration status for recommendations).
   Needs qualified counsel before this reaches real users, the same "needs a licensed
   professional" framing `docs/PRD.md` already uses for its own open legal questions.
4. **Local-model hardware/throughput reality check.** The `<30 min`/`10,000 emails` target (§7)
   needs a real benchmark on realistic user hardware before it's treated as a spec rather than a
   hope.
5. **Bus factor.** `docs/PRD.md` §17.1 already discloses this project has one engineer. A Gmail
   OAuth integration handling real users' financial correspondence raises the cost of that being
   true higher than a research tool over public data does.

None of these block writing code for Phase 1 in a personal/local capacity. All of them block
treating this as a real multi-user product feature.

---

## 9. Phased scope

### Phase 1 — Gmail transaction import

Goal: a Gmail-connected account's buy/sell/IPO/dividend/SIP emails land in the *existing*
Portfolio Aggregator `transactions` table, reviewed by the user before being committed, exactly as
real, no invented data.

- **Gmail OAuth**, read-only scope, tied to a signed-in AlphaPulse account (see §8.1).
- **Incremental sync** via the Gmail History API — only new messages since the last synced
  cursor, mirroring the "only re-fetch what's stale" convention already used throughout the cache
  layer.
- **Email classification** (Buy/Sell/IPO Applied/IPO Allotted/Dividend/Bonus/Split/Rights/MF
  Purchase/SIP/Redemption/Contract Note/Statement/Other) — **local LLM only** (Decision 3), a
  small/fast model (Qwen3 8B, Gemma 3 12B, or Llama 3.1 8B — all already reachable via
  AlphaPulse's existing `litellm`+Ollama support), hardcoded with no cross-provider failover, so a
  local-model hiccup can never silently send email content to a cloud provider.
- **Broker auto-detection** from sender/template, extensible without code changes (config-driven,
  matching how `tools/market_picks_tools.py` merges in brokerage source modules today).
- **Structured extraction** (order/IPO/mutual-fund fields per the original FR-5 field list) — same
  local-only model, same no-failover rule. Every extracted field carries a confidence score and a
  source span (email ID + attachment + page/paragraph), the same provenance instinct as
  AlphaPulse's existing `_meta`/`data_freshness` fields, extended to a new source.
- **Review-before-commit UI.** Extracted transactions land in a staging state, not directly in
  `transactions` — the user confirms or corrects each one (at least until extraction accuracy is
  proven out) before it's written for real. This is the Phase 1 trust mechanism and should not be
  cut for speed.
- **Duplicate detection** via Gmail Message ID, attachment SHA-256, Order ID, Contract Note
  Number, and Exchange Order ID — same idempotency instinct as `csv_import.py`'s dedup-by-tuple
  and `cas_import.py`'s delete-and-replace-by-source pattern.
- **Writes into the existing schema** as a new `meta.source='gmail'` transaction, reusing
  `tools/securities_master.py::resolve_symbol()` for broker-code → NSE/BSE symbol resolution
  exactly as `csv_import.py` already does.

**Definition of done (Phase 1):**
- A connected Gmail account's supported email types are classified and extracted with a visible
  confidence score per field.
- Nothing reaches `transactions` without either passing a confidence threshold or explicit user
  confirmation.
- Duplicate imports (re-sync, overlapping date range) are provably idempotent.
- Zero raw email/attachment content is ever sent to a cloud LLM provider — verifiable by
  inspecting the call path, not just documented.
- Corporate actions (bonus/split/dividend) extracted from Gmail feed into the *existing*
  corporate-actions handling, not a new one.

### Phase 2 — Documents (contract notes, CAS, statements)

Goal: close the gap between "transaction-level emails" (Phase 1) and "full statements," reusing
rather than duplicating what already exists.

- **Contract note parsing** — new: most brokers email a contract note PDF per trade day, denser
  than a plain confirmation email. Same local-LLM extraction path as Phase 1.
- **CAS PDF and broker CSV/XLSX** — **already shipped** (`portfolio/cas_import.py`,
  `portfolio/csv_import.py`). This phase is about wiring Gmail as an *additional discovery
  mechanism* for these files (auto-detect a CAS/tradebook attachment in Gmail and route it through
  the existing importers) rather than reimplementing statement parsing.
- **Annual reports / research PDFs** ingested here as raw documents, indexing deferred to Phase 3.

**Definition of done (Phase 2):**
- A CAS PDF or broker tradebook that arrives by email is detected and offered to the existing
  import flow without the user manually downloading and re-uploading it.
- Contract notes contribute the same trade-level detail Phase 1's plain-text emails do, for
  brokers whose confirmation emails are too sparse to extract from directly.

### Phase 3 — Research knowledge base

Goal: index the documents Phase 2 collected (plus manually-added research/annual
reports/transcripts/filings) for semantic search — the part of the original pitch that's
genuinely new, not a re-skin of an existing AlphaPulse capability.

- **pgvector-backed embeddings** (Decision 1) — a `document_chunks` table with a `vector` column
  in the same Postgres instance as everything else.
- **Local embeddings model** (BGE-M3, Nomic Embed, or Jina Embeddings — all local, no cloud call,
  consistent with Decision 3's "raw content stays local" principle extended to documents) run as a
  batch pipeline under `backend/pipelines/`, the same shape as every other batch job in this repo.
- **Document ingestion**: annual reports, investor presentations, earnings-call transcripts,
  research reports, personal notes, company filings, news articles.

**Definition of done (Phase 3):**
- A semantic query over ingested documents returns ranked, cited passages (never a bare LLM
  paraphrase with no traceable source — matching AlphaPulse's "never invent" discipline).
- Ingesting a new document requires no code change for a new document *type*, only a new source
  adapter, matching the market-picks source-module pattern.

### Phase 4 — Portfolio intelligence / AI chat

Goal: natural-language queries over the now-real transaction/holdings data (Phase 1–2) and the
research knowledge base (Phase 3).

- Examples (unchanged from the original pitch): "How much have I invested in Tata Motors?", "Show
  all transactions for Reliance", "Which broker charged the most brokerage?", "What is my CAGR?"
  (already computable via the existing `xirr_report()`), "Show dividend income by year", "Which
  IPO generated maximum returns?", "Compare TCS with Infosys using my research."
- **Uses AlphaPulse's existing multi-provider `litellm` layer**, not the local-only path — by this
  point the LLM is reasoning over already-extracted structured numbers and cited document
  passages, not raw private email content, so Decision 3's local-only constraint does not apply
  here. Cross-provider failover is fine and desirable at this stage.
- **Every answer must cite sources** — the transaction(s) or document passage(s) it's grounded
  in — matching the "never invent a number that isn't in the data" discipline `config/analyst.json`
  already enforces for AlphaPulse's stock-analysis LLM prompt.

**Definition of done (Phase 4):**
- Every AI-generated answer includes a traceable citation back to a transaction row or an ingested
  document passage.
- A query with no grounded answer says so, rather than guessing — same convention as every other
  "absent, never invented" field elsewhere in AlphaPulse.

---

## 10. Data model (high level)

Deliberately not a full schema here — that's implementation-plan-level detail, to be written when
a phase is actually greenlit (matching this repo's own two-stage convention: PRD/design first,
implementation plan second — see `docs/superpowers/plans/` for examples of the latter). The shape:

- **Reused, unchanged:** `assets`, `holdings`, `valuations`, `transactions` (existing Portfolio
  Aggregator tables) — Gmail becomes a new `meta.source` value alongside `'cas'` and `'csv'`.
- **New, Phase 1:** a Gmail-connection table (OAuth tokens, sync cursor) scoped to a signed-in
  `user_id`, not a bare `profiles` row (Decision 6) — encrypted token storage is part of §8.2, not
  optional. A staging table for extracted-but-unconfirmed transactions (the review-before-commit
  mechanism), separate from `transactions` itself so an unreviewed extraction can never be mistaken
  for a real one.
- **New, Phase 3:** `research_documents` (source metadata, one row per ingested document) and
  `document_chunks` (text chunk + `vector` embedding column, FK'd to `research_documents`).

## 11. Architecture (reconciled)

```text
                       Gmail (read-only OAuth)
                              │
                    Incremental Sync (History API)
                              │
                     Email Normalization
                              │
              Attachment Extraction + OCR (local)
                              │
              Local LLM Classification + Extraction   ◄── never leaves the
                              │                             local model; no
                    Confidence + Source Span                cross-provider
                              │                             failover here
                  Review / Confirm (human-in-the-loop)
                              │
                   Duplicate Detection (existing-pattern)
                              │
        Existing Portfolio Aggregator schema (assets/holdings/
              valuations/transactions) — same tables Phase 2's
                   CAS/CSV importers already write into
                              │
                 ┌────────────┴────────────┐
                 │                          │
      pgvector-backed document      Existing multi-provider
      embeddings (Phase 3,               litellm layer
      local embeddings model)        (Phase 4 AI chat —
                 │                    cloud-capable, as today)
                 └────────────┬────────────┘
                          AI Assistant
                    (every answer cited)
```

Every box above is a Python module/pipeline stage in the existing FastAPI monolith — no new
service, no broker, no orchestrator, per `../CLAUDE.md`'s binding constraints.

## 12. Non-functional requirements

| Area | Requirement | Note |
|---|---|---|
| Privacy | Raw email/attachment content never leaves the local model (Decision 3) | Hard requirement, Phase 1 |
| Security | Gmail OAuth tokens encrypted at rest | Part of the open decision in §8.2 — not solved by this doc |
| Performance | See §7's success-metrics table | Throughput target explicitly flagged as unverified |
| Reliability | Crash recovery, resumable sync, idempotent imports | Matches AlphaPulse's existing pipeline conventions (self-healing `eod_prices_pipeline.py`, idempotent CAS/CSV import) |
| Auditability | Every extracted field stores source email/attachment/page/paragraph, prompt version, model version, confidence, timestamp | Extends AlphaPulse's existing `_meta`/provenance convention to a new data source |

---

## 13. Relationship to the rest of AlphaPulse

This is additive to, not a replacement for, the existing Portfolio Aggregator — CAS/CSV import
stay exactly as they are for users who don't connect Gmail. It's also unrelated to AlphaPulse's
core research flows (Stock Analysis, Market Picks, SME Signals, Screener), which keep using the
existing cloud-capable `litellm` layer unchanged; this proposal only touches the LLM call path for
raw Gmail content specifically.

**Not committed.** See the status line at the top of this document.
