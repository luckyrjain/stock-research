# Data Ownership

**23 tables**, all declared against one shared `sqlalchemy.MetaData()` in `backend/db/models.py`
(verified: `backend/db/models.py:3-9` uses `Table`/`Column`/`MetaData()`, confirming SQLAlchemy
**Core**, not the ORM — HIGH, independently read by this engagement's backend-core research
agent). `DATABASE_URL` is the only connection config; it is optional — see
`ALPHAPULSE_MAP.md` § Core Domain Deep Dive for the full per-feature degradation table.

> **Doc-drift finding (HIGH confidence, both sources independently read in this engagement):**
> root `CLAUDE.md` and `backend/CLAUDE.md` disagree with each other on the table count — root
> `CLAUDE.md`'s architecture overview says "22 PostgreSQL tables," while `backend/CLAUDE.md`
> itself, `docs/database.md`, and the actual `backend/db/models.py` schema all agree on **23**.
> Trusting the code (per evidence-precedence): **23 is correct.** Filed in `UNKNOWNS.md`.

| Entity / table | Authoritative source (repo + table/API) | Repository methods / write path | Schema evidence | Replicas | Caches | Search indexes | Consumers | Confidence |
|---|---|---|---|---|---|---|---|---|
| `users` | `backend/auth.py::verify_magic_link()` — `INSERT ... ON CONFLICT (email) DO UPDATE`, first successful magic-link click *is* account creation | `auth.py:106-110` (verified read) | `db/models.py:117-132`; unique `email`; `ck_users_tier` CHECK; no `ON DELETE` on referencing FKs (schema gap) | none | none | none | `sessions.user_id`, `api_keys.user_id`, `watchlist_items.user_id`, `positions.user_id` (plain FKs) | HIGH (backend-core agent read `auth.py:106-110` directly) |
| `magic_links` | `auth.py::create_magic_link()` | `auth.py:68-75` (verified read); consumed atomically at `auth.py:94-101` (`UPDATE ... WHERE used_at IS NULL AND expires_at > NOW() ... RETURNING`) | `db/models.py:138-148`; unique `token_hash`; index on `email` | none | none | none | `auth.verify_magic_link()` | HIGH |
| `sessions` | `auth.py::create_session()` | `auth.py:122-130` (verified read) | `db/models.py:154-163`; FK `user_id`; unique `token_hash` | none | none | none | `auth.get_user_for_session()` on every authenticated request | HIGH |
| `api_keys` | `auth.py::create_api_key()` | `auth.py:186-205` region (verified read) | `db/models.py:174-186`; FK `user_id`; unique `key_hash` | none | none | none | `auth.get_user_for_api_key()` on every `/api/v1/*` call | HIGH |
| `watchlist_items` | `backend/routes/watchlist.py` — `POST /api/watchlist` (`ON CONFLICT (owner_col, symbol) DO NOTHING`, under `pg_advisory_xact_lock`) | `routes/watchlist.py` (out of scope for backend-core agent; independently confirmed by P3b security agent at `routes/watchlist.py:222-229`) | `db/models.py:70-88`; `ck_watchlist_exactly_one_owner`; `uq_watchlist_client_symbol`, `uq_watchlist_user_symbol` | none | none | none | `watchlist_alerts._get_watched_symbols()` (joins `users`, excludes anonymous rows); frontend `useWatchlist()` | HIGH (P3b agent read `routes/watchlist.py` directly) |
| `positions` | `backend/routes/positions.py` — `POST /api/positions` upsert; also `portfolio/broker_sync_common.py::upsert_position_from_holding()` mirrors synced broker holdings in | `routes/positions.py:117-130` (P3b agent, verified) | `db/models.py:263-285`; `ck_positions_exactly_one_owner`; two UNIQUE constraints | none | none | none | `GET /api/positions`; `routes/positions.py::compute_sector_concentration()` | HIGH (two independent writers confirmed: the route and the broker-sync mirror — see RISK_MAP.md "Multiple writers" candidate, judged non-conflicting since fields written don't overlap) |
| `verdict_history` | `backend/analytics/verdict_history.py::save_snapshot()` — `ON CONFLICT (symbol, verdict_date) DO UPDATE`, `signal_score` COALESCEd | `analytics/verdict_history.py:33-109` (verified read, backend-core agent); callers: `main.py` CLI, `api.py:593,751,776` (SSE path), `pipelines/watchlist_alerts.py` | `db/models.py:95-108`; `uq_verdict_history_symbol_date`; no FK on `symbol` (deliberate — symbols aren't this app's own referential authority) | none | none | none | `GET /api/verdict-history/{symbol}`; `watchlist_alerts._detect_change()`/`_detect_price_move()` | HIGH |
| `mf_holdings_history` | `backend/analytics/mf_holdings_history.py::save_snapshot()` — `ON CONFLICT (symbol, as_of_date, fund) DO UPDATE` | `analytics/mf_holdings_history.py:62-84` (verified read); called from `main._fetch_task()` | `db/models.py:236-247`; `uq_mf_holdings_history_symbol_date_fund` | none | none | none | `mf_holdings_history.compute_stake_deltas()` | HIGH |
| `sme_stocks` | `backend/pipelines/sme_ema_pipeline.py` (`_upsert_stocks`/`_upsert_liquidity`/`_upsert_market_cap`) | Not independently re-read this engagement (out of scope for dispatched agents); corroborated by `docs/database.md:367-390` | `db/models.py:11-32` (verified structurally by backend-core agent); PK `symbol` | none | none | none | `GET /api/sme-signals`; `ema_signals.symbol` FK | MEDIUM (schema confirmed HIGH; writer module not independently code-read this engagement) |
| `ema_signals` | `backend/pipelines/sme_ema_pipeline.py::_upsert_signals()` | Not independently re-read; `docs/database.md:392-427` | `db/models.py:34-52`; `uq_ema_signals_symbol_date`; FK `symbol → sme_stocks.symbol` | none | none | index `idx_ema_signals_cross (cross_type)` (a secondary-attribute index, not a search index) | `GET /api/sme-signals`, `GET /api/sme-signals/{symbol}/history` | MEDIUM |
| `screener_stocks` | `backend/pipelines/screener_pipeline.py` — `INSERT ... ON CONFLICT (symbol) DO UPDATE` | Not independently re-read; `docs/database.md:430-471` | `db/models.py:199-221`; PK `symbol`; indexes on `nse_industry`, `sector` (known mismatch vs. actually-filtered columns — see RISK_MAP.md) | none | none | `idx_screener_stocks_industry`, `idx_screener_stocks_sector` | `GET /api/screener` | MEDIUM |
| `securities` | `backend/pipelines/eod_prices_pipeline.py` — two upserts (bhavcopy-light + `EQUITY_L.csv`-full) | Not independently re-read; `docs/database.md:482-504` | `db/models.py:291-303`; PK `symbol`; `isin` neither unique nor indexed (known gap) | none | none | none | `tools/securities_master.load_nse_main_board()` → `resolve_symbol()` → `portfolio/csv_import.py` | MEDIUM |
| `prices_daily` | `backend/pipelines/eod_prices_pipeline.py` (all columns except `adj_close`); **`adj_close` has exactly one writer after insert**: `backend/pipelines/corporate_actions_pipeline.py`'s recompute — `_upsert_prices`'s own `ON CONFLICT` clause deliberately omits `adj_close` so a bhavcopy re-ingest can't clobber it | Not independently re-read; `docs/database.md:506-538` | `db/models.py:305-328`; composite PK `(symbol, trade_date)`; `close` nullable | none — this is the base table | none | index `idx_prices_daily_date` | `portfolio_valuation._latest_close()` (reads raw `close`, **not** `adj_close` — disclosed: the adjustment pipeline currently has zero production readers, see RISK_MAP.md) | MEDIUM |
| `mf_nav_daily` | `backend/pipelines/eod_prices_pipeline.py` (AMFI `NAVAll.txt` + `api.mfapi.in` backfill) | Not independently re-read; `docs/database.md:539-561` | `db/models.py:330-337`; composite PK `(scheme_code, nav_date)`; `nav` NOT NULL | none | none | none | `portfolio_valuation._latest_nav()` | MEDIUM |
| `corporate_actions` | `backend/pipelines/corporate_actions_pipeline.py` (sole writer) | Not independently re-read; `docs/database.md:562-596` | `db/models.py:339-360`; `uq_corp_actions_sym_ex_purpose`; `price_factor` NULL for non-adjusting types | none | none | index `idx_corp_actions_symbol` | `corporate_actions_pipeline.adjusting_actions()` | MEDIUM |
| `profiles` | `backend/routes/portfolio_aggregator.py` CRUD, owner-scoped (migration `ec7850b73d2f` added `client_id`/`user_id` ownership — previously a bare unowned picker) | Not independently re-read this engagement (portfolio_aggregator.py out of dispatched-agent scope); corroborated by `docs/database.md:599-639` and `docs/backlog.md` item #3 (ownership fix confirmed shipped) | `db/models.py:381-397`; `ck_profiles_exactly_one_owner`; two UNIQUE (client_name, user_name) | none | none | none | Every `accounts`/`assets`/... row scoped transitively through this | MEDIUM |
| `accounts` | `routes/portfolio_aggregator.py` CRUD | Not independently re-read | `db/models.py:399-410`; FK `profile_id`; `type` app-enforced only (no CHECK — known gap) | none | none | none | `cas_import`/`csv_import` account resolution; `portfolio_valuation.xirr_report()` | MEDIUM |
| `assets` | `routes/portfolio_aggregator.py` CRUD; `cas_import.import_cas()` (unmatched CAS schemes); `csv_import.import_rows()` (unmatched broker symbols, via `securities_master.resolve_symbol()`) | Not independently re-read | `db/models.py:412-433`; FK `account_id`; `symbol` unindexed (known gap); `type` app-enforced only | none | none | none | `compute_networth()`, `portfolio_valuation.refresh_valuations()`, `eod_prices_pipeline._held_scheme_codes()` | MEDIUM |
| `holdings` | `routes/portfolio_aggregator.py`; `cas_import` (upserts to CAS closing balance); `csv_import` (derives `Σbuy − Σsell`, floored at 0) | Not independently re-read | `db/models.py:435-446`; unique `asset_id` (1:1 with `assets`) | none | none | none | asset list endpoints, `portfolio_valuation.refresh_valuations()` | MEDIUM |
| `valuations` | `routes/portfolio_aggregator.py` (manual entry, 422 on future date); `portfolio/portfolio_valuation.py::refresh_valuations()` (nightly auto-value, authoritative for mf/stock — overwrites same-day manual edit on those two types) | Not independently re-read | `db/models.py:448-459`; `uq_valuations_asset_date` (same-day upsert, new day inserts new row — history from day one) | none | none | none | `compute_networth()`, asset list | MEDIUM — **two evidenced writers to the same table for overlapping asset types (manual + engine); not a "Multiple writers" smell since the upsert key and precedence (engine wins same-day for mf/stock) are explicit and documented, not accidental** |
| `transactions` | Three writers, three different idempotency models (see RISK_MAP.md): `cas_import` (replace-by-source), `csv_import` (append+content-dedup, no DB constraint — known gap), broker-sync `broker_sync_common.py::sync_trades()` (append, DB-enforced via `external_ref` + `uq_transactions_asset_external_ref`) | Not independently re-read; `docs/database.md:731-770` | `db/models.py:464-485`; FK `asset_id`; `uq_transactions_asset_external_ref` (NULL-safe — only binds broker-sync rows) | none | none | none | `portfolio_valuation.xirr_report()` | MEDIUM — **genuine "Multiple writers" candidate**, tracked in RISK_MAP.md, mitigated by a `meta.source` discriminator so writers don't collide on the same logical rows |
| `broker_connections` | `backend/routes/broker_sync.py` (four+ broker endpoints) | P3b security agent independently read `routes/broker_sync.py:219,244,302,313,407,563` confirming every write to `api_secret_enc`/`access_token_enc` goes through `core/crypto.py::encrypt()` first | `db/models.py:525-577` (per backend-core agent numbering) / `docs/database.md:772-836`; `uq_broker_connections_account_broker`; `broker` app-enforced allowlist only (no CHECK) | none | none | none | `sync_account()` in each `portfolio/<broker>_sync.py` module | HIGH (encryption path independently verified by P3b agent) |
| `app_state` | `backend/core/state_store.py::save()`/`mutate()` — generic `(namespace, key) → JSON` store; `mutate()` uses `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` purely to take a row lock, replacing three former `fcntl.flock` helpers | `core/state_store.py:71-224` (verified read, backend-core agent) | `db/models.py:593-601`; composite PK `(namespace, key)`, no secondary index (PK serves prefix scans) | none | none | none | Namespaces observed in scope: `llm_cost` (`analyst/llm_cost.py`), `cli_report` (`main.py`); documented-not-independently-verified: `market_picks_history`, `source_health`, `scraper_errors`, `source_quality`, `cas_archive` | HIGH for mechanism + 2 namespaces; MEDIUM for the other 5 namespaces (doc-sourced) |

## Multiple-writer / ownership-conflict candidates

Per this engagement's data-ownership detection rule (2+ modules writing the same table/entity →
flag), two real candidates were found, both already disclosed in the codebase's own docs — this
engagement did not discover them independently, but confirms them against schema evidence:

