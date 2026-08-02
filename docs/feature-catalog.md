# AlphaPulse — Feature Catalog

**Status:** Living document — the detailed "what is already built" companion to
[`PRD.md`](PRD.md). That document covers *why* AlphaPulse exists, what it's optimizing for, and
what's prioritized next; this one is the implementation inventory — every shipped feature area,
one level above `backend/CLAUDE.md`'s exhaustive engineering detail. Where this drifts from
`backend/CLAUDE.md`/`frontend/CLAUDE.md`, those are ground truth and this document should be
corrected to match them.

---

## Current Implementation — by Feature Area

Each area below is **shipped and live** unless marked otherwise. "Depth" links to the `CLAUDE.md`
section with full behavioral/engineering detail — this catalog intentionally does not re-derive
that detail, to avoid a second copy that drifts.

### Stock Analysis (the core product)
Given an NSE/BSE ticker (or ISIN, or company name), AlphaPulse:
1. Validates the symbol across NSE autocomplete, BSE, and Screener.in.
2. Fetches six data slices in parallel — price/quote, fundamentals, news, shareholding, mutual
   fund holdings, corporate filings.
3. Runs a **quantitative signal engine** — six independent signals (valuation, growth, volume,
   filings, technical [RSI14 + EMA20/50], macro [FII/DII flow + RBI rate/inflation]), combined
   with **sector-aware weight tilts** (rate-sensitive / growth / cyclical sector groups get
   different signal weightings) into one bounded `final_score` and a five-tier quant verdict.
4. Calls an LLM analyst with all of the above, under guardrails that reject a response whose
   recommendation directionally contradicts the quant signal (both a SELL against strongly
   positive signals *and* a BUY against strongly negative signals are rejected — this is a
   bidirectional cross-check, not a one-sided sanity check) and whose stated confidence is
   implausible given a near-neutral score. A **numeric-misread guardrail** additionally compares
   every number the analyst cites in prose (dividend yield, P/E, ROE, ROCE, book value, growth,
   EBITDA margin, market cap, and sector-average comparisons) against the real source data,
   catching transcription errors like a 0.46 dividend yield written as "47%" — a 2x-tolerance
   mismatch triggers the same corrective retry. A guardrail failure triggers one corrective retry,
   then a labeled, visibly-degraded HOLD fallback — never a confident-looking guess.
5. Streams progress and the final report to the browser via Server-Sent Events, then persists a
   dated snapshot (`verdict_history`) so future visits can show "how has this call tracked since."

Quote reliability: when yfinance has no live quote on either NSE or BSE (common for thinly-traded
stocks), the price/quote fetch falls back to Screener.in's own top-ratios widget (supplemented
with a stockanalysis.com scrape for EPS/52-week-range/volume) rather than failing the whole
analysis.

Layered on top of the core report: peer comparison + absolute valuation-anchor (own P/E history),
multi-year financial statements + a deterministic DCF estimate, concalls (management commentary
links), insider/institutional activity (PIT filings + bulk/block deals), street consensus
(Trendlyne-sourced numeric analyst consensus + cited coverage), a verdict timeline with
win/loss scoring against live price, MF-holdings quarter-over-quarter stake deltas, detailed
shareholding (every individually-named promoter/institutional holder from NSE's own shareholding
filing, not just aggregate category percentages), filings classification (corporate actions /
rating actions / next-results date), stock-vs-Nifty relative performance, and hoverable price/
quarterly/EMA sparklines.

*Depth: `CLAUDE.md` §"Stock analysis flow" onward through §"Verdict history flow" (the bulk of
the document).*

### Market Picks
A weekly multi-agent discovery pipeline: scrapes 28 Indian/global financial sources (RSS +
GNews + structured feeds), extracts stock mentions with an LLM, validates against the NSE equity
master, runs the same due-diligence/signal-engine pass Stock Analysis uses on each candidate (up
to 35 stocks per run), and produces a confidence-ranked, sector-balanced watchlist with a 4-tier
BUY/WATCHLIST/HOLD/SELL rating and deterministic entry/target/stop-loss levels (never
LLM-generated prices). Auto-refreshes weekly via GitHub Actions; a track-record page shows
historical accuracy against actual subsequent price moves, alpha vs. Nifty, and per-tier win
rates. Per-run **source-quality telemetry** (yield, syndication-dedup rate, extraction success
per source) complements the existing day-level source-health/error-counter monitoring. If the
user has tracked "I bought this" positions, a **sector-concentration badge** flags a pick whose
sector is already ≥25% of their tracked position value.

*Depth: `CLAUDE.md` §"Market picks flow", §"LLM cost instrumentation..." point on peer/valuation
wiring, §"Peer/valuation-anchor wired into scoring".*

