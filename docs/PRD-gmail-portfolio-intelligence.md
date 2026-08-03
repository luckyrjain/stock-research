# AlphaPulse — Automated Portfolio Intelligence (Broker APIs + Gmail)

**Status:** Draft proposal — reconciled against AlphaPulse's existing architecture and binding
constraints. **Not yet on the committed roadmap.** [`docs/PRD.md` §15](PRD.md#15-roadmap)
deliberately leaves "Next/Later" as an open prioritization question rather than a fabricated
sequence — this document does not change that. If a phase below is greenlit, it belongs in that
roadmap explicitly at that time, not by implication from this doc existing.

**Origin:** started as a standalone, self-contained PRD for a privacy-first, Gmail-parsing
personal-portfolio tool. Reconciled here against `../CLAUDE.md`'s binding "Architectural
Constraints" and the already-shipped Portfolio Aggregator, since building this as originally
scoped would mean overriding roughly half of that constraints list. A second reconciliation pass
then re-ordered the phasing itself: several of the user's actual brokers turned out to have free,
official, structured APIs, which are strictly more reliable than LLM-parsed email for anything
they cover. **Gmail-parsing is no longer Phase 1** — it's the fallback for brokers without a
usable API and for historical backfill, not the primary ingestion mechanism. Every place this
document diverges from the original pitch is called out explicitly, not silently smoothed over.

---

## 1. Reconciliation decisions — binding for everything below

These are the load-bearing calls that make this buildable as an AlphaPulse feature instead of a
parallel product. Each one traded something away from the original pitch; each tradeoff is stated
plainly rather than assumed.

| # | Original ask | Conflict | Reconciled decision |
|---|---|---|---|
| 1 | Dedicated vector database | `../CLAUDE.md`: "PostgreSQL as the only datastore" — Cassandra/ClickHouse/Mongo/a data lake/a feature store are explicitly rejected | **pgvector** — a Postgres extension, not a second datastore. Embeddings live in a normal table with a `vector` column, in the same database as everything else. |
| 2 | "Portfolio Knowledge Graph" | Implies a graph database, same constraint | Model relationally: companies/holdings/transactions/documents as ordinary FK'd Postgres tables. Nothing in FR-10/FR-11 actually needs graph-traversal semantics — it needs joins and vector similarity search, both of which Postgres does natively. |
| 3 | "100% local inference, no cloud AI" (whole product) | AlphaPulse's existing analyst LLM layer (`analyst/crew.py::run_analysis_with_fallback`) is cloud-first via `litellm`, auto-detecting whichever of five cloud providers is configured, with cross-provider failover — Ollama is one optional provider among six, not the only one | **Scoped, not product-wide.** Local-only applies *specifically* to raw email/attachment content (now Phase 2's job, not Phase 1's). Broker API responses are already structured JSON, so **Phase 1 needs no LLM at all** — this constraint doesn't even apply to the first, primary phase anymore. Everything else in this doc (Phase 4 research Q&A, Phase 5 portfolio chat) uses AlphaPulse's existing multi-provider `litellm` layer exactly as today. |
| 4 | Architecture diagram's "engines"/"processors" | Reads like separate long-running services if taken literally | Built the same way the Market Picks six-phase pipeline already is: sequential Python functions in one process under `backend/pipelines/`, `ThreadPoolExecutor` for parallelism, no message broker between stages. |
| 5 | New "Unified Order Book" / "Portfolio Engine" / "Corporate Action Processor" | AlphaPulse already has this — `profiles → accounts → assets → holdings → valuations → transactions` (Portfolio Aggregator, `backend/db/models.py`), fed today by CAS PDF import and broker CSV/XLSX import, with a working XIRR engine (`portfolio/portfolio_valuation.py`) and corporate-actions pipeline (`pipelines/corporate_actions_pipeline.py`) | Broker APIs and, later, Gmail become **additional writers into the existing schema**, not a parallel ledger. Reuses the exact parse/fetch → reconcile-against-existing-assets → dedupe → write shape `portfolio/cas_import.py` and `portfolio/csv_import.py` already established, and the same `tools/securities_master.py::resolve_symbol()` broker-code resolver `csv_import.py` already wires up. |
| 6 | Implicit "just works for any user" | Portfolio Aggregator is explicitly documented as **no-auth, localhost/Tailscale-only, not a multi-tenant product** — a deliberate scope call, not an oversight. Both broker API keys/OAuth and Gmail OAuth inherently require a real signed-in identity (the grant is tied to an account), which that no-auth model can't express | Every connected broker or Gmail account — and every row it writes — must be tied to AlphaPulse's **existing** magic-link account system (`users`/`sessions`, already shipped for Watchlist/Positions/API keys), not the bare-name `profiles` picker. Real, disruptive-enough decision that it needs explicit sign-off before Phase 1 starts, not a schema afterthought — see §8. |
| 7 | "Encrypted database... encrypted embeddings... encrypted document storage" | Stated as an NFR with no design behind it; AlphaPulse's Postgres data isn't encrypted at rest today (auth tokens are hashed, not the database itself) | Flagged as a **real, unaddressed gap** — and it now matters from Phase 1 onward, since broker API keys/access tokens are as sensitive as Gmail OAuth tokens. No phase below should store real broker credentials in a shared/production deployment until this has its own design pass. Fine for local-only/single-user use in the meantime. |
| 8 | (implicit) reading a user's actual Gmail / connecting real brokerage accounts | Raises the compliance/privacy stakes well past anything AlphaPulse handles today — [`docs/PRD.md` §17`](PRD.md#17-explicitly-out-of-scope-organizational-legal--business) already discloses **no legal review of the scraping surface** and an **unassessed SEBI registration question**, both about *public* data and read-only research | Called out here as its own risk, not inherited quietly. A broker API key can place trades on a real account (even if this feature only ever reads); Gmail access reads private correspondence. Both are a different order of exposure than scraping Screener.in. See §8. |
| 9 | *(new — discovered during broker research, not in the original pitch)* Gmail-parsing as the primary ingestion mechanism | Zerodha, HDFC Securities, and Paytm Money all have **free, official, structured APIs** covering holdings/positions/funds/orders — strictly more reliable than LLM-extracted email for anything they cover, with zero extraction-accuracy risk | **Broker APIs are Phase 1; Gmail-parsing is demoted to Phase 2**, scoped down to backfill (pre-API-connection history) and brokers/email-types APIs don't cover. See §9. |

---

## 2. Vision

Extend AlphaPulse with an automated personal-portfolio layer: connect real brokerage accounts via
their own official APIs where one exists (no parsing, no LLM, no extraction error), and fall back
to privacy-conscious Gmail parsing — processed by a **local** LLM only — for brokers or history an
API can't reach. The rest of the product — synthesis over already-extracted structured data,
research Q&A — uses AlphaPulse's existing LLM infrastructure unchanged.

The end state: a single source of truth inside the existing Portfolio Aggregator for portfolio
across brokers, order history, holdings, P&L, dividends, IPO allocations, mutual funds, and a
searchable research knowledge base, queryable in natural language.

## 3. Problem statement

Unchanged from the original pitch — this is a real, common pain point independent of any
architectural reconciliation:

Retail investors typically use multiple brokers over several years, and the result is fragmented
data: holdings spread across brokers, no unified order book, contract notes scattered in Gmail,
manual average-cost calculation, missing dividend history, manual IPO tracking, research scattered
everywhere, forgotten investment theses, and manual reconciliation at tax time.

AlphaPulse's existing Portfolio Aggregator already addresses part of this (net worth across
manually-entered or CAS/CSV-imported assets) but requires the user to go find and upload each
statement by hand. This proposal automates the sourcing — first via direct broker connection where
possible, then via Gmail where it isn't.

