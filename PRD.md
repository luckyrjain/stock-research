# AlphaPulse — Product Requirements Document

**Status:** Living document — reflects the shipped product as of this revision. The single
source of truth for exhaustive engineering/behavioral detail is [`CLAUDE.md`](CLAUDE.md); this
document is the product-level counterpart — what the product is *for*, what's actually built,
what's deliberately not, and what's next. Where the two could drift, `CLAUDE.md` describes
implementation truth and this document should be corrected to match it, not the other way round.

---

## 1. Problem & Vision

Individual investors researching Indian equities (NSE/BSE) stitch together data from half a
dozen disconnected places — a broker's app for quotes, Screener.in for fundamentals, NSE's own
filings portal for disclosures, Twitter/news sites for sentiment, and their own memory for
"what did I decide about this stock last month." None of these individually apply a consistent,
repeatable analysis framework, and none of them tell an investor when a call has already been
made and changed.

**AlphaPulse's vision:** one place that (a) pulls every relevant public data source for a stock,
(b) applies the same disciplined, disclosed analysis framework every time — quantitative signals
first, LLM synthesis second, never inventing a number that isn't actually in the data — and
(c) remembers what it told you, so you can see whether its own calls have been right.

**Product principle that shapes every feature below:** *never invent data.* A missing scraped
field is `null`, not a guess. An LLM claim must be grounded in the data it was given, checked by
guardrails before it reaches a user. A cache miss degrades to "not yet available," never to a
plausible-looking fabrication. This is stated explicitly in `config/analyst.json`'s own analyst
instructions and is the single most load-bearing design constraint in the codebase — most of the
"disclosed limitation" notes throughout `CLAUDE.md` exist because of it (an unverified scraper
degrades to an empty field rather than a wrong one).

---

## 2. Target Users

| Persona | What they need from AlphaPulse |
|---|---|
| **Retail investor researching one stock** | A fast, trustworthy BUY/HOLD/SELL read on a specific ticker, backed by real fundamentals/news/filings — not a black box. |
| **Screener/discovery user** | "What's worth looking at this week?" — a ranked, sourced watchlist rather than reading ten brokerage notes by hand. |
| **Momentum/technical trader (SME segment)** | A systematic golden-cross/death-cross screener over the NSE Emerge + BSE SME universe, which mainstream tools don't cover well. |
| **Active portfolio tracker** | A place to star stocks, log actual buys, and see aggregate P&L — without needing a real brokerage integration. |
| **Power user / builder** | Programmatic API access (API keys, `/api/v1/*`) to pull AlphaPulse's own aggregated view into their own tooling. |

AlphaPulse is explicitly **not** trying to be a trade-execution platform, a brokerage, or a
real-time tick-data terminal — see §6 for what's deliberately out of scope and why.

---

## 3. Current Implementation — by Feature Area

Each area below is **shipped and live** unless marked otherwise. "Depth" links to the `CLAUDE.md`
section with full behavioral/engineering detail — this PRD intentionally does not re-derive that
detail, to avoid a second copy that drifts.

### 3.1 Stock Analysis (the core product)
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
   implausible given a near-neutral score. A guardrail failure triggers one corrective retry,
   then a labeled, visibly-degraded HOLD fallback — never a confident-looking guess.
5. Streams progress and the final report to the browser via Server-Sent Events, then persists a
   dated snapshot (`verdict_history`) so future visits can show "how has this call tracked since."

Layered on top of the core report: peer comparison + absolute valuation-anchor (own P/E history),
multi-year financial statements + a deterministic DCF estimate, concalls (management commentary
links), insider/institutional activity (PIT filings + bulk/block deals), street consensus
(Trendlyne-sourced numeric analyst consensus + cited coverage), a verdict timeline with
win/loss scoring against live price, MF-holdings quarter-over-quarter stake deltas, filings
classification (corporate actions / rating actions / next-results date), stock-vs-Nifty relative
performance, and hoverable price/quarterly sparklines.

*Depth: `CLAUDE.md` §"Stock analysis flow" onward through §"Verdict history flow" (the bulk of
the document).*

### 3.2 Market Picks
A weekly multi-agent discovery pipeline: scrapes 28 Indian/global financial sources (RSS +
GNews + structured feeds), extracts stock mentions with an LLM, validates against the NSE equity
master, runs the same due-diligence/signal-engine pass §3.1 uses on each candidate (up to 35
stocks per run), and produces a confidence-ranked, sector-balanced watchlist with a 4-tier
BUY/WATCHLIST/HOLD/SELL rating and deterministic entry/target/stop-loss levels (never
LLM-generated prices). Auto-refreshes weekly via GitHub Actions; a track-record page shows
historical accuracy against actual subsequent price moves, alpha vs. Nifty, and per-tier win
rates.

*Depth: `CLAUDE.md` §"Market picks flow", §"LLM cost instrumentation..." point on peer/valuation
wiring, §"Peer/valuation-anchor wired into scoring".*