### SME Signals
A PostgreSQL-backed batch screener over the full NSE Emerge + BSE SME universe: EMA20/EMA50
golden-cross/death-cross detection, RSI(14), volume-spike confirmation, liquidity/market-cap
context, per-cross forward-return outcomes, and an aggregate 90-day golden-cross hit rate.
Auto-refreshes on a weekday cron; on-demand refresh available from the UI.

*Depth: `CLAUDE.md` §"SME golden cross flow".*

### Screener (NIFTY 500 custom screener)
The same filter-chip screening pattern as SME Signals, generalized to the primary NSE/BSE
large/mid-cap universe: filterable/sortable by industry, P/E, market cap, RSI, and EMA trend,
served from a daily-refreshed stored-metrics table (no live scrape per request).

*Depth: `CLAUDE.md` §"Custom screener flow".*

### Watchlist
A cross-mode "stocks I care about" list — one star button, usable from every dashboard —
backed by PostgreSQL, with live price fan-out and a corporate-action calendar roll-up. Owned by
either an anonymous per-browser `client_id` or (once signed in) an account, with an **explicit,
user-initiated "claim my data" flow** to migrate anonymous rows onto an account (never automatic
— see the PRD's Trust Framework / Risk Register for why). Daily email digests notify signed-in
users when a watched stock's recommendation changes or its price moves >10% — currently the only
"alerting" channel; there is no push-notification infrastructure (see the PRD's Roadmap).

*Depth: `CLAUDE.md` §"Watchlist flow", §"Watchlist alert emails".*

### Portfolio / Positions
"I bought this" tracking from any Market Picks row — entry/target/stop-loss captured at
mark-time, an optional user-entered share count for a capital-weighted P&L view, and an
aggregate Portfolio page (win rate, average P&L%, best/worst performer). Same
anonymous-or-account ownership model and claim flow as Watchlist.

*Depth: `CLAUDE.md` §"Market picks flow" → "Positions" and "Portfolio summary" subsections.*

### Compare
Two full stock-analysis reports side by side, each with its own independent SSE fetch, plus a
real head-to-head diff table for metrics with an unambiguous "better" direction (never asserted
for a ratio the app doesn't recognize — same never-invent-a-judgment principle throughout this
codebase).

*Depth: `CLAUDE.md` §"Compare flow".*

### Consolidated Search
A shared search box in every page's nav answering "what does AlphaPulse already think about X"
in one query — pure aggregation of whatever the three main pipelines have already computed/cached
for that symbol, with no new fetching or LLM calls. A `null` section means "hasn't been analyzed
yet," not an error.

*Depth: `CLAUDE.md` §"Consolidated view flow".*

### Accounts & Programmatic Access
A minimal, passwordless account system (magic-link email, no OAuth) ties Watchlist/Positions
ownership and the daily alert digest together. Signed-in users can mint long-lived API keys for
scripted access to `GET /api/v1/consolidated/{symbol}` (deliberately the one public `/api/v1/*`
endpoint today), gated by a tiered, per-user rate limit (`free`: 100 calls/hr, `pro`: 1,000/hr —
**tier is set by an operator by hand; there is no self-serve checkout**, disclosed openly on the
Pricing page rather than faked — see the PRD's Business Model section).

*Depth: `CLAUDE.md` §"Account & magic-link auth flow", §"Programmatic API access flow".*

### Portfolio Aggregator (personal net-worth tracker)
A separate, deliberately unauthenticated personal-finance tool — genuinely distinct from
Portfolio/Positions above (that's a "I bought this" P&L tracker seeded from Market Picks; this is
a broader net-worth aggregator across every account type). Profiles → accounts → assets (stock,
mutual fund, FD, EPF, PPF, cash, loan, manual), with valuation history and a net-worth summary
(assets minus loans, broken down by type/account). Fed by:
- An **EOD price store** (`securities`/`prices_daily`/`mf_nav_daily`) ingesting NSE's daily
  bhavcopy and AMFI mutual-fund NAVs, plus a **corporate-actions** pipeline that recomputes
  split/bonus/dividend-adjusted close prices.
- A **valuation engine** that auto-values every stock/MF holding nightly from the price store
  (yfinance live-quote fallback for stocks), and an **XIRR** engine (per-asset and portfolio-
  level) that returns null until real transaction history exists.
- **CAS PDF ingestion** (CAMS/KFintech mutual-fund statements) and **broker CSV import**
  (generic column-mapping with a Zerodha preset) — both reconcile against a **securities master +
  symbol resolver** (NSE main-board + BSE main-board + NSE Emerge/BSE SME lists, ISIN → exact
  code → fuzzy name matching) to turn a broker's own stock code into a canonical NSE/BSE symbol,
  then write real transactions/holdings, lighting up XIRR for the first time.

No authentication by design — a personal-scale tool (profiles are a picker, not an account
system), explicitly separate from the account-gated features above. Reachable at
`/portfolio-aggregator` (not `/portfolio`, which stays the existing Positions page).

*Depth: `CLAUDE.md` §"Portfolio Aggregator" onward through its valuation-engine, CAS-ingestion,
and broker-CSV-import subsections; §"EOD price store + corporate actions flow"; §"Securities
master + symbol resolver".*

### Platform Quality Investments (not user-facing features, but load-bearing)
- **Data integrity:** schema-drift detection on the six core data slices, source-freshness/volume
  monitoring across the 28 market-picks sources + macro feeds, per-scraper error counters that
  distinguish a real failure from a legitimately-empty result (surfaced to the user as
  "temporarily unavailable" rather than silent blank cards), an opt-in weekly live-contract-check
  test suite against the four highest-blast-radius scrapers.
- **Reliability & scale:** file-based caching with an optional Redis backing for genuinely
  cross-host shared state, sliding-window rate limiting (Redis-backed or in-memory), Alembic
  schema migrations (replacing ad-hoc `create_all()` as the schema-of-record process), per-call
  LLM cost instrumentation with a running daily total, cross-provider LLM failover (respecting an
  explicit provider pin as a deliberate deployment choice, never silently overridden), and a
  visible "degraded analysis" banner whenever a safe-fallback HOLD (not a genuine analyst call)
  reaches the user.
- **Security:** SSRF-hardened scrapers (host-allowlist checks pre- *and* post-redirect), XXE
  hardening on XML/XBRL parsing, sanitized error payloads (no raw exception text ever reaches the
  browser), a trusted-proxy-secret scheme so per-IP rate limiting survives sitting behind a
  reverse proxy, tightly-scoped and audit-logged account-claim endpoints.
- **Observability:** structured JSON logging throughout, an optional Sentry-compatible error-
  tracking hook, PWA installability with an offline-aware (never-cache-live-data) service worker.

*Depth: the majority of `CLAUDE.md`'s middle section, roughly from "NSE session consolidation"
through "Schema migrations".*

---

## Current Scale & Numbers (as of this revision)

| Metric | Value |
|---|---|
| HTTP endpoints (`api.py` + `routes/`) | ~55 |
| Frontend pages | 12 top-level routes (+ nested auth/verify, market-picks/history) |
| PostgreSQL tables | 21 |
| Market Picks source scrapers | 28 (RSS + GNews + structured), capped at 35 researched stocks/run |
| Backend automated tests | 1,500+ passing (`python -m pytest tests/`) |
| E2E (Playwright) specs | 44 passing |
| LLM providers supported | Anthropic, OpenAI, Groq, Google, OpenRouter, Ollama |

---

## Data Freshness

What "live" actually means, per data slice — every TTL below is read straight from `cache.py`'s
own `TTL_HOURS` map, not estimated:

| Dataset | Refresh (TTL) | Why |
|---|---|---|
| Live quote / price | 1 hour | Intraday price moves; hourly is the practical floor given this is a batch-fetch model, not a streaming feed. |
| News headlines | 1 hour | Same cadence as price — news moves the story faster than fundamentals. |
| Corporate filings | 1 hour (default) | Filings can land anytime during market hours. |
| Fundamentals / ratios | 24 hours | Screener.in itself only updates daily. |
| Peer comparison, financial statements + DCF, street consensus, insider/bulk-deal activity | 24 hours | Fundamentals-adjacent; doesn't move intraday. |
| FII/DII flow, RBI rate/inflation (macro overlay) | 24 hours | Published once per trading day (FII/DII) or changes at most monthly (RBI). |
| Price-history / sparkline series | 6 hours | Daily-close series; doesn't need hourly refresh. |
| Shareholding pattern, MF holdings, detailed shareholding | 7 days (168 hours) | Sourced from quarterly regulatory filings — there's nothing newer to fetch in between. |
| Market Picks (full scan) | 6 hours (cache), weekly (scheduled auto-refresh) | Matches the product's own "Top Stocks This Week" framing. |
| SME Signals, Screener (NIFTY 500) | Daily (weekday cron) | Batch-pipeline model; on-demand refresh available from the UI. |
| Portfolio Aggregator valuations | Nightly (cron) + on-demand refresh | Auto-valued from the EOD price store; a "Refresh valuations" button exists for immediate need. |

---

*See [`PRD.md`](PRD.md) for product vision, goals, priorities, roadmap, and everything this
catalog deliberately leaves out.*
