# Architecture

This is an architecture-level reference: how the pieces fit together, module boundaries, and
request/data flow. For exhaustive per-feature detail (exact formulas, edge cases, disclosed
limitations, historical "why" of a given design choice), see `CLAUDE.md` — this document
deliberately summarizes and points there rather than duplicating it.

## System overview

A FastAPI backend (`api.py` + `routes/`) talks to yfinance, Screener.in, NSE, BSE, Trendlyne,
RBI, and Google News, normalizes what it scrapes, runs a deterministic quant signal engine over
it, and (for the flagship single-stock flow) calls an LLM for a structured recommendation. A
Next.js 15 frontend never talks to FastAPI directly — every call goes through a same-shaped proxy
route under `frontend/app/api/*` first. PostgreSQL (via SQLAlchemy Core, migrated with Alembic)
is the shared, persistent store for anything cross-session: accounts, watchlist, positions,
verdict history, SME/screener batch results. Redis is optional, additive shared state for
rate limiting and cache sharing once a deployment runs more than one backend worker/host; every
Redis-backed module degrades to an in-memory/local-disk equivalent when `REDIS_URL` is unset, so
a single-process deployment behaves identically with or without it.

Four user-facing "modes" share this backend:

| Mode | Entry point | Backing store |
|---|---|---|
| Stock analysis | `GET /api/analyse/{symbol}` (SSE) | File cache (`output/<SYMBOL>/`) + Postgres (`verdict_history`, `mf_holdings_history`) |
| Market Picks | `GET /api/market-picks` (SSE) | File cache (`output/_market_picks/`, `output/_history/`) |
| SME Signals | `GET /api/sme-signals` | Postgres (`sme_stocks`, `ema_signals`) |
| NIFTY 500 Screener | `GET /api/screener` | Postgres (`screener_stocks`) |

Two cross-cutting features tie all four together: a **Watchlist** (star any stock from any mode)
and an **account system** (magic-link auth, optional — anonymous `client_id` usage still works
everywhere). A shared **search box** (`GET /api/consolidated/{symbol}`) aggregates whatever the
above modes have already cached for one symbol into a single "what does this app think about X"
view, with zero new fetching.

---

## Request flow: stock analysis

```text
Browser (Next.js :3000)
  └─ EventSource → /api/analyse/{symbol}
        └─ Next.js proxy route (app/api/analyse/[symbol]/route.ts) → FastAPI :8000, unbuffered

FastAPI GET /api/analyse/{symbol}
  1. cache.is_fresh() per task → emits `start` (stale vs. cached task list)
  2. Stale tasks dispatched concurrently via ThreadPoolExecutor → main._fetch_task()
       each completion emits `task_done`; schema_drift.log_drift_if_any() runs inline
  3. schemas.normalize() turns each raw tool payload into its canonical dict
  4. run_signal_engine(symbol, all_data) — see "Signal engine" below.
       technical_signal()/macro_signal() do their own I/O, so this whole call now runs
       through loop.run_in_executor() (previously called unwrapped, since the engine
       used to be pure CPU over already-fetched data — see "SSE bridge pattern")
  5. crew.run_analysis_with_fallback() runs the LLM analyst call in a thread;
       the SSE loop sends `: heartbeat` comments every 15s while it's in flight
  6. main._build_report() merges everything (report fields, filings_summary,
       mf_holdings_trend, degraded flag) → emits `done`
  7. verdict_history.save_snapshot() (fire-and-forget) records today's call
```

The six data slices fetched in step 2 (`stock_info`, `research`, `news`, `shareholding`,
`mf_holdings`, `filings`) are unchanged from the original design — see CLAUDE.md's "Agent
architecture" table for the tool/source mapping. What's grown since is everything layered on top
of that base fetch, all standalone/on-demand (own cache entries, outside the six-task TTL
lockstep, fetched by the frontend only after the main report has loaded):

| Endpoint | Adds |
|---|---|
| `GET /api/peers/{symbol}` | Peer percentile ranking + `absolute_anchor` (own P/E vs. own 3-5y history) — `peer_analytics.py` |
| `GET /api/financials/{symbol}` | Multi-year Income Statement/Balance Sheet/Cash Flow + `dcf` (deterministic two-stage DCF, `dcf_valuation.py`) + `concalls` |
| `GET /api/insider-activity/{symbol}` | Promoter/director PIT disclosures + bulk/block deals, scoped to one symbol (90-day window) |
| `GET /api/street-consensus/{symbol}` | GNews articles citing Trendlyne + a real scraped numeric consensus (rating/analyst count/target price) from trendlyne.com directly |
| `GET /api/verdict-history/{symbol}` | Timeline of stored daily verdicts, each BUY/SELL entry scored win/loss against today's live price |
| `GET /api/prices/history/{symbol}?benchmark=true` | Daily-close sparkline series, optionally diffed against Nifty50 over the same window |

Each is independently optional and degrades to `null`/`[]`/an empty section rather than failing
the page — see CLAUDE.md's own section for each (Peer comparison flow, Insider & institutional
activity flow, Street consensus flow, Multi-year financial statements + DCF valuation flow,
Verdict history flow) for exact shapes and disclosed scraper-verification caveats.

**Filings classification**: `signals/filings_classifier.py` runs pure text classification (no new
scrape) over the same `filings` list — corporate actions (dividend/split/bonus/buyback), the most
recent rating action, and the next results date. `main._build_report()` adds this as
`filings_summary`; `signals/filings.py::filings_signal()` separately calls
`classify_rating_action()` to nudge the filings *signal's* score.

**MF holdings trend**: `mf_holdings_history.py` (Postgres) snapshots the `mf_holdings` task's
shareholding disclosure on every fetch and computes quarter-over-quarter stake deltas
(`compute_stake_deltas()`). Kept out of `_build_report()` itself (it's a DB call, and
`_build_report()` runs directly on the event loop) — each caller (`api.py` via `run_in_executor`,
`main.py`'s CLI directly) computes it and passes it in as a parameter, same pattern as `signals`.

### SSE events

| Event | When |
|---|---|
| `start` | Immediately; lists stale vs. cached tasks |
| `task_done` | Each of the 6 data tasks completes |
| `analysing` | LLM analyst call starts |
| `done` | Report ready (includes `degraded: bool` — see "LLM analyst layer" below) |
| `error` | Unrecoverable failure |

### Symbol validation (`GET /api/validate/{symbol}`)

Three input forms, resolved in `api.py`: ISIN (NSE equity master CSV, then yfinance fallback),
BSE-forced (`?exchange=BSE`, resolves a Screener.in slug), and ticker/company name (NSE + BSE
autocomplete in parallel, Screener.in as final fallback). Also reused by the SME Signals page for
BSE SME rows' deep links (BSE's own numeric scrip code isn't directly analyzable — see "SME
Signals" below).

---

## Request flow: Market Picks

Unchanged in shape from the original design — six sequential phases, all blocking work in a
`ThreadPoolExecutor`, bridged to the SSE stream via `asyncio.Queue` + `loop.call_soon_threadsafe`.
See CLAUDE.md's "Agent architecture" → Market picks pipeline table for the phase-by-phase
breakdown (`_phase_scrape` → `_phase_extract` → `_phase_consolidate` → `_phase_research` →
`_phase_analyze` → `_phase_score`) and "Peer/valuation-anchor wired into scoring" for how
`_phase_research` now also pulls each candidate's absolute valuation anchor (via the same
`peer_analytics.build_peer_result()` shared with `GET /api/peers/{symbol}`, sharing its cache
entry) as a small confirmation nudge on `_compute_confidence()`.