## 4. Goals

### Primary
- Automatically sync holdings/positions/funds/orders from **connected broker accounts** via their
  own free APIs, writing into the existing Portfolio Aggregator schema.
- Fall back to Gmail parsing for brokers without a usable API, and for historical transactions
  predating the broker connection.
- Extend the existing consolidated order book across every broker (not build a new one).
- Build a searchable investment research knowledge base (Phase 4).
- Enable AI-powered portfolio analysis (Phase 5), reusing AlphaPulse's existing LLM layer.
- Keep raw email/attachment content on-device wherever Gmail parsing is used — see Decision 3.

### Secondary
- Reduce manual portfolio tracking to a one-time "connect your broker" action per account.
- Simplify tax preparation.
- Preserve investment history.
- Extend AlphaPulse's existing single-stock research depth (financials, DCF, peer comparison,
  street consensus, insider activity) into a genuine document-level research memory.

### Non-goals (explicit)
- **Not a new ledger, portfolio engine, or corporate-actions system.** All exist; see Decision 5.
- **Not a vector database or graph database as separate infrastructure.** See Decisions 1–2.
- **Not a product-wide "local LLM only" mandate**, and Phase 1 specifically needs no LLM at all.
  See Decision 3.
- **Not a redesign of Portfolio Aggregator's core no-auth model** for its existing manual/CAS/CSV
  users — only broker- or Gmail-connected data requires a signed-in account.