### 3.3 SME Signals
A PostgreSQL-backed batch screener over the full NSE Emerge + BSE SME universe: EMA20/EMA50
golden-cross/death-cross detection, RSI(14), volume-spike confirmation, liquidity/market-cap
context, per-cross forward-return outcomes, and an aggregate 90-day golden-cross hit rate.
Auto-refreshes on a weekday cron; on-demand refresh available from the UI.

*Depth: `CLAUDE.md` §"SME golden cross flow".*

### 3.4 Screener (NIFTY 500 custom screener)
The same filter-chip screening pattern as SME Signals, generalized to the primary NSE/BSE
large/mid-cap universe: filterable/sortable by industry, P/E, market cap, RSI, and EMA trend,
served from a daily-refreshed stored-metrics table (no live scrape per request).

*Depth: `CLAUDE.md` §"Custom screener flow".*

### 3.5 Watchlist
A cross-mode "stocks I care about" list — one star button, usable from every dashboard —
backed by PostgreSQL, with live price fan-out and a corporate-action calendar roll-up. Owned by
either an anonymous per-browser `client_id` or (once signed in) an account, with an **explicit,
user-initiated "claim my data" flow** to migrate anonymous rows onto an account (never automatic
— see §6.2 for why). Daily email digests notify signed-in users when a watched stock's
recommendation changes or its price moves >10% — currently the only "alerting" channel; there is
no push-notification infrastructure (see §5).

*Depth: `CLAUDE.md` §"Watchlist flow", §"Watchlist alert emails".*

### 3.6 Portfolio / Positions
"I bought this" tracking from any Market Picks row — entry/target/stop-loss captured at
mark-time, an optional user-entered share count for a capital-weighted P&L view, and an
aggregate Portfolio page (win rate, average P&L%, best/worst performer). Same
anonymous-or-account ownership model and claim flow as Watchlist.

*Depth: `CLAUDE.md` §"Market picks flow" → "Positions" and "Portfolio summary" subsections.*

### 3.7 Compare
Two full stock-analysis reports side by side, each with its own independent SSE fetch, plus a
real head-to-head diff table for metrics with an unambiguous "better" direction (never asserted
for a ratio the app doesn't recognize — same never-invent-a-judgment principle as §1).

*Depth: `CLAUDE.md` §"Compare flow".*

### 3.8 Consolidated Search
A shared search box in every page's nav answering "what does AlphaPulse already think about X"
in one query — pure aggregation of whatever the three main pipelines have already computed/cached
for that symbol, with no new fetching or LLM calls. A `null` section means "hasn't been analyzed
yet," not an error.

*Depth: `CLAUDE.md` §"Consolidated view flow".*

### 3.9 Accounts & Programmatic Access
A minimal, passwordless account system (magic-link email, no OAuth) ties Watchlist/Positions
ownership and the daily alert digest together. Signed-in users can mint long-lived API keys for
scripted access to `GET /api/v1/consolidated/{symbol}` (deliberately the one public `/api/v1/*`
endpoint today), gated by a tiered, per-user rate limit (`free`: 100 calls/hr, `pro`: 1,000/hr —
**tier is set by an operator by hand; there is no self-serve checkout**, disclosed openly on the
Pricing page rather than faked, see §6.3).

*Depth: `CLAUDE.md` §"Account & magic-link auth flow", §"Programmatic API access flow".*

### 3.10 Platform Quality Investments (not user-facing features, but load-bearing)
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

## 4. Current Scale & Numbers (as of this revision)

| Metric | Value |
|---|---|
| HTTP endpoints (`api.py` + `routes/`) | ~38 |
| Frontend pages | 10 top-level routes (+ nested auth/verify, market-picks/history) |
| PostgreSQL tables | 11 |
| Market Picks source scrapers | 28 (RSS + GNews + structured), capped at 35 researched stocks/run |
| Backend automated tests | 1,165+ passing (`python -m pytest tests/`) |
| E2E (Playwright) specs | 35+ passing |
| LLM providers supported | Anthropic, OpenAI, Groq, Google, OpenRouter, Ollama |

---

## 5. Documented Product Gaps (deferred, not forgotten)

These are real, currently-open items already surfaced by this project's own development history
(deep-review cycles, adversarial code review) and explicitly deferred rather than silently
dropped — pulled together here as the forward-looking half of this PRD, distinct from the
org/legal/business items in §6 that require a human decision rather than more engineering.

**Near-term (small-to-medium engineering lift):**
- `api.py` still holds ~28 of the app's ~38 endpoints inline (only Watchlist/Positions were
  extracted into `routes/`) — real maintainability debt on the two largest handlers (`analyse`,
  `validate_symbol`).
- `market_picks_pipeline.py` (the single largest Python module in the repo) has never been
  decomposed, unlike `api.py`/`results-dashboard.tsx`.
- No central, typed env-var configuration module — ~20 backend env vars are still read via
  scattered `os.getenv` calls, with `CLAUDE.md`'s prose as the closest thing to a schema.