```text
Browser → EventSource → /api/market-picks[?force=true] → Next.js proxy → FastAPI :8000
  1. Check output/_market_picks/picks.json (7-day TTL) → cache hit: emit `done` immediately
  2. Cache miss: MarketPicksPipeline.run() wrapped in run_in_executor
  3. Pipeline's on_event() → loop.call_soon_threadsafe(q.put_nowait, ...) → SSE
  4. Six phases run synchronously inside the executor thread
  5. Result saved via market_picks_pipeline.save_picks_cache()
```

**Weekly auto-refresh**: `.github/workflows/market-picks-cron.yml` (Mondays 01:30 UTC) calls
`GET /api/market-picks?force=true` on the deployed backend directly — the picks cache is a local
file, not reachable from a GitHub Actions runner, unlike the Postgres-backed SME/Screener cron
jobs. `GET /api/market-picks/status` exposes cache metadata (`last_run_at`, `cache_fresh`,
`next_scheduled_at`) with no pipeline run, powering the idle page's "Last scan / Next scheduled
scan" line. `GET /api/market-picks/history` aggregates `output/_history/<date>.json` snapshots
into a per-symbol track record (win rate, Nifty-benchmarked alpha) or, with `?date=`, returns one
day's snapshot verbatim.

**Positions / Portfolio**: originally client-only, now a Postgres-backed `positions` table with
the exact same anonymous-`client_id`-or-account-`user_id` ownership shape as `watchlist_items`
(`routes/positions.py`, reusing `routes/watchlist.py`'s `resolve_owner()`/`owner_column()`
directly — nothing about that resolution is watchlist-specific). `PositionButton` marks a pick as
bought; `PositionsStrip` (on `/market-picks`) and `/portfolio` (aggregate P&L, capital-weighted
stats when `shares` has been filled in) both poll the existing `GET /api/prices` endpoint
client-side — no new backend work for live pricing. See CLAUDE.md's "Positions" and "Portfolio
summary" subsections for the full field/endpoint list.

---

## Request flow: SME Signals

`sme_ema_pipeline.py` is a standalone batch job (own `main()`, PostgreSQL via `DATABASE_URL`) —
not called from `api.py` except via the background-refresh endpoint. Fetches NSE Emerge + BSE SME
stock lists, downloads a year of daily OHLCV per stock via yfinance, computes EMA20/EMA50 (flags
golden/death crosses), RSI(14), volume-spike, liquidity, and market cap, then upserts into
`sme_stocks`/`ema_signals` (idempotent, keyed on symbol + trade_date; ~3 months retained).

`GET /api/sme-signals?view=crosses|regime` serves either cross events in a lookback window or
(regime view) every monitored stock's latest row, plus a 90-day golden-cross follow-through hit
rate computed via a `LEAD(...) OVER (PARTITION BY symbol ...)` window-function query.
`GET /api/sme-signals/{symbol}/history` adds per-cross forward returns (`ret_10d_pct`,
`ret_20d_pct`). `POST /api/sme-signals/refresh` runs the pipeline in the background (409 while
already running, on top of a separate rate limit). Daily cron: `.github/workflows/sme-cron.yml`
(13:00 UTC weekdays). CLI: `--setup-db`, `--reset-db` (drops/recreates **every** table via the
shared `MetaData()` — a disclosed footgun, see CLAUDE.md), `--force`, `--lookback N`.

The DB column is `cross_type` (`CROSS` is a reserved SQL keyword); the API/TS field is `cross`.

---

## Request flow: NIFTY 500 Screener

