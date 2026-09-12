# Backlog

**The single "what's left" file.** Check here, not the git log, not the commit messages.

Every item below is open unless marked otherwise, and each links to the document that carries the
full detail. Gaps also live next to the thing they describe — `docs/database.md` §Known schema
gaps, `docs/api-reference.md` §Response-contract inconsistencies, `docs/design.md` §10/§12,
`docs/feature-catalog.md` §Known Gaps, `docs/PRD.md` §16/§17 — this file is the index over all of
them, not a replacement. When you close something, update both.

Severity is about *consequence if left alone*, not effort.

---

## Architectural stance — decided, not up for re-litigation

**Moved to [`../CLAUDE.md`](../CLAUDE.md) §Architectural Constraints, which is the authoritative
copy** — it lives there because Claude Code loads `CLAUDE.md` on every task, so the constraint is
actually enforced rather than merely recorded. `backend/CLAUDE.md`'s "Important Rules for Claude"
restates the backend-facing half.

In short: an extremely reliable monolith, sized for one operator and tens of users. PostgreSQL
only; Redis optional and never required; `ThreadPoolExecutor` not a broker; GitHub Actions cron
not an orchestrator; plain deploys not Kubernetes. Rejected outright — Kafka, RabbitMQ, Celery,
Temporal, Airflow, Kubernetes, microservices, a feature store, a data lake, event sourcing. The
risk here is maintenance fatigue from infrastructure one person has to operate alone, not scale.
Revisit only on a measured performance wall, never an anticipated one.

Nothing on that rejected list should be proposed or scaffolded without the human asking first.

---

## Needs a human decision — no code change closes these

~~**SEBI registration status**~~ — decided for current scope, not left open: the operator has
determined registration isn't required *because* distribution is scoped to a private, unpaid
circle of friends and family (now a stated non-goal, `PRD.md` §3), not offered to the public or
for consideration. This is the operator's own scope-based call, not a formal opinion from
qualified counsel, and it's contingent on that distribution scope holding — it reopens (needs
real counsel before, not after) if the circle ever grows past personal/informal sharing, a fee is
introduced, or it's advertised/marketed. The non-registration disclaimer stays live on every
recommendation surface as continued good practice, not as the thing that makes the determination
true. *(`PRD.md` §17.4, §3)*

~~**No legal review of the scraping surface**~~ — accepted risk for current scope, not left open:
unlike the SEBI item above, this one is *not* an audience-size argument — the cron pipelines hit
`screener.in`/`nseindia.com`/`bseindia.com`/`trendlyne.com`/`rbi.org.in`/AMFI at the same request
volume regardless of viewer count, so "just friends and family" doesn't itself reduce scraping
load. The operator has instead accepted the risk on narrower grounds: the data is used for
personal, non-commercial research, never resold or offered as a paid feed, nothing in the code
evades rate limits or misrepresents the client, and the posture is to comply immediately (stop
scraping a source) if that source ever objects, rather than to contest it. This is a risk
acceptance, not a legal conclusion — nobody has read these sites' actual ToS against Indian law.
It reopens (needs a licensed professional before continuing, not after) on materially higher
volume, resale/redistribution of scraped data, a distribution-scope change per the SEBI item's own
contingency, or if any source objects, rate-limits, or sends a cease-and-desist. *(`PRD.md` §17.2)*

~~**Bus factor of one**~~ — accepted risk for current scope, not left open: this can't actually be
*resolved* by any fact pattern — it stays true until a second engineer or a written handoff plan
exists — but the operator has judged that, for a project run privately with no SLA, no paying
customers, and nobody's real financial decisions depending on its uptime, building either isn't
justified by what's at stake today. The accepted downside if the operator becomes unavailable is
that the tool stops running, which costs the friends-and-family circle something but breaches no
commitment, since none was made. It reopens (needs a real second engineer or a written handoff
plan before this is treated as closed) if usage starts being depended on like real infrastructure,
or if distribution scope changes per the SEBI item's own contingency. *(`PRD.md` §17.1)*

| Item | Detail |
|---|---|
| **No real payments** — `users.tier` is set by an operator by hand. Pricing, processor, India tax/compliance and refund policy are all undecided; that decision precedes any engineering. | `PRD.md` §17.3 |

---

## Security & correctness

1. ~~**`GET /api/v1/consolidated/{symbol}` applies no rate limit before authenticating.**~~ — done.
   `_require_api_key_user()` now calls `_rate_limit()` (30/min per IP) before the DB lookup, so a
   stream of invalid `X-API-Key` attempts is bounded instead of costing an unbounded number of DB
   round trips.
2. **`cas_import`/`csv_import` still have no unique constraint, and no advisory lock either.**
   Both importers' idempotency is a read-then-write with no DB-level guard, so two concurrent
   uploads for the same account can both insert (the CAS same-scheme-across-folios *duplicate*
   bug found in this pass — see Data model below — was a same-request instance of this same class
   of gap; two concurrent *requests* can still race the same way this item already described). The
   broker-API-sync writer closed this exact gap for its own rows (`transactions.external_ref` +
   `uq_transactions_asset_external_ref`); the same fix, or the `pg_advisory_xact_lock` pattern
   `routes/_shared.py::claim_anonymous_rows_sync()` already uses, would close it for these two
   paths too, if it's ever worth doing. *(`database.md` §Known schema gaps #6)*
