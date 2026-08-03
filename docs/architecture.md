# Architecture

System-level reference: how the pieces fit together, module boundaries, request/data flow. For
per-feature depth (exact formulas, edge cases, disclosed limitations, the historical "why" behind a
design choice) see **`backend/CLAUDE.md`** — the engineering ground truth this document summarizes
and links into rather than duplicating. Frontend detail lives in `frontend/CLAUDE.md`.

## System overview

A FastAPI backend (`backend/api.py` + `backend/routes/`) talks to yfinance, Screener.in, NSE, BSE,
AMFI, Trendlyne, RBI, and Google News, normalizes what it scrapes, runs a deterministic quant
signal engine over it, and (for the flagship single-stock flow) calls an LLM for a structured
recommendation. It serves **61 HTTP routes** (29 in `api.py`, 5 + 6 + 21 across the three
extracted `routes/` modules; 59 of them under `/api/*`, plus `/` and `/health`). A
Next.js 15 frontend never talks to FastAPI directly — every call goes through a same-shaped proxy
route under `frontend/app/api/*` first. PostgreSQL (via SQLAlchemy Core, migrated with Alembic)
is the shared, persistent store for anything cross-session: accounts, watchlist, positions,
verdict history, SME/screener batch results. Redis is optional, additive shared state for
rate limiting and cache sharing once a deployment runs more than one backend worker/host; every
Redis-backed module degrades to an in-memory/local-disk equivalent when `REDIS_URL` is unset, so
a single-process deployment behaves identically with or without it.

Five user-facing modes share this backend:

| Mode | Entry point | Backing store | LLM? |
|---|---|---|---|
| Stock analysis | `GET /api/analyse/{symbol}` (SSE) | File cache (`output/<SYMBOL>/`) + Postgres (`verdict_history`, `mf_holdings_history`) | yes — 1 analyst call |
| Market Picks | `GET /api/market-picks` (SSE) | File cache (`output/_market_picks/`) + PostgreSQL (`app_state`, daily snapshots) | yes — extraction + batched analysis |
| SME Signals | `GET /api/sme-signals` | Postgres (`sme_stocks`, `ema_signals`) | no |
| NIFTY 500 Screener | `GET /api/screener` | Postgres (`screener_stocks`) | no |
| Portfolio Aggregator | `GET /api/portfolio/*` | Postgres (`profiles`…`transactions`, `prices_daily`, `mf_nav_daily`) | no |

Cross-cutting: a **Watchlist** (star any stock from any of the first four modes), an **account
system** (magic-link auth — optional; anonymous `client_id` usage works everywhere), and a shared
**search box** (`GET /api/consolidated/{symbol}`) that aggregates whatever those modes have already
cached for one symbol, with zero new fetching. Behind all of it, an **EOD price store** ingests
NSE bhavcopy + AMFI NAV nightly — no endpoint of its own, it exists to feed the valuation engine.

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

Step 2's six data slices are `main.ALL_DATA_TASKS`: `stock_info`, `research`, `news`,
`shareholding`, `mf_holdings`, `filings` (see CLAUDE.md's "Agent architecture" table for the
tool/source mapping). Everything else on the report page is a **standalone on-demand endpoint** —
own cache entry, outside the six-task TTL lockstep, fetched by the frontend only after the main
report has loaded, so none of them can delay or fail the SSE stream:

| Endpoint | Adds | Cache TTL |
|---|---|---|
| `GET /api/peers/{symbol}` | Peer percentile ranking + `absolute_anchor` (own P/E vs. own 3-5y history) — `analytics/peer_analytics.py` | 24h |
| `GET /api/financials/{symbol}` | Multi-year Income Statement/Balance Sheet/Cash Flow + `dcf` (deterministic two-stage DCF, `portfolio/dcf_valuation.py`) + `concalls` | 24h |
| `GET /api/shareholding-detail/{symbol}` | Individually-named shareholders (promoters + every other category) from NSE's quarterly shareholding XBRL — the granular counterpart to the aggregate `shareholding` slice | 168h |
| `GET /api/insider-activity/{symbol}` | Promoter/director PIT disclosures + bulk/block deals, scoped to one symbol (90-day window) | 24h |
| `GET /api/street-consensus/{symbol}` | GNews articles citing Trendlyne + a scraped numeric consensus (rating/analyst count/target price) from trendlyne.com directly | 24h |
| `GET /api/verdict-history/{symbol}` | Timeline of stored daily verdicts, each BUY/SELL entry scored win/loss against today's live price | — (Postgres) |
| `GET /api/prices/history/{symbol}?benchmark=true` | Daily-close sparkline series, optionally diffed against Nifty50 over the same window | 6h |

All degrade to `null`/`[]`/an empty section rather than failing the page. A genuine scrape
*failure* is deliberately **not** cached (retried next request) and increments a
`telemetry/scraper_error_counters.py` counter; a legitimately empty result is cached normally. See
CLAUDE.md's per-flow sections for exact shapes and the disclosed scraper-verification caveats.

**Filings classification**: `signals/filings_classifier.py` runs pure text classification (no new
scrape) over the same `filings` list — corporate actions (dividend/split/bonus/buyback), the most
recent rating action, and the next results date. `main._build_report()` adds this as
`filings_summary`; `signals/filings.py::filings_signal()` separately calls
`classify_rating_action()` to nudge the filings *signal's* score.

**MF holdings trend**: `analytics/mf_holdings_history.py` (Postgres) snapshots the `mf_holdings` task's
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

Six sequential phases inside one `MarketPicksPipeline.run()`, all blocking work in a
`ThreadPoolExecutor`, bridged to the SSE stream via `asyncio.Queue` + `loop.call_soon_threadsafe`:

| Phase | Does | Fan-out |
|---|---|---|
| `_phase_scrape` | Fetches **20 sources** — 5 direct RSS (ET Markets, LiveMint, NDTV Profit, Hindu BusinessLine, Zerodha Z-Connect), 12 GNews-mediated (Moneycontrol/BS/FE, 3 global-brokerage groups, 3 India-brokerage groups, 2 HDFC Securities, Trendlyne), 3 structured (NSE bulk/block deals, NSE insider trades, Screener.in fundamental screen). `SOURCES`/`SCRAPER_FNS` in `tools/market_picks_tools.py` merge the five satellite modules' `*_SOURCES`/`*_SCRAPERS` exports at import time | 6 workers |
| `_phase_extract` | One LLM call per source; checks `output/_extract_cache/` (6h, content-aware key) first; Jaccard ≥ 0.60 syndication detection down-weights the same story appearing across sources | 6 workers |
| `_phase_consolidate` | Groups by ticker, validates against the NSE equity master, confirms a live yfinance price (guards pre-IPO/unlisted names), rapidfuzz company-name matching | — |
| `_phase_research` | Per candidate: `stock_info` + `research` + `run_signal_engine()` + absolute valuation anchor via `peer_analytics.build_peer_result()` — sharing the same `"peers"` cache entry as `GET /api/peers/{symbol}`, so a re-scan doesn't re-scrape Screener for every candidate | 3-4 workers, ≤ `_MAX_STOCKS` (35) |
| `_phase_analyze` | Batched LLM calls (8 stocks/batch) for qualitative summary + bull/bear factors. Never asked for prices | parallel |
| `_phase_score` | Deterministic: `_compute_confidence()` = 50% signal engine + 30% consensus + 20% recency, ±3 valuation-percentile nudge, clamped 0-100. The 4-tier rec is a *separate* formula (`0.55 × consensus + 0.45 × signal_score`, thresholded, with a quant-veto demoting BUY → WATCHLIST on a strongly negative score). Entry/target/stop from price + signal score, never the LLM. `_apply_sector_balance()` caps 2 per sector in the primary list. Writes one daily snapshot to `app_state` (`market_picks_history`) | — |

