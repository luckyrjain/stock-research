# Output Schema

**Scope.** This doc covers **response payload shapes** — the merged report, the `MarketPick`
shape, standalone endpoint response bodies, and the on-disk cache-file formats. It does *not*
cover how to call anything.

The **request contract** — every endpoint's method, path, auth, query/path params, request body,
status codes, rate limits, and cache TTLs — lives in [`api-reference.md`](api-reference.md).
The two are deliberately non-overlapping: when you need to know *what a field means*, read here;
when you need to know *how to make the call*, read there.

## Per-symbol cache files

Each symbol gets its own folder under `backend/output/<SYMBOL>/` (or, when `REDIS_URL` is set, the same
data lives in Redis and disk becomes a fast local mirror/fallback — see `cache.py` and backend/CLAUDE.md's
"Redis-backed cache for multi-host deployments" section). One file per cache "task", each with its
own TTL (`cache.TTL_HOURS`):

```text
backend/output/TCS/
├── stock_info.json        # 1h
├── research.json          # 24h
├── news.json              # 1h
├── shareholding.json      # 168h (7 days)
├── mf_holdings.json       # 168h (7 days)
├── filings.json           # 1h (default TTL — not in TTL_HOURS)
├── analysis.json          # 24h
├── price_history.json     # 6h  — shared sparkline/technical-signal series
├── peers.json             # 24h — standalone, outside ALL_DATA_TASKS
├── financials.json        # 24h — standalone
├── insider_activity.json  # 24h — standalone
├── street_consensus.json  # 24h — standalone
└── report_2026-05-06.json
```

`fii_dii_flow` and `macro_context` (both 24h) are cached under a fixed `"_MACRO"` pseudo-symbol
(market-wide, not per-symbol), and `index_history` (24h) under a `"NSEI"` pseudo-symbol — neither
lives under a real ticker's folder.

Each per-task cache file includes `_meta.fetched_at`:

```json
{
  "symbol": "TCS",
  "...": "...",
  "_meta": {
    "fetched_at": "2026-05-06T12:36:05.017134+00:00"
  }
}
```

The merged `report_<DATE>.json` strips `_meta` from all nested sections, but surfaces each
task's own real fetch timestamp separately via the top-level `data_freshness` field (see below) —
`generated_at` alone is stamped fresh on every report-assembly call regardless of whether any
underlying task was actually refetched, so a 7-day-stale `shareholding` table would otherwise
still read as "updated today."

---

## Merged report shape