- **Not trade execution.** Every broker connection in this doc is read-only in intent — see §8 for
  why that intent still needs to be a verified, enforced scope, not just a description.

## 5. Design principles

- **Prefer the source of truth over inference.** A broker's own API is authoritative, structured,
  and free of extraction error — use it wherever it exists before reaching for anything
  LLM-based. This is the principle that reordered this whole document.
- **Private content stays local — where content is actually private.** No raw Gmail/attachment
  content is ever sent to a cloud LLM provider (Decision 3). This doesn't apply to broker API
  responses, which are already structured data with no free-text extraction step.
- **Explainable.** Every synced or extracted value traces back to its source (a specific API call
  + timestamp, or an email/attachment/page/paragraph) — the same instinct as AlphaPulse's existing
  `_meta`/`data_freshness` provenance fields, applied to new data sources.
- **Incremental.** Only fetch what's new — broker APIs polled for today's orders/trades since the
  last sync, Gmail via the History API's delta cursor — matching the existing "only re-fetch
  what's stale" convention throughout AlphaPulse's cache layer.
- **Human-verifiable where extraction is involved.** Broker API data, being structured and
  authoritative, can commit directly. LLM-extracted Gmail data cannot — users review and correct
  extracted values before they're committed to `transactions`. Never auto-commit a low-confidence
  extraction silently; mirrors AlphaPulse's own "never invent" convention applied to LLM output
  instead of a scraped field.

## 6. Users

Same as AlphaPulse's existing personas (`docs/PRD.md` §5) — this feature is aimed squarely at the
existing **"Personal finance tracker"** and **"Active portfolio tracker"** personas already
documented there, not a new audience.

**Unverified, worth flagging (DRAFT):** no user research has confirmed these personas want to
grant broker API access or Gmail access to a small, non-SEBI-registered app for their most
sensitive financial data. Broker API keys are arguably an easier trust ask than full Gmail
access (narrower blast radius, broker-issued and broker-revocable, no reading of unrelated
correspondence) — but that's a hypothesis, not something validated here.

## 7. Success metrics

| Metric | Target | Status |
|---|---|---|
| Broker API sync accuracy (holdings/positions/funds) | 100% | Should genuinely be achievable — it's a direct read of authoritative data, not an extraction. If this isn't 100%, something is broken, not just imprecise. |
| Broker API sync latency (holdings/positions refresh) | <10 sec | Plausible — these are simple REST calls, not bulk historical fetches |
| Gmail transaction extraction precision (Phase 2, backfill only) | >99% | Aspirational — needs a real eval set before it's a commitment |
| Duplicate detection across sources (broker API + Gmail + CAS + CSV) | >99.9% | Achievable — same dedup patterns `cas_import.py`/`csv_import.py` already prove out; broker-API-sourced rows need their own dedup key too (broker order ID) |
| Gmail initial sync (10,000 emails, Phase 2) | <30 min | **Flagged, not committed** — a local small model (8B-class) doing classification + structured extraction per email, on realistic consumer hardware, may not hit this. Also bounded by Gmail API's own quota limits, not just local-inference throughput. Needs a real benchmark before this number ships anywhere user-facing. |
| Manual corrections needed (Gmail path only) | <1% | Depends entirely on the extraction accuracy above; not applicable to the broker-API path, which shouldn't need correction at all |
| AI query response (Phase 5) | <3 sec | Achievable via the existing cloud-capable `litellm` path |

