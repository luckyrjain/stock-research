# AlphaPulse — Feature Catalog

**Status:** Living document — the implementation inventory companion to [`PRD.md`](PRD.md) (which
covers *why* AlphaPulse exists and what's prioritized next). Every area below is **shipped and
live** unless marked otherwise. "Depth" points at the `CLAUDE.md` section carrying the full
engineering detail; where this document and [`../backend/CLAUDE.md`](../backend/CLAUDE.md) /
[`../frontend/CLAUDE.md`](../frontend/CLAUDE.md) disagree, those are ground truth and this
document should be corrected to match.

---

## Current Implementation — by Feature Area

### Stock Analysis (the core product)
Given an NSE/BSE ticker (or ISIN, or company name), AlphaPulse:
1. Validates the symbol across NSE autocomplete, BSE, and Screener.in.
2. Fetches six data slices in parallel — price/quote, fundamentals, news, shareholding, mutual
   fund holdings, corporate filings.
3. Runs a **quantitative signal engine** — six independent signals (valuation, growth, volume,
   filings, technical [RSI14 + EMA20/50], macro [FII/DII flow + RBI rate/inflation]), combined
   with **sector-aware weight tilts** (rate-sensitive / growth / cyclical sector groups get
   different weightings) into one bounded `final_score` (−1..1) and a five-tier quant verdict
   (BUY / WATCHLIST / HOLD / AVOID / SELL).
4. Calls an LLM analyst with all of the above, under guardrails: a **bidirectional directional
   cross-check** rejects a response whose recommendation contradicts the quant signal (both a
   SELL against strongly positive signals *and* a BUY against strongly negative ones), plus a
   stated-confidence plausibility check; a **numeric-misread guardrail** compares every number
   the analyst cites in prose (dividend yield, P/E, ROE, ROCE, book value, growth, EBITDA
   margin, market cap, sector-average comparisons) against the real source data, catching
   transcription errors like a 0.46 dividend yield written as "47%". A guardrail failure
   triggers one corrective retry, then a second configured LLM provider if one exists, then a
   labeled, visibly-degraded HOLD fallback — never a confident-looking guess.
5. Streams progress and the final report to the browser via Server-Sent Events, then persists a
   dated snapshot (`verdict_history`) so future visits can show how the call has tracked since.

Source-fallback behaviour: when yfinance has no live quote on either NSE or BSE (common for
thinly-traded stocks), the price/quote fetch falls back to Screener.in's own top-ratios widget,
supplemented with a stockanalysis.com scrape for EPS/52-week-range/volume. Separately, when
Screener.in returns no ratios table at all (e.g. a recent IPO), a best-effort EPS figure is
parsed from the company's most recent NSE XBRL results filing and labeled as such in the UI.

Layered on top of the core report: peer comparison + absolute valuation anchor (current P/E vs.
its own 3–5 year band), multi-year financial statements + a deterministic DCF estimate, concalls
(management commentary links), insider/institutional activity (PIT filings + bulk/block deals),
street consensus (Trendlyne numeric analyst consensus + cited coverage), a verdict timeline with
win/loss scoring against live price, MF-holdings quarter-over-quarter stake deltas, detailed
shareholding (every individually-named promoter/institutional holder from NSE's own shareholding
XBRL filing, not just aggregate category percentages), filings classification (corporate actions /
rating actions / next-results date), stock-vs-Nifty relative performance, and hoverable price/
quarterly/EMA sparklines.

*Depth: `backend/CLAUDE.md` §"Stock analysis flow" through §"Verdict history flow".*

### Market Picks
A weekly multi-agent discovery pipeline: scrapes **20 sources** (5 RSS feeds + 12 GNews-mediated
brokerage/news queries + 3 structured feeds — NSE bulk/block deals, NSE insider trades, a
Screener.in fundamental screen), extracts stock mentions with an LLM, validates against the NSE
equity master, runs the same due-diligence/signal-engine pass Stock Analysis uses on each
candidate (capped at 35 stocks/run), and produces a confidence-ranked, sector-balanced list
(max 2 per sector promoted) with a 4-tier BUY/WATCHLIST/HOLD/SELL rating and deterministic
entry/target/stop-loss levels (computed from price, signal score, and 52-week range, or a
credibility-weighted analyst target parsed out of the source text — never LLM-generated).

Auto-refreshes weekly via GitHub Actions (Mon 01:30 UTC), with a status endpoint surfacing a true
"last scan / next scheduled scan" instead of an unverifiable "every week" claim. A track-record
page shows historical accuracy against actual subsequent price moves, alpha vs. Nifty, per-tier
win rates, and a date picker for browsing any single past snapshot. Per-run **source-quality
telemetry** (articles fetched, picks extracted, picks surviving symbol validation, per source)
complements the day-level source-health and error-counter monitoring. If the user has tracked
"I bought this" positions, a **sector-concentration badge** flags a pick whose sector is already
≥25% of their tracked position value.

*Depth: `backend/CLAUDE.md` §"Market picks flow", §"Source-quality telemetry",
§"Peer/valuation-anchor wired into scoring".*

### SME Signals
A PostgreSQL-backed batch screener over the full NSE Emerge + BSE SME universe: EMA20/EMA50
golden-cross/death-cross detection, RSI(14), volume-spike confirmation, liquidity/market-cap
context (with an illiquidity badge), per-cross forward-return outcomes, an aggregate 90-day
golden-cross hit rate, and a "regime" view listing every monitored stock's current EMA posture.
Auto-refreshes on a weekday cron; on-demand refresh from the UI. BSE rows (whose symbol is a
numeric scrip code) deep-link into Stock Analysis via ISIN resolution.

*Depth: `backend/CLAUDE.md` §"SME golden cross flow".*

### Screener (NIFTY 500 custom screener)
The same filter-chip screening pattern as SME Signals, generalized to the primary NSE/BSE
large/mid-cap universe: filterable/sortable by NSE industry, P/E, market cap, RSI, and EMA trend,
served from a daily-refreshed stored-metrics table (no live scrape per request). Industry filter
chips are built from the values actually present in the table, not a hardcoded list.

*Depth: `backend/CLAUDE.md` §"Custom screener flow".*

### Watchlist
A cross-mode "stocks I care about" list — one star button, usable from every dashboard — backed
by PostgreSQL, with live price fan-out and a corporate-action calendar roll-up (next-results
date, rating actions, dividends/splits/bonuses/buybacks, read off already-cached filings).
The calendar also flags a **same-day recommendation change or ≥10% price move** on any watched
symbol; a dot on the nav-bar Watchlist link surfaces that from any page rather than only on
`/watchlist` itself.

Rows are owned by either an anonymous per-browser `client_id` or (once signed in) an account,
with an **explicit, user-initiated "claim my data" flow** to migrate anonymous rows onto an
account — never automatic. Daily email digests notify signed-in users of the same two triggers;
this is currently the only push-style channel (there is no real notification infrastructure —
see the PRD's Roadmap).

*Depth: `backend/CLAUDE.md` §"Watchlist flow", §"Watchlist alert emails".*

### Portfolio / Positions
"I bought this" tracking from any Market Picks row — entry/target/stop-loss captured at
mark-time, an optional user-entered share count for a capital-weighted P&L view, and an
aggregate Portfolio page (win rate, average P&L%, best/worst performer, counts at target/
stop-loss). Positions with no share count are excluded from capital-weighted figures rather than
assumed to be one share. Same anonymous-or-account ownership model and claim flow as Watchlist.

*Depth: `backend/CLAUDE.md` §"Market picks flow" → "Positions", "Portfolio summary".*

### Compare
Two full stock-analysis reports side by side, each with its own independent SSE fetch, plus a
head-to-head diff table for metrics with an unambiguous "better" direction — never asserted for
a ratio the app doesn't recognize.

*Depth: `backend/CLAUDE.md` §"Compare flow".*

### Consolidated Search
A shared search box in every page's nav answering "what does AlphaPulse already think about X" in
one query — pure aggregation of whatever the three main pipelines have already computed/cached
for that symbol, with no new fetching or LLM calls. A `null` section means "hasn't been analyzed
yet," not an error.

*Depth: `backend/CLAUDE.md` §"Consolidated view flow".*

### Accounts & Programmatic Access
A minimal, passwordless account system (magic-link email, no OAuth) ties Watchlist/Positions
ownership and the daily alert digest together. Signed-in users can mint long-lived API keys for
scripted access to `GET /api/v1/consolidated/{symbol}` (deliberately the one public `/api/v1/*`
endpoint today), gated by a tiered, per-user hourly rate limit (`free`: 100 calls/hr, `pro`:
1,000/hr) with a usage meter on the key-management page. **Tier is set by an operator by hand;
there is no self-serve checkout** — stated plainly on the Pricing page rather than faked.

*Depth: `backend/CLAUDE.md` §"Account & magic-link auth flow", §"Programmatic API access flow".*

### Portfolio Aggregator (personal net-worth tracker)
A separate, deliberately unauthenticated personal-finance tool — distinct from Portfolio/Positions
above (that's an "I bought this" P&L tracker seeded from Market Picks; this is a net-worth
aggregator across every account type). Profiles → accounts → assets (stock, mutual fund, FD, EPF,
PPF, cash, loan, manual), with valuation history and a net-worth summary (assets minus loans,
broken down by type/account). Fed by:
- An **EOD price store** (`securities` / `prices_daily` / `mf_nav_daily`) ingesting NSE's daily
  bhavcopy and AMFI mutual-fund NAVs, self-healing across the last 5 weekdays, plus a
  **corporate-actions** pipeline that recomputes split/bonus-adjusted close prices (dividends,
  rights and buybacks are recorded but never adjust prices — no total-return series).
- A **valuation engine** that auto-values every stock/MF holding nightly from the price store
  (yfinance live-quote fallback for stocks), and an **XIRR** engine (per-asset and portfolio
  level, Newton's method with a bisection fallback) that returns null until real transaction
  history exists.
- **CAS PDF ingestion** (CAMS/KFintech detailed statements; re-import replaces rather than
  duplicates) and **broker CSV/XLSX import** (generic column mapping with a Zerodha auto-detect
  preset; appends and de-duplicates, since a tradebook is a date-ranged partial). Both reconcile
  against a **securities master + symbol resolver** (NSE main-board + BSE main-board + NSE
  Emerge/BSE SME, ISIN → exact code → fuzzy name matching) to turn a broker's own stock code into
  a canonical NSE/BSE symbol — a fuzzy or unresolved match keeps the broker's raw code and warns,
  never silently substitutes a guess.
- **Broker API sync** (Zerodha Kite Connect, HDFC Securities InvestRight, Paytm Money Open
  API — all free-tier for personal use) as a live alternative to the file-based imports above: a
  connected broker account's holdings and trades sync directly, no upload, no manual re-run.
  Every account supplies its own app credentials (API key/secret, registered under that broker's
  own developer portal) inline when connecting — there is no shared, deployment-wide broker key,
  since a Kite Connect/HDFC/Paytm Money "app" is only ever issued to one specific broker login.
  The app secret and the broker's access token are both encrypted at rest (Fernet,
  `PORTFOLIO_ENCRYPTION_KEY`); re-registering a broker account's credentials invalidates its
  prior access token rather than leaving a stale one on file.

No authentication by design — profiles are a picker, not an account system. Reachable at
`/portfolio-aggregator` (not `/portfolio`, which stays the Positions page); labelled "Net Worth"
in the nav so the two aren't confused.

*Depth: `backend/CLAUDE.md` §"Portfolio aggregator", §"Portfolio valuation engine",
§"CAS PDF import", §"Broker CSV/XLSX import", §"Broker API sync", §"EOD price store + corporate
actions flow", §"Securities master + symbol resolver".*

### Platform Quality Investments (not user-facing features, but load-bearing)
- **Data integrity:** schema-drift (type-shape) detection on the six core data slices;
  source-freshness/volume monitoring across the 20 market-picks sources + macro feeds; per-scraper
  error counters that distinguish a real failure from a legitimately-empty result (surfaced to the
  user as "temporarily unavailable" rather than a silent blank card); an opt-in weekly
  live-contract-check suite against the four highest-blast-radius scrapers.
- **Reliability & scale:** file-based caching with optional Redis backing for genuinely cross-host
  shared state; sliding-window rate limiting (Redis-backed or in-memory); a global LLM concurrency
  ceiling and single-flight locks on every expensive pipeline (analysis, market picks, SME,
  screener refresh); Alembic schema migrations (replacing ad-hoc `create_all()` as the
  schema-of-record process); per-call LLM cost instrumentation with a running daily total;
  cross-provider LLM failover (respecting an explicit provider pin as a deliberate deployment
  choice, never silently overridden); and a visible "degraded analysis" banner whenever a
  safe-fallback HOLD reaches the user.
- **Security:** SSRF-hardened scrapers (host-allowlist checks pre- *and* post-redirect); XXE
  hardening on XML/XBRL parsing; sanitized error payloads (no raw exception text ever reaches the
  browser, including on SSE streams); hash-only storage of magic-link, session, and API-key
  secrets; a trusted-proxy-secret scheme so per-IP rate limiting survives sitting behind a reverse
  proxy; tightly-scoped and audit-logged account-claim endpoints.
- **Observability:** structured JSON logging throughout; an optional Sentry-compatible
  error-tracking hook; PWA installability with an offline-aware service worker that never caches
  live market data or credential-bearing URLs.

*Depth: `backend/CLAUDE.md` §"NSE session consolidation" through §"Schema migrations".*

---

## Current Scale & Numbers (as of this revision)

| Metric | Value |
|---|---|
| HTTP endpoints | 57 — 29 in `api.py`, 5 watchlist, 6 positions, 17 portfolio-aggregator (`routes/`) |
| Frontend page routes | 13 — 11 top-level (`/`, market-picks, sme-signals, screener, watchlist, portfolio, portfolio-aggregator, compare, api-keys, pricing, login) + `/market-picks/history` and `/auth/verify` |
| PostgreSQL tables | 22 (`backend/db/models.py`) |
| Market Picks source scrapers | 20 (5 RSS + 12 GNews + 3 structured), capped at 35 researched stocks/run |
| Backend automated tests | 1,509 passing, 0 failed, 0 skipped (`cd backend && python -m pytest tests/`) |
| E2E (Playwright) specs | 44 passing (`cd frontend && npm run test:e2e`) |
| Alembic migration revisions | 4 (baseline schema, EOD price store + corporate actions, portfolio-aggregator foundation, `app_state` durable JSON state) |
| LLM providers supported | 6 — Anthropic, OpenAI, Groq, Google, OpenRouter, Ollama |

---

## Data Freshness

What "live" actually means, per dataset. Every cache TTL below is read from `backend/cache.py`'s
`TTL_HOURS` map; cron times are from the workflow files in `.github/workflows/`.

| Dataset | Refresh | Why |
|---|---|---|
| Live quote / price | 1 hour | Intraday price moves; hourly is the practical floor for a batch-fetch model, not a streaming feed. |
| News headlines | 1 hour | News moves the story faster than fundamentals. |
| Corporate filings | 1 hour (map default) | Filings can land any time during market hours. |
| Fundamentals / ratios | 24 hours | Screener.in itself only updates daily. |
| Peer comparison, financial statements + DCF, street consensus, insider/bulk-deal activity | 24 hours | Fundamentals-adjacent; don't move intraday. |
| FII/DII flow, RBI rate/inflation (macro overlay) | 24 hours | Published once per trading day (FII/DII) or monthly at most (RBI). |
| LLM analyst report | 24 hours | Re-runs only when at least one input slice went stale — the single most expensive step in the pipeline. |
| Price-history / sparkline series, Nifty benchmark series | 6 hours / 24 hours | Daily-close series; no need for hourly refresh. |
| Shareholding pattern, MF holdings, detailed shareholding | 7 days (168 hours) | Quarterly regulatory filings — nothing newer exists to fetch in between. |
| Market Picks (full scan) | 192-hour cache TTL; weekly cron (Mon 01:30 UTC) | TTL is the weekly cadence plus 24h of slack for a delayed run. |
| SME Signals | Weekday cron 13:00 UTC (18:30 IST) | ~3h after NSE's close for EOD data to settle; on-demand refresh in the UI. |
| Screener (NIFTY 500) | Weekday cron 14:00 UTC (19:30 IST) | Same batch-pipeline model; on-demand refresh in the UI. |
| Watchlist alert digest | Weekday cron 13:30 UTC (19:00 IST) | One digest email per user per run, account-owned rows only. |
| EOD price store + corporate actions | Weekday cron 14:15 UTC (19:45 IST) | The bhavcopy with delivery data publishes around 19:00 IST. |
| Portfolio Aggregator valuations | Nightly, as the final step of the EOD pipeline + on-demand | Auto-valued from the EOD price store; a "Refresh valuations" button exists for immediate need. |

---

## Known Gaps & Disclosed Limitations

Real, deliberate, and documented — listed here so the PRD's Roadmap and Risk Register can point at
something concrete rather than restating them.

- **No back-tested calibration.** Signal weights, sector tilts, verdict thresholds, and the
  confidence formula are reasoned judgments, not fitted against a realized-return dataset. There
  is no backtest harness.
- **`UNKNOWN` signals still carry full weight** in `final_score` (contributing a neutral 0 rather
  than being excluded with the rest renormalized), which biases thin-history stocks toward HOLD.
- **Roughly a dozen scraper assumptions were never verified against a live response** (Screener
  section ids, Trendlyne DOM labels, NSE XBRL tag names, RBI's rate table, the NIFTY 500 CSV
  layout, the yfinance sector taxonomy). All degrade to null/empty rather than a wrong number; a
  weekly live-contract-check job covers the four highest-blast-radius of them, not all.
- **No push-notification infrastructure.** The only alert channel is the daily email digest.
- **No user-behaviour analytics.** Nothing measures activation, retention, or feature adoption
  (see the PRD's Success Metrics).
- **No self-serve payments.** `users.tier` is set by an operator by hand.
- **Portfolio Aggregator has no authentication** (profiles are a picker) and is not
  cross-referenced with Watchlist/Positions — a stock researched in one is invisible to the other.
- **HDFC Securities' and Paytm Money's broker-sync REST shapes were never verified against a
  live response** (their developer portals were unreachable from this sandbox) — endpoint
  paths, the checksum-signing scheme, and response field names follow the general shape other
  Indian broker "Open APIs" (Kite Connect included) publicly document, not a confirmed live
  contract. A mismatch degrades a holding/trade to skipped, never a fabricated value; Zerodha's
  own field names are similarly unverified live, taken from Kite's published docs.
- **EOD price store is ingestion-only** outside the aggregator: no BSE bhavcopy, no intraday, no
  total-return (dividend-adjusted) series, and the SME pipeline still fetches its own OHLCV from
  yfinance rather than reading from it.
- **`--reset-db` scoping is a convention, not an enforced rule.** Every pipeline now scopes its
  reset to the tables it owns (`sme_ema_pipeline` was the last holdout and has been brought in
  line), but nothing stops a new pipeline from reaching for `metadata.drop_all()` — the shared
  SQLAlchemy `MetaData()` carries all 23 tables, including seven holding non-regenerable personal
  financial data (and, for `broker_connections`, real broker API credentials). See
  `docs/database.md` for the table-ownership map.
- **`client_id` is a grouping key, not a security boundary** — anyone holding one can read/write
  that browser's anonymous watchlist and positions. The claim endpoints (which reassign rows
  exclusively and permanently) are rate-limited to 5/hour and audit-logged, which bounds
  automated abuse but not a single targeted guess of a leaked ID.

---

*See [`PRD.md`](PRD.md) for product vision, goals, priorities, roadmap, and business context.*