1. **`transactions`** — three writers (`cas_import`, `csv_import`, broker-sync). Not a conflict in
   practice: each writer tags its rows with `meta.source` and only ever touches its own tag's rows
   (`cas_import` deletes-then-reinserts only `meta.source='cas'`; `csv_import` only compares against
   `meta.source='csv'`). Flagged `Medium` severity in RISK_MAP.md because **`csv_import`'s
   idempotency has no DB-level constraint** (a read-then-write race), unlike the broker-sync writer.
2. **`positions`** — written by both the direct `POST /api/positions` route and
   `portfolio/broker_sync_common.py::upsert_position_from_holding()` (a broker sync mirrors each
   synced holding into `positions` too). Not a conflict: the broker-sync writer only overwrites
   `exchange`/`entry_price`/`shares`, explicitly leaving `target_price`/`stop_loss`/`bought_at`
   untouched — a deliberate partial-write convention, not an accidental collision.
3. **`valuations`** — written by both a manual entry endpoint and the nightly
   `refresh_valuations()` engine. Documented precedence: the engine is authoritative for `mf`/
   `stock` assets and overwrites a same-day manual edit on those two types only.

No candidate reaches the `Multiple writers` **Critical** severity default from
`architectural-smells.md` (that default applies to *undocumented*, *conflicting* concurrent
writers to the *same fields*) — all three here are disclosed, field-partitioned, or
source-discriminated. See `RISK_MAP.md` for the full smell entries.