---

## 8. Open decisions requiring human sign-off before Phase 1 starts

Not engineering calls — the same "flag it, don't silently assume it" instinct
[`docs/PRD.md` §17`](PRD.md#17-explicitly-out-of-scope-organizational-legal--business) already
uses for AlphaPulse's other unresolved organizational/legal questions.

1. **Auth model for broker- and Gmail-connected data** (Decision 6). Tying these to the existing
   `users`/`sessions` system is the plan, but the exact mechanism — does a `profiles` row gain an
   optional `user_id`? does connected use bypass `profiles` entirely? — is a real schema decision,
   not filled in here. Needs its own design pass at Phase 1 kickoff.
2. **Encryption-at-rest for broker credentials and Gmail tokens** (Decision 7). No design exists
   yet. Acceptable to defer for a genuinely single-user/local deployment; not acceptable before
   this is used by more than the operator — and this now applies starting Phase 1, not Phase 2.
3. **Legal/privacy review.** Connecting real brokerage accounts and reading a user's actual
   Gmail — not public NSE/Screener.in data — is a materially bigger trust and compliance surface
   than anything AlphaPulse does today. This compounds the *already-disclosed, already-unassessed*
   gaps in `docs/PRD.md` §17.2/§17.4 (no scraping-surface legal review, unassessed
   SEBI-registration status for recommendations). Needs qualified counsel before this reaches real
   users.
4. **Read-only scope enforcement, not just intent.** §4's non-goals say "not trade execution," but
   several of these broker APIs (Kite Connect, InvestRight, Paytm Money Open API) are full trading
   APIs — the same credential that reads holdings can usually place orders. The requested OAuth/API
   scope needs to be the narrowest read-only grant each broker actually offers, verified per
   broker, not assumed from "we only call the read endpoints."
5. **Aditya Birla Money's API access/cost is unconfirmed.** Their Open Store API exists but no
   public pricing was found for it specifically (their account plans have separate subscription
   fees that may or may not gate API access). Needs a direct check with them before it's scheduled
   into any phase.
6. **Groww costs ₹499/month for API access** (down from ₹2,000/month, but not free like the other
   three). Whether that's worth paying is a product/budget call, not an engineering one — Groww is
   deliberately left out of Phase 1 below pending that decision.
7. **Local-model hardware/throughput reality check** (Phase 2 only, now that Phase 1 needs no LLM).
   The `<30 min`/`10,000 emails` target (§7) needs a real benchmark on realistic user hardware
   before it's treated as a spec rather than a hope.
8. **Bus factor.** `docs/PRD.md` §17.1 already discloses this project has one engineer. Connecting
   real brokerage accounts and reading real users' financial correspondence raises the cost of
   that being true higher than a research tool over public data does.

None of these block writing code for Phase 1 in a personal/local capacity, using your own accounts
and your own API credentials. All of them block treating this as a real multi-user product feature.

---

## 9. Phased scope

### Phase 1 — Broker API integration (free-tier brokers)

Goal: connected broker accounts' holdings, positions, funds, and orders sync into the *existing*
Portfolio Aggregator schema automatically — no LLM, no extraction, no review queue, because the
data is already structured and authoritative.

**In scope — confirmed free APIs:**
- **Zerodha** — Kite Connect, free "Personal API" tier (holdings, positions, funds, orders; the
  paid ₹500/mo tier is only needed for live/historical *market* data, not portfolio data)
- **HDFC Securities** — InvestRight Open API, reported free for InvestRight users (profile,
  holdings, positions, funds, tradebook, order details)
- **Paytm Money** — Open API, explicitly free (order, trade, position, portfolio, funds, profile —
  requires an active KYC'd trading account)

**Explicitly out of scope for this phase** (see §8.5–8.6): Groww (₹499/month — a budget decision,
not scheduled here until made) and Aditya Birla Money (API cost/access unconfirmed).

- **Per-broker OAuth/API-key connection flow**, tied to a signed-in AlphaPulse account (§8.1), read
  -only scope verified per broker (§8.4).
- **Encrypted credential storage** — access tokens/API keys/secrets, not just hashed like session
  tokens (§8.2 — a real gap, not solved by this doc, but now a Phase 1 blocker rather than a
  Phase 2 one).
- **Holdings/positions/funds sync** — a straightforward read, refreshed on demand or on a schedule
  (matching the existing `ThreadPoolExecutor`-based pipeline pattern), writing into `holdings`/
  `valuations` exactly as `portfolio_valuation.py::refresh_valuations()` already does for CAS/CSV
  -sourced assets.
- **Forward trade capture** — since these APIs generally expose only the *current* trading day's
  orders/trades (not historical), a periodic sync job records each day's trades before they roll
  off, the same "capture as it happens, don't rely on the API for history" pattern any of these
  brokers' own API docs already recommend. Writes into `transactions` as
  `meta.source='<broker>_api'` (e.g. `'zerodha_api'`), reusing
  `tools/securities_master.py::resolve_symbol()` where a broker's own symbol differs from the
  canonical NSE/BSE one, exactly as `csv_import.py` already does.
- **Does not solve historical backfill.** A newly-connected account's *past* trades (before the
  connection existed) are not retrievable from these APIs — that remains the job of the existing
  CSV tradebook importer (already shipped) or Phase 2's Gmail parsing.

**Definition of done (Phase 1):**
- A connected Zerodha, HDFC Securities, or Paytm Money account's current holdings/positions/funds
  are visible in the existing Portfolio Aggregator UI within a normal page load.
- New trades placed after connecting are captured into `transactions` within one sync cycle, with
  no manual entry and no review step (this is structured data — if it needs a review step,
  something upstream is wrong).
- Duplicate syncs (re-running the same day's fetch) are provably idempotent.
- No credential is ever logged or displayed in plaintext after initial connection.
- The connected credential can place trades in principle (per broker) but the app itself never
  calls a write/order-placement endpoint — verified by code review of the actual API calls made,
  not just documented intent (§8.4).

### Phase 2 — Gmail transaction import (fallback + historical backfill)

Goal: for brokers Phase 1 doesn't cover (today: Aditya Birla Money, Groww if not budgeted for) and
for transactions predating a Phase 1 connection, reconstruct history from Gmail — reviewed by the
user before being committed, exactly as real, no invented data.

- **Gmail OAuth**, read-only scope, tied to a signed-in AlphaPulse account (see §8.1).
- **Incremental sync** via the Gmail History API — only new messages since the last synced
  cursor.
- **Email classification** (Buy/Sell/IPO Applied/IPO Allotted/Dividend/Bonus/Split/Rights/MF
  Purchase/SIP/Redemption/Contract Note/Statement/Other) — **local LLM only** (Decision 3), a
  small/fast model (Qwen3 8B, Gemma 3 12B, or Llama 3.1 8B — all already reachable via
  AlphaPulse's existing `litellm`+Ollama support), hardcoded with no cross-provider failover, so a
  local-model hiccup can never silently send email content to a cloud provider.
- **Broker auto-detection** from sender/template, extensible without code changes (config-driven,
  matching how `tools/market_picks_tools.py` merges in brokerage source modules today).
- **Structured extraction** (order/IPO/mutual-fund fields per the original FR-5 field list) — same
  local-only model, same no-failover rule. Every extracted field carries a confidence score and a
  source span (email ID + attachment + page/paragraph).
- **Review-before-commit UI.** Extracted transactions land in a staging state, not directly in
  `transactions` — the user confirms or corrects each one before it's written for real. This is
  the Phase 2 trust mechanism and should not be cut for speed.
- **Duplicate detection** via Gmail Message ID, attachment SHA-256, Order ID, Contract Note
  Number, and Exchange Order ID, **plus cross-source dedup against Phase 1's broker-API rows** —
  a trade Phase 1 already captured live must not also land here as a duplicate from its
  confirmation email.
- **Writes into the existing schema** as `meta.source='gmail'`.

**Definition of done (Phase 2):**
- A connected Gmail account's supported email types are classified and extracted with a visible
  confidence score per field.
- Nothing reaches `transactions` without either passing a confidence threshold or explicit user
  confirmation.
- A trade already captured via Phase 1's broker API sync is never double-counted from its
  confirmation email.
- Zero raw email/attachment content is ever sent to a cloud LLM provider — verifiable by
  inspecting the call path, not just documented.
- Corporate actions (bonus/split/dividend) extracted from Gmail feed into the *existing*
  corporate-actions handling, not a new one.

### Phase 3 — Documents (contract notes, CAS, statements)

Goal: close the gap between "transaction-level data" (Phases 1–2) and "full statements," reusing
rather than duplicating what already exists.

- **Contract note parsing** — most brokers email a contract note PDF per trade day, denser than a
  plain confirmation email. Same local-LLM extraction path as Phase 2.
- **CAS PDF and broker CSV/XLSX** — **already shipped** (`portfolio/cas_import.py`,
  `portfolio/csv_import.py`). This phase is about wiring Gmail as an *additional discovery
  mechanism* for these files (auto-detect a CAS/tradebook attachment in Gmail and route it through
  the existing importers) rather than reimplementing statement parsing.
- **Annual reports / research PDFs** ingested here as raw documents, indexing deferred to Phase 4.

**Definition of done (Phase 3):**
- A CAS PDF or broker tradebook that arrives by email is detected and offered to the existing
  import flow without the user manually downloading and re-uploading it.
- Contract notes contribute the same trade-level detail Phase 2's plain-text emails do, for
  brokers whose confirmation emails are too sparse to extract from directly.

### Phase 4 — Research knowledge base

Goal: index the documents Phase 3 collected (plus manually-added research/annual
reports/transcripts/filings) for semantic search — the part of the original pitch that's
genuinely new, not a re-skin of an existing AlphaPulse capability.

- **pgvector-backed embeddings** (Decision 1) — a `document_chunks` table with a `vector` column
  in the same Postgres instance as everything else.
- **Local embeddings model** (BGE-M3, Nomic Embed, or Jina Embeddings — all local, no cloud call)
  run as a batch pipeline under `backend/pipelines/`, the same shape as every other batch job in
  this repo.
- **Document ingestion**: annual reports, investor presentations, earnings-call transcripts,
  research reports, personal notes, company filings, news articles.

**Definition of done (Phase 4):**
- A semantic query over ingested documents returns ranked, cited passages (never a bare LLM
  paraphrase with no traceable source — matching AlphaPulse's "never invent" discipline).
- Ingesting a new document requires no code change for a new document *type*, only a new source
  adapter, matching the market-picks source-module pattern.

### Phase 5 — Portfolio intelligence / AI chat

Goal: natural-language queries over the now-real transaction/holdings data (Phases 1–3) and the
research knowledge base (Phase 4).

- Examples: "How much have I invested in Tata Motors?", "Show all transactions for Reliance",
  "Which broker charged the most brokerage?", "What is my CAGR?" (already computable via the
  existing `xirr_report()`), "Show dividend income by year", "Which IPO generated maximum
  returns?", "Compare TCS with Infosys using my research."
- **Uses AlphaPulse's existing multi-provider `litellm` layer**, not the local-only path — by this
  point the LLM is reasoning over already-synced/extracted structured numbers and cited document
  passages, not raw private email content, so Decision 3's local-only constraint does not apply
  here. Cross-provider failover is fine and desirable at this stage.
- **Every answer must cite sources** — the transaction(s) or document passage(s) it's grounded
  in — matching the "never invent a number that isn't in the data" discipline `config/analyst.json`
  already enforces for AlphaPulse's stock-analysis LLM prompt.

**Definition of done (Phase 5):**
- Every AI-generated answer includes a traceable citation back to a transaction row or an ingested
  document passage.
- A query with no grounded answer says so, rather than guessing.

---

## 10. Data model (high level)

Deliberately not a full schema here — that's implementation-plan-level detail, to be written when
a phase is actually greenlit (matching this repo's own two-stage convention: PRD/design first,
implementation plan second — see `docs/superpowers/plans/` for examples of the latter). The shape:

- **Reused, unchanged:** `assets`, `holdings`, `valuations`, `transactions` (existing Portfolio
  Aggregator tables) — broker-API sync and Gmail each become a new `meta.source` value (e.g.
  `'zerodha_api'`, `'hdfc_api'`, `'paytm_api'`, `'gmail'`) alongside the existing `'cas'`/`'csv'`.
- **New, Phase 1:** a single **data-connection table** (source type — broker name or `'gmail'`;
  encrypted credential/token; sync cursor/last-synced-at) scoped to a signed-in `user_id`, not a
  bare `profiles` row (Decision 6). One shape, reused for every broker plus Gmail, rather than a
  bespoke table per source. Encrypted credential storage is part of §8.2, not optional.
- **New, Phase 2:** a staging table for extracted-but-unconfirmed Gmail transactions (the
  review-before-commit mechanism), separate from `transactions` itself so an unreviewed extraction
  can never be mistaken for a real one.
- **New, Phase 4:** `research_documents` (source metadata, one row per ingested document) and
  `document_chunks` (text chunk + `vector` embedding column, FK'd to `research_documents`).

## 11. Architecture (reconciled)

```text
        Broker APIs (Zerodha, HDFC Sec,        Gmail (read-only OAuth)
         Paytm Money — free tier)                       │
                    │                          Incremental Sync (History API)
         Holdings/Positions/Funds/                       │
           Orders (structured,               Email Normalization + Attachment
            no LLM needed)                      Extraction + OCR (local)
                    │                                     │
                    │                     Local LLM Classification + Extraction  ◄── never
                    │                                     │                          leaves
                    │                       Confidence + Source Span                 the local
                    │                                     │                          model; no
                    │                     Review / Confirm (human-in-the-loop)       cloud
                    │                                     │                          failover
                    └───────────────┬─────────────────────┘
                          Duplicate Detection (cross-source: broker-API rows
                                  win over a matching Gmail extraction)
                                              │
                Existing Portfolio Aggregator schema (assets/holdings/
                    valuations/transactions) — same tables the CAS/CSV
                              importers already write into
                                              │
                 ┌────────────────────────────┴────────────────────────────┐
                 │                                                          │
      pgvector-backed document                                  Existing multi-provider
      embeddings (Phase 4,                                          litellm layer
      local embeddings model)                                  (Phase 5 AI chat —
                 │                                               cloud-capable, as today)
                 └────────────────────────────┬────────────────────────────┘
                                          AI Assistant
                                    (every answer cited)
```

Every box above is a Python module/pipeline stage in the existing FastAPI monolith — no new
service, no broker (in the message-queue sense), no orchestrator, per `../CLAUDE.md`'s binding
constraints.

## 12. Non-functional requirements

| Area | Requirement | Note |
|---|---|---|
| Privacy | Raw email/attachment content never leaves the local model (Decision 3) | Applies to Phase 2 (Gmail) only — Phase 1's broker API data is already structured, nothing to keep local in the same sense |
| Security | Broker credentials and Gmail OAuth tokens encrypted at rest | Part of the open decision in §8.2 — not solved by this doc, and now a Phase 1 requirement |
| Security | Every broker connection scoped to the narrowest read-only grant available | §8.4 — verified per broker, not assumed |
| Performance | See §7's success-metrics table | Gmail throughput target explicitly flagged as unverified; broker API targets should be straightforwardly achievable |
| Reliability | Crash recovery, resumable sync, idempotent imports | Matches AlphaPulse's existing pipeline conventions (self-healing `eod_prices_pipeline.py`, idempotent CAS/CSV import) |
| Auditability | Every synced/extracted field stores its source (API call + timestamp, or email/attachment/page/paragraph), and for Gmail: prompt version, model version, confidence | Extends AlphaPulse's existing `_meta`/provenance convention to new data sources |

---

## 13. Relationship to the rest of AlphaPulse

This is additive to, not a replacement for, the existing Portfolio Aggregator — CAS/CSV import
stay exactly as they are for users who don't connect a broker or Gmail. It's also unrelated to
AlphaPulse's core research flows (Stock Analysis, Market Picks, SME Signals, Screener), which keep
using the existing cloud-capable `litellm` layer unchanged; this proposal only touches the
local-only LLM constraint for Phase 2's raw Gmail content specifically.

**Not committed.** See the status line at the top of this document.
