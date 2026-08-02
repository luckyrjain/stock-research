# Tools Reference

The Python data layer is split into six groups:

1. **Stock analysis tools** — the six data slices fetched for every symbol (`ALL_DATA_TASKS`)
2. **Standalone per-symbol enrichment tools** — peers, multi-year financials, DCF, insider/bulk-block activity, street consensus, price history — fetched on demand outside the six-task pipeline
3. **Macro overlay tools** — market-wide (not per-symbol) FII/DII flow and RBI rate/inflation context feeding the `macro` signal
4. **Market picks scrapers** — one per financial source, feeding the multi-agent weekly-picks pipeline
5. **Universe/stock-list fetchers** — NSE Emerge + BSE SME lists (SME Signals) and NIFTY 500 constituents (Screener)
6. **EOD price store + Portfolio Aggregator support tools** — NSE bhavcopy/equity-master + AMFI NAV fetchers, NSE corporate-actions fetch/parser, and the NSE+BSE+SME securities-master merge/symbol-resolver consumed by broker CSV import

Every scraper in this codebase follows the same "never raise" convention: on failure, a tool
returns `{"error": "...", ...}` (or, for the newer non-`@tool` helpers, an all-`None`/empty-list
shape) rather than throwing — see "Important Rules for Claude" in `CLAUDE.md`.

---

## Stock analysis tools

These six are wrapped with `@tool` from `crewai.tools` for a stable `.run(**kwargs)` calling
convention (see `main._fetch_task`) — that's the only thing this codebase still uses CrewAI for.

### `get_stock_quote`

- File: `tools/nse_tools.py`
- Source: Yahoo Finance via `yfinance`; supplemented by NSE API

Returns quote and company metadata:

| Field | Type | Description |
|---|---|---|
| `symbol` | string | NSE/BSE ticker |
| `exchange` | string | Primary exchange |
| `company_name` | string | Full company name |
| `current_price` | number | Last traded price |
| `previous_close` | number | Prior session close |
| `change_pct` | number | % change from previous close |
| `volume` | number | Session volume |
| `avg_volume_10d` | number | 10-day average volume |
| `market_cap_cr` | number | Market cap in crores |
| `pe_ratio` | number | Trailing P/E |
| `eps` | number | Trailing EPS |
| `book_value` | number | Book value per share |
| `price_to_book` | number | Price-to-book ratio |
| `52w_high` | number | 52-week high |
| `52w_low` | number | 52-week low |
| `dividend_yield_pct` | number | Dividend yield % |
| `beta` | number | Beta vs index |
| `sector` | string | Sector classification (yfinance's own taxonomy — see the "Sector-aware signal weights" disclosed limitation in `CLAUDE.md`) |
| `industry` | string | Industry classification |
| `about` | string | Company description |
| `prices_by_exchange` | object | Per-exchange price map (NSE, BSE) |

`dividend_yield_pct` is passed through `_percent_from_ambiguous_value()` — yfinance's
`dividendYield` field is documented to sometimes arrive already as a percent rather than a
fraction; a result implausible for a real equity (>25%) is dropped to `None` rather than trusted.

**Screener.in fallback**: when yfinance has no usable quote on either `.NS`/`.BO` suffix (common
for thinly-traded stocks), `_screener_fallback_quote()` scrapes Screener.in's `#top-ratios` widget
for a price/market-cap/P-E/book-value/dividend-yield instead of hard-failing the whole analysis.
EPS and price-to-book are derived from price÷P-E and price÷book-value (Screener's widget doesn't
carry either directly); `_stockanalysis_extra_fields()` additionally scrapes stockanalysis.com for
a real EPS/52-week-range/volume where reachable, overriding the derived EPS when available. No
intraday change % is available from this path (`change_pct` is `0.0`).

---

### `get_fundamentals`

- File: `tools/screener_tools.py`
- Source: Screener.in

Scrapes key ratios, a quarterly Sales/EPS/(optional) operating-margin mini-trend, and a
company description. Returns:

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Ticker |
| `ratios` | object | Metric name → string value (e.g. `"ROCE": "76.7"`), plus 3Y/5Y sales/profit growth and EBITDA margin when parseable |
| `about` | string | Company description |
| `quarterly_trend` | object | *(optional)* `{quarters, revenue, eps, operating_margin?}` — see `_extract_quarterly_trend` below |
| `nse_fallback_ratios` | object | *(optional)* `{eps, source: "nse_xbrl", as_of_date}` — only present when `ratios` came back completely empty and `tools.nse_tools.get_nse_basic_ratios()` found a usable EPS from NSE's own XBRL filings (see below) |

`_extract_quarterly_trend(soup, max_periods=8)` (module-private) parses Screener's
`section#quarters` table into an oldest-first Sales/EPS mini-trend, capped at 8 quarters.
Returns `{}` (never a partial/misaligned series) if Sales or EPS is missing, or any cell in the
window doesn't parse as a number. `operating_margin` is an independently-optional third line —
several sectors (banks, NBFCs) routinely omit Screener's OPM % row even when Sales/EPS are
present, so it's dropped from the payload rather than backfilled.

---

### `get_peer_comparison`

- File: `tools/screener_tools.py`
- Source: Screener.in (`section#peers`, `section#ratios`)

Scrapes Screener's Peer comparison table — the company's own row, up to 5 sector peers, and
Screener's own sector-median row when present. Column parsing (`_parse_peer_table`) is driven
entirely by the table's own headers, not a hardcoded schema, since the ratio set varies by
sector.

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Ticker |
| `self` | object\|null | This company's own peer-table row (`{name, slug, values}`) |
| `peers` | array | Up to 5 sector peer rows |
| `sector_median` | object\|null | Screener's own median row, when present |
| `valuation_band` | object | *(optional)* `{years, pe}` — see `_extract_valuation_band` below |

`_extract_valuation_band(soup, max_years=5)` (module-private) parses Screener's yearly
`section#ratios` table for a "Price to Earning" row — the same company page fetch, no extra
round trip. Oldest-first, capped at 5 years; returns `{}` if the row is absent, fewer than 3
years are available, or any cell in the window doesn't parse. This is what
`api.py::_compute_valuation_anchor()` ranks the live current P/E against to build the
`absolute_anchor` field on `GET /api/peers/{symbol}` (see `docs/output-schema.md`).
**Disclosed limitation**: whether Screener actually renders this yearly P/E row was not verified
against a live response in this sandbox — see the module docstring.

---

### `get_financial_statements`

- File: `tools/screener_tools.py`
- Source: Screener.in (`section#profit-loss`, `section#balance-sheet`, `section#cash-flow`, `section#concalls`)

Scrapes up to 10 years each of Profit & Loss, Balance Sheet, and Cash Flow, plus Screener's own
Concalls list — the fuller financial-history view neither `get_fundamentals` (current ratios
only) nor `get_peer_comparison` (P/E-only valuation band) covers. This is the tool behind
`GET /api/financials/{symbol}`.

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Ticker |
| `profit_loss` | object | *(optional)* `{years, rows: [{label, values}]}` |
| `balance_sheet` | object | *(optional)* same shape |
| `cash_flow` | object | *(optional)* same shape — the `values` for whichever row's label contains "operating activit" feeds `dcf_valuation.compute_dcf_estimate()` |
| `concalls` | array | *(optional, omitted when empty)* `[{date, transcript_url?, ppt_url?, notes_url?, audio_url?}]` |

`_extract_yearly_statement(soup, section_id, max_years=10)` (module-private) is the generic
extractor shared by all three statements — deliberately not a hardcoded row schema (a bank's
balance sheet looks nothing like an FMCG company's). Unlike `_extract_quarterly_trend`'s
strict "every cell must parse or the row is dropped" rule, a row here keeps `None` for any
single year it can't parse — across up to a decade of history a gap in one year is expected, not
a misalignment to guard against.

`_extract_concalls(soup, max_entries=8)` (module-private) parses Screener's own "Concalls"
section into one entry per quarterly earnings call, with whichever of
Transcript/PPT/Notes/REC links Screener has published. A call whose date can't be confidently
parsed is dropped rather than kept with a `None` date.

**Disclosed limitation** (both extractors): Screener's exact section ids/row labels/markup for
these tables were not verified against a live response in this sandbox — see the module
docstrings. A mismatch degrades to `{}`/`[]`, never a fabricated figure.

---

### `get_holdings`

- File: `tools/screener_tools.py`
- Source: Screener.in

Returns:

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Ticker |
| `shareholding_pattern` | object | Category → % (Promoters, FIIs, DIIs, Government, Public) |
| `pledge_pct` | number | *(optional)* Promoter pledge %, parsed from the same shareholding table as its own field rather than folded into `shareholding_pattern` |

---

### `get_mf_holdings`

- File: `tools/nse_tools.py`
- Source: NSE shareholding API + XBRL parsing

Returns:

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Ticker |
| `as_of_date` | string | Reporting date |
| `mutual_funds` | array | `[{ fund, holding_pct }]`, top 15 by stake |

XBRL parsing uses `_parse_xbrl_xml()` (entity resolution disabled, defense against XXE) and
`_is_nse_host()` guards every attachment URL taken out of NSE's own API response before fetching
it (SSRF defense — see the module's own comment). `holding_pct` is passed through
`_percent_from_ambiguous_value()`, same ambiguous-format handling as `dividend_yield_pct` above.

---

### `get_shareholding_detail`

- File: `tools/nse_tools.py`
- Source: same NSE shareholding XBRL filing as `get_mf_holdings` (shares `_fetch_shareholding_xbrl()`, a common fetch helper, so both don't independently re-fetch the same document)

Every individually-named shareholder the filing discloses — not just mutual funds. Generalizes
`get_mf_holdings`' own proven extraction (same `NameOfTheShareholder`/
`ShareholdingAsAPercentageOfTotalNumberOfShares` XBRL facts) to every `typedMember` category the
filing actually has, rather than filtering to ones whose tag contains `"MutualFunds"`.

Returns:

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Ticker |
| `as_of_date` | string | Reporting date |
| `promoters` | array | `[{ name, holding_pct }]` — entries whose XBRL category tag contains `"Promoter"`, top 20 by stake |
| `shareholder_categories` | array | `[{ category, holders: [{ name, holding_pct }] }]` — every other named-shareholder category the filing contains (mutual funds, FPIs, insurance companies, whatever's really there), `category` a human-readable label derived from NSE's own raw XBRL tag |

A promoter/promoter-group entity can plausibly hold up to 100% of a closely-held company; any
other single named holder above ~30% is dropped as an implausible format guess (same
`_percent_from_ambiguous_value` reasoning as `get_mf_holdings`, ceiling raised only for entries
already bucketed as promoters). `_humanize_category()` strips a trailing `"Member"` (a generic
XBRL dimensional-modeling convention, not a guess specific to NSE) and word-spaces the remainder
for display.

**Disclosed limitation**: the exact XBRL category tag names NSE's real filings use beyond
`"MutualFunds"` (already proven correct by `get_mf_holdings`) were not verified against a live
filing in this sandbox — same disclosure as every other scraper in this doc. A filing whose
promoter tag doesn't contain `"Promoter"` degrades those records into `shareholder_categories`
under their own raw label rather than the `promoters` field — a real, named holder either way,
never a fabricated one.

---

### `get_latest_news`

- File: `tools/news_tools.py`
- Source: Google News RSS via `gnews`

Returns:

| Field | Type | Description |
|---|---|---|
| `query` | string | Search query used |
| `articles` | array | `[{ title, description, source, published_at, url }]` |

---

### `get_nse_filings`

- File: `tools/nse_filings_tools.py`
- Source: NSE corporate announcements API

Not `@tool`-decorated (a plain function, called directly by `main._fetch_task`). Returns
`{symbol, count, filings}` with `filings: [{title, desc, date, category, attachment}]` for the
last `days` (default 30) of corporate filings — results, AGM notices, board meetings, etc.
`title`/`desc` are always strings (never `None`), since a bare `None` used to crash
`signals/filings.py`'s string concatenation. This list is what
`signals/filings_classifier.py::classify_filings()` (see `docs/output-schema.md`) turns into the
report's `filings_summary`.

---

## Standalone per-symbol enrichment tools

These feed the standalone, on-demand endpoints (`/api/peers`, `/api/financials`,
`/api/insider-activity`, `/api/street-consensus`) — cached (24h TTL) but intentionally **outside**
`ALL_DATA_TASKS`, so they never block the six-task analysis SSE stream.

### `dcf_valuation.py::compute_dcf_estimate`

- File: **`dcf_valuation.py`** (repo root, *not* under `tools/`) — a pure computation module, not a scraper
- Input: the `cash_flow` dict from `get_financial_statements`, plus `current_price` and `market_cap_cr`

A deterministic two-stage DCF off the cash-flow table's Operating Activity row — never
LLM-generated, same "computed, not model-generated" convention as Market Picks' entry/target/
stop-loss levels. Returns `None` when there's fewer than 3 years of OCF history, the latest OCF
isn't positive, or a share count can't be derived from `market_cap_cr` + `current_price`.
Otherwise returns:

| Field | Type | Description |
|---|---|---|
| `fair_value_per_share` | number | DCF fair value |
| `current_price` | number | Echoed input |
| `upside_pct` | number | `(fair_value - current_price) / current_price × 100` |
| `verdict` | string | `Undervalued` (≥+20%) / `Overvalued` (≤−20%) / `Fair` |
| `growth_rate_used` | number | % — clamped historical OCF CAGR used to project forward |
| `discount_rate` | number | % — fixed at 12% |
| `terminal_growth` | number | % — fixed at 5% |
| `latest_ocf_cr` | number | ₹ Cr — the OCF the projection started from |

**Disclosed simplifications** (from the module's own docstring — see CLAUDE.md's "Multi-year
financial statements + DCF valuation flow" section for the full rationale):
1. Operating Cash Flow is used as the Free-Cash-Flow proxy — Screener's cash-flow table has no
   cleanly-labelled, sector-independent Capex row to net against it.
2. Discount rate (12%) and terminal growth (5%) are fixed, market-wide assumptions, not
   per-company (beta/leverage/sector-adjusted).
3. Historical OCF growth is clamped to `[-20%, +25%]` before being used to project forward, so a
   couple of noisy years can't imply an absurd CAGR.

This is a genuinely different valuation lens from the two this app already has: `api.py`'s
peer-relative percentile and `_compute_valuation_anchor`'s own-P/E-history anchor both answer
"cheap vs. what" (peers, or its own trading history); this answers "cheap vs. what its cash flows
are worth."

---

### `tools/nse_insider_trades.py`

- Source: NSE's promoter/director PIT (Prohibition of Insider Trading) disclosure feed

| Function | Scope | Returns |
|---|---|---|
| `fetch_insider_trades()` | Market-wide, 14-day lookback | `{"source": "NSE Insider Trades", "type": "brokerage", "articles": [...]}` — plain-language articles for the market-picks LLM extraction step |
| `fetch_insider_trades_for_symbol(symbol, lookback_days=90)` | One symbol, 90-day lookback | `{"symbol", "trades": [...]}` — structured records, not LLM articles |

Both call the shared `_parse_pit_row(row)` (module-private) parse+noise-filter step — person
category must be promoter/director, transaction mode excludes ESOP/pledge/gift/bonus/
rights/inter-se, and value must be at least ₹25L (`_MIN_VALUE_INR`) — so the market-wide and
per-symbol paths can never disagree on what counts as a "real" insider trade. The per-symbol
function uses a wider 90-day window (vs. 14 for the market-wide feed) since one stock's insider
activity is comparatively sparse. A `trade` record: `{person, category, action, quantity, value,
date, date_iso}` — `action` is `BUY`/`SELL`, `date_iso` is a true-chronological-order ISO string
(NSE's own `dd-Mon-yyyy` format doesn't sort lexically).

---

### `tools/nse_bulk_block_deals.py`

- Source: NSE's bulk-deals (≥0.5% of equity) and block-deals (8:45–9:00 AM window) endpoints

| Function | Scope | Returns |
|---|---|---|
| `fetch_nse_bulk_block_deals()` | Market-wide | `{"source": "NSE Bulk/Block Deals", "type": "brokerage", "articles": [...]}` |
| `fetch_bulk_block_deals_for_symbol(symbol)` | One symbol | `{"symbol", "deals": [...]}` — structured, not LLM articles |

Both use the shared `_parse_deal_row(deal, deal_type)` (module-private) step. Field lookups go
through alias lists (`_SYMBOL_KEYS`, `_QTY_KEYS`, etc.) since NSE has used different field-naming
conventions across API versions. Minimum quantity thresholds: 50,000 shares (bulk), 100,000
(block) — `_MIN_BULK_QTY`/`_MIN_BLOCK_QTY`. Unlike insider trades, there's no wider lookback for
the per-symbol path — NSE's endpoints only ever return "recent trading days" with no date-range
parameter. A `deal` record: `{client, action, quantity, price, deal_type, date, date_iso}`.

---

### `tools/trendlyne_agent.py`

Article-search module — never scrapes trendlyne.com itself, only GNews articles that *cite*
Trendlyne. Deliberately never returns a numeric consensus rating/target price (see
`tools/trendlyne_scraper.py` below for the module that actually does).

| Function | Scope | Returns |
|---|---|---|
| `fetch_trendlyne_consensus()` | Market-wide, 3 GNews queries (upgrades/initiations, target raises, general Trendlyne mentions) | `{"source": "Trendlyne / Analyst Consensus", "type": "brokerage", "articles": [...]}` |
| `fetch_trendlyne_consensus_for_symbol(symbol, max_results=10)` | One symbol, one GNews query ANDing the ticker + "Trendlyne" + a buy/upgrade/target phrase | `{"symbol", "articles": [...]}` |

---

### `tools/trendlyne_scraper.py`

- Source: trendlyne.com's own company page (direct scrape, not GNews-mediated)

`fetch_trendlyne_numeric_consensus(symbol)` — the real numeric complement to
`trendlyne_agent.py`'s article search. Resolves a bare NSE symbol to Trendlyne's company page
via `_resolve_trendlyne_url()` (tries `/equity/{symbol}/` directly first, falls back to
Trendlyne's own search page), then regex-parses the page's flattened text (label-anchored, not
CSS selectors, since text labels are more likely to survive a markup change).

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Ticker |
| `analyst_count` | number\|null | From the first "N Analyst(s)" phrase on the page |
| `consensus_rating` | string\|null | One of `STRONG BUY`/`BUY`/`ACCUMULATE`/`HOLD`/`REDUCE`/`SELL`/`STRONG SELL` |
| `mean_target_price` | number\|null | ₹ |
| `target_upside_pct` | number\|null | Signed; downside is negated |
| `source_url` | string\|null | The resolved Trendlyne page, or `null` if unresolved |

Every field is independently `None` when its label/value pair isn't cleanly present — a partial
page yields a partial result, not a discarded one. `_is_trendlyne_host()` guards every candidate
URL (the redirected direct-URL result *and* every parsed search-page anchor) against
`trendlyne.com` before it's fetched — an SSRF defense, since this module fetches whatever URL it
resolves to with a real browser User-Agent. **Disclosed limitation**: neither Trendlyne's
symbol-resolution path nor its DOM/label text were verified against a live response in this
sandbox; `_ANALYST_COUNT_RE` in particular searches the whole page's text (less label-specific
than the other three regexes) and could in principle match an unrelated "N Analysts" phrase.

---

### `tools/price_history_tools.py::get_price_series`

- Source: `yfinance` daily-close OHLCV, `.NS` then `.BO` fallback

`get_price_series(symbol, days=180)` — the shared daily-close series fetch used by both
`GET /api/prices/history/{symbol}` (the sparkline endpoint) and `signals/technical.py`'s RSI/EMA
computation, extracted so both share one fetch and one `price_history` cache (6h TTL). The cache
always stores the maximum window (`_MAX_DAYS = 365`); a caller's `days` value only trims the
cached series (`_trim()`), so a short-range request can never silently populate the shared cache
key with too few rows and starve a later long-range request within the same TTL window. Returns
`{symbol, exchange, dates, closes}` — `dates`/`closes` are `[]` (never an error dict) if neither
exchange suffix returned usable data.

---

### `tools/nse_tools.py::get_nse_basic_ratios`

- Source: NSE's `corporate-announcements` endpoint with `reqXbrl=true`, parsing the most recent
  "Financial Results" filing's XBRL attachment

Not `@tool`-decorated, not one of the six `ALL_DATA_TASKS` — a best-effort fallback called
(lazy import) from `get_fundamentals()` **only when Screener's own `ratios` dict came back
completely empty** (e.g. a recent IPO Screener hasn't indexed yet, but NSE already has a results
filing). Returns `{}` on any failure. On success: `{"eps": float, "source": "nse_xbrl",
"as_of_date": str}`.

**Deliberately EPS-only** — EPS is self-scaled (always "₹/paise per share"), so there's no unit
ambiguity. Sales/profit are aggregate rupee figures XBRL reports at a scale
(rupees/lakhs/crore) that would need real-filing verification to resolve correctly, which
couldn't be done in this sandbox — guessing wrong would inject a confidently-incorrect figure
(e.g. off by 100×), worse than the missing-data case this fallback exists to improve on.
`_is_nse_host()` guards the XBRL attachment URL (and its post-redirect URL) before fetching, same
SSRF-defense pattern as `get_mf_holdings`.

---

## Macro overlay tools

Market-wide, not per-symbol — fetched and cached once per TTL window under a fixed pseudo-symbol
(`"_MACRO"`) by `signals/macro.py`, the same convention `GET /api/market-picks/history` uses for
the Nifty benchmark series under a `"NSEI"` pseudo-symbol. Both feed the `macro` signal (weight
0.15) in `signals/engine.py`.

### `tools/nse_fii_dii_tools.py::get_fii_dii_flow`

- Source: NSE's `fiidiiTradeReact` daily provisional FII/DII net equity-flow endpoint

Returns `{"date", "fii_net_cr", "dii_net_cr"}` — either net figure is `None` if that category's
row isn't present in NSE's response. A parsed value beyond `_PLAUSIBLE_MAX_NET_CR` (₹100,000 Cr)
is dropped to `None` rather than trusted, since a real single-day net flow that large would be
extraordinary (more likely a unit mismatch than a genuine figure). Returns `{"error": ...}` only
on a total fetch/parse failure. **Disclosed limitation**: NSE's response shape was not verified
against a live response in this sandbox.

### `tools/macro_context_tools.py::get_macro_context`

- Source: RBI's own homepage "Current Rates" table

Returns `{"repo_rate_pct", "cpi_inflation_pct"}` — either field is `None` if the page doesn't
clearly expose it (CPI in particular is often unavailable). Each parsed percentage is bounded to
a real-world-plausible range (`_REPO_RATE_RANGE = (2.0, 15.0)`, `_CPI_RANGE = (-5.0, 20.0)`)
before being trusted, since the `table tr` selector scans every table on the page, not just the
intended widget. Returns `{"error": ...}` only on a total fetch failure. **Disclosed limitation**:
RBI's page structure was not verified against a live response in this sandbox.

---

## Market picks scrapers

Scrapers live in `tools/market_picks_tools.py`, `tools/hdfc_sec_agent.py`,
`tools/nse_bulk_block_deals.py`, `tools/nse_insider_trades.py`, `tools/screener_scanner.py`, and
`tools/trendlyne_agent.py`, and are merged into one registry at the bottom of
`tools/market_picks_tools.py`. All return the same shape:

```json
{
  "source": "Source Name",
  "type": "news | brokerage",
  "articles": [
    {
      "title": "Headline",
      "summary": "Short excerpt (up to 500 chars)",
      "url": "https://...",
      "published_at": "2026-05-06T10:00:00+00:00"
    }
  ]
}
```

### Source registry (20 sources)

`SOURCES`/`SCRAPER_FNS` in `tools/market_picks_tools.py` merge in five other modules' own
`*_SOURCES`/`*_SCRAPERS` exports (`HDFC_SEC_SOURCES`, `NSE_BULK_SOURCES`, `INSIDER_SOURCES`,
`SCREENER_SCAN_SOURCES`, `TRENDLYNE_SOURCES`) — 14 defined directly in that file + 2 (HDFC) + 1
(NSE bulk/block) + 1 (NSE insider) + 1 (Screener.in scan) + 1 (Trendlyne) = **20 total**.
Credibility weights (`_SOURCE_CREDIBILITY` in `market_picks_pipeline.py`) — sources not listed
default to **0.50** (`_DEFAULT_CREDIBILITY`):

| Source name | Type | Mechanism | Credibility weight |
|---|---|---|---|
| `Morgan Stanley / JPMorgan` | brokerage | GNews query | 1.00 |
| `Jefferies / Macquarie / Citi` | brokerage | GNews query | 0.95 |
| `HSBC / BofA / Bernstein / Investec` | brokerage | GNews query | 0.95 |
| `NSE Bulk/Block Deals` | brokerage | NSE API (`/api/bulk-deals`, `/api/block-deals`) | 0.85 |
| `NSE Insider Trades` | brokerage | NSE API (`/api/corporates-pit`) | 0.85 |
| `HDFC Securities Fundamental` | brokerage | GNews query | 0.85 |
| `ShareKhan / Mirae Asset` | brokerage | GNews query | 0.80 |
| `Motilal Oswal / ICICI Direct / Axis Securities` | brokerage | GNews query | 0.80 |
| `SMIFS / IDBI Capital / Geojit / Deven Choksey` | brokerage | GNews query | 0.75 |
| `HDFC Securities Technical` | brokerage | GNews query | 0.75 |
| `Trendlyne / Analyst Consensus` | brokerage | GNews queries (upgrades, initiations, target raises) | 0.75 |
| `Zerodha Z-Connect` | brokerage | RSS feed | 0.70 |
| `Screener.in Fundamental Screen` | brokerage (falls back to `news` if the screen itself fails) | Screener `/screen/raw/` + GNews fallback | 0.70 |
| `GNews — Moneycontrol` | news | GNews query | 0.65 |
| `ET Markets` | news | RSS feed | 0.60 |
| `LiveMint` | news | RSS feed | 0.60 |
| `GNews — Business Standard` | news | GNews query (direct RSS is Akamai-blocked) | 0.60 |
| `NDTV Profit` | news | RSS feed | 0.55 |
| `Hindu BusinessLine` | news | RSS feed | 0.55 |
| `GNews — Financial Express` | news | GNews query (site RSS feeds are disabled) | 0.55 |

Fixed normalization reference for the consensus scoring component: `_CONSENSUS_REF = 12.0`
(a well-covered stock with ~4 non-syndicated brokerage BUY calls at credibility 0.80) —
prevents the higher-weight sources (NSE Bulk 0.85, Trendlyne 0.75, Screener 0.70) from inflating
`max_effective_signal` and compressing every other stock's consensus score.

### Adding a new source

1. Define scraper functions in a new module (e.g. `tools/my_brokerage.py`)
2. Export `MY_SOURCES` (list of `(name, type, fn_name)` tuples) and `MY_SCRAPERS` (dict of `name → fn`)
3. Import and merge into `SOURCES` and `SCRAPER_FNS` at the bottom of `tools/market_picks_tools.py`
4. Add a credibility entry in `_SOURCE_CREDIBILITY` in `market_picks_pipeline.py`

---

## SME stock-list fetchers

Used only by `sme_ema_pipeline.py`. Live in `tools/sme_tools.py`; lists are cached under `output/` for 24 h.

| Function | Source | Returns |
|---|---|---|
| `fetch_nse_emerge_stocks(force=False)` | NSE `/api/live-analysis-emerge` | NSE Emerge (SME) stocks; names enriched via Screener.in |
| `fetch_bse_sme_stocks(force=False)` | BSE `ListofScripData` API (Groups M + MS) | BSE SME stocks (symbol = numeric scrip code) |
| `get_all_sme_stocks(force=False)` | both of the above | Merged list of `{symbol, name, isin, series, exchange}` dicts |

> **Dedup note:** the NSE Emerge endpoint never returns an ISIN (`isin` is always `None`
> on NSE records), so ISIN matching alone can't catch a company dual-listed on both
> exchanges. `get_all_sme_stocks` falls back to a high-confidence fuzzy match on
> normalized company name (rapidfuzz, score cutoff 90) using the Screener.in-enriched
> NSE names. This is best-effort, not a guarantee — a company whose NSE and BSE names
> diverge too much, or whose Screener.in name lookup failed, can still appear twice.
> See the docstrings in `tools/sme_tools.py` for detail.

---

## NIFTY 500 constituent fetcher

Used only by `screener_pipeline.py` (the custom NIFTY 500 screener, `/screener` /
`GET /api/screener`) — the bounded, curated universe a daily per-stock `yfinance .info` scrape is
reasonable at, unlike the full ~2000-symbol NSE equity master.

| Function | Source | Returns |
|---|---|---|
| `tools/nifty500_tools.py::get_nifty500_constituents(force=False)` | NSE archive CSV (`ind_nifty500list.csv`) | `[{"symbol", "company_name", "industry", "isin"}, ...]` |

Cached 24h under `output/_nifty500_master.json`. A truncated-but-nonempty response (a partial
download, a paginated/rate-limited NSE reply) is treated as a failed fetch, not cached, if it
returns fewer than `_MIN_PLAUSIBLE_COUNT` (400) rows — a screener silently built on a truncated
universe would look correct while quietly missing most of the market. Falls back to a stale cache
(logged as such) rather than returning `[]` outright when a fresh fetch fails but a prior cache
exists. **Disclosed limitation**: the exact NSE archive URL and CSV column layout was not
verified against a live response in this sandbox.

---

## EOD price store + Portfolio Aggregator support tools

Feeds `eod_prices_pipeline.py`, `corporate_actions_pipeline.py`, and (via `resolve_symbol`)
`csv_import.py`'s broker-CSV import — see CLAUDE.md's "EOD price store + corporate actions flow",
"Securities master + symbol resolver", and "Portfolio Aggregator" sections for full design detail.

### `tools/eod_sources.py`

| Function | Source | Returns |
|---|---|---|
| `download_bhavcopy(trade_date, session)` | NSE `sec_bhavdata_full_DDMMYYYY.csv` archive | Raw CSV text, archived to `output/_bhavcopy/` before parsing (replay without re-hitting NSE) |
| `parse_bhavcopy(csv_text)` | — | List of per-symbol OHLC/volume/turnover/delivery-% dicts, filtered to `EQ`/`BE`/`BZ` series only |
| AMFI NAV fetch/parse | AMFI `NAVAll.txt` | Filtered to scheme codes actually held in the Portfolio Aggregator's `assets` table — ~40k total schemes is too many to store wholesale |

A 404 on the bhavcopy URL means holiday/weekend (skip silently); malformed/degenerate response
bodies are rejected rather than partially ingested. **Note**: this module has its own
`make_nse_session()` rather than delegating to the shared `tools/_nse_session.py` helper described
below — a minor inconsistency with the other seven NSE-touching modules' convention, not yet
reconciled.

### `tools/corporate_actions.py`

| Function | Source | Returns |
|---|---|---|
| `fetch_corporate_actions(from_date, to_date, session)` | NSE corporate-actions feed | Raw per-symbol action records |
| PURPOSE-string parser | — | Structured `{symbol, action_type, ratio, ex_date}`-shaped records for splits/bonuses/dividends/rights — a ratio the free-text PURPOSE field doesn't parse cleanly is skipped, never guessed |

### `tools/securities_master.py`

| Function | Source | Returns |
|---|---|---|
| `load_nse_main_board(engine)` | The `securities` table (populated by `eod_prices_pipeline.py` from the bhavcopy) | `{"symbol", "name", "isin", "exchange": "NSE", "series"}` dicts |
| `fetch_bse_main_board(force=False)` | BSE `ListofScripData` API, looped over main-board groups (A/B/T/Z/X/XT/P/MT/TS) | Same shape, `exchange="BSE"`; 24h file-mtime cache, dedup by scrip code across groups |
| `get_full_securities_master(engine, force=False)` | Merges the above two + the existing `tools/sme_tools.py::get_all_sme_stocks()` | Combined list, deduped by ISIN (NSE preferred on collision) |
| `resolve_symbol(engine, code, company_name=None, isin=None)` | — | `{"symbol", "exchange", "confidence": "isin"\|"exact"\|"fuzzy"\|"unresolved", "candidate_name"}` — resolution order: ISIN exact → code exact (with EQ/SM/ST/BE/BZ/IV suffix-stripping) → fuzzy company-name (rapidfuzz, threshold 85, case-insensitive) → unresolved |

`resolve_symbol()`'s only current consumer is `csv_import.py`'s new-asset creation path: an
`"isin"`/`"exact"` match substitutes the resolved symbol; `"fuzzy"`/`"unresolved"` keeps the
broker's raw code as-is and adds a warning — never silently substituting a guessed symbol.

---

## Shared NSE session-priming helper

`tools/_nse_session.py::get_nse_session(timeout=8.0, accept="application/json",
extra_headers=None, sleep_after_prime=0.5)` is the single place NSE-session-priming logic lives.
NSE rejects a cold request with no prior cookie, so every NSE-touching module needs a `GET` to
`nseindia.com`'s homepage before its real API/CSV call.

Each of the seven NSE-touching modules (`nse_tools.py`, `nse_filings_tools.py`,
`nse_insider_trades.py`, `nse_bulk_block_deals.py`, `nse_fii_dii_tools.py`, `sme_tools.py`,
`nifty500_tools.py`) keeps its own thin local wrapper function (same name/signature it already
had — e.g. `nse_tools.py` still defines `_nse_session()`, `nse_filings_tools.py` still defines
`_get_session()`) that delegates here with its own timeout/header needs. This is deliberate, not
duplication left over from a partial refactor: every module's existing tests patch that
per-module function name directly (e.g. `patch("tools.nse_tools._nse_session", ...)`), so the
thin wrapper keeps those patch targets working without a rewrite across seven test files.

Resilience is standardized here across all seven callers: every priming attempt is wrapped in a
swallow-and-continue `try`/`except` (a priming failure never propagates — the caller's own real
request fails on its own terms) followed by a short sleep (`sleep_after_prime`, default 0.5s) on
success.

---

## Normalization

Raw tool output from the six `ALL_DATA_TASKS` tools is always normalized through
`schemas.normalize(task_name, raw)` before it is passed to the cache, signal engine, guardrails,
or analyst prompt. If a tool changes its output shape, only `schemas.py` needs updating. The
standalone enrichment tools above (peers, financials, insider activity, street consensus, price
history, macro) are **not** part of this six-task contract — their endpoints (`api.py`) read
tool output directly and build their own response shape.

All tool functions must return `{"error": "...", "symbol": sym}` (or an equivalent all-`None`
shape for the newer non-`@tool` helpers) on failure — never raise. The cache layer silently
discards error payloads; guardrails detect them and trigger retries.

`schema_drift.py` additionally checks that the *type* (not just presence) of container-shaped
raw fields (e.g. `research.ratios` should stay a `dict`) hasn't silently changed shape — see
`schemas.CONTRACTS`'s `"types"` entries and CLAUDE.md's "Schema-drift detection" section. This is
scoped to the six data slices only, not the standalone tools in this doc.