3. ~~**Portfolio Aggregator had no auth and no ownership scoping.**~~ — fixed. All 23 endpoints
   (including the 6 broker-API-sync ones) now resolve the caller's owner via the same
   `client_id`/signed-in-`user_id` shape `watchlist_items` already used
   (`routes.watchlist.resolve_owner()`), and every `profile_id`/`account_id`/`asset_id` path param
   is checked against that owner (`_owned_profile_id`/`_owned_account_id`/`_owned_asset_id`) before
   any read or write — a mismatch is always a 404, never a 403, so a caller can't distinguish
   "doesn't exist" from "not yours" and probe for other owners' ids. This closes the specific
   exploit this item used to describe: `POST /broker/{broker}/login-url` and `POST
   /broker/{broker}/connect` (which register `api_key`/`api_secret` and exchange a
   `request_token`/overwrite `access_token_enc`) now 404 on any `account_id` the caller doesn't
   own, so an attacker without that owner's `client_id` or session can no longer silently take over
   another account's broker connection. A separate, narrower ordering bug in this same landing —
   `broker_sync`'s ownership check used to run *after* its rate-limit/lock acquisition, letting an
   unauthorized caller burn another owner's rate-limit budget before hitting the 404 — was fixed
   alongside it (ownership now checked first). New migration: `ec7850b73d2f_add_profiles_ownership`
   adds `client_id`/`user_id` to `profiles`. **What's still genuinely open, from the same
   review**: `POST /broker/{broker}/login-url`/`connect` still has **no CSRF `state` parameter**
   binding the OAuth redirect back to the account that initiated it — a caller who already owns
   *some* account, or who can otherwise reach this port, could still race a `connect` call against
   another owner's in-flight login for the same `(account_id, broker)` if they can trigger or
   guess the timing. Deliberate for a localhost/Tailscale tool; this sub-feature needs the CSRF
   `state` fix before ever being exposed on a public interface. *(`api-reference.md`,
   `database.md`, `feature-catalog.md`)*
4. **`client_id` is a grouping key, not a security boundary.** Anyone holding one can read/write
   that browser's anonymous watchlist and positions. Claim endpoints are rate-limited and
   audit-logged, which bounds abuse without eliminating a targeted guess. *(`feature-catalog.md`)*
5. ~~**`GET /api/auth/me` and `POST /api/auth/logout` are entirely unrate-limited**~~ — done. Both
   now call `_rate_limit()` (60/min per IP), matching every neighboring auth endpoint.
6. **CAS/CSV import have no upload size cap** — was true, now fixed: all three endpoints (CAS
   import, CSV import, CSV preview) go through `routes/_shared.py::read_upload_capped()`, which
   rejects (413) anything over 20 MB instead of buffering an unbounded request body into memory
   first, and are now rate-limited *before* that read runs (not after — an independent-review
   finding: the original ordering meant `run_owned_db_call`'s own internal rate-limit check ran
   only after the file was already fully read into memory). A first-pass fix called `_rate_limit()`
   explicitly ahead of the read at each of the 3 call sites, with `skip_rate_limit=True` passed to
   `run_owned_db_call()` to avoid double-counting against the same sliding-window bucket — correct,
   but a second, independent adversarial-review round flagged the *shape* as a footgun for a future
   4th upload endpoint (the unsafe combination needs no deliberate action to reach; the safe one
   needs remembering an extra kwarg at a call site physically far from the line it depends on).
   Consolidated into one `rate_limited_upload()` helper that does both steps as a single call, so
   a caller can no longer separate them — all 3 endpoints now call this instead of two loose
   statements. Every one of the 3 endpoints (not just import-cas, the first-pass fix's original
   target) now has its own `test_..._consumes_exactly_one_rate_limit_slot` regression test.
   Two residual, disclosed (not silently assumed solved) gaps found by the same review passes:
   - A small, tightly-compressed malicious `.xlsx` within the 20 MB cap could still decompress
     into a very large in-memory DataFrame via `pandas.read_excel` (a "zip bomb" on the upload
     path) — the size cap bounds the *transport*, not the *decompressed* size. Worth a follow-up
     (e.g. a row-count ceiling in `csv_import.parse_broker_file`) if this is ever exposed beyond a
     trusted operator.
   - The 20 MB figure is currently aspirational for files between 1 MB and 20 MB: FastAPI's
     automatic `UploadFile = File(...)` injection parses the multipart body via Starlette's own
     `request.form()`, which enforces its own hardcoded `max_part_size` (1 MB, in the installed
     Starlette version) *before* `read_upload_capped()` — or any endpoint code — ever runs, with no
     way to override it through the declarative `File(...)` marker. A file in that range gets
     Starlette's generic 400 instead of this function's friendlier 413. Not a security regression
     (the real ceiling is *tighter* than advertised, not looser), but the stated cap doesn't fully
     hold — closing it needs rewriting the 3 upload endpoints to call
     `request.form(max_part_size=...)` manually instead of relying on FastAPI's automatic
     injection, a larger, separate change not attempted here. See `routes/_shared.py`'s own
     disclosure next to `read_upload_capped()`.