## `app_state` namespace table (durable JSON state)

| Namespace | Key | Written by | Payload | Confidence |
|---|---|---|---|---|
| `llm_cost` | UTC date | `analyst/llm_cost.py::record_call_cost()` (verified: `analyst/crew.py:779-787` calls it after every `litellm.completion()`, not just the validating one) | `call_count`, `total_cost_usd`, `calls_with_unknown_cost` | HIGH |
| `cli_report` | `SYMBOL:YYYY-MM-DD` | `main.py` (CLI) | The CLI's finished report; nothing reads it back; falls back to `output/<SYMBOL>/report_<date>.json` when `DATABASE_URL` unset | HIGH |
| `market_picks_history` | `YYYY-MM-DD` | `pipelines/market_picks_pipeline.py::_save_history()` (out of scope, doc-sourced) | One day's pick snapshot | MEDIUM |
| `source_health` | sanitized source name | `telemetry/source_health.py::record_and_check()` (out of scope, doc-sourced) | Rolling 20-day ok/not-ok window + `ever_healthy` flag | MEDIUM |
| `scraper_errors` | sanitized scraper name | `telemetry/scraper_error_counters.py::record_scraper_error()` (out of scope, doc-sourced) | `error_count` | MEDIUM |
| `source_quality` | Market Picks run id | `telemetry/source_quality.py::record_run()` (out of scope, doc-sourced) | Per-source articles/picks telemetry for that run | MEDIUM |
| `cas_archive` | `YYYY-MM-DD-HHMMSS` | `portfolio/cas_import.py::archive_parsed()` — **PII-scrubbed before archival**, independently confirmed by the P3b security agent (`cas_import.py:55-62` `_scrub()` strips PAN/KYC before archiving; PDF bytes never persisted) | PII-scrubbed parsed CAS statement, for replay | HIGH (PII-scrubbing mechanism independently verified) |

## Consumer/producer detection rule applied

Per this engagement's convention: a migration/`Table(...)` definition's owning pipeline/route is
the producer; every other module reading the table without writing it is a consumer. A shared
library (`db/models.py` itself, `routes/_shared.py`) is never marked producer of a table — it only
declares the schema/plumbing, not the data.