Two health gates set `self.healthy = False` (logged, and surfaced so a CLI/cron run can fail
loudly rather than "succeed" with empty data): more than 70% of sources returning zero articles
(`_MAX_ACCEPTABLE_EMPTY_SOURCE_RATE`), or zero picks surviving consolidation. After `_phase_score`,
`_aggregate_source_stats()` → `source_quality.record_run()` writes per-source articles-fetched /
picks-extracted / picks-validated telemetry for the run — see "Observability" below.

```text
Browser → EventSource → /api/market-picks[?force=true] → Next.js proxy → FastAPI :8000
  1. Check output/_market_picks/picks.json (7-day TTL) → cache hit: emit `done` immediately
  2. Cache miss: MarketPicksPipeline.run() wrapped in run_in_executor
  3. Pipeline's on_event() → loop.call_soon_threadsafe(q.put_nowait, ...) → SSE
  4. Six phases run synchronously inside the executor thread
  5. Result saved via market_picks_pipeline.save_picks_cache()
```

`GET /api/market-picks/status` exposes cache metadata (`last_run_at`, `cache_fresh`,
`next_scheduled_at`) with no pipeline run, powering the idle page's "Last scan / Next scheduled
scan" line. `GET /api/market-picks/history` aggregates the stored daily snapshots
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

`pipelines/sme_ema_pipeline.py` is a standalone batch job (own `main()`, PostgreSQL via `DATABASE_URL`) —
not called from `api.py` except via the background-refresh endpoint. Fetches NSE Emerge + BSE SME
stock lists, downloads a year of daily OHLCV per stock via yfinance, computes EMA20/EMA50 (flags
golden/death crosses), RSI(14), volume-spike, liquidity, and market cap, then upserts into
`sme_stocks`/`ema_signals` (idempotent, keyed on symbol + trade_date; ~3 months retained).

`GET /api/sme-signals?view=crosses|regime` serves either cross events in a lookback window or
(regime view) every monitored stock's latest row, plus a 90-day golden-cross follow-through hit
rate computed via a `LEAD(...) OVER (PARTITION BY symbol ...)` window-function query.
`GET /api/sme-signals/{symbol}/history` adds per-cross forward returns (`ret_10d_pct`,
`ret_20d_pct`). `POST /api/sme-signals/refresh` runs the pipeline in the background (409 while
already running, on top of a separate rate limit). CLI: `--setup-db`, `--reset-db`
(drops/recreates **every** table via the shared `MetaData()` — a disclosed footgun, see CLAUDE.md),
`--force`, `--lookback N`.

The DB column is `cross_type` (`CROSS` is a reserved SQL keyword); the API/TS field is `cross`.

---

## Request flow: NIFTY 500 Screener