- Dense data tables (Screener, SME Signals, Watchlist) have no mobile card layout, only
  horizontal scroll — real friction for this product's mobile-heavy Indian audience.
- Several `_nse_session()` wrapper functions remain duplicated across NSE-touching tool modules
  rather than sharing one default (kept this way deliberately for test-patch compatibility, but
  it's still a real "write a ninth near-duplicate" cost for the next NSE integration).

**Medium-term (needs real infrastructure, not a small patch):**
- **Real push notifications.** The only proactive alert channel today is a once-daily email
  digest (`watchlist_alerts.py`). A genuine push channel — device tokens, APNs/FCM/web-push
  infra, a subscription UI — is new product infrastructure this repo doesn't have anywhere yet.
  This is the single most-cited "not yet, and here's specifically why" gap in the codebase's own
  documentation.
- **A calibrated, back-tested signal model.** The signal engine's weights (including the
  sector-tilt overrides) are principled but explicitly *not* back-tested against realized
  returns — three sector buckets rather than one override per GICS sector, by design, "since
  splitting further would read as more empirical precision than the underlying judgment
  actually has." A real backtest harness would let this move from "reasoned defaults" to
  "calibrated."
- **Broader `/api/v1/*` surface.** Only one public, API-key-gated endpoint exists today
  (`consolidated/{symbol}`) — a deliberate "ship one real endpoint, not a speculative surface"
  scope call. Other internal endpoints (peers, financials, screener) are natural next candidates
  once real external demand is confirmed.

**Explicitly and permanently declined (not "later," but "no," with a stated reason):**
- **IPO grey-market-premium (GMP) data.** Considered and rejected: GMP is an unregulated,
  informal indicator that SEBI itself has warned doesn't reflect a security's real value, sourced
  only from grey-market-tracking portals with materially different ToS/reliability risk than
  this app's existing regulator/vendor sources. Revisit only if a specific, reliable,
  ToS-compatible source is identified.

---

## 6. Explicitly Out of Scope: Organizational, Legal & Business

These three items are unchanged from a prior deep cross-functional review and are **not
engineering problems** — no amount of further code work closes them. Restated here because a PRD
that omits them would misrepresent the product's actual readiness for scale.

### 6.1 Bus factor of one
The entire commit history traces to a single human author (with AI pair-programming assistance).
The density of `CLAUDE.md` itself is real engineering discipline, but it is not evidence a team
exists — if anything it reads as compensation for the absence of one. **Needs:** a real second
engineer, or at minimum a written handoff plan, before this is treated as infrastructure a
business depends on.

### 6.2 No legal/compliance review of the scraping surface
AlphaPulse scrapes `screener.in`, `nseindia.com`/`nsearchives.nseindia.com`,
`bseindia.com`/`api.bseindia.com`, `trendlyne.com`, `rbi.org.in`, plus GNews-mediated coverage of
several news publishers, on a recurring schedule at a scale beyond casual use — with no confirmed
Terms-of-Service review by qualified counsel behind any of it. (This is also *why* Watchlist/
Positions ownership is never auto-migrated on sign-in, and why the account-claim flow is
tightly rate-limited and audit-logged — the product's default posture throughout is "ask,
disclose, and bound the blast radius," not "assume it's fine.") **Needs:** a licensed
professional reviewing each source's actual ToS and applicable Indian data-protection/scraping
case law before scaling traffic materially beyond where it sits today.

### 6.3 No real payment processing
`users.tier` and the informational `/pricing` page exist specifically to *stop short* of a real
checkout flow — there is no payment processor integration, and `'pro'` tier is set by an operator
by hand today. This is disclosed by design, not an oversight: standing up real billing is itself
a business decision (processor choice, actual pricing, India-specific tax/compliance, refund
policy) that has to precede engineering work, not follow it. **Needs:** those business decisions
made first; the engineering task that follows is then a normal, scoped piece of work.

---

## 7. Appendix — Documentation Map

| Doc | Purpose |
|---|---|
| `CLAUDE.md` | Exhaustive, always-current engineering reference — the ground truth for every feature's exact behavior, disclosed limitations, and design rationale. Read this for "how does X actually work." |
| `PRD.md` (this doc) | Product-level view — what AlphaPulse is for, what's shipped, what's next, what's deliberately not being built. Read this for "what does AlphaPulse do and why." |
| `README.md` | Quickstart — install, run, top-level feature summary. |
| `docs/setup.md` | Full environment variable reference, local dev setup, troubleshooting. |
| `docs/deployment.md` | Docker Compose, manual deployment, scaling guidance. |
| `docs/architecture.md` | System-level request flows and module boundaries. |
| `docs/tools.md` | Reference for every data-fetching tool/scraper and its output shape. |
| `docs/output-schema.md` | JSON schema reference for the report, cache files, and standalone endpoint responses. |
| `design.md` | AlphaPulse Design System — colors, typography, component patterns. |
