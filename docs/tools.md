# Tools Reference

The Python data layer is built from tool functions in `tools/`. These tools fetch external data and return JSON-shaped results used by the CLI, API, cache layer, and analyst step.

## `get_stock_quote`

- File: `tools/nse_tools.py`
- Source: Yahoo Finance via `yfinance`
- Input: `symbol`

Returns quote and company metadata, including:

- `symbol`
- `exchange`
- `primary_exchange`
- `company_name`
- `current_price`
- `previous_close`
- `change_pct`
- `volume`
- `avg_volume_10d`
- `market_cap_cr`
- `pe_ratio`
- `eps`
- `book_value`
- `price_to_book`
- `52w_high`
- `52w_low`
- `dividend_yield_pct`
- `beta`
- `sector`
- `industry`
- `about`
- `prices_by_exchange`

`prices_by_exchange` is a map keyed by exchange code such as `NSE` and `BSE`, where each value contains the same quote fields listed above for that exchange.

## `get_fundamentals`

- File: `tools/screener_tools.py`
- Source: Screener.in
- Input: `symbol`

Returns:

- `symbol`
- `ratios`
- `about`

`ratios` is typically a map of metric name to string value, for example:

```json
{
  "ROCE": "76.7",
  "ROE": "65.2",
  "Stock P/E": "16.6"
}
```

## `get_holdings`

- File: `tools/screener_tools.py`
- Source: Screener.in
- Input: `symbol`

Returns:

- `symbol`
- `shareholding_pattern`

Example shareholding categories:

- `Promoters`
- `FIIs`
- `DIIs`
- `Government`
- `Public`

## `get_mf_holdings`

- File: `tools/nse_tools.py`
- Source: NSE shareholding API plus XBRL parsing
- Input: `symbol`

Returns:

- `symbol`
- `as_of_date`
- `mutual_funds`

Each mutual fund item typically contains:

- `fund`
- `holding_pct`

## `get_latest_news`

- File: `tools/news_tools.py`
- Source: Google News RSS through `gnews`
- Input: `query`

Returns:

- `query`
- `articles`

Each article typically contains:

- `title`
- `description`
- `source`
- `published_at`
- `url`

## Notes on normalization

Raw tool output is normalized by `schemas.py` before downstream use.

Current normalization rules:

- `news.json` stores articles under `articles`
- `report_<DATE>.json` exposes news as a top-level `news` array
- `shareholding` and `mf_holdings` are merged into the final `holdings` object
- `_meta.fetched_at` is added only when cache files are written