This is `main._build_report()`'s return value — produced by both the CLI (`main.py`) and
`api.py`'s `/api/analyse/{symbol}` SSE endpoint's final `done` event (`frontend/types/index.ts`'s
`Report` interface is the canonical TypeScript mirror; the two are kept in lockstep by
convention, see backend/CLAUDE.md's "Important Rules for Claude").

```json
{
  "symbol": "TCS",
  "generated_at": "2026-05-06",
  "data_freshness": {},
  "analysis": {},
  "degraded": false,
  "signals": {},
  "stock_info": {},
  "research": {},
  "news": [],
  "holdings": {},
  "filings": [],
  "filings_summary": {},
  "mf_holdings_trend": []
}
```

### Top-level fields

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Uppercased stock symbol |
| `generated_at` | string | Run date in `YYYY-MM-DD` format — stamped fresh on every assembly, not a per-task freshness signal (see `data_freshness`) |
| `data_freshness` | object | `{stock_info, research, news, shareholding, mf_holdings, filings}` — each task's own real `_meta.fetched_at` ISO timestamp (or `null`), captured before `_meta` is stripped |
| `analysis` | object | Final LLM analyst output (see below) |
| `degraded` | boolean | `true` when every configured LLM provider failed (or failed guardrails past its retry) and `analysis` is `crew.py`'s generic safe-fallback HOLD, not a real analyst call. A sibling of `analysis` (not nested inside it) so it isn't subject to the four-file analyst-schema lockstep rule — the LLM never produces this field. See backend/CLAUDE.md's "LLM cost instrumentation + cross-provider failover" point 3 |
| `signals` | object | Quantitative signal engine output (see below) |
| `stock_info` | object | Quote and company information |
| `research` | object | Fundamental ratios, about text, quarterly trend |
| `news` | array | News article list |
| `holdings` | object | Shareholding pattern + MF holdings + promoter pledge % |
| `filings` | array | Raw corporate filings list (`[{title, desc, date, category, attachment}]`) — also what `signals.filings` and `filings_summary` are both derived from |
| `filings_summary` | object | Best-effort classification of `filings` — see below |
| `mf_holdings_trend` | array | Per-fund stake deltas vs. the prior stored quarterly snapshot — see below |

---

### `analysis`

```json
{
  "symbol": "TCS",
  "recommendation": "HOLD",
  "confidence": "MEDIUM",
  "summary": "A short multi-sentence recommendation.",
  "valuation": {
    "verdict": "Fairly Valued",
    "comment": "Comment with cited numbers."
  },
  "business_quality": "Short explanation of operating quality.",
  "bull_factors": ["Plain string factor"],
  "bear_factors": [
    { "metric": "Beta", "value": 0.272, "comment": "Example structured factor" }
  ],
  "key_risks": ["Risk 1", "Risk 2"],
  "news_sentiment": "Neutral",
  "news_highlights": "Short summary",
  "institutional_trend": "Stable institutional ownership."
}
```

- `recommendation`: `BUY` | `SELL` | `HOLD`
- `confidence`: `HIGH` | `MEDIUM` | `LOW`
- `bull_factors` and `bear_factors` may contain plain strings or structured objects — the frontend normalizes both
- `news_highlights` may be a string or array of strings
- Adding/removing any field here requires updating `config/analyst.json`'s `output_schema`,
  `crew._validate_analysis_payload()`, `main._build_report()`, and `frontend/types/index.ts`'s
  `Analysis` interface in lockstep — see backend/CLAUDE.md's "Important Rules for Claude"

---

### `signals`

```json
{
  "final_score": 0.42,
  "verdict": "BUY",
  "signals": {
    "valuation":  { "name": "valuation", "value": "Fairly Valued", "score": 0.3, "meta": {} },
    "growth":     { "name": "growth",    "value": "Strong",        "score": 0.8, "meta": {} },
    "volume":     { "name": "volume",    "value": "Above Average", "score": 0.5, "meta": {} },
    "filings":    { "name": "filings",   "value": "Neutral",       "score": 0.0, "meta": {} },
    "technical":  { "name": "technical", "value": "Bullish",       "score": 0.4, "meta": {} },
    "macro":      { "name": "macro",     "value": "Tailwind",      "score": 0.2, "meta": {} }
  }
}
```

- `final_score`: –1 (strong sell) to +1 (strong buy), a weighted sum of all six signals
- `verdict`: `BUY` | `WATCHLIST` | `HOLD` | `AVOID` | `SELL` (`signals/engine.py::run_signal_engine`'s
  thresholds: `>0.5` BUY, `>0.1` WATCHLIST, `>-0.3` HOLD, `>-0.6` AVOID, else SELL — a 5-tier
  scale, not the 3-tier `BUY`/`HOLD`/`SELL` the `analysis.recommendation` field uses; the frontend's
  `SignalSummary.verdict` type is `'BUY' | 'SELL' | 'HOLD' | string` to accommodate this)
- `signals.technical` and `signals.macro` are the two signals that do their own I/O (RSI14/EMA20/50
  off the cached `price_history` series, and market-wide FII/DII flow + RBI rate/inflation,
  respectively — see `docs/tools.md`) rather than reading only from already-fetched data
- **Sector-aware weight tilts**: the baseline weights (`valuation` 0.4, `growth` 0.4, `volume` 0.2,
  `filings` 0.2, `technical` 0.2, `macro` 0.15) are tilted per `stock_info.sector` for three
  economically-similar groups (`signals/engine.py::_weights_for_sector`) — rate-sensitive
  (`Financial Services`/`Real Estate`/`Utilities`: valuation + macro up, growth down), growth
  (`Technology`/`Communication Services`/`Healthcare`: growth up, macro down), and cyclical
  (`Basic Materials`/`Energy`/`Industrials`/`Consumer Cyclical`: technical + volume up, valuation +
  growth down). Every override reallocates weight from other signals so each group's weights still
  sum to the baseline 1.55. Any other/missing sector uses the unchanged default weights. See
  backend/CLAUDE.md's "Sector-aware signal weights" section for the full disclosed-limitation writeup
  (whether yfinance's `sector` field is actually GICS-taxonomy-shaped for NSE/BSE symbols was never
  verified against a live response).

---

### `stock_info`

```json
{
  "symbol": "TCS",
  "exchange": "NSE",
  "company_name": "Tata Consultancy Services Limited",
  "current_price": 2396.9,
  "change_pct": -4.95,
  "market_cap_cr": 867219.0,
  "pe_ratio": 17.6,
  "52w_high": 3630.5,
  "52w_low": 2346.2,
  "sector": "Technology",
  "prices_by_exchange": {
    "NSE": { "exchange": "NSE", "current_price": 2396.9, "change_pct": -4.95 },
    "BSE": { "exchange": "BSE", "current_price": 2398.1, "change_pct": -4.89 }
  }
}
```

---

### `research`

```json
{
  "symbol": "TCS",
  "ratios": {
    "Market Cap": "867219",
    "Stock P/E": "16.6",
    "ROCE": "76.7",
    "ROE": "65.2"
  },
  "quarterly_trend": {
    "quarters": ["Jun 2025", "Sep 2025", "Dec 2025", "Mar 2026"],
    "revenue": [61237, 62612, 63973, 64479],
    "eps": [30.2, 31.1, 32.4, 33.0],
    "operating_margin": [24.3, 24.8, 24.1, 24.5]
  },
  "nse_fallback_ratios": null,
  "about": "Company description..."
}
```

- `quarterly_trend` (optional, omitted when absent) — oldest-first Sales/EPS/(optional)
  operating-margin mini-trend, capped at 8 quarters, from Screener's Quarterly Results table.
  `operating_margin` is independently optional (several sectors, e.g. banks/NBFCs, routinely omit
  Screener's OPM % row even when Sales/EPS are present).
- `nse_fallback_ratios` (optional, omitted when absent) — `{eps, source: "nse_xbrl",
  as_of_date}`, present only when Screener's own `ratios` came back completely empty and NSE's own
  XBRL results filings had a usable EPS. Deliberately EPS-only (see `docs/tools.md`).

---

### `news`

The merged report flattens `news.json.articles` into a top-level array:

```json
[
  {
    "title": "Headline",
    "source": "Publisher",
    "published_at": "Fri, 06 May 2026 10:30:00 GMT",
    "url": "https://example.com/article"
  }
]
```

---

### `holdings`

```json
{
  "symbol": "TCS",
  "shareholding_pattern": {
    "Promoters": 71.77,
    "FIIs": 9.66,
    "DIIs": 13.34,
    "Public": 5.16
  },
  "pledge_pct": 0.0,
  "mutual_funds": [
    { "fund": "SBI Nifty 50 ETF", "holding_pct": 1.25 }
  ]
}
```

`pledge_pct` (optional) is promoter pledge %, parsed as its own field rather than folded into
`shareholding_pattern`.

---

### `filings_summary`

Best-effort keyword/regex classification of the raw `filings` list — see
`signals/filings_classifier.py::classify_filings()`. Never guesses; every field is `None`/`[]`
when nothing in the fetch window matches a known pattern.

```json
{
  "corporate_actions": [
    { "type": "dividend", "date": "2026-05-10", "title": "Board recommends final dividend" }
  ],
  "rating_action": {
    "agency": "CRISIL",
    "action": "upgrade",
    "from_rating": "AA",
    "to_rating": "AA+",
    "date": "2026-04-02",
    "title": "CRISIL upgrades long-term rating"
  },
  "next_results_date": "2026-07-15"
}
```

- `corporate_actions`: one entry per matching filing (dividend/split/bonus/buyback), newest first
- `rating_action`: the single most recent credit-rating filing (or `null`); `from_rating`/
  `to_rating` are only present when a clean "from X to Y" phrase was found
- `next_results_date`: a future `YYYY-MM-DD` parsed from the most recent "board meeting to
  consider financial results" filing, or `null`

---

### `mf_holdings_trend`

Per-fund stake deltas vs. the prior stored quarterly snapshot — see `mf_holdings_history.py`
(PostgreSQL-backed; empty array when `DATABASE_URL` isn't set or no prior snapshot exists).

```json
[
  {
    "fund": "SBI Nifty 50 ETF",
    "holding_pct": 1.25,
    "delta_pct": 0.08,
    "as_of_date": "2026-06-30",
    "prior_as_of_date": "2026-03-31"
  }
]
```

`delta_pct` is `null` (never guessed) when there's no prior snapshot, or the fund is a new
entrant absent from it.

---

## Market picks output

The market picks result is cached at `backend/output/_market_picks/picks.json`:

```json
{
  "picks": [ ... ],
  "generated_at": "2026-05-06T08:00:00+00:00",
  "_meta": { "fetched_at": "2026-05-06T08:00:00+00:00" }
}
```

### `MarketPick` shape

Each item in `picks`:

| Field | Type | Description |
|---|---|---|
| `rank` | number | Sorted rank (1 = highest confidence) |
| `symbol` | string | NSE/BSE ticker |
| `company` | string | Company name |
| `exchange` | string | `NSE` or `BSE` |
| `sector` | string | From `stock_info`; `"Unknown"` when NSE/yfinance doesn't report one — real, filterable data (see the sector-balance note below) |
| `mention_count` | number | Total source mentions |
| `sources` | array | See below |
| `confidence_score` | number | 0–100 |
| `action_score` | number | 0–1 directional conviction magnitude |
| `signal_score` | number | –1 to +1 (quant signal engine) |
| `signal_verdict` | string | `BUY` / `HOLD` / `SELL` |
| `recommendation` | string | `BUY` / `WATCHLIST` / `HOLD` / `SELL` — a **separate** 4-tier formula from `signal_verdict` (`combined_dir = 0.55×consensus + 0.45×signal_score`, thresholded, with a quant-veto demoting BUY→WATCHLIST on a strongly negative signal score) |
| `trend` | string | `rising` / `falling` / `stable` / `new` |
| `trend_delta` | number\|null | Confidence delta vs prior 3-day average |
| `current_price` | number\|null | Last traded price |
| `change_pct` | number | % change today |
| `pe_ratio` | number\|null | Trailing P/E |
| `market_cap_cr` | number\|null | Market cap in crores |
| `valuation_percentile` | number\|null | 0–100, where current P/E sits vs. this stock's own 3–5y Screener-published P/E history (absolute anchor, not peer-relative); `null` when Screener didn't have a parseable band. Also folded into `confidence_score` as a small ±3-point nudge (≤33rd percentile +3, ≥67th percentile −3) |
| `summary` | string | LLM-generated investment thesis |
| `bull_factors` | string[] | Specific positive catalysts |
| `bear_factors` | string[] | Key risks |
| `entry_price` | number\|null | Suggested entry (deterministic, never LLM-generated) |
| `target_price` | number\|null | Analyst target or formula-derived |
| `stop_loss` | number\|null | Formula-derived stop (7–15 % range) |
| `upside_pct` | number\|null | `(target - price) / price × 100` |
| `ranking_reasons` | string[] | Up to 4 plain-English reasons for the rank |
| `is_recent_ipo` | boolean | Listed < 8 months ago |
| `horizon` | string | *(optional)* `short` / `medium` / `long` — investment horizon from LLM analysis |

`sector` also drives `_apply_sector_balance()`: max 2 stocks per sector are promoted to the
primary list; excess picks of an over-represented sector are deferred to the end of the list
(not dropped).

#### `sources` items

| Field | Type | Description |
|---|---|---|
| `name` | string | Source name (e.g. `Morgan Stanley / JPMorgan`) — see `docs/tools.md`'s 20-source registry |
| `type` | string | `news` or `brokerage` |
| `url` | string | Article URL |
| `headline` | string | Analyst reason / rating text |
| `direction` | string | `BUY` / `SELL` / `NEUTRAL` |

---

## Standalone endpoint response shapes

These endpoints are outside the six-task analysis pipeline — fetched on demand by the frontend
after the main report loads, each independently cached. **Response bodies only below**; for each
one's params, status codes, rate limit, and cache TTL see
[`api-reference.md`](api-reference.md#per-symbol-research-add-ons). See [`tools.md`](tools.md)
for the underlying tool functions.

### `GET /api/peers/{symbol}`

```json
{
  "symbol": "TCS",
  "self": { "name": "TCS", "slug": "TCS", "values": { "P/E": "17.6", "...": "..." } },
  "peers": [ { "name": "Infosys", "slug": "INFY", "values": {} } ],
  "sector_median": { "name": "Median", "slug": "", "values": {} },
  "percentiles": { "P/E": 42.0 },
  "absolute_anchor": {
    "current_pe": 17.6,
    "years": ["2022", "2023", "2024", "2025", "2026"],
    "pe_history": [24.1, 22.8, 20.5, 19.0, 17.6],
    "low": 17.6,
    "median": 20.5,
    "high": 24.1,
    "percentile": 8.0
  }
}
```

`percentiles` ranks the company against its peers per shared ratio column (mean-rank percentile,
0-100) — a column absent from a sector's table or that no peer reports simply doesn't appear.
`absolute_anchor` is `null` when there's no parseable current P/E or fewer than 3 years of yearly
P/E history — it answers "cheap/expensive vs. its own history," distinct from `percentiles`
("cheap/expensive vs. peers").

### `GET /api/financials/{symbol}`

```json
{
  "symbol": "TCS",
  "profit_loss":   { "years": ["2022", "...", "2026"], "rows": [ { "label": "Sales", "values": [178000, null, 245000] } ] },
  "balance_sheet": { "years": [], "rows": [] },
  "cash_flow":     { "years": [], "rows": [] },
  "dcf": {
    "fair_value_per_share": 2650.4,
    "current_price": 2396.9,
    "upside_pct": 10.6,
    "verdict": "Fair",
    "growth_rate_used": 8.2,
    "discount_rate": 12.0,
    "terminal_growth": 5.0,
    "latest_ocf_cr": 45000.0
  },
  "concalls": [
    { "date": "Apr 2026", "transcript_url": "https://...", "ppt_url": "https://..." }
  ]
}
```

`profit_loss`/`balance_sheet`/`cash_flow`/`dcf` are each independently `null` when Screener
doesn't have that table, or (for `dcf`) `compute_dcf_estimate()`'s own preconditions aren't met
(see [`tools.md`](tools.md)). `concalls` is `[]` (never `null`) when Screener has no calls on
record. A `null` value inside a statement row's `values` array is a genuine per-year gap (e.g. a
line item that didn't exist pre-IPO), not a parse failure. Note that this endpoint has no
`unavailable` flag — an upstream outage and a genuine "Screener has no data" produce identical
bodies (see [api-reference.md § Response-contract
inconsistencies](api-reference.md#response-contract-inconsistencies)).

### `GET /api/insider-activity/{symbol}`

```json
{
  "symbol": "TCS",
  "insider_trades": [
    { "person": "N. Chandrasekaran", "category": "Director", "action": "BUY", "quantity": 5000, "value": 15000000, "date": "02-Jul-2026", "date_iso": "2026-07-02T00:00:00+00:00" }
  ],
  "bulk_block_deals": [
    { "client": "HDFC Mutual Fund", "action": "BUY", "quantity": 200000, "price": 2390.5, "deal_type": "Bulk Deal", "date": "01-Jul-2026", "date_iso": "2026-07-01T00:00:00+00:00" }
  ],
  "insider_trades_unavailable": false,
  "bulk_block_deals_unavailable": false
}
```

`insider_trades`/`bulk_block_deals` are `[]` (never `null`) for the expected common case of no
recent activity. The two `*_unavailable` flags distinguish a genuine scrape failure from that
common empty case — both previously collapsed to the same empty list with no way for the UI to
tell them apart; a card renders "temporarily unavailable" only when the corresponding flag is
`true`.

### `GET /api/shareholding-detail/{symbol}`

```json
{
  "symbol": "TCS",
  "as_of_date": "2026-06-30",
  "promoters": [
    { "name": "Tata Sons Private Limited", "holding_pct": 71.77 }
  ],
  "shareholder_categories": [
    { "category": "Mutual Funds", "holders": [
      { "name": "SBI Nifty 50 ETF", "holding_pct": 1.25 }
    ] },
    { "category": "Foreign Portfolio Investors", "holders": [
      { "name": "Government of Singapore", "holding_pct": 1.02 }
    ] }
  ],
  "unavailable": false
}
```

Every individually-named shareholder NSE's own shareholding XBRL filing discloses — a more
granular view than `research.ratios`'/`holdings.shareholding_pattern`'s aggregate category
percentages or `holdings.mutual_funds`' mutual-fund-only list. `promoters` and each
`shareholder_categories[].category` are `[]` (never `null`) for a filing with no individually-
named holders above the plausibility threshold — the expected common case for a thinly-disclosed
filing, not an error. `category` is NSE's own raw filing category, cleaned up for display — not a
fixed enum this app maintains (see `tools/nse_tools.py::get_shareholding_detail`'s own disclosed
limitation). `unavailable: true` distinguishes a genuine scrape failure from that legitimately-
thin case, same convention as insider activity/street consensus below.

### `GET /api/street-consensus/{symbol}`

```json
{
  "symbol": "TCS",
  "articles": [
    { "title": "Trendlyne: TCS upgraded to Buy", "summary": "...", "url": "https://...", "published_at": "2026-06-20T09:00:00+00:00" }
  ],
  "numeric_consensus": {
    "symbol": "TCS",
    "analyst_count": 38,
    "consensus_rating": "BUY",
    "mean_target_price": 2650.0,
    "target_upside_pct": 10.6,
    "source_url": "https://trendlyne.com/equity/12345/TCS/tata-consultancy-services/"
  },
  "articles_unavailable": false,
  "numeric_consensus_unavailable": false
}
```

`articles` is `[]` and `numeric_consensus` is `null` for the expected common case (no
Trendlyne-cited coverage, or the page couldn't be resolved) — the same `*_unavailable`-flag
pattern as insider activity distinguishes that from a genuine fetch failure. Each sub-fetch
(`fetch_trendlyne_consensus_for_symbol`, `fetch_trendlyne_numeric_consensus`) is isolated in its
own try/except, so one failing doesn't blank out the other.

### `GET /api/verdict-history/{symbol}`

```json
{
  "symbol": "TCS",
  "history": [
    { "date": "2026-06-01", "recommendation": "HOLD", "confidence": "MEDIUM", "current_price": 2410.0, "signal_score": 0.12, "return_since_pct": -0.5, "outcome": null },
    { "date": "2026-06-15", "recommendation": "BUY", "confidence": "HIGH", "current_price": 2380.0, "signal_score": 0.55, "return_since_pct": 0.7, "outcome": "win" }
  ],
  "win_rate": 100.0,
  "scored_count": 1
}
```

One row per day the analysis pipeline actually ran (both CLI and web, same-day re-runs upsert
rather than duplicate) — see `verdict_history.py`. `return_since_pct`/`outcome` grade each entry
against **today's** live price; `outcome` is only ever `'win'`/`'loss'` for `BUY`/`SELL` calls (a
`HOLD` makes no directional claim, so it's never graded) and `null` when ungraded or the live
price fetch failed. An unset `DATABASE_URL` or a failed query degrades to the same shape with an
empty `history` rather than erroring — see
[api-reference.md](api-reference.md#get-apiverdict-historysymbol--15).

### `GET /api/consolidated/{symbol}` (and the API-key-gated `GET /api/v1/consolidated/{symbol}`)

Pure read-aggregation, no new fetching/scraping/LLM calls — three independently-`null`-able
sections, fetched concurrently:

```json
{
  "symbol": "TCS",
  "analysis":    { "recommendation": "HOLD", "confidence": "MEDIUM", "summary": "...", "as_of": "2026-07-27T09:00:00+00:00" },
  "market_pick": { "rank": 4, "recommendation": "BUY", "confidence_score": 78.0, "summary": "...", "generated_at": "2026-07-21T01:30:00+00:00" },
  "sme":         { "trade_date": "2026-07-24", "cross": "golden", "in_golden_cross": true, "name": "Example Corp", "exchange": "NSE" }
}
```

`analysis` comes from the same 24h `analysis` cache the stock-analysis flow writes to; `market_pick`
from the current `backend/output/_market_picks/picks.json` cache; `sme` from the latest stored
`ema_signals`/`sme_stocks` row. Each is `null` independently — "not yet analyzed" / "not on the
picks list" / "no SME data" / a DB hiccup on the `sme` section alone — never an error for the
whole response.

---

### `GET /api/portfolio/concentration`

Capital-weighted sector concentration over the caller's tracked `positions` (the "I bought this"
table) — a display-only overlay Market Picks reads to badge a new pick's sector, unrelated to the
separate Portfolio Aggregator below despite sharing the `/api/portfolio` prefix.

```json
{
  "by_sector": { "IT": 42.5, "Banking": 18.0 },
  "concentrated_sectors": ["IT"]
}
```

`concentrated_sectors` lists every sector at or above the 25% threshold. A position missing
`shares`, an unresolvable live price, or an unresolvable sector simply doesn't contribute to
either field — never guessed. `{"by_sector": {}, "concentrated_sectors": []}` when nothing
contributes (e.g. no positions have a share count yet).

### Portfolio Aggregator (`/api/portfolio/*`)

A separate personal net-worth tracker. `profiles`/`accounts`/`assets` are plain CRUD returning
standard `{"id": N}` / `{"ok": true}` / `{"<collection>": [...]}` shapes — see
[api-reference.md § Portfolio Aggregator](api-reference.md#portfolio-aggregator-4157) for their
full request contract (including the disclosed no-auth, no-ownership-scoping design). The
computed and import endpoints have less obvious bodies:

**`POST /api/portfolio/refresh-valuations`** — auto-values every non-archived `mf`/`stock` asset
with a `holdings` row, from `prices_daily`/`mf_nav_daily` (live yfinance quote as a stock fallback):

```json
{
  "valued": 7,
  "skipped": 1,
  "details": [
    {"asset_id": 12, "name": "TCS", "type": "stock", "symbol": "TCS",
     "status": "valued", "price": 3450.5, "price_date": "2026-08-01", "value": 172525.0},
    {"asset_id": 13, "name": "Some Closed Scheme", "type": "mf", "symbol": "123456",
     "status": "skipped", "reason": "no NAV for scheme code"}
  ]
}
```

**`GET /api/portfolio/xirr`** — per-asset and pooled portfolio XIRR from `transactions`
+ each asset's latest valuation as the terminal cashflow; `null` wherever an asset has no
transaction history yet:

```json
{
  "portfolio_xirr": 0.142,
  "assets": [
    {"asset_id": 12, "name": "TCS", "xirr": 0.181},
    {"asset_id": 14, "name": "Manual FD", "xirr": null}
  ]
}
```

**`POST /api/portfolio/import-cas`** — imports a CAMS/KFintech detailed CAS PDF:

```json
{
  "schemes": 5, "assets_created": 3, "assets_matched": 2,
  "transactions": 148, "skipped_rows": 0, "warnings": []
}
```

A wrong password, an unparseable PDF, a summary-only statement, or an unknown `account_id` never
reach this shape — they surface as an `HTTPException` instead (see
[api-reference.md](api-reference.md#computed--import)).

**`POST /api/portfolio/import-csv/preview`** — returns headers, a mapping suggestion, and Zerodha
auto-detection:

```json
{
  "headers": ["Symbol", "ISIN", "Trade Date", "Trade Type", "Quantity", "Price"],
  "sample_rows": [["TCS", "INE467B01029", "2026-01-15", "buy", "10", "3200.5"]],
  "suggested_mapping": {"date": "Trade Date", "symbol": "Symbol", "side": "Trade Type",
                          "quantity": "Quantity", "price": "Price", "amount": null, "isin": "ISIN"},
  "detected": "zerodha"
}
```

**`POST /api/portfolio/import-csv`** — imports the mapped rows, append + content-key dedupe:

```json
{
  "rows": 42, "imported": 40, "duplicates": 2, "skipped": 0,
  "assets_created": 1, "assets_matched": 3, "warnings": []
}
```

New-asset broker codes are resolved to a canonical NSE/BSE symbol via
`tools/securities_master.py::resolve_symbol()` before an asset is created — an unresolved/fuzzy
match keeps the raw broker code and adds a warning naming the row (and, for a fuzzy match, the
candidate name) rather than guessing.

---

## Pipeline-internal cache files

| Path | TTL | Key | Description |
|---|---|---|---|
| `backend/output/_extract_cache/<hash>.json` | 6 h | SHA-256 of source name + article titles/URLs | LLM extraction result per source |
| `backend/output/_history/<YYYY-MM-DD>.json` | Permanent | Date | Daily pick snapshot (symbol, confidence, effective_signal, mention_count, current_price, recommendation) |
| `backend/output/_nse_master.txt` | 24 h | — | Newline-separated set of valid NSE equity symbols from EQUITY_L.csv |
| `backend/output/_nifty500_master.json` | 24 h | — | NIFTY 500 constituent list (`{symbol, company_name, industry, isin}[]`) — screener_pipeline.py's universe |
| `backend/output/_llm_cost/<date>.json` | Daily | Date | Running LLM cost/token counter (`call_count`, `total_cost_usd`, `calls_with_unknown_cost`) |
| `backend/output/_source_health/<source>.json` | — | Source name | Per-source daily ok/not-ok history for market-picks sources + macro overlay fetches |
| `backend/output/_scraper_error_counters/<name>.json` | — | Scraper name (`peers`, `financials`, `insider_trades`, `bulk_block_deals`, `trendlyne_articles`, `trendlyne_numeric_consensus`) | Error counter for the standalone per-symbol scrapers |
| `backend/output/_source_quality/<run_id>.json` | — | Market Picks run id | Per-run source telemetry (yield, syndication-dedup rate, extraction success) for the 20 Market Picks sources |
| `backend/output/_bhavcopy/<YYYY-MM-DD>.csv` | Permanent | Trade date | Raw NSE bhavcopy archive — EOD price store ingestion replay without re-hitting NSE |
| `backend/output/_cas/<YYYY-MM-DD-HHMM>.json` | Permanent | Timestamp | PII-scrubbed parsed CAS-statement JSON — replay via (from `backend/`) `python cas_import.py --replay <file> --account-id N` |

---

## Error payloads

If a task fails, the tool returns an error object:

```json
{
  "error": "No market data found",
  "symbol": "TCS"
}
```

Other tasks can still succeed. The pipeline degrades per-section rather than failing as a whole
batch. Standalone endpoints follow the same instinct but surface it differently per endpoint —
e.g. `/api/insider-activity` and `/api/street-consensus` use explicit `*_unavailable` boolean
flags (see above) rather than a top-level `error` key, so a genuine scrape failure is
distinguishable from the equally-common "nothing to report" empty case.

This is distinct from an **HTTP** error, which is always FastAPI's `{"detail": "..."}` and never
carries raw exception text. For which endpoint returns which status, and where the two
mechanisms diverge between siblings, see
[api-reference.md § Status codes](api-reference.md#status-codes) and
[§ Response-contract inconsistencies](api-reference.md#response-contract-inconsistencies).
