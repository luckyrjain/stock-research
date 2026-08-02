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

| Item | Detail |
|---|---|
| **SEBI registration status** — the product issues BUY/SELL calls with targets and stop-losses to Indian retail investors and publishes a track record. Whether that is regulated activity has never been assessed. A non-registration disclaimer now ships on every recommendation surface, but **a disclaimer is risk-reduction, not compliance**, and it *asserts* non-registration — correct it if that's wrong. | `PRD.md` §17.4 |
| **No legal review of the scraping surface** — screener.in, nseindia.com, bseindia.com, trendlyne.com, rbi.org.in, on a recurring schedule, at beyond-hobby scale. No ToS review by counsel. | `PRD.md` §17.2 |
| **No real payments** — `users.tier` is set by an operator by hand. Pricing, processor, India tax/compliance and refund policy are all undecided; that decision precedes any engineering. | `PRD.md` §17.3 |
| **Bus factor of one.** | `PRD.md` §17.1 |

---

## Security & correctness

1. **`GET /api/v1/consolidated/{symbol}` applies no rate limit before authenticating.**
   `_require_api_key_user()` does a DB lookup on every request, and only rate-limits *after* a key
   resolves — so invalid `X-API-Key` attempts are unbounded, each costing a DB round trip, with no
   IP throttle and no rate-limit middleware. Keys are 256-bit (`secrets.token_urlsafe(32)`), so
   this is **not** a credential brute-force risk; it is unauthenticated DB-load amplification.
   One `_rate_limit()` call before the lookup fixes it. *(`api-reference.md` §Response-contract
   inconsistencies #3)*
2. **`transactions` has no unique constraint.** Both importers' idempotency is a read-then-write
   with no DB-level guard, so two concurrent uploads of overlapping tradebooks can both insert.
   Needs a partial unique index — i.e. a migration. *(`database.md` §Known schema gaps #6)*
3. **Portfolio Aggregator has no auth and no ownership scoping.** All 17 endpoints accept any
   profile id from any caller, and the six tables hold real personal financial data. Deliberate
   for a localhost/Tailscale tool — but it must not be exposed on a public interface as-is.
   *(`api-reference.md`, `database.md`, `feature-catalog.md`)*
4. **`client_id` is a grouping key, not a security boundary.** Anyone holding one can read/write
   that browser's anonymous watchlist and positions. Claim endpoints are rate-limited and
   audit-logged, which bounds abuse without eliminating a targeted guess. *(`feature-catalog.md`)*
5. **`GET /api/auth/me` and `POST /api/auth/logout` are entirely unrate-limited**, despite
   `/api/auth/me` doing a DB session lookup per anonymous call.
   *(`api-reference.md` #4)*

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
   happened. **Real risk is silent rot**: an NSE format change would break the parser and nothing
   would surface it. Cheapest mitigation is wiring the CA parse into the existing
   `source_health`/error-counter machinery. *(#5)*
4. **`assets.symbol` unindexed** (scanned by `csv_import` and `eod_prices_pipeline`);
   **`securities.isin` neither unique nor indexed** while `securities_master` dedupes by ISIN in
   Python. *(#3, #8)*
5. **`screener_stocks` is indexed on `nse_industry`/`sector`** but the API filters on `pe_ratio`,
   `market_cap_cr`, `rsi14`, `ema_trend`. Correct to seq-scan at ~500 rows — revisit only if the
   universe widens. *(#2)*
6. **`sessions` pruning is coupled to sign-in traffic** — both expiry deletes live inside
   `create_magic_link()`, so a deployment where everyone stays signed in never prunes. Storage
   growth, not an auth hole. *(#7)*

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
- **Market-picks source curation.** Per-source telemetry now ships (`source_quality.py`); actually
  dropping or down-weighting sources in `_SOURCE_CREDIBILITY` is deliberately deferred until real
  telemetry accrues. Decide by reading the report, not by guessing.

---

## Frontend & accessibility

Fixed on this branch: global focus indicator, `muted` contrast, solid-fill ink, the retired
`#6c71f0` accent, disclaimer legibility. Still open — all in `design.md` §10/§12:

- **No focus trap** in modals/dropdowns; **no skip-to-content link**; **no landmark elements**
  (`<main>`/`<nav>`/`<header>` unused); **heading order unaudited**.
- **Touch targets below 24px** — `InfoTooltip` trigger is 14×14, the `sm` watchlist star ~16.
- **Five inputs use `focus:outline-none`** and out-specify the global focus rule. Each has its own
  ring/border so none are blind, but it's a second inconsistent treatment that also fires on mouse
  click.
- **Contrast still under AA**: `sell` 4.33 and `accent` 4.43 as text on `card`; every
  reduced-opacity `muted` (`/70` 3.59, `/60` 2.95, `/50` 2.41). Nothing a user must read should go
  below full `muted`.
- **`Skeleton` duplicated** byte-identically in four places; **no `warning` token**, so two
  components invented different substitutes; **page width unstandardised** across five `max-w-*`.
- **Dense tables have no mobile card layout** — horizontal scroll only, on a mobile-heavy audience.

---

## Engineering debt

- **`api.py` is ~2,760 lines and holds 29 of the 57 routes.** Only watchlist, positions and the
  Portfolio Aggregator have been extracted to `routes/`.
- **`market_picks_pipeline.py` has never been decomposed** — the largest module in the repo, with
  six phases sharing mutable state and threading/async coordination.
- **No typed config module.** ~20 env vars read via scattered `os.getenv`; `docs/setup.md` is the
  closest thing to a schema.
- **Seven near-duplicate `_nse_session()` wrappers**, kept deliberately for test-patch
  compatibility — an eighth NSE integration means a ninth copy.
- **No user-behaviour analytics**, so every KPI in `PRD.md` §12 except the track record is
  unmeasurable today.

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

---

## Declined — decided "no", not "later"

- **IPO grey-market premium (GMP).** Unregulated, informal, SEBI has warned it doesn't reflect a
  security's value, and it exists only on grey-market portals with materially different
  reliability and ToS risk than the regulator/vendor sources this codebase limits itself to.
  Revisit only if a reliable, ToS-compatible source appears. *(`PRD.md` §15)*
- **Non-Indian markets, live trading/brokerage execution, and an intraday terminal** — non-goals.
  *(`PRD.md` §3)*