7. **No security headers on the Next.js frontend** — was true, now fixed: `next.config.ts` sets
   `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy:
   strict-origin-when-cross-origin` on every response. Deliberately not a full Content-Security-
   Policy — this app has no `dangerouslySetInnerHTML` and no inline scripts, but a CSP strict
   enough to matter (nonces/hashes for Next's own injected scripts) needs verifying against a real
   production build rather than added blind. Tracked here as a follow-up.
8. **Session cookie's `Secure` flag depends on `process.env.NODE_ENV === 'production'` exactly**
   (`frontend/lib/auth-cookie.ts`). A Docker/Compose deployment that runs the standalone server
   without explicitly exporting `NODE_ENV=production` (common if a container just runs `node
   server.js` from the `next build` output, or a reverse-proxy setup that strips/overrides env)
   would silently ship the 30-day session cookie without `Secure`. Not fixed in this pass — the
   safer fix (derive the flag from whether the request itself is HTTPS, or an explicit
   `COOKIE_SECURE` env var) touches the request-handling path and deserves its own verification
   pass rather than a blind one-line change.
9. **`analysis_error.reason` leaks a truncated raw exception string over SSE** — already tracked
   below for the single-stock analysis flow; this pass found the identical pattern a second time,
   independently, in `pipelines/market_picks_pipeline.py`'s own `analysis_error` SSE event. Not
   fixed in this pass (same fix in both places: map to a generic client-facing message, keep
   logging the real exception server-side).

---

## Data model

Ordered by consequence. None are live bugs today.

1. **No `ON DELETE` on any FK to `users`** (`sessions`, `api_keys`, `watchlist_items`,
   `positions`) or down the `profiles → accounts → assets → {holdings, valuations, transactions}`
   chain. Latent only because no deletion path exists — account deletion would FK-violate rather
   than cascade. *(#1)*
2. **`accounts.type` / `assets.type` have no `CHECK`**, unlike `users.tier`. A bad value would
   silently misclassify in `compute_networth()`'s loan-subtraction branch. *(#4)*
3. **`prices_daily.adj_close` has no production reader.** Written by two pipelines, read only in
   tests. Not a bug: `portfolio_valuation` reads the latest raw `close`, which is correct for
   current market value, and every *historical* series in the app comes from yfinance with
   `auto_adjust=True` — so adjustment correctness is already handled for every consumer that
   exists. The corporate-actions pipeline is pre-built infrastructure for a migration that hasn't
   happened. **Real risk is silent rot**, and this pass found the precise mechanism on both sides
   of it: `eod_sources.parse_bhavcopy()` only guards the 4 columns required to avoid `malformed`
   (`SYMBOL`/`DATE1`/`CLOSE_PRICE`/`PREV_CLOSE`) — a rename of any *other* column (e.g.
   `DELIV_PER`) parses "successfully" with zero logged signal, silently NULLing that field
   platform-wide indefinitely. `corporate_actions.parse_corporate_actions()` is weaker still: a
   parse failure is a bare `except: continue` logged only at `logger.debug` (invisible at this
   app's default INFO level, never routed through `observability.log_event`), and
   `corporate_actions_pipeline.py` has no health-gate equivalent to `eod_prices_pipeline`'s own
   "empty bhavcopy → error status → non-zero exit" pattern — a total rename of `symbol`/`exDate`
   silently zeroes the whole ingest with `ca_ingested actions=0` logged at plain INFO, identical to
   a genuinely action-free week. Cheapest mitigation: apply `eod_prices_pipeline`'s own
   fail-loudly pattern to `corporate_actions_pipeline`, and widen `parse_bhavcopy`'s required-field
   check to the fields report readers actually use (at minimum `DELIV_PER`). *(#5)*
4. **`assets.symbol` unindexed** (scanned by `csv_import` and `eod_prices_pipeline`);
   **`securities.isin` neither unique nor indexed** while `securities_master` dedupes by ISIN in
   Python. *(#3, #8)*
5. **`screener_stocks` is indexed on `nse_industry`/`sector`** but the API filters on `pe_ratio`,
   `market_cap_cr`, `rsi14`, `ema_trend`. Correct to seq-scan at ~500 rows — revisit only if the
   universe widens. *(#2)*
6. **`sessions` pruning is coupled to sign-in traffic** — both expiry deletes live inside
   `create_magic_link()`, so a deployment where everyone stays signed in never prunes. Storage
   growth, not an auth hole. *(#7)*
7. ~~**`cas_import.py`'s stale `by_amfi`/`by_isin` lookup dicts create duplicate MF assets.**~~ —
   fixed. `existing` was snapshotted once before the folio loop started and never updated when a
   new asset was created mid-loop — a scheme held via two SIP folios (or split across a folio
   merger) that matched neither folio's *existing* DB row used to create two separate `mf` assets
   for the same scheme instead of matching the one the import itself just created, splitting that
   scheme's holdings/XIRR across two rows. `import_cas()` now backfills both lookup dicts
   immediately on insert (the same pattern `csv_import.py` already used correctly). Fixing this
   also surfaced two further compounding bugs, both fixed alongside it in the same pass:
   `_write_transactions()`'s per-folio delete-then-insert would have wiped out an earlier folio's
   just-inserted rows for the same now-correctly-matched asset (fixed: delete only once per asset
   per `import_cas()` call, not once per folio) — and, caught one round later by an independent
   adversarial-review pass on this same fix, the `holdings` upsert ran once per folio via a plain
   `ON CONFLICT DO UPDATE SET units = EXCLUDED.units`, which now *overwrites* rather than sums once
   both folios resolve to the same asset: a scheme genuinely held across two folios with real
   non-zero balances in each (the exact scenario this fix targets) silently lost the
   first-processed folio's units, keeping only the last-processed folio's balance as the final
   stored total. Fixed by accumulating each asset's closing balance across all its folios during
   the loop and upserting the summed total once per asset afterward, not once per folio —
   regression-tested (`test_same_scheme_across_two_folios_in_one_statement_is_one_asset` now
   asserts the summed `holdings.units`, not just asset/transaction counts).
8. **`DELETE /api/portfolio/accounts/{id}` doesn't pre-check `broker_connections` before
   deleting**, unlike its existing `assets` pre-check (422 on a non-empty account). An account with
   a registered/connected broker but zero assets yet hits an unhandled `IntegrityError` on the
   `DELETE` (no `ondelete` on that FK either — see #1), surfacing as an opaque 503 instead of a
   clear 422. Same fix shape as the existing `assets` check, or `ondelete="CASCADE"` on that FK
   (defensible — a connection is disposable metadata, unlike an asset).
9. **No DB-level `CHECK` constraint on any monetary/quantity column, anywhere** — not isolated to
   `positions.entry_price`/`target_price`/`stop_loss` (already known, see API contract
   consistency's #9 below). `transactions.amount`/`.units`, `valuations.value`, `holdings.units`/
   `.avg_cost` all have zero backstop against a negative value; only `AssetIn.value`/`ValuationIn.
   value` get an app-level `Field(ge=0)` in the Pydantic models. Any future write path (a new
   import format, a manual SQL fix) has nothing stopping a negative price/quantity from silently
   corrupting XIRR or net-worth math.
10. ~~**`app_state`'s `cas_archive` namespace grows unbounded**~~ — fixed (90-day retention via
    `state_store.delete_older_than()` after every write, matching the pattern CLAUDE.md's own rule
    already requires for a namespace like this). `market_picks_pipeline`'s `market_picks_history`
    namespace was also flagged by this pass as technically matching that same rule's letter (no
    `delete_older_than` call) but is a deliberate exception, not a gap: unlike `cas_archive`
    (a debug/replay convenience), this namespace is real, deliberately-permanent product history —
    `GET /api/market-picks/history`'s `available_dates` and the track-record win-rate/alpha
    computation both depend on browsing arbitrarily old snapshots, the same "kept forever on
    purpose" shape as `verdict_history`. Growth is also slow (one row per weekly cron run, not
    per-request), so left as-is rather than pruned.
11. **`GET /api/watchlist/calendar` is an N+1 against `verdict_history`** — fans out via
    `asyncio.gather` over up to 200 symbols, and each one independently queries
    `verdict_history.detect_recent_changes()` (its own `load_history(sym, limit=2)` round trip).
    Thread-pooled, so not serialized, but still up to 200 individual `SELECT`s per page load
    instead of one batched `WHERE symbol = ANY(:symbols)` query. Low severity at this app's stated
    scale — worth revisiting if the per-identity watchlist cap ever grows materially past 200.

---

## API contract consistency

All in `api-reference.md` §Response-contract inconsistencies. Individually defensible; the cost is
that no client can rely on a uniform rule.

- **`peers` and `financials` lack the `unavailable` flag** their three sibling research add-ons
  use — an upstream outage returns a body byte-identical to "there genuinely is no data." The
  error counter fires server-side; it just never reaches the client. *(#1)*
- **Missing-`DATABASE_URL` splits three ways**: `503`, `200`+empty, `401`, `200`+`null` section.
  *(#2)*
- **Auth-vs-rate-limit ordering inverted on the three API-key endpoints** — an unauthenticated
  caller exhausts the IP budget then gets `429` where `401` is accurate. *(#5)*
- `409` vs `422` for state conflicts (#6); only one per-symbol endpoint 404s on unknown symbol
  while seven return empty `200` (#7); `/api/prices` silently drops malformed symbols (#8);
  `POST /api/positions` accepts negative prices while `PATCH` rejects negative shares (#9);
  `/api/watchlist/calendar` is unauthenticated and takes an arbitrary symbol list (#10);
  `/api/portfolio/concentration` logs under the wrong event prefix (#11); the CSV *preview* burns
  the write rate-limit bucket (#12); two `202` endpoints never report an unhealthy run back (#13);
  `analysis_error.reason` leaks a truncated raw exception string — the one break in otherwise
  uniform sanitization (#14).

---

## Signal engine & data quality

- **`UNKNOWN` signals keep full weight** in `final_score` rather than the remaining signals being
  renormalized — compresses the achievable range against fixed thresholds, biasing thin-history
  stocks toward HOLD. *(`feature-catalog.md` §Known Gaps)*
- **No back-tested calibration.** Signal weights, sector tilts, and verdict thresholds are
  reasoned defaults, not fitted to realized returns. A backtest harness would move this from
  "principled" to "calibrated."
- **~12 scraper assumptions were never verified against a live response** (Screener section ids,
  Trendlyne DOM, NSE XBRL tags, RBI table layout, the sector taxonomy). Each degrades to
  empty/`None` rather than a wrong value, and `tests_live/` covers the four
  highest-blast-radius ones weekly — the rest are unverified.
- **Market-picks source curation.** Per-source telemetry now ships (`telemetry/source_quality.py`); actually
  dropping or down-weighting sources in `_SOURCE_CREDIBILITY` is deliberately deferred until real
  telemetry accrues. Decide by reading the report, not by guessing.
- ~~**`change_pct` was invented as `0.0` (not `None`) on the primary quote path when yfinance had
  no `previousClose`**~~ — fixed. `tools/nse_tools.py::_build_quote_payload()` (the six-task
  pipeline's own quote path) now mirrors `api._fetch_live_price_sync()`'s existing "no prev close
  → `None`, never a fabricated flat day" convention. This mattered beyond display: `signals/
  volume.py::volume_signal()` reads `change_pct` to tell a volume spike's direction apart
  (accumulation vs. distribution) — the invented `0.0` was read as a genuine "not a down day,"
  silently resolving a real data gap toward the bullish `ACCUMULATION`/`STRONG_ACCUMULATION`
  verdict whenever the underlying data was actually missing. `volume_signal()` itself needed no
  change — it already treated a `None` `change_pct` correctly, per its own docstring; only the
  producer was fabricating a value instead of passing the gap through. `frontend/types/index.
  ts`'s `StockInfo.change_pct` is now `number | null`; its one real consumer (`ExchangeTable` in
  `dashboard-primitives.tsx`) already coalesced a missing value to 0 for display, so this needed
  no further frontend change. The identical fallback in `_screener_fallback_quote()` (used only
  when yfinance has no quote on either exchange) has since been fixed too — it also now returns
  `None` rather than a fabricated `0.0`. A follow-up review pass also found and fixed the same
  fabricated-default pattern one layer downstream: `core/schemas.py::_norm_exchange_quote()`'s
  `d.get("change_pct", 0.0)` didn't fire on a present-but-`None` key (a `dict.get` default only
  substitutes when the key is *absent*), so the `None` was silently turned back into `0.0` during
  normalization — fixed to `d.get("change_pct")`. `main.py`'s CLI report printer and
  `pipelines/market_picks_pipeline.py`'s `MarketPick.change_pct` had the identical bug (the
  former crashed formatting `None` with `:+.2f}`, the latter silently coerced back to `0`) — both
  fixed, and `frontend/types/index.ts`'s `MarketPick.change_pct` widened to `number | null` to
  match.
- ~~**Watchlist alert emails were not deduplicated across reruns**~~ — fixed.
  `pipelines/watchlist_alerts.py` had no record of "this (user, symbol, verdict_date, kind) alert
  was already emailed" — a second run on the same day (a `workflow_dispatch` retry, a manual
  `--force` rerun while `cache.is_fresh()` was still true) recomputed the identical
  "yesterday → today" diff and resent the exact same digest. `run()` now atomically *claims* each
  key against a `watchlist_alerts_sent` `app_state` namespace (keyed by date) via
  `_claim_alert_keys()` — a `state_store.mutate()` call returning only the subset NOT already
  claimed by another run — **before** sending, not after. An earlier version of this same fix (the
  prior deep-review pass) checked "already sent" up front and recorded the sent keys only
  afterward; an independent adversarial-review pass on that fix caught that this ordering still
  can't prevent two genuinely concurrent runs from both reading "not yet sent" and both dispatching
  the same email before either recorded it. Claiming first closes that window: at most one of two
  racing `mutate()` calls can ever see a given key as unclaimed. If the claimed batch's send then
  fails (SMTP down), the keys are released (`_release_alert_keys()`) so a transient failure doesn't
  permanently masquerade as "already sent" and silently drop a real alert forever — pruned to 3
  days' retention each run regardless, since only "today" is ever read. The corresponding cron
  workflow also gained a `concurrency` guard (see Engineering debt) as a second, independent layer
  against the same failure mode. This claim-before-send fix itself shipped with one more real gap,
  caught by a third review round: `_claim_alert_keys()` returned its local `claimed` set
  unconditionally, even when `state_store.mutate()`'s own transaction failed *after* the callback
  that populates `claimed` had already run (a connection blip, a serialization error) — `mutate()`
  correctly returns `None` on that failure, meaning the claim was never actually persisted, but the
  function still reported those keys as claimed and `run()` sent the email anyway. A later run's DB
  read would then show them as still unclaimed, reintroducing the exact duplicate-send this whole
  mechanism exists to prevent, via a DB-failure window instead of a concurrency window. Fixed:
  `_claim_alert_keys()` now returns an empty set whenever `mutate()` itself returns `None`,
  regardless of what the callback's side effect populated.
- **Corporate-actions/EOD-price scraper drift detection has real gaps** — see Data model's #3
  above for the precise mechanism on both `eod_prices_pipeline` (partial/optional-column drift
  uncaught) and `corporate_actions_pipeline` (near-total silent-failure blind spot).
- **The analyst's numeric-claim guardrail only cross-checks 9 named metrics**
  (`analyst/crew.py::_NUMERIC_FIELD_CHECKS` — P/E, ROE, ROCE, dividend yield, book value,
  sales/profit growth, EBITDA margin, market cap). A fabricated number for anything else the LLM
  might cite in prose (promoter/institutional holding %, debt-to-equity, a specific RSI/EMA value,
  a filing date) has no grounding check at all — `_analysis_support_issues` only flags
  *directional words* against a single shareholding snapshot, never a cited percentage value. Not
  a regression — a real, narrow guardrail surface worth widening (e.g. promoter-holding % is
  already available in `shareholding.shareholding_pattern` and would be cheap to add to the
  existing pattern-check table).

---

## Frontend & accessibility

Fixed (verified in this pass, not just claimed): global focus indicator, `muted` contrast at full
opacity, solid-fill ink, the retired `#6c71f0` accent, disclaimer legibility, `sell`/`accent` text
contrast on `card` (recomputed at ~5.11:1 / ~5.21:1 — both clear AA now, correcting the older
4.33/4.43 figures below), touch targets for `InfoTooltip` and the `sm` watchlist star (a `p-3
-m-3` hit-area trick brings both to ~38px — the code comment there claims 44px, which is itself a
small, harmless doc-code drift worth a one-line fix sometime). `PageShell` now gives every page a
skip-link plus `<header>`/`<main id="main">` landmarks, and `ConsolidatedCard` has a real focus
trap (`inert` on the background + manual Tab-wrap) — neither was tracked as fixed before this
pass. Still open — most in `design.md` §10/§12:

- **The mobile nav dropdown (`site-nav.tsx`) now has a real Tab-wrap focus trap** (`useFocusTrap`,
  same hook as `ConsolidatedCard`) plus an accessible name on the panel itself. The nav bar as a
  whole is still a plain `<div>`, not a `<nav>` landmark (only the surrounding `<header>` is real)
  — **heading order unaudited**.
- **Three inputs still use `focus:outline-none`** (down from five) and out-specify the global focus
  rule — each has its own ring/border so none are blind, but it's a second inconsistent treatment
  that also fires on mouse click.
- **Contrast still under AA for reduced-opacity `muted`** — `/70` 3.59, `/60` 2.95, `/50` 2.41,
  used at 30+ call sites, several at `text-[9px]`/`text-[10px]`/`text-xs`. Nothing a user must read
  should go below full `muted`.
- **`Skeleton` duplicated** byte-identically in four places; **no `warning` token**, so two
  components invented different substitutes; **page width unstandardised** across five `max-w-*`.
- **Dense tables have no mobile card layout** — horizontal scroll only. The Screener table
  specifically renders 12 columns per row, on the persona (§5's SME/momentum trader) most likely
  to be checking it mid-day on a phone rather than at a desk (see Product & UX below).
- ~~**`TickerSearch` misreported a backend outage as "symbol not found"**~~ — fixed.
  `components/ticker-search.tsx`'s validate handler never checked `res.ok` before branching on the
  parsed body — `GET /api/validate/{symbol}` returns `{found:false, valid:false}` with a real 503
  status when the backend is down, and the ignored status meant that read identically to a
  genuinely invalid ticker. Now checks `res.ok` first and routes a non-2xx response to the
  existing, distinct `'error'` state instead. This fix itself shipped with a real regression, caught
  one round later by an independent adversarial-review pass: `selectSuggestion()` (the "Also try"
  suggestion row) calls `validate()` directly without first resetting `validSymbol.current`, unlike
  `handleChange()` — so a stale symbol from a *prior* successful validation could survive into the
  new `'error'` branch and still fire on pressing Enter, silently analysing the wrong stock while
  the UI showed a connection-error message for a different one. Fixed by clearing
  `validSymbol.current` unconditionally at the top of `validate()` (not just in the branches that
  already did), plus a defense-in-depth `status === 'valid'` check added to the Enter-key handler
  (matching the CTA button's own pre-existing, correct `disabled={status !== 'valid'}` gating).
  Regression-tested end-to-end in `e2e/home.spec.ts` (confirmed to fail against the pre-fix code).
- **No `error.tsx`/`global-error.tsx` anywhere under `app/`.** Any render-time exception from a
  wrong-*type* (not just missing) field — e.g. an unexpected SSE payload shape, since
  `lib/useStockAnalysis.ts` parses `JSON.parse(e.data) as SSEMessage` with no runtime shape
  validation — has no route-level fallback UI, just the default Next.js crash screen. Most
  components are heavily `?.`/`?? []`-guarded against *missing* fields, so this is a narrow but
  real gap, not a systemic one.
- **No e2e coverage for two explicitly user-critical flows**: "I bought this" position *creation*
  (`e2e/portfolio.spec.ts` only covers reading/aggregating already-tracked positions, never the
  actual mark-as-bought action) and broker CSV/CAS statement import
  (`e2e/portfolio-aggregator.spec.ts` covers only axe-violations and asset-value editing).
- **`app/api/portfolio/[...path]/route.ts`'s catch-all proxy builds the upstream URL via
  `path.join('/')` with no `..`-segment validation.** Low severity given this whole feature is a
  disclosed personal/localhost-only tool with no auth passthrough anyway, but worth a segment
  allowlist given it's a genuine catch-all forwarding to the backend.
- ~~**An `UNKNOWN` signal's score rendered as `0.00`, visually identical to a genuinely-computed
  neutral score**~~ — fixed. `results-dashboard.tsx`'s Quant Signals card now renders `—` when
  `signal.value === 'UNKNOWN'` instead of `fmt(signal.score, 2)` (which was always `0` for an
  `UNKNOWN` signal by the backend's own convention) — a UI-layer fix for a real, visible instance
  of this product's own "Data > Opinion... missing is null, never a guessed value" principle being
  violated at the last mile, independent of the backend-level `signals/*.py` gap already tracked
  in `feature-catalog.md`'s Known Gaps. Regression-tested in `e2e/home.spec.ts` (confirmed to fail
  against the pre-fix code) — this closes the "zero test coverage" gap noted below for this
  specific fix, though `frontend/lib/*.ts` unit-test coverage (Engineering debt, above) remains open.

---

## Engineering debt

- **`api.py` is ~2,790 lines and holds 29 of the 61 routes.** Only watchlist, positions and the
  Portfolio Aggregator have been extracted to `routes/`. A deep architecture pass this round traced
  the full module dependency graph and found the extraction itself clean — no circular imports, no
  business logic leaking into `api.py` beyond what's already disclosed here.
- **`pipelines/market_picks_pipeline.py` has never been decomposed** — the largest module in the repo, with
  six phases sharing mutable state and threading/async coordination. Re-audited this round for a
  thread-safety bug specifically (shared dict/list/counter written from worker threads without a
  lock) — none found; every `ThreadPoolExecutor` result is collected single-threaded via
  `as_completed`, and the three explicit `threading.Lock()` usages are all correctly scoped.
- **No typed config module.** Closer to ~50 `os.getenv`/`os.environ` call sites across `backend/`
  today (re-counted this round), not the "~20" this doc previously estimated — the gap has grown
  as broker sync/portfolio-aggregator/API-keys landed, not shrunk. `docs/setup.md` is still the
  closest thing to a schema. One concrete, low-risk gap in the existing startup-warning coverage:
  `_log_startup_config_warnings()` checks for zero/multiple LLM keys and a missing
  `TRUSTED_PROXY_SECRET`, but not a malformed-but-present `DATABASE_URL` or an invalid
  `PORTFOLIO_ENCRYPTION_KEY` — both fail only at first use, reading identically to "the database is
  down" / "encryption isn't configured" in logs rather than "the connection string/key itself is
  wrong," costing debugging time. A cheap format-only check (no live connection) for both would fit
  the same pattern as the two existing checks.
- **Seven near-duplicate `_nse_session()` wrappers**, kept deliberately for test-patch
  compatibility — an eighth NSE integration means a ninth copy.
- **No user-behaviour analytics**, so every KPI in `PRD.md` §12 except the track record is
  unmeasurable today.
- **Frontend business logic (`frontend/lib/*.ts`) has zero unit tests** — `useStockAnalysis.ts`'s
  SSE event parsing, the `watchlist.ts`/`positions.ts`/`auth.ts` shared-cache-plus-generation-
  counter pattern, and `client_id` generation are all exercised only indirectly through Playwright
  specs where, per `frontend/CLAUDE.md`'s own note, every backend response is mocked. A fast
  Vitest/Jest suite for `lib/*.ts` in isolation (SSE parsing edge cases especially) would catch a
  regression Playwright's mocked, happy-path-oriented specs are less likely to.
- **`main.py`'s CLI has one narrow, harmless divergence from `api.py`'s SSE path**: on a pure
  cache-hit, the CLI's early-return branch calls `save_verdict_snapshot(..., signal_context=None)`
  — it never runs the signal engine at all on that path, while `api.py` always computes
  `signal_context` before checking cache freshness. `verdict_history.save_snapshot()`'s own
  `COALESCE` against the prior stored value means the *database* never ends up wrong from this, but
  the CLI's own printed report on a full cache hit lacks a signal breakdown the equivalent web
  request would show. Worth a one-line docstring note in `main.py` if not fixed outright.

---

## Deployment & operations

Not previously tracked as its own section — surfaced by a dedicated ops/deployment review this
round. Severity here is about operational risk (data loss, a job silently going dark, a widened
attack surface), not user-facing correctness.

1. **No documented Postgres backup strategy.** `docs/database.md` warns to keep
   `PORTFOLIO_ENCRYPTION_KEY` separate from "a database backup," implying one exists, but no
   `pg_dump` cron, retention policy, or restore procedure is documented anywhere. Every piece of
   this app's durable state funnels into this one Postgres instance (`app_state` included) — losing
   the `postgres_data` volume loses everything with no recovery path. Fits this repo's existing
   "GitHub Actions cron, not a new orchestrator" pattern: a documented nightly `pg_dump | gzip` to
   off-host storage plus a stated retention window is the natural shape, not new infrastructure.
2. ~~**`docker-compose.yml`'s `POSTGRES_PASSWORD` defaulted to a fixed, publicly-documented
   value (`stockresearch`) if unset in `.env`**~~ — fixed. Both interpolation sites now use
   Compose's `${VAR:?message}` required-variable syntax — `docker compose up` refuses to start
   without a real password, rather than silently accepting the weak default. `.env.example` and
   `docs/deployment.md`'s quickstart now document the requirement and how to generate one.
3. **The five GitHub Actions cron pipelines had no `timeout-minutes` or `concurrency` guard** —
   fixed for all five (`sme-cron.yml`, `screener-cron.yml`, `eod-prices-cron.yml`,
   `watchlist-alerts-cron.yml`, `market-picks-cron.yml`): each now sets a `timeout-minutes` sized to
   its own workload and a `concurrency` group (`cancel-in-progress: false`, so an overlapping
   trigger queues rather than racing the in-flight run) to stop a hung run — or a
   `workflow_dispatch` fired while the schedule is still in flight — from launching a second
   concurrent pipeline against the same tables. Most of these pipelines' upserts are idempotent, so
   an overlap was "wasteful, not corrupting" — except `watchlist_alerts.py`, which sends real
   emails; that job now also has its own application-level dedup guard (see Signal engine & data
   quality above) as a second, independent layer.
4. **Both `backend/Dockerfile` and `frontend/Dockerfile` run as root** — no `USER` directive in
   either, so a container-escape or dependency RCE gets root inside the container for free. Neither
   image needs root at runtime (installs happen at build time). Not fixed in this pass — adding a
   non-root user is simple in principle but changes runtime file-ownership assumptions for the
   `backend_output` volume mount, which deserves a quick verification pass (container actually
   starts and can still write to `output/`) rather than a blind Dockerfile edit.
5. **`docker-compose.yml` maps the backend's port directly to the host (`8000:8000`)** even though
   the frontend only ever needs to reach it over the internal Docker network
   (`API_URL=http://backend:8000`). Once a reverse proxy is added in front of the frontend for
   production TLS (as `docs/deployment.md` instructs), this leaves `/api/*` also reachable
   directly, bypassing `ALLOWED_ORIGINS` (a browser-only protection) and `TRUSTED_PROXY_SECRET`
   entirely for a caller hitting `:8000` directly. Not fixed in this pass — the right fix depends on
   the deployment (drop the mapping in a production compose override vs. firewall the port), which
   is an operator decision this doc shouldn't make unilaterally.
6. **No healthcheck on the `backend`/`frontend` services themselves** — `postgres`/`redis` have
   one; `backend`/`frontend` don't, and `frontend`'s `depends_on: [backend]` has no
   `condition: service_healthy`, so it can start serving before the backend actually accepts
   connections. Combined with `SENTRY_DSN` shipping commented-out in `.env.example` and nothing
   polling the existing `GET /health` endpoint, a production deployment following the compose
   quickstart as documented gets no automated signal at all of a crashed backend process. Not fixed
   in this pass — adding a `healthcheck` block and documenting an external uptime check (even a
   free third-party pinger on `/health`) as a "day-one" step in `docs/deployment.md` is the shape of
   the fix.
7. **`frontend/Dockerfile` pins `node:20-slim`**, past active LTS as of this pass — worth bumping to
   `node:22-slim` (current LTS) on the next Dockerfile touch.
8. **`backend/requirements.txt` has no lockfile** — disclosed in the file's own comment already;
   re-confirmed still true. Wide-enough version ranges mean two `docker compose up --build` runs
   weeks apart aren't guaranteed to resolve identical transitive dependencies.

---

## Product & UX

A genuine critique of the shipped product, not a restatement of the roadmap below — surfaced by a
dedicated product/UX review this round, walking the actual pages against `PRD.md`'s stated
journeys and trust framework (§6–§7).

- **A concrete "Data > Opinion" violation, now fixed** — see Frontend & accessibility above
  (`UNKNOWN` signal rendering as `0.00`). Worth naming here too since it's the clearest example
  found this round of the product's own stated principle (§4: "a missing scraped field is `null`,
  never a guessed plausible-looking value") being violated at the UI layer specifically, not just
  the already-disclosed backend engine gap.
- **The SME/momentum-trader persona (§5) has an internal contradiction, visible directly in the
  shipped freshness table.** That persona is described as wanting to "catch a real cross signal
  before it's obvious from price action," checking "daily/near-daily during active trading
  periods" — but SME Signals refreshes once, on a weekday cron, ~3 hours after close
  (`feature-catalog.md`'s own Data Freshness table), and intraday data is a stated non-goal (§3).
  A persona defined by wanting to catch a signal *during* active trading is being served a
  screener that, by this product's own batch-fetch architecture, can only ever report what already
  happened after the market closed. Not a call to add intraday data (explicitly out of scope,
  correctly) — flagged because the persona description itself overstates what this screener can
  deliver, which is a copy/positioning fix, not an engineering one.
- **Cross-mode verdict disagreement is only ever reconciled in the one place fewest users will
  find it.** `consolidated-card.tsx::verdictsDisagree()` exists specifically because Stock
  Analysis (3-tier) and Market Picks (4-tier) run independently and can legitimately disagree —
  but that reconciliation only surfaces inside the search box's consolidated modal. A user who
  stars a stock as BUY from Market Picks and then opens its full Stock Analysis report (an
  encouraged, plausible next step) sees a completely separate `results-dashboard.tsx` with no
  acknowledgment a different verdict exists elsewhere for the same symbol. The comparison logic
  already exists; surfacing a small "Market Picks called this WATCHLIST on [date]" note on the
  analysis hero when a divergent pick exists would reuse it.
- **The trust pipeline that's supposed to be the product's core differentiator (§7 — quant
  signals → guardrails → LLM → track record) only narrates itself on failure.** The "⚠ Analysis
  degraded" banner exists, but on every normal, successful run (the overwhelming majority) a user
  sees a BUY/HOLD/SELL badge and a confidence label with nothing that says "this passed 4
  independent checks before you saw it" — the disclaimer's own wording ("generated by an AI
  model") reads, in the common case, as exactly the generic "an AI said BUY" positioning §7 exists
  to differentiate against. Compounding this: the aggregate track record (win-rate, alpha vs.
  Nifty — PRD §12's one "real and computed today" metric) lives only at `/market-picks/history`;
  the per-stock `VerdictTimeline` strip on the analysis page shows that symbol's own call history
  but never links out to the aggregate page, so the strongest trust signal in the product is
  structurally disconnected from the page where a user is actually deciding whether to trust a
  call.
- **The same concept has at least four names across the product**: "verdict" (results-dashboard's
  own prose), `recommendation` (the underlying field), "Signal Verdict" (the sidebar's own quant
  card), "Quant signal" (`consolidated-card.tsx`), "rating"/"pick" (Market Picks), "Track Record"
  (the nav label for the aggregate page). None is wrong in isolation, but a user moving between
  pages has no fixed vocabulary to hold onto.
- **The Screener table renders 12 columns per row with horizontal-scroll-only mobile handling** —
  see Frontend & accessibility above; called out again here specifically because it's the surface
  most exposed to the SME/momentum persona, who per §5 is more likely mid-day on a phone than at a
  desk.
- **`/pricing` isn't in the primary nav** — reachable only via a direct URL or from `/api-keys`
  (which links to it for free-tier accounts). A signed-out visitor curious "does this cost
  anything" has no path to it otherwise, unlike every other page (Compare, SME Signals, Screener,
  API Keys), all of which are correctly in `site-nav.tsx`'s `LINKS`.
- **`/portfolio-aggregator` ("Net Worth" in the nav) gives a first-time visitor zero framing that
  it's a separate tool** from the "Portfolio" page they likely just came from — both are genuinely
  well-designed on their own terms (each has a real, deliberate empty state), but nothing on first
  load of either says "these are unrelated" the way the code comments explaining the split already
  do internally.

---

## Product roadmap

**Now:** push notifications, better screener filters. *(`PRD.md` §15)*

**Not yet speced** — each needs the normal brainstorm → spec → plan cycle, not straight to code:

1. **Data provenance on every number** — store `{value, source, updated_at}` rather than a bare
   value, so any figure in a report traces to where and when it came from. Highest trust upgrade
   per unit of work.
2. **A single `MarketDataService`** all consumers go through, instead of each fetching
   independently. Real refactor touching most of the codebase; needs its own scope decision.
3. **Version the AI prompts** — store `prompt_version`/model/cost/latency alongside each analysis
   so a recommendation is traceable to what produced it. Additive; could ride on the existing
   `analysis` cache.
4. **Risk layer** — position sizing, stop-loss-hit tracking, portfolio concentration/correlation.
5. **Portfolio insights beyond valuation** — allocation drift, beta, overlapping-business risk.
   Needs historical allocation snapshots, which don't exist.
6. **Investment journal** — capture thesis/catalysts/risks at buy time, prompt for review later.
7. **"What changed since yesterday" digest** — turns the app from lookup tool into daily
   companion. Blocked on a notification channel decision.
8. **Fundamentals store** — the next data-layer sub-project after the EOD price store.

**Untested, closes on first real use:** CAS PDF and broker CSV import are covered by synthetic
fixtures, but nobody has run a real CAMS/KFintech PDF or a real broker export through them. The
untestable part is third-party (`casparser`'s extraction of your actual statement layout) and your
broker's actual CSV quirks — more synthetic fixtures would re-test the same code paths.

- **~~HDFC Securities and Paytm Money broker-sync should be labeled experimental/beta~~ — done.**
  `BrokerRow` (`frontend/app/portfolio-aggregator/page.tsx`) now shows a "beta" tag for both. Still
  open: actually completing one successful live sync against a real account for each, to confirm
  their REST shape (base URL, endpoint paths, checksum-signing scheme, response field names) —
  currently inferred from public docs/SDK snippets, not verified against a live response, since
  this sandbox's outbound fetches to both developer portals are blocked (403). Zerodha's
  `kiteconnect` integration doesn't carry this caveat (verified against the installed package's
  real API surface). See `backend/CLAUDE.md`'s "Broker API sync" §8 for the full disclosure.

---

## Declined — decided "no", not "later"

- **IPO grey-market premium (GMP).** Unregulated, informal, SEBI has warned it doesn't reflect a
  security's value, and it exists only on grey-market portals with materially different
  reliability and ToS risk than the regulator/vendor sources this codebase limits itself to.
  Revisit only if a reliable, ToS-compatible source appears. *(`PRD.md` §15)*
- **Non-Indian markets, live trading/brokerage execution, and an intraday terminal** — non-goals.
  *(`PRD.md` §3)*
