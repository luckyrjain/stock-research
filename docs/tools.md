# Tools Reference

The Python data layer is split into three groups: **stock analysis tools** (one per data task), **market picks scrapers** (one per financial source), and **SME stock-list fetchers** (for the golden cross pipeline).

---

## Stock analysis tools

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
| `sector` | string | Sector classification |
| `industry` | string | Industry classification |
| `about` | string | Company description |
| `prices_by_exchange` | object | Per-exchange price map (NSE, BSE) |

---

### `get_fundamentals`

- File: `tools/screener_tools.py`
- Source: Screener.in

Returns:

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Ticker |
| `ratios` | object | Metric name → string value (e.g. `"ROCE": "76.7"`) |
| `about` | string | Company description |

---

### `get_holdings`

- File: `tools/screener_tools.py`
- Source: Screener.in

Returns:

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Ticker |
| `shareholding_pattern` | object | Category → % (Promoters, FIIs, DIIs, Government, Public) |

---

### `get_mf_holdings`

- File: `tools/nse_tools.py`
- Source: NSE shareholding API + XBRL parsing

Returns:

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Ticker |
| `as_of_date` | string | Reporting date |
| `mutual_funds` | array | `[{ fund, holding_pct }]` |

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

- File: `tools/nse_tools.py`
- Source: NSE corporate announcements API

Returns recent corporate filings (results, AGM notices, board meetings, etc.).

---

## Market picks scrapers

Scrapers live in `tools/market_picks_tools.py` and `tools/hdfc_sec_agent.py`. All return the same shape:

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

### Source registry

| Source name | Type | Mechanism | Credibility weight |
|---|---|---|---|
| `ET Markets` | news | RSS feed | 0.60 |
| `LiveMint` | news | RSS feed | 0.60 |
| `NDTV Profit` | news | RSS feed | 0.55 |
| `Hindu BusinessLine` | news | RSS feed | 0.55 |
| `Zerodha Z-Connect` | brokerage | RSS feed | 0.70 |
| `GNews — Moneycontrol` | news | GNews query | 0.65 |
| `Morgan Stanley / JPMorgan` | brokerage | GNews query | 1.00 |
| `Jefferies / Macquarie / Citi` | brokerage | GNews query | 0.95 |
| `HSBC / BofA / Bernstein / Investec` | brokerage | GNews query | 0.95 |
| `ShareKhan / Mirae Asset` | brokerage | GNews query | 0.80 |
| `SMIFS / IDBI Capital / Geojit / Deven Choksey` | brokerage | GNews query | 0.75 |
| `HDFC Securities Fundamental` | brokerage | GNews query | 0.85 |
| `HDFC Securities Technical` | brokerage | GNews query | 0.75 |
| `NSE Bulk/Block Deals` | brokerage | NSE API (`/api/bulk-deals`, `/api/block-deals`) | 0.85 |
| `Screener.in Fundamental Screen` | brokerage | Screener `/screen/raw/` + GNews fallback | 0.70 |
| `Trendlyne / Analyst Consensus` | brokerage | GNews queries (upgrades, initiations, target raises) | 0.75 |

Credibility weights are defined in `_SOURCE_CREDIBILITY` in `market_picks_pipeline.py`. Sources not in the dict default to **0.50**.

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

---

## Normalization

Raw tool output is always normalized through `schemas.normalize(task_name, raw)` before it is passed to the cache, signal engine, guardrails, or analyst prompt. If a tool changes its output shape, only `schemas.py` needs updating.

All tool functions must return `{"error": "...", "symbol": sym}` on failure — never raise. The cache layer silently discards error payloads; guardrails detect them and trigger retries.