`pipelines/screener_pipeline.py` generalizes the SME pattern to the primary NSE/BSE large/mid-cap universe.
Universe is NIFTY 500 specifically (`tools/nifty500_tools.py`, 24h cache), not the full ~2000-symbol
NSE master — a daily per-stock yfinance `.info` scrape is only reasonable at a bounded, curated
scale. No new scraping logic: reuses `tools.nse_tools.get_stock_quote` (price/P-E/market
cap/sector) and `signals.technical.technical_signal` (RSI14 + EMA trend, off the already-cached
`price_history` series), upserting both into `screener_stocks`. `GET /api/screener` filters/sorts
by `industry` (NSE's own published classification — preferred over yfinance's `sector`, whose
GICS-vs-Indian-market taxonomy is a disclosed unverified assumption), `ema_trend`, `pe_max`,
`market_cap_min`, `rsi_min/max`; `sort` is validated against a column whitelist before being
interpolated into `ORDER BY` (can't bind a column name as a parameter). A `NULL` metric excludes
that stock from that filter rather than being guessed a value. The response's `industries` field is
the real currently-populated set, so the frontend's filter chips are never a hardcoded list.
`POST /api/screener/refresh` mirrors the SME refresh endpoint's 409-then-429 lock ordering.
`--reset-db` here is correctly scoped to only `screener_stocks.drop()`/`.create()`.

---

## Request flow: EOD Price Store + Corporate Actions

`pipelines/eod_prices_pipeline.py` is a standalone batch job, same shape as `pipelines/sme_ema_pipeline.py`/
`pipelines/screener_pipeline.py` — but ingestion-only, no request-serving endpoint of its own (the first
consumer is the valuation engine below). Two data sources: NSE's daily
`sec_bhavdata_full_DDMMYYYY.csv` bhavcopy (OHLC, previous close, volume, turnover, delivery %) and
AMFI's `NAVAll.txt` (mutual-fund NAVs, filtered to schemes actually held in the Portfolio
Aggregator's `assets` table). `tools/eod_sources.py` does the fetch/parse; raw bhavcopy CSVs are
archived to `output/_bhavcopy/` before parsing (replay without re-hitting NSE). Upserts into
`securities` (symbol/ISIN/company-name/series/last_seen — refreshed from each day's bhavcopy, so a
symbol that stops appearing is detectable as delisted) and `prices_daily` (only `EQ`/`BE`/`BZ`
series; debt/rights series filtered at parse time).

Default run mode is self-healing: `_missing_dates()` ingests any gap in the last `_GAP_WINDOW` (5)
weekdays, not just today, so a cron run that fires before the file is published isn't a permanent
hole. CLI: `--setup-db`/`--reset-db` (scoped to its own 3 tables), `--date YYYY-MM-DD`,
`--backfill YYYY-MM-DD`. Scheduled after the bhavcopy's ~19:00 IST publish — see the cron table.

`pipelines/corporate_actions_pipeline.py` (`tools/corporate_actions.py` for the fetch/parse) ingests NSE's
corporate-actions feed (splits/bonuses/dividends/rights, parsed from the free-text PURPOSE field —
never guesses a ratio it can't parse cleanly) into `corporate_actions`, then recomputes
`prices_daily.adj_close` for affected symbols. CLI: `--setup-db`/`--reset-db`,
`--backfill`/`--recompute SYMBOL`/`--recompute-all`. Wired into `pipelines/eod_prices_pipeline.py::run()` as
an isolated step after the bhavcopy/NAV ingestion (own try/except, own `log_event()` — a
corporate-actions failure never affects the pipeline's own exit code or the equity data it already
landed). `portfolio/portfolio_valuation.py`'s nightly refresh (see below) is wired in the same way, as a
further isolated step after corporate actions — so the run order is always bhavcopy/NAV → adjusted
prices → portfolio valuations.

---

## Request flow: Portfolio Aggregator

A **separate** personal net-worth tracker (`/portfolio-aggregator`), distinct from `/portfolio` —
the "I bought this" Market Picks P&L tracker backed by the `positions` table. The two share a
`/api/portfolio` URL prefix by accident of routing, nothing else: different tables, different
lifecycle, different purpose. This one has **no auth** — profiles are a bare picker with no
credentials, a deliberate personal-scale-tool decision, not an oversight.

- **Foundation** (`routes/portfolio_aggregator.py`, mounted at `/api/portfolio`): `profiles` →
  `accounts` (bank/broker/amc/epfo/other) → `assets` (mf/stock/fd/epf/ppf/cash/manual/loan, with a
  JSON `meta` column absorbing per-type variance — an FD's rate/maturity date, a stock's ISIN) →
  `holdings` (units + avg_cost, mf/stock only) → `valuations` (one row per asset per `as_of` date,
  upserted same-day, accumulating history from day one). `transactions` is schema-only until the
  import flows below populate it. Net worth = sum of each non-archived asset's latest valuation,
  with `loan` assets subtracted. Full CRUD for profiles/accounts/assets + a manual valuation-upsert
  endpoint (422 on a future `as_of` date).
- **Valuation engine** (`portfolio/portfolio_valuation.py`): `refresh_valuations(engine)` auto-values every
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
- **CAS PDF import** (`portfolio/cas_import.py`, `POST /api/portfolio/import-cas`): parses a CAMS/KFintech
  detailed CAS statement (`casparser` library; the PDF itself never touches disk, only a
  PII-scrubbed parsed-JSON archive in `app_state`'s `cas_archive` namespace), reconciles against `assets` by AMFI
  scheme code then ISIN, creates missing `mf` assets, sets `holdings.units` to the CAS closing
  balance, and inserts `transactions` tagged `meta.source='cas'` — re-import replaces only those
  tagged rows, never a manually-entered one. Calls `refresh_valuations()` on success so imported
  schemes get an immediate value wherever a NAV already exists.
- **Broker CSV import** (`portfolio/csv_import.py`, `POST /api/portfolio/import-csv/preview` +
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
  merges NSE main-board (queried from the `securities` table the EOD pipeline populates), a BSE
  main-board fetch (24h cache, `output/_bse_main_master.json`), and the NSE Emerge/BSE SME lists,
  deduped by ISIN.
- **Broker API sync** (`portfolio/kite_sync.py` / `hdfc_sync.py` / `paytm_sync.py`, all four
  `POST/GET /api/portfolio/broker/*` endpoints in `routes/portfolio_aggregator.py`): a live
  alternative to the two file-based imports above — Zerodha Kite Connect, HDFC Securities
  InvestRight, and Paytm Money's Open API all expose a free "personal" tier for holdings/trades.
  Every one of the three modules implements the identical `get_login_url(api_key)` /
  `exchange_request_token(api_key, api_secret, request_token)` /
  `sync_account(engine, account_id, access_token, api_key)` interface, so
  `_broker_sync_module(broker)` dispatches on the path parameter with a plain `if/elif`; the
  actual "write a normalized holding/trade into `assets`/`holdings`/`valuations`/`transactions`"
  logic lives once, in `portfolio/broker_sync_common.py`, which every module calls into after
  translating its own broker's raw JSON shape. Credentials are **per-connection, not an env
  var** — each broker app (`api_key`/`api_secret`) is registered under one specific broker login,
  so the caller supplies both inline when connecting; they're stored in `broker_connections`
  (`api_key` plaintext, `api_secret_enc`/`access_token_enc` Fernet-encrypted via
  `core/crypto.py`). `POST .../login-url` doubles as "register credentials" (first call) and
  "resume the OAuth handshake" (later calls, crediential fields omitted) in one endpoint;
  re-registering different credentials clears any stale access token rather than leaving
  `connections` reporting a connection that can no longer actually authenticate.

---

## Batch pipelines & scheduling

Six standalone batch jobs, all sharing the same shape: a `run()` that returns a bool, a `main()`
CLI wrapper that exits non-zero when `run()` is false, and a health gate so a substantially-failed
run fails its GitHub Actions job loudly rather than "succeeding" with mostly-empty data.

| Job | Schedule (UTC) | Writes | Health gate |
|---|---|---|---|
| `pipelines/sme_ema_pipeline.py` | `0 13 * * 1-5` (18:30 IST) | `sme_stocks`, `ema_signals` | empty stock list, or OHLCV error rate > 50% (`_MAX_ACCEPTABLE_ERROR_RATE`) |
| `pipelines/watchlist_alerts.py` | `30 13 * * 1-5` | emails; `verdict_history` snapshots | per-symbol failure rate > 50% |
| `pipelines/screener_pipeline.py` | `0 14 * * 1-5` | `screener_stocks` | — |
| `pipelines/eod_prices_pipeline.py` | `15 14 * * 1-5` (19:45 IST) | `securities`, `prices_daily`, `mf_nav_daily`; then corporate actions, then portfolio valuations | — |
| `pipelines/market_picks_pipeline.py` | `30 1 * * 1` (Mondays) | `output/_market_picks/`, `app_state` (`market_picks_history`) | > 70% empty sources, or zero consolidated picks |
| `tests_live/` contract check | `0 6 * * 1` (Mondays) | nothing — early-warning only | job fails on a real contract break |

Staggering is deliberate: SME → watchlist alerts → screener → EOD, 30-60 min apart, so two jobs
don't contend for the same DB connection pool. All Postgres-backed jobs need a `DATABASE_URL`
repository secret and fail fast with a clear message when it's missing rather than a raw traceback.
`.github/workflows/ci.yml` is the ordinary pytest + `tsc` + Playwright gate, unrelated to these.

`market-picks-cron.yml` is the odd one out — it calls `GET /api/market-picks?force=true` on the
already-deployed backend over HTTP rather than running the pipeline itself, because the picks cache
is a local file on the backend host and a GitHub runner's computed result would have nowhere to
land. `pipelines/market_picks_pipeline.py`'s own `main()` exists for a self-hosted crontab on that same host.

### Watchlist alert emails (`pipelines/watchlist_alerts.py`)

The one batch job wired to the *single-stock* pipeline rather than a bulk scrape. One query joins
`watchlist_items` to `users` filtered to `user_id IS NOT NULL` — an anonymous `client_id` row has
no email to notify — and groups by symbol, so a stock five users watch is re-analysed once, not
five times. Each symbol then re-runs the same `main._fetch_task()` → `run_signal_engine()` →
`run_analysis_with_fallback()` flow the CLI runs, **respecting the existing cache TTLs**, so a
symbol some visitor already refreshed on the website today isn't re-fetched or re-billed.
`verdict_history.save_snapshot()` is called on every path including the all-fresh cache-hit path,
so a day is never silently missing a snapshot.

`verdict_history.detect_recent_changes(symbol, threshold_pct)` compares the two most recent stored
snapshots and yields up to two alert kinds per symbol: a **recommendation change** (differs from
the prior verdict; needs both rows to exist, so a first-time symbol never alerts) and a **price
move** ≥ `_PRICE_MOVE_THRESHOLD_PCT` (10%) — a stock can move double digits and still close as a
HOLD, which the recommendation check alone would miss. `_MAX_ALERT_SYMBOLS` (50) caps distinct
symbols per run, since each one is a real paid LLM call; symbols past the cap are logged, not
silently dropped. `core/email_sender.py` sends **one digest per user per run**, not one email per
symbol; it shares `_send_via_smtp()` with the magic-link sender and is best-effort — returns
`True`/`False`, never raises, and a missing `SMTP_HOST` just means nothing arrives.

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
  `routes/_shared.py::claim_anonymous_rows_sync()`, which takes the *same* per-account advisory
  lock key the ordinary add endpoint takes (`watchlist:user:<id>`) — a separate lock namespace
  would let a concurrent claim and add both read the same pre-write count and both commit, blowing
  the cap. The one caller is `/auth/verify`'s success page, which checks both counts before
  redirecting and shows an explicit Claim/Skip prompt.
  **Disclosed residual risk**: `client_id` was never a secret, and claiming is more severe than an
  ordinary read/write because it *exclusively* reassigns rows, permanently cutting off the original
  browser. The 5/hour limit and a `watchlist_claimed` audit event bound automated abuse but do not
  stop a single targeted guess of one leaked ID; that would need proof of possession.
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

`signals/engine.py::run_signal_engine(symbol, all_data)` runs `extract_features(all_data)` once,
then blends six independent `Signal`s into one `SignalResult` — `final_score` in [-1, 1] plus a
**5-tier** `verdict`, thresholded on the *rounded* score (`> 0.5` BUY, `> 0.1` WATCHLIST, `> -0.3`
HOLD, `> -0.6` AVOID, else SELL). Deciding and reporting off the same rounded value is deliberate:
float arithmetic yields `0.10000000000000003`, so thresholding the raw score while displaying a
rounded copy could report `final_score: 0.1` next to `verdict: WATCHLIST` when this engine's own
rule says 0.1 isn't a WATCHLIST score — a self-contradiction shown to the user *and* fed verbatim
into the analyst prompt. (Distinct from Market Picks' own 4-tier BUY/WATCHLIST/HOLD/SELL rating,
which is a separate formula — see that pipeline's `_phase_score`.)

| Signal | Module | Does its own I/O? | Default weight |
|---|---|---|---|
| `valuation` | `signals/valuation.py` | No — reads `features` | 0.4 |
| `growth` | `signals/growth.py` | No | 0.4 |
| `volume` | `signals/volume.py` | No | 0.2 |
| `filings` | `signals/filings.py` (± a small nudge from `filings_classifier.classify_rating_action()`) | No | 0.2 |
| `technical` | `signals/technical.py` | **Yes** — RSI(14) + EMA20/50 posture off a cached `price_history` series | 0.2 |
| `macro` | `signals/macro.py` | **Yes** — FII/DII net flow + RBI repo rate/CPI, cached under a fixed `"_MACRO"` pseudo-symbol (identical for every stock on a given day) | 0.15 |

`technical`/`macro` hit their own caches, independently TTL'd from the six-task lockstep — so a
`?force=true` re-analysis bypasses `ALL_DATA_TASKS` but **not** these, and the technical signal can
lag up to 6h behind everything else in the same report. Acceptable for a momentum confirmation on
daily closes; surprising in a support ticket if you don't know it. Their I/O also means
`run_signal_engine()` is not pure CPU, so any caller on an event loop must go through
`loop.run_in_executor()`; the three other callers (`main.py` CLI, `pipelines/watchlist_alerts.py`,
`_phase_research`) are already in a sync script or an executor thread.

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

`signals/interpreter.py::interpret()` turns a `SignalResult` into a plain-English string. (There
used to be a `signals/store.py` writing a 90-day audit trail to `signals_data/` that nothing ever
read back; it was deleted rather than migrated.) The Market Picks pipeline's
`_compute_confidence()` uses the engine's `final_score`/`verdict` directly as 50% of its own
confidence formula.

---

## LLM analyst layer

`analyst/crew.py::run_analysis_with_fallback()` — no CrewAI orchestration, a direct `litellm.completion`
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

A missing `signal_context` skips these three rather than raising — a `KeyError` here would be
caught by an outer broad `except` and misread as a provider outage, burning a failover attempt.

Then two content checks run over the analyst's own prose (`summary`, `business_quality`,
`news_highlights`, `institutional_trend`, and the three factor/risk lists, concatenated):

- **Grounded claims** (`_analysis_support_issues`) — rejects language implying support (e.g.
  "strong institutional buying") that the actual source data doesn't back.
- **Numeric misread** (`_analysis_numeric_issues`) — the LLM transcribing a real number wrongly
  (a 0.46% dividend yield written as "47%") is a distinct failure from inventing one, and the
  grounded-claims check doesn't catch it. `_NUMERIC_FIELD_CHECKS` pairs nine metrics (dividend
  yield, P/E, ROE, ROCE, book value, sales growth, profit growth, EBITDA margin, market cap) with
  a regex for how the analyst might name them and a getter for the true value out of `all_data`.
  Any cited figure off by more than **2x** from the source value is rejected; anything closer is
  assumed to be legitimate rounding. Sales/profit growth accept *either* the 3Y or 5Y window
  (analyst prose rarely says which) and only fail when off by 2x from both. Market cap has its own
  parser (`_parse_cited_market_cap`) because the unit is written every possible way — crore, lakh
  crore, bn, USD.
- **Sector-range plausibility** (`_sector_range_issues`, folded into the same list) — the
  single-stock flow fetches *no* peer-benchmark data, so any analyst-cited "sector average P/E"
  or "peer average ROE" is by construction ungrounded. A cited figure outside a static plausible
  range for that stock's own sector bucket is rejected. **Disclosed limitation**: the ranges are
  hand-set judgment, not fetched data — this catches obvious fabrication, not a plausible-looking
  wrong number.

A guardrail failure triggers one corrective LLM retry with the validation error appended.

**Cross-provider failover**: six providers have a default model (`_ANALYST_DEFAULTS`) — Anthropic,
OpenAI, Groq, Google, OpenRouter, Ollama. Five are auto-detectable from an API key
(`_API_KEY_ENV`, checked in that order); Ollama is local and has no key, so it only ever runs via
an explicit `LLM_PROVIDER=ollama`. `_attempt_provider()` is one full attempt (its own guardrail
retry + rate-limit retry) against one provider. `run_analysis_with_fallback()` tries the primary
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

**Cost instrumentation** (`analyst/llm_cost.py`): every `litellm.completion()` call (guardrail retries and
failed failover attempts included, not just the one that ultimately validates) is logged
(`llm_call_cost` event) and accumulated into `app_state`'s `llm_cost` namespace, one record per
UTC day (`call_count`/`total_cost_usd`/`calls_with_unknown_cost`), serialized by
`state_store.mutate()`'s row lock so concurrent backend workers can't undercount via a lost
update.
`estimate_cost_usd()` wraps `litellm.completion_cost()` and never guesses — a model litellm has no
pricing data for degrades to `None`, never a fabricated number.

---

## Caching architecture (`core/cache.py`)

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
- A Redis read/write failure logs a warning and falls back to disk for that one call.
- `save()` refuses to write a failed payload at all (`_is_failed_payload` — a dict with a
  top-level `error` key), so a transient scrape failure is never cached as if it were data. It
  also returns `_meta` rather than mutating the caller's dict, because several endpoints return
  the very object they just cached and an in-place stamp would leak `_meta` into the HTTP response.

**TTL map** (`cache.TTL_HOURS`, 15 entries):

| Hours | Tasks |
|---|---|
| 1 | `stock_info`, `news` |
| 6 | `price_history` |
| 24 | `research`, `analysis`, `peers`, `financials`, `index_history`, `insider_activity`, `fii_dii_flow`, `macro_context`, `street_consensus` |
| 168 (7d) | `shareholding`, `mf_holdings`, `shareholding_detail` |

**Cache-key conventions**: the key is `(symbol, task)` → `output/<SYMBOL>/<task>.json` on disk,
`cache:<SYMBOL>:<task>` in Redis. Three tasks aren't per-symbol at all and use a **pseudo-symbol**
so they get one shared entry instead of one per stock analysed: `"_MACRO"` for
`fii_dii_flow`/`macro_context` (identical market-wide for a given day) and `"NSEI"` for
`index_history` (the Nifty50 benchmark series). Concurrent workers can race to fill a pseudo-symbol
entry; the atomic tempfile + `os.replace` write means the worst case is a few redundant fetches,
never a corrupt file.

**Outside `core/cache.py` entirely**: `output/_market_picks/picks.json` (192h / 7d + 24h buffer, its own
`_PICKS_CACHE_TTL_HOURS`), `output/_extract_cache/<hash>.json` (6h, content-aware key over title +
URL + summary, pruned once per pipeline run), `output/_nse_master.txt` (24h),
`output/_bse_main_master.json` (24h, securities master), and `output/_bhavcopy/` (raw bhavcopy
archive for replay). Everything under `output/` is regenerable; durable state is in `app_state`.

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
| `broker_connections` | Portfolio Aggregator broker API sync (Zerodha/HDFC Securities/Paytm Money) |

23 tables total (`grep -c "= Table(" backend/db/models.py`).

**Schema-of-record process**: Alembic (`alembic.ini` + `migrations/env.py`, targeting
`db.models.metadata` directly — no second, Alembic-specific model layer). Eight revisions today:
`0001_baseline_schema.py` (autogenerated against an empty database, the original 11 tables
`db/schema.sql` already produced by hand), `684c8a31e7e0_add_eod_price_store_and_corporate_.py`
(the 4 EOD/corporate-actions tables), `8613aafc2d9d_add_portfolio_aggregator_foundation_.py`
(the 6 Portfolio Aggregator tables), `a7f2c1d09b34_add_app_state_durable_json_state.py` (the
`app_state` table), `df6b59581b8b_add_broker_connections_table.py` (`broker_connections`,
Zerodha-only shape at the time), `6c43f4a2d489_add_broker_connections_per_account_api_.py`
(`api_key`/`api_secret_enc` — the per-connection-credential redesign),
`b7cf5b79ce66_add_transactions_external_ref_for_.py` (`transactions.external_ref` + its unique
constraint — DB-enforced broker-trade dedup, see "Broker API sync" below), and
`35f10ea4dac3_add_broker_connections_background_sync_.py` (`broker_connections.sync_status`/
`.last_sync_summary`/`.last_sync_error` — backs the background-sync poll, same section) — each
verified to `upgrade head`/`downgrade base` cleanly against an isolated scratch database before
landing. **Existing deployments must `alembic stamp 0001`, then `upgrade head`,** — they already
have those 11 tables (via `db/schema.sql` or a pipeline's own `--setup-db`), so replaying `CREATE
TABLE` would fail on the first statement; the seven later revisions apply normally via `upgrade
head`. From here on, schema changes are authored as new Alembic revisions (`alembic revision
--autogenerate`), not hand-edited into `db/schema.sql` — that file is kept only as a frozen
historical reference and for the two tables `tests/test_schema_sql_migrations.py` still exercises
its old guard-convention against.

Each pipeline's own `--reset-db` CLI flag varies in blast radius: `pipelines/screener_pipeline.py --reset-db`
is scoped to just `screener_stocks`; `pipelines/sme_ema_pipeline.py --reset-db` calls
`metadata.drop_all()`/`create_all()` against the *shared* `MetaData()`, so it drops every table in
the database, not just its own two — a disclosed, not-yet-fixed footgun (see CLAUDE.md).

---

## Route module extraction (`routes/`)

`api.py` is still the majority of the backend (~2,760 lines, **29 of the 61 routes**) — three
domains have been split out into `APIRouter` modules so far (the first two were the most
duplicated; the third, `portfolio_aggregator.py`, is a large self-contained new domain that made
more sense as its own router from the start):

```text
routes/
├── _shared.py                run_owned_db_call() — the rate-limit → 503-if-no-DATABASE_URL →
│                              run_in_executor → sanitize-error wrapper multiple domains share;
│                              also claim_anonymous_rows_sync() (watchlist/positions claim flow)
├── watchlist.py    (5)        GET/POST /api/watchlist, DELETE /api/watchlist/{symbol},
│                              GET /api/watchlist/calendar, POST /api/watchlist/claim
│                              + resolve_owner()/owner_column()/WatchlistOwner (shared identity
│                              resolution, imported directly by positions.py)
├── positions.py    (6)        GET/POST /api/positions, PATCH+DELETE /api/positions/{symbol},
│                              POST /api/positions/claim, GET /api/portfolio/concentration
│                              (sector-concentration overlay for Market Picks — reads the
│                              positions table only, unrelated to the Portfolio Aggregator
│                              despite the shared /api/portfolio prefix, which it lands on
│                              because this router has no prefix= of its own)
└── portfolio_aggregator.py (21)  APIRouter(prefix="/api/portfolio") — profiles, accounts,
                               assets (+/valuations), networth, refresh-valuations, xirr,
                               import-cas, import-csv(/preview), broker/{broker}/login-url,
                               broker/{broker}/connect, broker/{broker}/sync,
                               broker/connections. Full CRUD, hence the count
```

**`run_owned_db_call(request, rate_limit_name, max_calls, sync_fn, event_prefix, window_seconds=60)`**
is the wrapper the DB-backed endpoints share instead of each re-implementing it: rate-limit → 503
immediately if `DATABASE_URL` is unset → run `sync_fn` off the event loop via `run_in_executor` →
map exceptions to status codes. `ValueError` → 422 (validation, e.g. cap exceeded),
`PermissionError` → 401 (session-required endpoint with no valid session), a deliberate
`HTTPException` from inside `sync_fn` → re-raised as-is (so a 404 for a missing id isn't swallowed),
anything else → a **sanitized** 503 with the real exception logged server-side, never returned.

All three routers `import api` (not `from api import X`) and reach shared state (`_get_db_engine`,
`_rate_limit`, `LOGGER`, `log_event`) via dotted access at call time. Two reasons: `api.py`
registers these routers, so they can't bind `api.py`'s names at their own module top level before
those names exist; and `unittest.mock.patch("api.X", ...)` only intercepts lookups made through the
module object, not a name a `from X import Y` already copied at import time. `api.py` re-exports
`_MAX_WATCHLIST_ITEMS_PER_CLIENT`/`_MAX_POSITIONS_PER_CLIENT` (both 200) because existing tests read
them as plain values, not just as patch targets.

The other 29 routes — SME signals, Screener, Market Picks, auth, API keys, financials, peers,
insider activity, street consensus, shareholding detail, verdict history, consolidated view, symbol
validation, prices — are still inline in `api.py`. Splitting further is disclosed future work.

---

## Rate limiting & shared guard state

Three pieces of backend guard state were originally in-memory, which silently became *per-worker*
the moment the backend scaled past one process. All three now optionally share state through Redis.

**`core/rate_limiter.py`** exposes three primitives: `is_allowed()` (sliding window), `try_acquire_slot()`/
`release_slot()` (named concurrency ceiling — the LLM-call slot), `try_acquire_lock()`/
`release_lock()` (single-run lock — the SME/Screener refresh guard). Redis-backed via small Lua
scripts / `SET NX EX` for atomic check-and-set, falling back to the original in-memory
implementation when `REDIS_URL` is unset or a Redis call raises. A Redis-held slot/lock carries a
TTL (600s for slots, 3600s for the SME refresh lock) so a worker that crashes before releasing
can't strand it; the in-memory path needs no TTL since a crash resets it anyway.
`get_usage_count()` is a **non-mutating** peek at the same sliding-window state (`ZREMRANGEBYSCORE`
then `ZCARD`), used by the API-keys usage dashboard — checking your usage must not itself count.

**Who is "per IP"** (`api.py::_client_ip()`): every request reaches FastAPI through the Next.js
proxy server-to-server, so `request.client.host` is always the Next.js server's own IP — which
would collapse every per-IP limiter into one site-wide bucket. `_client_ip()` trusts the first
`X-Forwarded-For` address **only** when the request also presents a matching `TRUSTED_PROXY_SECRET`
in `X-Internal-Proxy-Secret`; otherwise it falls back to `request.client.host` unchanged. The
secret is what proves the forwarded value came from this deployment's own frontend — without it,
any direct caller could spoof `X-Forwarded-For` to dodge its own limit or to get an innocent IP
throttled. `frontend/lib/proxy-headers.ts::clientIpHeaders()` is the frontend half, merged into
every proxy route's outbound fetch. Scoped to rate limiting only — no log line or stored record
uses this value.

**Applied limits** (`_rate_limit(request, bucket, max_calls, window_seconds)`; every bucket is
keyed `<bucket>:<client_ip>`):

| Limit | Routes |
|---|---|
| 3 / hour | `market-picks?force=true`, `sme-signals/refresh`, `screener/refresh` |
| 5 / 15 min | `auth/request-link` (**plus** a separate 5/hour keyed on the target *email address* — rotating IPs would otherwise allow unbounded inbox-bombing of one victim) |
| 5 / hour | `watchlist/claim`, `positions/claim` (a claim permanently reassigns rows away from the anonymous browser, so it's held far tighter than an ordinary write) |
| 20 / 5 min | `analyse/{symbol}`, `auth/verify` |
| 20 / hour | `api-keys` create |
| 30 / min | `validate`, `prices`, `peers`, `financials`, `shareholding-detail`, `insider-activity`, `street-consensus`, `consolidated`, `watchlist/calendar` |
| 60 / min | `prices/history`, `verdict-history`, `sme-signals` (+ history), `screener`, `market-picks/status`, `market-picks/history`, `api-keys` list/revoke |
| tier-scaled / hour | `/api/v1/*`, keyed on `user_id` not IP — a legitimate integration may run from a shared or rotating IP |

The two refresh endpoints take their single-run lock **before** the rate-limit check and release it
on a 429, preserving "409 already-running takes priority over 429 too-many-requests".

---

## Observability

- **`core/observability.py`** — `log_event(logger, event, level=..., exc=None, **fields)` is the single
  structured-JSON-logging entry point every backend module calls. `LOG_LEVEL` (default `INFO`)
  gates it.
- **`core/error_tracking.py`** — `log_event()`'s error-level path forwards `(event, fields, exc)` to
  `capture_error()` when `SENTRY_DSN` is set (unset by default: zero behavior change out of the
  box). Pluggable in the sense of "any Sentry-protocol ingest endpoint," not a multi-backend
  registry. `init_error_tracking()` runs once per process at every CLI/server entry point and is
  idempotent, since `sentry_sdk.init()` isn't safe to call twice. It passes
  `LoggingIntegration(event_level=None)` explicitly — the SDK's default would auto-capture the
  plain `logger.error()` line `log_event` emits *immediately before* calling `capture_error()`,
  shipping every error as two differently-shaped events. A broken or unreachable Sentry backend
  can never break the primary log line.
- **`core/schema_drift.py`** — type-drift detection, for the six `ALL_DATA_TASKS` slices only.
  `schemas.CONTRACTS`'s optional `"types"` map (`{field: dict|list}`) is the single source of
  truth, so there's no second field list to drift from `core/schemas.py`. `check_drift()` flags a field
  that is *present but the wrong shape* — never one that's legitimately absent, which is the
  common, expected case under this codebase's "never invent" convention. Wired into
  `main._fetch_task()`, the one choke point both the CLI and the SSE endpoint already go through.
  Logs at `warning` (a human should look at the scraper; this isn't a page), never raises.
- **`telemetry/source_health.py`** — volume/freshness anomaly detection, for the 20 Market Picks sources +
  the two macro-overlay fetches. Records a per-source daily ok/not-ok result in `app_state`'s
  `source_health` namespace and warns once a source with an established healthy baseline (≥5 prior
  days, at least one successful) has failed 3 consecutive *days*. Time-normalized — several calls
  on the same UTC day collapse to one data point, so a burst of `?force=true` retries can't trip
  the threshold in minutes. Serialized per source by `state_store.mutate()`'s row lock, which is
  what stops two racing read-modify-write cycles losing an update. A brand-new source never alerts
  (no baseline to regress from).
- **`telemetry/source_quality.py`** — per-*run* Market Picks source telemetry, complementing the day-level
  view above: one record per run in `app_state`'s `source_quality` namespace, recording for each source how
  many articles it yielded, how many picks the LLM extracted from them, and how many survived
  NSE-symbol validation. This is the funnel `telemetry/source_health.py` can't see — a source that reliably
  returns articles which never once become a validated pick looks perfectly healthy to a
  boolean ok/not-ok check. Each run owns its own key, so no lock is needed.
  `telemetry/source_quality_report.py` is the CLI that aggregates these across runs.
- **`telemetry/scraper_error_counters.py`** — for the standalone per-symbol endpoints (`peers`, `financials`,
  `insider_activity`'s two sub-fetches, `street_consensus`'s two sub-fetches), where an empty
  result is the expected common case and the volume-anomaly heuristic above would be pure noise.
  Distinguishes a genuine `{"error": ...}` tool result from a legitimate empty one and counts/logs
  only the former — no "N bad days" threshold, since these are on-demand endpoints where a single
  error already means one real user's request degraded. A grep-able counter file plus a warning
  log line, not a metrics platform.
- **`analyst/llm_cost.py`** — see "LLM analyst layer" above.
- **`tests_live/`** — a second, opt-in test root (`RUN_LIVE_TESTS=1`, weekly cron) that checks four
  high-blast-radius scrapers against live responses, so a contract break surfaces before production
  traffic finds it. It probes each host with a bare `requests.head()` **first**: tools never raise,
  so a connectivity failure and a real layout change both look like `{"error": ...}`, and only a
  reachable host makes a still-failing scrape a genuine contract failure. Four scrapers, not all
  ~10 standalone ones — a disclosed starting point, not full coverage.

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

From `backend/`: `python main.py <SYMBOL>` (`--force` to bypass cache) runs the identical fetch → normalize →
signal-engine → analyst flow the web app's SSE endpoint runs, sharing `main._fetch_task()` and
`main._build_report()` with `api.py` — the two entry points cannot drift on report shape. Also
saves its finished report to `app_state` under `cli_report`, keyed `SYMBOL:YYYY-MM-DD` (or to
`output/<SYMBOL>/report_<date>.json` when `DATABASE_URL` is unset). Batch pipelines each have their own CLI
too: `pipelines/sme_ema_pipeline.py`, `pipelines/screener_pipeline.py`, `pipelines/market_picks_pipeline.py` (`main()`, for a
self-hosted crontab alternative to the GitHub Actions cron), `pipelines/watchlist_alerts.py` (`--force`),
`pipelines/eod_prices_pipeline.py` (`--setup-db`/`--reset-db`/`--date`/`--backfill`),
`pipelines/corporate_actions_pipeline.py` (`--setup-db`/`--reset-db`/`--backfill`/`--recompute`/
`--recompute-all`), `portfolio/portfolio_valuation.py` (standalone-runnable, no flags — just runs
`refresh_valuations()` once), and `portfolio/cas_import.py --replay <archived-json> --account-id N` (re-runs
a CAS import from a previously-archived parse, for debugging/recovery without re-uploading the PDF).

---

## File layout

Backend paths are relative to `backend/`, a sibling of `frontend/`. Python imports are unqualified
(`import cache`, `from routes.watchlist import ...`), so **every backend command must run from
inside `backend/`** — the directory move changed the top-level nesting, not any import path.

```text
stock-research/
├── backend/
│   ├── api.py                     FastAPI server — 29 of the 57 routes, both SSE endpoints,
│   │                               symbol validation, shared helpers routes/ depends on
│   ├── main.py                    CLI entry point; _fetch_task/_build_report shared with api.py
│   ├── analyst/crew.py                    Analyst guardrails, cross-provider failover, run_analysis_with_fallback
│   ├── analyst/llm_cost.py                Per-call LLM cost instrumentation + running daily total
│   ├── core/cache.py                   File-based TTL cache, optional Redis write-through/read-first
│   ├── core/rate_limiter.py            Shared-state (Redis or in-memory) rate limits, slots, locks
│   ├── core/schemas.py                 Normalization contracts: raw tool output → canonical dicts
│   ├── core/schema_drift.py            Type-drift detection for the six ALL_DATA_TASKS slices
│   ├── telemetry/source_health.py           Freshness/volume monitoring for Market Picks' 20 sources + macro
│   ├── telemetry/scraper_error_counters.py  Error counters for the 4 standalone per-symbol scrapers
│   ├── core/observability.py           Structured JSON logging (log_event())
│   ├── core/error_tracking.py          Optional Sentry-compatible hook, wired into log_event()
│   ├── analytics/peer_analytics.py          Peer-percentile + absolute valuation-anchor math (shared by
│   │                               api.py's /api/peers and pipelines/market_picks_pipeline.py's _phase_research)
│   ├── portfolio/dcf_valuation.py           Deterministic two-stage DCF off cash-flow statement data
│   ├── analytics/verdict_history.py         Daily verdict/price snapshots (Postgres) — verdict timeline strip
│   ├── analytics/mf_holdings_history.py     Quarterly MF stake snapshots (Postgres) — stake-delta badges
│   ├── auth.py                    Magic-link auth: token/session/API-key issuance + validation
│   ├── core/email_sender.py            Magic-link + watchlist-alert emails over generic SMTP
│   ├── pipelines/watchlist_alerts.py        Daily batch job: emails users on a watched stock's rec change
│   ├── pipelines/market_picks_pipeline.py   6-phase multi-agent weekly picks pipeline
│   ├── pipelines/sme_ema_pipeline.py        SME golden/death cross batch pipeline (Postgres)
│   ├── pipelines/screener_pipeline.py       NIFTY 500 custom screener batch pipeline (Postgres)
│   ├── pipelines/eod_prices_pipeline.py     NSE bhavcopy + AMFI NAV ingestion; also runs corporate-actions
│   │                               and portfolio-valuation as isolated final steps
│   ├── pipelines/corporate_actions_pipeline.py  NSE corporate-actions ingestion + adj_close recompute
│   ├── telemetry/source_quality.py          Per-run Market Picks source telemetry (yield, dedup, extraction rate)
│   ├── telemetry/source_quality_report.py   CLI aggregating telemetry/source_quality.py's per-run JSON files
│   ├── portfolio/portfolio_valuation.py     Portfolio Aggregator: refresh_valuations(), xirr(), xirr_report()
│   ├── portfolio/cas_import.py              CAMS/KFintech CAS PDF import → Portfolio Aggregator transactions
│   ├── portfolio/csv_import.py              Generic broker-CSV import (Zerodha preset) → transactions
│   ├── portfolio/broker_sync_common.py      Shared assets/holdings/valuations/transactions upsert logic
│   │                               every portfolio/<broker>_sync.py module builds on
│   ├── portfolio/kite_sync.py               Zerodha Kite Connect holdings/trades sync
│   ├── portfolio/hdfc_sync.py               HDFC Securities InvestRight Open API holdings/trades sync
│   ├── portfolio/paytm_sync.py              Paytm Money Open API holdings/trades sync
│   ├── core/crypto.py                  Fernet encrypt/decrypt (PORTFOLIO_ENCRYPTION_KEY) — broker
│   │                               app secrets + access tokens in broker_connections
│   ├── requirements.txt
│   ├── alembic.ini                Schema-migration config
│   ├── Dockerfile
│   ├── migrations/                env.py + versions/ (0001_baseline_schema,
│   │                               684c8a31e7e0_add_eod_price_store_and_corporate_,
│   │                               8613aafc2d9d_add_portfolio_aggregator_foundation_,
│   │                               a7f2c1d09b34_add_app_state_durable_json_state,
│   │                               df6b59581b8b_add_broker_connections_table,
│   │                               6c43f4a2d489_add_broker_connections_per_account_api_,
│   │                               b7cf5b79ce66_add_transactions_external_ref_for_,
│   │                               35f10ea4dac3_add_broker_connections_background_sync_)
│   ├── db/
│   │   ├── models.py               SQLAlchemy Core tables (one shared MetaData(), 23 tables)
│   │   └── schema.sql               Frozen pre-Alembic reference; still tested for 2 tables' guards
│   ├── routes/                    Extracted APIRouter modules — see "Route module extraction" above
│   │   ├── _shared.py
│   │   ├── watchlist.py
│   │   ├── positions.py            Also /api/portfolio/concentration (sector-concentration overlay)
│   │   └── portfolio_aggregator.py Portfolio Aggregator CRUD + valuation/XIRR + CAS/CSV import
│   ├── config/
│   │   ├── analyst.json            Analyst role/goal/backstory + output_schema + section labels
│   │   └── crew_tasks.py            Builds the analyst prompt from analyst.json
│   ├── signals/                   Quantitative signal engine
│   │   ├── engine.py                run_signal_engine(), sector-aware weight tilts
│   │   ├── features.py              Feature extraction from normalized data
│   │   ├── valuation.py / growth.py / volume.py / filings.py   Signals reading `features` only
│   │   ├── technical.py             RSI14 + EMA20/50 posture — own I/O (price_history cache)
│   │   ├── macro.py                 FII/DII flow + RBI rate/CPI — own I/O ("_MACRO" pseudo-symbol cache)
│   │   ├── filings_classifier.py    Corporate actions / rating action / next-results-date text classifier
│   │   ├── models.py                Signal / SignalResult dataclasses
│   │   ├── interpreter.py           SignalResult → plain-English string
│   ├── tools/                     Data-fetching functions (never raise — return {"error": ...})
│   │   ├── nse_tools.py              yfinance quote + NSE API + best-effort XBRL EPS fallback
│   │   ├── screener_tools.py         Fundamentals, peers, valuation band, statements, concalls
│   │   ├── news_tools.py             gnews wrapper
│   │   ├── nse_filings_tools.py      Corporate announcements
│   │   ├── market_picks_tools.py     RSS + GNews scrapers (merges in hdfc_sec_agent.py's sources)
│   │   ├── hdfc_sec_agent.py         HDFC Securities scrapers
│   │   ├── sme_tools.py              NSE Emerge + BSE SME stock-list fetchers
│   │   ├── nifty500_tools.py         NIFTY 500 constituent list (pipelines/screener_pipeline.py's universe)
│   │   ├── nse_insider_trades.py / nse_bulk_block_deals.py   PIT + bulk/block deal feeds
│   │   ├── nse_fii_dii_tools.py      Daily FII/DII net equity flow
│   │   ├── macro_context_tools.py    RBI repo rate + CPI inflation
│   │   ├── trendlyne_agent.py        GNews search for Trendlyne-cited coverage
│   │   ├── trendlyne_scraper.py      Direct trendlyne.com numeric consensus scrape
│   │   ├── price_history_tools.py    Shared daily-close OHLCV fetch (sparklines, technical signal)
│   │   ├── screener_scanner.py       Screener.in fundamental-screen scraper — a Market Picks source
│   │   ├── eod_sources.py            NSE bhavcopy + equity-master fetch/parse, AMFI NAV fetch/parse
│   │   ├── corporate_actions.py      NSE corporate-actions fetch + PURPOSE-string parser
│   │   ├── securities_master.py      NSE+BSE main-board + SME merge, resolve_symbol() (broker-code
│   │   │                               → canonical symbol; consumed by portfolio/csv_import.py)
│   │   ├── _gnews_timeout.py         Patches a hard timeout onto gnews→feedparser→urllib, which
│   │   │                               has none — one hung GNews call would otherwise block
│   │   │                               _phase_scrape's executor shutdown indefinitely
│   │   └── _nse_session.py           Shared NSE session-priming helper every NSE module delegates to
│   ├── tests/                     1,502 unittest-based tests, collected by pytest. Heavy deps
│   │                               (crewai, tool imports) mocked via sys.modules patching; no
│   │                               live network call, ever. `python -m pytest tests/`
│   ├── tests_live/                Opt-in (RUN_LIVE_TESTS=1), weekly-cron-only live contract
│   │                               checks. A separate root precisely so the command above
│   │                               can never pick them up
│   └── output/                    Cache files (gitignored) + CLI report JSON
│       ├── <SYMBOL>/                 Per-symbol task caches
│       ├── _extract_cache/           LLM extraction cache (6h TTL)
│       ├── _market_picks/            Market picks result cache (7-day TTL)
│       ├── _bhavcopy/                Raw NSE bhavcopy CSV archive (EOD price store replay)
│       ├── _nse_master.txt           NSE equity symbol master (24h refresh)
│       └── _bse_main_master.json     BSE main-board master (24h) — securities_master.py
├── .env / .env.example         Shared by both stacks; stays at the repo root
├── docker-compose.yml          backend + frontend + postgres + redis
├── .github/workflows/         ci, market-picks-cron, sme-cron, screener-cron,
│                               watchlist-alerts-cron, eod-prices-cron, live-contract-check
├── frontend/                  Next.js 15 (TypeScript, Tailwind, App Router) — 13 pages, all
│   │                           'use client'; no React Query, no state library
│   ├── app/
│   │   ├── page.tsx               Stock analysis (?symbol= deep links)
│   │   ├── compare/page.tsx       Two reports side by side + diff table
│   │   ├── market-picks/page.tsx
│   │   ├── market-picks/history/page.tsx  Per-symbol track record + snapshot date picker
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
│   │   │                           (icons generated at request time via next/og ImageResponse —
│   │   │                            no binary assets checked in)
│   │   └── api/                   34 thin proxy routes → FastAPI (adds client-IP + auth headers;
│   │                               auth/verify additionally sets the httpOnly session cookie)
│   ├── components/                29 files — one per card/domain (see "Dashboard component
│   │                               extraction" in CLAUDE.md), plus dashboard-format.ts and
│   │                               dashboard-primitives.tsx for shared helpers/atoms
│   ├── lib/                       watchlist.ts, positions.ts, auth.ts, auth-cookie.ts,
│   │                               useStockAnalysis.ts, proxy-headers.ts
│   ├── public/sw.js               Hand-written service worker (no Workbox): cache-first statics,
│   │                               network-first navigations, never intercepts /api/*, never
│   │                               caches a URL with a query string (/auth/verify?token= is a
│   │                               single-use credential and Cache API keys on the full URL)
│   ├── e2e/                       44 Playwright specs — every backend response mocked at
│   │                               page.route(); no FastAPI process runs in the E2E job at all
│   └── types/index.ts             Canonical TS types for every SSE message + report field
└── docs/                     index, architecture, setup, deployment, tools, output-schema,
                              PRD, design, feature-catalog
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