`screener_pipeline.py` generalizes the SME pattern to the primary NSE/BSE large/mid-cap universe.
Universe is NIFTY 500 specifically (`tools/nifty500_tools.py`, 24h cache), not the full ~2000-symbol
NSE master — a daily per-stock yfinance `.info` scrape is only reasonable at a bounded, curated
scale. No new scraping logic: reuses `tools.nse_tools.get_stock_quote` (price/P-E/market
cap/sector) and `signals.technical.technical_signal` (RSI14 + EMA trend, off the already-cached
`price_history` series), upserting both into `screener_stocks`. `GET /api/screener` filters/sorts
by `industry` (NSE's own published classification — preferred over yfinance's `sector`, whose
GICS-vs-Indian-market taxonomy is a disclosed unverified assumption), `ema_trend`, `pe_max`,
`market_cap_min`, `rsi_min/max`; `sort` is validated against a column whitelist before being
interpolated into `ORDER BY` (can't bind a column name as a parameter). `POST
/api/screener/refresh` mirrors the SME refresh endpoint's 409-then-429 lock ordering. Daily cron:
`.github/workflows/screener-cron.yml` (14:00 UTC weekdays, staggered after `sme-cron.yml`).
`--reset-db` here is correctly scoped to only `screener_stocks.drop()`/`.create()`.

---

## Request flow: EOD Price Store + Corporate Actions

`eod_prices_pipeline.py` is a standalone batch job, same shape as `sme_ema_pipeline.py`/
`screener_pipeline.py` — but ingestion-only, no request-serving endpoint of its own (the first
consumer is the valuation engine below). Two data sources: NSE's daily
`sec_bhavdata_full_DDMMYYYY.csv` bhavcopy (OHLC, previous close, volume, turnover, delivery %) and
AMFI's `NAVAll.txt` (mutual-fund NAVs, filtered to schemes actually held in the Portfolio
Aggregator's `assets` table). `tools/eod_sources.py` does the fetch/parse; raw bhavcopy CSVs are
archived to `output/_bhavcopy/` before parsing (replay without re-hitting NSE). Upserts into
`securities` (symbol/ISIN/company-name/series/last_seen — refreshed from each day's bhavcopy, so a
symbol that stops appearing is detectable as delisted) and `prices_daily` (only `EQ`/`BE`/`BZ`
series; debt/rights series filtered at parse time).

Default run mode is self-healing: ingests any missing date in the last 5 trading days, not just
today, so a cron run that fires before the file is published isn't a permanent gap. CLI:
`--setup-db`/`--reset-db` (scoped to its own 3 tables), `--date YYYY-MM-DD`, `--backfill
YYYY-MM-DD`. Cron: `.github/workflows/eod-prices-cron.yml`, `15 14 * * 1-5` (14:15 UTC / 19:45 IST
weekdays — after the bhavcopy's ~19:00 IST publish, after `sme-cron.yml`).

`corporate_actions_pipeline.py` (`tools/corporate_actions.py` for the fetch/parse) ingests NSE's
corporate-actions feed (splits/bonuses/dividends/rights, parsed from the free-text PURPOSE field —
never guesses a ratio it can't parse cleanly) into `corporate_actions`, then recomputes
`prices_daily.adj_close` for affected symbols. CLI: `--setup-db`/`--reset-db`,
`--backfill`/`--recompute SYMBOL`/`--recompute-all`. Wired into `eod_prices_pipeline.py::run()` as
an isolated step after the bhavcopy/NAV ingestion (own try/except, own `log_event()` — a
corporate-actions failure never affects the pipeline's own exit code or the equity data it already
landed). `portfolio_valuation.py`'s nightly refresh (see below) is wired in the same way, as a
further isolated step after corporate actions — so the run order is always bhavcopy/NAV → adjusted
prices → portfolio valuations.

---

## Request flow: Portfolio Aggregator

A **separate** personal net-worth tracker (`/portfolio-aggregator`) — not to be confused with the
unrelated, already-existing `/portfolio` page (the "I bought this" Market Picks P&L tracker,
backed by the `positions` table above). No auth: profiles are a bare picker with no credentials,
a deliberate personal-scale-tool decision, not an oversight.

- **Foundation** (`routes/portfolio_aggregator.py`, mounted at `/api/portfolio`): `profiles` →
  `accounts` (bank/broker/amc/epfo/other) → `assets` (mf/stock/fd/epf/ppf/cash/manual/loan, with a
  JSON `meta` column absorbing per-type variance — an FD's rate/maturity date, a stock's ISIN) →
  `holdings` (units + avg_cost, mf/stock only) → `valuations` (one row per asset per `as_of` date,
  upserted same-day, accumulating history from day one). `transactions` is schema-only until the
  import flows below populate it. Net worth = sum of each non-archived asset's latest valuation,
  with `loan` assets subtracted. Full CRUD for profiles/accounts/assets + a manual valuation-upsert
  endpoint (422 on a future `as_of` date).
- **Valuation engine** (`portfolio_valuation.py`): `refresh_valuations(engine)` auto-values every
  non-archived `mf`/`stock` asset with a `holdings` row — stock from `prices_daily.close` (live
  yfinance quote as fallback), mf from `mf_nav_daily.nav` — upserting into the same `valuations`
  table the manual-entry path writes to (the engine is authoritative for mf/stock: a nightly run
  overwrites a same-day manual edit on those two types only). Runs nightly as the EOD pipeline's
  last step (above) and on-demand via `POST /api/portfolio/refresh-valuations`. `xirr(cashflows)`
  is a pure Newton's-method-with-bisection-fallback function (bounded rate `[-0.99, 10.0]`, `None`
  on non-convergence or fewer than 2 flows); `GET /api/portfolio/xirr?profile_id=` reports
  per-asset and pooled portfolio XIRR from `transactions` rows (buy → −amount, sell/dividend →
  +amount) plus each asset's latest valuation as the terminal flow — null wherever `transactions`
  has nothing for that asset, which is every asset until one of the two import flows below runs.
- **CAS PDF import** (`cas_import.py`, `POST /api/portfolio/import-cas`): parses a CAMS/KFintech
  detailed CAS statement (`casparser` library; the PDF itself never touches disk, only a
  PII-scrubbed parsed-JSON archive under `output/_cas/`), reconciles against `assets` by AMFI
  scheme code then ISIN, creates missing `mf` assets, sets `holdings.units` to the CAS closing
  balance, and inserts `transactions` tagged `meta.source='cas'` — re-import replaces only those
  tagged rows, never a manually-entered one. Calls `refresh_valuations()` on success so imported
  schemes get an immediate value wherever a NAV already exists.
- **Broker CSV import** (`csv_import.py`, `POST /api/portfolio/import-csv/preview` +
  `POST /api/portfolio/import-csv`): a generic column-mapping importer (Zerodha tradebook
  auto-detected by header signature, any other broker via fuzzy header-name guessing) rather than
  one parser per broker. Appends + dedupes by content-key (date/type/units/amount) rather than
  ever deleting, since tradebook exports are date-ranged partials. On the new-asset path, calls
  **`tools/securities_master.py::resolve_symbol()`** (ISIN → exact code → fuzzy company-name →
  unresolved) to normalize a broker's internal stock code to a canonical NSE/BSE symbol — an exact
  or ISIN match substitutes the resolved symbol; a fuzzy or unresolved match keeps the raw code
  as-is and adds a warning, never silently guessing. `holdings.units` is derived as
  `Σbuy − Σsell` across all of an asset's transactions (any source), floored at 0 with a warning.
- `tools/securities_master.py::get_full_securities_master()` (used by `resolve_symbol` above)
  merges NSE main-board (queried from the `securities` table the EOD pipeline populates) + a new
  BSE main-board fetch + the existing NSE Emerge/BSE SME lists, deduped by ISIN.

---

## Watchlist, Positions & the claim-to-account flow

`watchlist_items` and `positions` (Postgres) share one ownership shape: each row belongs to
*exactly one* identity — an anonymous per-browser `client_id` (UUID in `localStorage`) or,
post-sign-in, an account's `user_id` — enforced by a `CHECK` constraint plus two separate
`UNIQUE` constraints (one per identity column; Postgres treats every row's `NULL` as distinct, so
one combined constraint can't cap either identity independently).

- `routes/watchlist.py::resolve_owner()` is the shared identity-resolution rule (also imported
  directly by `routes/positions.py`): a valid `Authorization: Bearer <token>` session always wins
  over `client_id` when both are present; an invalid/expired token is *not* a 401 here (these
  endpoints don't require sign-in) — it just falls through to `client_id`.
- **No automatic migration on sign-in** — a freshly-signed-in user sees only what their account
  already owns; anonymous rows stay reachable by that same browser's `client_id` while logged
  out. An explicit opt-in **claim flow** exists instead: `POST /api/watchlist/claim` / `POST
  /api/positions/claim` (session-required, real 401 if missing) move a browser's anonymous rows
  onto the signed-in account, oldest-first up to the account's remaining cap, skipping duplicates
  and reporting `skipped_over_cap` rather than silently exceeding the 200-item cap. Both share
  `routes/_shared.py::claim_anonymous_rows_sync()` and are tightly rate-limited (5/hour, not the
  ordinary 60/min) since a leaked `client_id` makes this a meaningfully more sensitive operation
  than an ordinary read/write. The one caller is `/auth/verify`'s success page, which checks both
  counts before redirecting and shows an explicit Claim/Skip prompt.
- `GET /api/watchlist/calendar?symbols=...` is a "what's coming up" roll-up over already-cached
  `filings` per symbol (via `filings_classifier.classify_filings()`) — not a DB query at all, no
  new scrape.
- `frontend/lib/watchlist.ts`'s `useWatchlist()` / `frontend/lib/positions.ts`'s `usePositions()`
  both hold a module-level shared cache + subscriber list so every mounted button on a page (up to
  35 rows on Market Picks) shares one fetch; both are refreshed from `/auth/verify`'s success path
  and `useAuth()`'s `logout()`, since neither hook's cache otherwise learns the caller's identity
  changed.

---

## Compare flow (`/compare?symbols=A,B`)

No new backend — two independent `GET /api/analyse/{symbol}` SSE streams (via a shared
`frontend/lib/useStockAnalysis.ts` hook), each column fetching/progressing independently. Once
both finish, `CompareDiffTable` renders a metric-by-metric table with the stronger side
highlighted, but only for metrics with a documented, unambiguous direction (lower P/E — cheaper;
higher EPS/market cap/yield/signal-score — stronger); an unrecognized `research.ratios` key is
shown side by side with no highlight rather than guessing a direction. Capped at 2 symbols —
`ResultsDashboard`'s internal grid breakpoints are viewport-relative (no container queries), so
`/compare`'s own layout only goes side-by-side at `2xl:`.

---

## Consolidated search (`GET /api/consolidated/{symbol}`)

Pure read-aggregation, no LLM calls, no scraping — three lookups run concurrently via
`asyncio.gather(loop.run_in_executor(...))`: cached `analysis` (24h cache, `None` if never
analyzed), the current Market Picks cache entry for this symbol (`None` if not on the list or
cache stale), and the latest stored SME regime row (`None` if not an SME/Emerge stock or
`DATABASE_URL` unset). Each section is independently `null` — "not yet analyzed" is the expected
common case, not an error. `frontend/components/header-search.tsx` (in every page's nav bar) opens
`ConsolidatedCard` on submit. `GET /api/v1/consolidated/{symbol}` is the same payload gated behind
an API key instead of a browser session — see "Programmatic API access" below.

---

## Account & magic-link auth flow

Passwordless — no OAuth, no signup step separate from first login. `POST
/api/auth/request-link {email}` (rate-limited per-IP and per-address) stores a single-use,
SHA-256-hashed token (15 min expiry) and emails a link to `{FRONTEND_URL}/auth/verify?token=...`.
The `/auth/verify` page requires an explicit button click before calling `GET /api/auth/verify`
(never auto-fires on page load — corporate email link-scanners would otherwise burn the single-use
token before a human clicks it). Verification atomically consumes the token, get-or-creates the
`users` row (first successful click *is* account creation), and issues a 30-day session token
(same hash-only storage). `frontend/app/api/auth/verify/route.ts` is the one proxy route that sets
an httpOnly `SameSite=Lax` session cookie on the Next.js origin — the browser only ever talks to
that origin; every other authenticated proxy route reads the cookie server-side and forwards it to
FastAPI as `Authorization: Bearer <token>`, so `api.py` never sees a cookie. `frontend/lib/auth.ts`'s
`useAuth()` follows the same module-level-shared-cache pattern as watchlist/positions.

---

## Programmatic API access (API keys + tiers)

A signed-in user can mint long-lived API keys (`POST /api/api-keys`, raw key shown exactly once;
`GET /api/api-keys` lists metadata + `tier` + rolling-hour `usage`; `DELETE
/api/api-keys/{id}`), independent of the session-cookie identity. `GET /api/v1/consolidated/{symbol}`
is the one gated route today — a thin wrapper around the same `_consolidated_payload()` helper the
internal endpoint uses, authenticated via `X-API-Key` (deliberately not `Authorization: Bearer`,
which is reserved for session tokens — reusing it would let a forwarded session accidentally pass
this check), rate-limited per-*user* (not per-IP) at a tier-scaled hourly ceiling
(`_TIER_LIMITS = {"free": 100, "pro": 1000}`). **No real payment processing exists** — `users.tier`
is set by an operator by hand; `/pricing` is an informational page stating this plainly, not a
checkout flow. See CLAUDE.md's "Explicitly out of scope" section, item 3.

---

## Signal engine

`signals/engine.py::run_signal_engine(symbol, all_data)` blends six independent signals into one
`SignalResult` (`final_score` in [-1, 1], `verdict` BUY/HOLD/SELL, per-signal `SignalItem`s):

| Signal | Module | Does its own I/O? | Default weight |
|---|---|---|---|
| `valuation` | `signals/valuation.py` | No — reads `features` | 0.4 |
| `growth` | `signals/growth.py` | No | 0.4 |
| `volume` | `signals/volume.py` | No | 0.2 |
| `filings` | `signals/filings.py` (± a small nudge from `filings_classifier.classify_rating_action()`) | No | 0.2 |
| `technical` | `signals/technical.py` | **Yes** — RSI(14) + EMA20/50 posture off a cached `price_history` series | 0.2 |
| `macro` | `signals/macro.py` | **Yes** — FII/DII net flow + RBI repo rate/CPI, cached under a fixed `"_MACRO"` pseudo-symbol (identical for every stock on a given day) | 0.15 |

`technical`/`macro` are the two signals with their own network calls (both against their own
independently-TTL'd caches — `price_history` at 6h, `fii_dii_flow`/`macro_context` at 24h,
decoupled from the six-task cache lockstep). Because of that, `run_signal_engine()` itself is no
longer pure CPU, so every caller on an asyncio event loop (`api.py`'s SSE endpoint) must invoke it
through `loop.run_in_executor()` — the other three callers (`main.py`'s CLI,
`watchlist_alerts.py`'s batch loop, `market_picks_pipeline.py`'s `_phase_research`) already ran
inside a sync script or executor thread, so no change was needed there.

**Sector-aware weight tilts** (`_weights_for_sector()`, keyed off yfinance's `sector` field):
rate-sensitive sectors (Financial Services, Real Estate, Utilities) get valuation/macro weighted
up and growth down; growth sectors (Technology, Communication Services, Healthcare) get growth up
and macro down; cyclical sectors (Basic Materials, Energy, Industrials, Consumer Cyclical) get
technical/volume up and valuation/growth down. Every override reallocates weight from other
signals so each group still sums to the same 1.55 baseline as `_DEFAULT_WEIGHTS`. An unmatched or
`None` sector falls straight through to `_DEFAULT_WEIGHTS` — logged once per distinct unmatched
value (`_log_unmatched_sector_once`, `warning` level) since whether yfinance actually reports this
taxonomy for NSE/BSE symbols was never verified against a live response in this sandbox. Three
grouped buckets, not one override per individual sector — deliberately not presented as a
backtested calibration (see CLAUDE.md's "Sector-aware signal weights" for the full reasoning).

`signals/interpreter.py::interpret()` turns a `SignalResult` into a plain-English string.
`signals/store.py::save_signal()` writes a write-only, 90-day-retained audit trail to
`signals_data/<SYMBOL>/<date>.json` — nothing reads it back. The Market Picks pipeline's
`_compute_confidence()` uses the engine's `final_score`/`verdict` directly as 50% of its own
confidence formula.

---

## LLM analyst layer

`crew.py::run_analysis_with_fallback()` — no CrewAI orchestration, a direct `litellm.completion`
call (CrewAI's `@tool` decorator is the only remaining CrewAI usage anywhere in this repo, purely
for the data-fetching tools' `.run()` calling convention).

**Guardrails** (`_validate_analysis_payload()`): structural checks (enum values, required
non-empty fields, minimum list lengths for `bull_factors`/`bear_factors`/`key_risks`), then, when
a `signal_context` is available, three quant-vs-LLM cross-checks:
1. `final_score > 0.5` + `recommendation == SELL` → rejected (contradicts strong positive signal).
2. `final_score <= -0.6` + `recommendation == BUY` → rejected (symmetric counterpart to #1; `<=`
   matches the signal engine's own inclusive SELL-tier boundary at a rounded value).
3. `abs(final_score) < 0.15` (`_MARGINAL_SCORE_ABS`, well inside the engine's own HOLD band) +
   `confidence == HIGH` → rejected — a HIGH-confidence call isn't supportable when the quant
   engine itself found almost nothing directional.

Then a grounded-claims check (`_analysis_support_issues`) rejects language implying support (e.g.
"strong institutional buying") not backed by the actual source data. A guardrail failure triggers
one corrective LLM retry with the validation error appended.

**Cross-provider failover**: `_attempt_provider()` is one full attempt (its own guardrail retry +
rate-limit retry) against one provider. `run_analysis_with_fallback()` tries the primary
(`_resolve_provider()` — explicit `LLM_PROVIDER` wins, else auto-detected first configured key),
and, **only when `LLM_PROVIDER` was never explicitly set**, one alternate — the first other
configured provider's key found (`_configured_providers()`). An explicit `LLM_PROVIDER` is treated
as a deliberate pin (e.g. data-residency reasons), not just "whichever key happened to be
configured" — a stray second key left in the environment must not silently redirect analysis data
to it on a transient failure of the pinned provider. Only if both attempts fail does
`_safe_analysis_fallback()` return a safe HOLD.

**Degraded-state visibility**: the safe-fallback path sets an internal `_degraded: True` marker;
`main._build_report()` promotes it to a proper sibling `degraded: bool` field on the `Report`
itself (outside `analysis`, so it isn't subject to the four-file analyst-schema lockstep rule —
see "Important Rules" below). `results-dashboard.tsx` renders a "⚠ Analysis degraded" banner when
true, clarifying the scraped market data elsewhere in the report is still real.

**Cost instrumentation** (`llm_cost.py`): every `litellm.completion()` call (guardrail retries and
failed failover attempts included, not just the one that ultimately validates) is logged
(`llm_call_cost` event) and accumulated into `output/_llm_cost/<date>.json`
(`call_count`/`total_cost_usd`/`calls_with_unknown_cost`), guarded by an `fcntl.flock`-based
cross-process lock so concurrent backend workers can't undercount via a lost update.
`estimate_cost_usd()` wraps `litellm.completion_cost()` and never guesses — a model litellm has no
pricing data for degrades to `None`, never a fabricated number.

---

## Caching architecture (`cache.py`)

File-based JSON cache under `output/<SYMBOL>/<task>.json`, one file per (symbol, task), each
carrying a `_meta.fetched_at` timestamp that freshness is always re-derived from (never trusted
from a store's own expiry mechanism). Optional Redis backing (`REDIS_URL`) exists purely to close
a multi-host gap: without a shared disk volume, a second backend replica would otherwise fork
every cache independently, multiplying scraper load on already rate-limit-sensitive sources.

- `save()` writes through to Redis (`SET ... EX <ttl>`) **in addition to** local disk, never
  instead of it — disk stays the persistent store on a single-host deployment, a fast local mirror
  once Redis is configured.
- `load()`/`is_fresh()` check Redis first; they fall back to local disk **only when Redis has no
  entry at all** for that key. A stale/failed Redis entry is trusted as-is, never overridden by a
  possibly-fresher local disk copy — falling through there would silently reintroduce the same
  per-host fork this feature exists to close.
- A Redis read/write failure logs a warning and falls back to disk for that one call — same
  graceful-degradation convention as every other optional-infra module in this codebase.

Current TTL map (`cache.TTL_HOURS`): `stock_info` 1h, `news` 1h, `research` 24h, `analysis` 24h,
`shareholding` 168h, `mf_holdings` 168h, `price_history` 6h, `peers` 24h, `financials` 24h,
`index_history` 24h, `insider_activity` 24h, `fii_dii_flow` 24h, `macro_context` 24h,
`street_consensus` 24h. Market Picks caches separately: `output/_market_picks/picks.json` (7
days), `output/_extract_cache/<hash>.json` (6h, content-aware key), `output/_nse_master.txt` (24h
list), `output/_history/<date>.json` (permanent, one snapshot per day).

---

## Persistence: PostgreSQL

SQLAlchemy Core (not an ORM) — all tables declared against one shared `MetaData()` object in
`db/models.py`:

| Table | Powers |
|---|---|
| `sme_stocks`, `ema_signals` | SME Signals |
| `screener_stocks` | NIFTY 500 Screener |
| `watchlist_items` | Watchlist |
| `positions` | Positions / Portfolio (the "I bought this" P&L tracker) |
| `verdict_history` | Verdict timeline strip on the stock analysis hero |
| `mf_holdings_history` | Quarterly MF stake-delta badges |
| `users`, `magic_links`, `sessions` | Magic-link auth |
| `api_keys` | Programmatic API access |
| `securities`, `prices_daily`, `mf_nav_daily` | EOD price store (bhavcopy OHLCV + AMFI NAV) |
| `corporate_actions` | Splits/bonuses/dividends, feeds `prices_daily.adj_close` |
| `profiles`, `accounts`, `assets`, `holdings`, `valuations`, `transactions` | Portfolio Aggregator (the separate net-worth tracker — see its own section below) |

21 tables total (`grep -c "= Table(" db/models.py`).

**Schema-of-record process**: Alembic (`alembic.ini` + `migrations/env.py`, targeting
`db.models.metadata` directly — no second, Alembic-specific model layer). Three revisions today:
`0001_baseline_schema.py` (autogenerated against an empty database, the original 11 tables
`db/schema.sql` already produced by hand), `684c8a31e7e0_add_eod_price_store_and_corporate_.py`
(the 4 EOD/corporate-actions tables), and `8613aafc2d9d_add_portfolio_aggregator_foundation_.py`
(the 6 Portfolio Aggregator tables) — each verified to `upgrade head`/`downgrade base` cleanly
against an isolated scratch database before landing. **Existing deployments must `alembic stamp
head`, not `upgrade head`,** for the first revision only — they already have those 11 tables (via
`db/schema.sql` or a pipeline's own `--setup-db`), so replaying `CREATE TABLE` would fail on the
first statement; the two later revisions apply normally via `upgrade head`. From here on, schema
changes are authored as new Alembic revisions (`alembic revision --autogenerate`), not hand-edited
into `db/schema.sql` — that file is kept only as a frozen historical reference and for the two
tables `tests/test_schema_sql_migrations.py` still exercises its old guard-convention against.

Each pipeline's own `--reset-db` CLI flag varies in blast radius: `screener_pipeline.py --reset-db`
is scoped to just `screener_stocks`; `sme_ema_pipeline.py --reset-db` calls
`metadata.drop_all()`/`create_all()` against the *shared* `MetaData()`, so it drops every table in
the database, not just its own two — a disclosed, not-yet-fixed footgun (see CLAUDE.md).

---

## Route module extraction (`routes/`)

`api.py` is still the majority of the backend (~2900+ lines, ~28 routes) — three domains have been
split out into `APIRouter` modules so far (the first two were the most duplicated; the third,
`portfolio_aggregator.py`, is a large, self-contained new domain that made more sense as its own
router from the start than as more inline `api.py` routes):

```text
routes/
├── _shared.py                run_owned_db_call() — the rate-limit → 503-if-no-DATABASE_URL →
│                              run_in_executor → sanitize-error wrapper multiple domains share;
│                              also claim_anonymous_rows_sync() (watchlist/positions claim flow)
├── watchlist.py               /api/watchlist, /api/watchlist/calendar, /api/watchlist/claim
│                              + resolve_owner()/owner_column()/WatchlistOwner (shared identity
│                              resolution, imported directly by positions.py)
├── positions.py               /api/positions, /api/positions/{symbol} (PATCH shares),
│                              /api/positions/claim, /api/portfolio/concentration
│                              (sector-concentration overlay for Market Picks — reads this
│                              table only, unrelated to the Portfolio Aggregator below despite
│                              the shared `/api/portfolio` prefix)
└── portfolio_aggregator.py    /api/portfolio/{profiles,accounts,assets,networth,
                               refresh-valuations,xirr,import-cas,import-csv...} — the
                               Portfolio Aggregator net-worth tracker, see its own section below
```

All three routers `import api` (not `from api import X`) to reach shared state (`_get_db_engine`,
`_rate_limit`, `LOGGER`, `log_event`) via dotted access at call time — this avoids a circular
import (`api.py` registers these routers, so they can't import `api.py`'s names at their own
module top-level before those names exist) and preserves existing `unittest.mock.patch("api.X",
...)` test conventions, which only intercept lookups through the module object, not a name a
`from X import Y` already copied at import time. `api.py` re-exports
`_MAX_WATCHLIST_ITEMS_PER_CLIENT`/`_MAX_POSITIONS_PER_CLIENT` for backward compatibility with
existing tests that read them as plain values.

Everything else — SME signals, Screener, Market Picks, auth, API keys, financials, peers, insider
activity, street consensus, verdict history, consolidated view, symbol validation, prices — is
still defined inline in `api.py`. Splitting further is disclosed future work, not attempted in
this pass (same "first increment, not the full file" scope call as `tests_live/`'s scraper
coverage).

---

## Shared-state & guard infrastructure

Three pieces of backend guard state were originally single-process/in-memory, which silently
became *per-worker* the moment the backend scaled past one process. All three now optionally share
state through Redis, with an in-memory fallback:

- **`rate_limiter.py`** — `is_allowed()` (sliding-window rate limit), `try_acquire_slot()`/
  `release_slot()` (named concurrency ceiling, e.g. the LLM-call slot), `try_acquire_lock()`/
  `release_lock()` (single-run lock, e.g. the SME/Screener refresh guard). Redis-backed via small
  Lua scripts / `SET NX EX` for atomic check-and-set; falls back to the original in-memory
  implementation when `REDIS_URL` is unset or a Redis call fails. `get_usage_count()` is a
  non-mutating peek (used by the API-keys usage dashboard) that doesn't itself count as a call.
  A Redis-held slot/lock carries a TTL so a crashed worker can't strand it permanently.
- **Trusted client IP** (`api.py::_client_ip()`): every request arrives via the Next.js proxy
  server-to-server, so `request.client.host` is always the Next.js server's own IP — collapsing
  every per-IP limiter into one shared site-wide bucket. `_client_ip()` only trusts a caller-
  supplied `X-Forwarded-For` value when the request also presents a matching
  `TRUSTED_PROXY_SECRET` via `X-Internal-Proxy-Secret` (set on both processes); otherwise it falls
  back to `request.client.host` unchanged. `frontend/lib/proxy-headers.ts::clientIpHeaders()` is
  the frontend half, merged into every proxy route's outbound fetch.
- **`observability.py` + `error_tracking.py`** — `log_event()` is the one structured-JSON-logging
  entry point every module in this codebase calls; its error-level path optionally forwards
  `(event, fields, exc)` to `error_tracking.capture_error()` when `SENTRY_DSN` is set (unset by
  default — zero behavior change out of the box). Pluggable in the sense of "any Sentry-protocol-
  compatible ingest endpoint," not a multi-backend registry. `init_error_tracking()` is called once
  per process at every CLI/server entry point; idempotent, since `sentry_sdk.init()` isn't safe to
  call twice. A broken/unreachable Sentry backend can never break the primary log line itself.
- **`schema_drift.py`** — for the six `ALL_DATA_TASKS` slices only: `schemas.CONTRACTS`'s optional
  `"types"` map (`{field: dict|list}`) is the single source of truth; `check_drift()` flags a field
  that's *present but the wrong shape* (never a field that's legitimately absent, per this
  codebase's "never invent" convention). Wired into `main._fetch_task()`, the one choke point every
  entry point already goes through; logs at `warning`, never raises.
- **`source_health.py`** — for the 20 Market Picks sources + the two macro-overlay fetches: tracks
  a per-source daily ok/not-ok result under `output/_source_health/`, warning once a source with an
  established healthy baseline (≥5 prior days) has failed 3 consecutive *days*. Time-normalized
  (same-day repeat calls collapse to one data point) and lock-guarded (`fcntl.flock`) against
  concurrent writers racing on the same source file.
- **`scraper_error_counters.py`** — for the standalone per-symbol endpoints (`peers`,
  `financials`, `insider_activity`'s two sub-fetches, `street_consensus`'s two sub-fetches), where
  an empty result is the expected common case and volume-anomaly detection would just be noise.
  Distinguishes a genuine `{"error": ...}` tool result from a legitimate empty one and logs/counts
  only the former — a grep-able counter file plus a log line, not a metrics platform.

### SSE bridge pattern (critical)

```python
async def _launch():
    await loop.run_in_executor(None, blocking_fn)

asyncio.create_task(_launch())   # create_task needs a coroutine, not a Future
```

Never pass `loop.run_in_executor(...)` directly to `create_task` — it returns a `Future`, not a
coroutine, and raises `TypeError` at runtime. Every SSE endpoint in this codebase (`/api/analyse`,
`/api/market-picks`) follows this shape.

---

## CLI

`python main.py <SYMBOL>` (`--force` to bypass cache) runs the identical fetch → normalize →
signal-engine → analyst flow the web app's SSE endpoint runs, sharing `main._fetch_task()` and
`main._build_report()` with `api.py` — the two entry points cannot drift on report shape. Also
saves a dated `report_<date>.json` to `output/<SYMBOL>/`. Batch pipelines each have their own CLI
too: `sme_ema_pipeline.py`, `screener_pipeline.py`, `market_picks_pipeline.py` (`main()`, for a
self-hosted crontab alternative to the GitHub Actions cron), `watchlist_alerts.py` (`--force`),
`eod_prices_pipeline.py` (`--setup-db`/`--reset-db`/`--date`/`--backfill`),
`corporate_actions_pipeline.py` (`--setup-db`/`--reset-db`/`--backfill`/`--recompute`/
`--recompute-all`), `portfolio_valuation.py` (standalone-runnable, no flags — just runs
`refresh_valuations()` once), and `cas_import.py --replay <archived-json> --account-id N` (re-runs
a CAS import from a previously-archived parse, for debugging/recovery without re-uploading the PDF).

---

## File layout

```text
stock-research/
├── api.py                     FastAPI server — ~28 routes, SSE endpoints, symbol validation,
│                               shared helpers routes/ and _shared.py depend on
├── main.py                    CLI entry point; _fetch_task/_build_report shared with api.py
├── crew.py                    Analyst guardrails, cross-provider failover, run_analysis_with_fallback
├── llm_cost.py                Per-call LLM cost instrumentation + running daily total
├── cache.py                   File-based TTL cache, optional Redis write-through/read-first
├── rate_limiter.py            Shared-state (Redis or in-memory) rate limits, slots, locks
├── schemas.py                 Normalization contracts: raw tool output → canonical dicts
├── schema_drift.py            Type-drift detection for the six ALL_DATA_TASKS slices
├── source_health.py           Freshness/volume monitoring for Market Picks' 20 sources + macro
├── scraper_error_counters.py  Error counters for the 4 standalone per-symbol scrapers
├── observability.py           Structured JSON logging (log_event())
├── error_tracking.py          Optional Sentry-compatible hook, wired into log_event()
├── peer_analytics.py          Peer-percentile + absolute valuation-anchor math (shared by
│                               api.py's /api/peers and market_picks_pipeline.py's _phase_research)
├── dcf_valuation.py           Deterministic two-stage DCF off cash-flow statement data
├── verdict_history.py         Daily verdict/price snapshots (Postgres) — verdict timeline strip
├── mf_holdings_history.py     Quarterly MF stake snapshots (Postgres) — stake-delta badges
├── auth.py                    Magic-link auth: token/session/API-key issuance + validation
├── email_sender.py            Magic-link + watchlist-alert emails over generic SMTP
├── watchlist_alerts.py        Daily batch job: emails users on a watched stock's rec change
├── market_picks_pipeline.py   6-phase multi-agent weekly picks pipeline
├── sme_ema_pipeline.py        SME golden/death cross batch pipeline (Postgres)
├── screener_pipeline.py       NIFTY 500 custom screener batch pipeline (Postgres)
├── eod_prices_pipeline.py     NSE bhavcopy + AMFI NAV ingestion; also runs corporate-actions
│                               and portfolio-valuation as isolated final steps
├── corporate_actions_pipeline.py  NSE corporate-actions ingestion + adj_close recompute
├── source_quality.py          Per-run Market Picks source telemetry (yield, dedup, extraction rate)
├── source_quality_report.py   CLI aggregating source_quality.py's per-run JSON files
├── portfolio_valuation.py     Portfolio Aggregator: refresh_valuations(), xirr(), xirr_report()
├── cas_import.py              CAMS/KFintech CAS PDF import → Portfolio Aggregator transactions
├── csv_import.py              Generic broker-CSV import (Zerodha preset) → transactions
├── requirements.txt
├── alembic.ini                Schema-migration config
├── migrations/                env.py + versions/ (0001_baseline_schema,
│                               684c8a31e7e0_add_eod_price_store_and_corporate_,
│                               8613aafc2d9d_add_portfolio_aggregator_foundation_)
├── db/
│   ├── models.py               SQLAlchemy Core tables (one shared MetaData(), 21 tables)
│   └── schema.sql               Frozen pre-Alembic reference; still tested for 2 tables' guards
├── routes/                    Extracted APIRouter modules — see "Route module extraction" above
│   ├── _shared.py
│   ├── watchlist.py
│   ├── positions.py            Also /api/portfolio/concentration (sector-concentration overlay)
│   └── portfolio_aggregator.py Portfolio Aggregator CRUD + valuation/XIRR + CAS/CSV import
├── config/
│   ├── analyst.json            Analyst role/goal/backstory + output_schema + section labels
│   └── crew_tasks.py            Builds the analyst prompt from analyst.json
├── signals/                   Quantitative signal engine
│   ├── engine.py                run_signal_engine(), sector-aware weight tilts
│   ├── features.py              Feature extraction from normalized data
│   ├── valuation.py / growth.py / volume.py / filings.py   Signals reading `features` only
│   ├── technical.py             RSI14 + EMA20/50 posture — own I/O (price_history cache)
│   ├── macro.py                 FII/DII flow + RBI rate/CPI — own I/O ("_MACRO" pseudo-symbol cache)
│   ├── filings_classifier.py    Corporate actions / rating action / next-results-date text classifier
│   ├── interpreter.py           SignalResult → plain-English string
│   └── store.py                 Write-only 90-day audit trail (signals_data/<SYMBOL>/<date>.json)
├── tools/                     Data-fetching functions (never raise — return {"error": ...})
│   ├── nse_tools.py              yfinance quote + NSE API + best-effort XBRL EPS fallback
│   ├── screener_tools.py         Fundamentals, peers, valuation band, statements, concalls
│   ├── news_tools.py             gnews wrapper
│   ├── nse_filings_tools.py      Corporate announcements
│   ├── market_picks_tools.py     RSS + GNews scrapers (merges in hdfc_sec_agent.py's sources)
│   ├── hdfc_sec_agent.py         HDFC Securities scrapers
│   ├── sme_tools.py              NSE Emerge + BSE SME stock-list fetchers
│   ├── nifty500_tools.py         NIFTY 500 constituent list (screener_pipeline.py's universe)
│   ├── nse_insider_trades.py / nse_bulk_block_deals.py   PIT + bulk/block deal feeds
│   ├── nse_fii_dii_tools.py      Daily FII/DII net equity flow
│   ├── macro_context_tools.py    RBI repo rate + CPI inflation
│   ├── trendlyne_agent.py        GNews search for Trendlyne-cited coverage
│   ├── trendlyne_scraper.py      Direct trendlyne.com numeric consensus scrape
│   ├── price_history_tools.py    Shared daily-close OHLCV fetch (sparklines, technical signal)
│   ├── screener_scanner.py       (peer/valuation-band table parsing helpers)
│   ├── eod_sources.py            NSE bhavcopy + equity-master fetch/parse, AMFI NAV fetch/parse
│   ├── corporate_actions.py      NSE corporate-actions fetch + PURPOSE-string parser
│   ├── securities_master.py      NSE+BSE main-board + SME merge, resolve_symbol() (broker-code
│   │                               → canonical symbol; consumed by csv_import.py)
│   └── _nse_session.py           Shared NSE session-priming helper every NSE module delegates to
├── tests/                     unittest-based, no live network calls
├── tests_live/                Opt-in (RUN_LIVE_TESTS=1), weekly-cron-only live contract checks
├── .github/workflows/         market-picks-cron, sme-cron, screener-cron, watchlist-alerts-cron,
│                               eod-prices-cron, live-contract-check
├── frontend/                  Next.js 15 (TypeScript, Tailwind, App Router)
│   ├── app/
│   │   ├── page.tsx               Stock analysis (?symbol= deep links)
│   │   ├── compare/page.tsx       Two reports side by side + diff table
│   │   ├── market-picks/page.tsx
│   │   ├── sme-signals/page.tsx
│   │   ├── screener/page.tsx
│   │   ├── watchlist/page.tsx
│   │   ├── portfolio/page.tsx      "I bought this" P&L tracker (unrelated to the below)
│   │   ├── portfolio-aggregator/page.tsx  Separate net-worth tracker — profiles/accounts/
│   │   │                           assets/valuations/XIRR, CAS + broker-CSV import
│   │   ├── api-keys/page.tsx
│   │   ├── pricing/page.tsx
│   │   ├── login/page.tsx         Magic-link request form
│   │   ├── auth/verify/page.tsx   Consumes ?token=, claim-my-data prompt
│   │   ├── manifest.ts, icon.tsx, apple-icon.tsx, manifest-icons/[size]/route.tsx  PWA assets
│   │   └── api/                   ~30 thin proxy routes → FastAPI (adds client-IP + auth headers)
│   ├── components/                One file per card/domain (see "Dashboard component extraction"
│   │                               in CLAUDE.md) — results-dashboard.tsx, financial-statements-card,
│   │                               peer-comparison-card, insider-activity-card, street-consensus-card,
│   │                               verdict-timeline, quarterly-trend-card, price-sparkline,
│   │                               market-picks-dashboard, positions-strip, watchlist-button,
│   │                               position-button, header-search, consolidated-card, auth-widget,
│   │                               site-nav, ema-chart, sector-heatmap, service-worker-registration
│   ├── lib/                       watchlist.ts, positions.ts, auth.ts, auth-cookie.ts,
│   │                               useStockAnalysis.ts, proxy-headers.ts
│   ├── e2e/                       Playwright specs — every backend response mocked at page.route()
│   └── types/index.ts             Canonical TS types for every SSE message + report field
└── output/                    Cache files (gitignored) + CLI report JSON
    ├── <SYMBOL>/                 Per-symbol task caches
    ├── _extract_cache/           LLM extraction cache (6h TTL)
    ├── _history/                 Daily Market Picks snapshots
    ├── _market_picks/            Market picks result cache (7-day TTL)
    ├── _llm_cost/                Daily running LLM-cost totals
    ├── _source_health/           Per-source daily ok/not-ok history
    ├── _scraper_error_counters/  Per-scraper error counts
    ├── _source_quality/          Per-run Market Picks source telemetry JSON
    ├── _bhavcopy/                Raw NSE bhavcopy CSV archive (EOD price store replay)
    ├── _cas/                     PII-scrubbed parsed CAS-import JSON archive (replay)
    └── _nse_master.txt           NSE equity symbol master (24h refresh)
```

---

## Important invariants (see CLAUDE.md's "Important Rules for Claude" for the full list)

- **Schema boundary**: raw tool output must go through `schemas.normalize()` before reaching
  cache, guardrails, signal engine, or the analyst prompt.
- **Four-file lockstep**: the analyst JSON output schema (`config/analyst.json`'s `output_schema`,
  `crew._validate_analysis_payload()`, `main._build_report()`, `frontend/types/index.ts`'s
  `Analysis` interface) must change together.
- **Tools never raise** — every `tools/*.py` function returns `{"error": ...}` on failure; the
  cache layer discards error payloads, guardrails detect and retry on them.
- Cache TTLs, the 35-stock Market Picks cap, the 4-tier BUY/WATCHLIST/HOLD/SELL recommendation,
  and deterministic (non-LLM) trade levels are all deliberate and documented — don't casually
  change them.
