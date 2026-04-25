# Output Schema

Each symbol gets its own folder under `output/<SYMBOL>/`.

Example:

```text
output/TCS/
├── stock_info.json
├── research.json
├── news.json
├── shareholding.json
├── mf_holdings.json
├── analysis.json
└── report_2026-04-25.json
```

## Cache files

Each per-task cache file includes a top-level `_meta.fetched_at` timestamp. Example:

```json
{
  "symbol": "TCS",
  "...": "...",
  "_meta": {
    "fetched_at": "2026-04-25T12:36:05.017134+00:00"
  }
}
```

The merged `report_<DATE>.json` strips `_meta` out of nested sections.

## Merged report shape

```json
{
  "symbol": "TCS",
  "generated_at": "2026-04-25",
  "analysis": {},
  "stock_info": {},
  "research": {},
  "news": [],
  "holdings": {}
}
```

## Top-level fields

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | string | Uppercased stock symbol |
| `generated_at` | string | Run date in `YYYY-MM-DD` format |
| `analysis` | object | Final analyst output |
| `stock_info` | object | Quote and company information |
| `research` | object | Fundamental ratios and about text |
| `news` | array | News article list derived from `news.json` |
| `holdings` | object | Shareholding pattern merged with mutual fund holdings |

## `analysis`

Typical shape:

```json
{
  "symbol": "TCS",
  "recommendation": "HOLD",
  "confidence": "MEDIUM",
  "summary": "A short multi-sentence recommendation.",
  "valuation": {
    "verdict": "Fairly Valued",
    "comment": "Comment with actual cited numbers."
  },
  "business_quality": "Short explanation of operating quality.",
  "bull_factors": [
    "Plain string factor"
  ],
  "bear_factors": [
    {
      "metric": "Beta",
      "value": 0.272,
      "comment": "Example structured factor"
    }
  ],
  "key_risks": [
    "Risk 1",
    "Risk 2",
    "Risk 3"
  ],
  "news_sentiment": "Neutral",
  "news_highlights": "Short summary"
}
```

Notes:

- `recommendation` is one of `BUY`, `SELL`, `HOLD`
- `confidence` is one of `HIGH`, `MEDIUM`, `LOW`
- `valuation` contains `verdict` and `comment`
- `bull_factors` and `bear_factors` may contain plain strings or structured objects, depending on analyst output
- `news_highlights` may arrive as a string or an array of strings

## `stock_info`

Typical fields:

```json
{
  "symbol": "TCS",
  "exchange": "NSE",
  "primary_exchange": "NSE",
  "company_name": "Tata Consultancy Services Limited",
  "current_price": 2396.9,
  "previous_close": 2521.8,
  "change_pct": -4.95,
  "volume": 5106879,
  "avg_volume_10d": 3720212,
  "market_cap_cr": 867219.0,
  "pe_ratio": 17.637234,
  "eps": 135.9,
  "book_value": 313.727,
  "price_to_book": 7.640082,
  "52w_high": 3630.5,
  "52w_low": 2346.2,
  "dividend_yield_pct": 263.0,
  "beta": 0.272,
  "sector": "Technology",
  "industry": "Information Technology Services",
  "about": "Company summary...",
  "prices_by_exchange": {
    "NSE": {
      "exchange": "NSE",
      "current_price": 2396.9,
      "change_pct": -4.95
    },
    "BSE": {
      "exchange": "BSE",
      "current_price": 2398.1,
      "change_pct": -4.89
    }
  }
}
```

Notes:

- Top-level quote fields continue to represent the primary/default exchange used by the rest of the report.
- `prices_by_exchange` includes per-exchange quotes when the symbol is available on more than one exchange.

## `research`

Typical fields:

```json
{
  "symbol": "TCS",
  "ratios": {
    "Market Cap": "867219",
    "Current Price": "2397",
    "Stock P/E": "16.6",
    "ROCE": "76.7",
    "ROE": "65.2"
  },
  "about": "Company description..."
}
```

Notes:

- `ratios` is usually a map of metric name to string value
- Scraped values are kept close to source formatting

## `news`

The merged report flattens `news.json.articles` into a top-level array:

```json
[
  {
    "title": "Headline",
    "description": "Short excerpt",
    "source": "Publisher",
    "published_at": "Fri, 24 Apr 2026 10:30:00 GMT",
    "url": "https://example.com/article"
  }
]
```

## `holdings`

The final report merges:

- `shareholding.json`
- `mf_holdings.json`

Example:

```json
{
  "symbol": "TCS",
  "shareholding_pattern": {
    "Promoters": 71.77,
    "FIIs": 9.66,
    "DIIs": 13.34,
    "Government": 0.06,
    "Public": 5.16
  },
  "mutual_funds": [
    {
      "fund": "Sbi Nifty 50 Etf",
      "holding_pct": 1.25
    }
  ]
}
```

## Error cases

If a task fails, that task may return an object like:

```json
{
  "error": "No market data found",
  "symbol": "TCS"
}
```

Other sections can still succeed. The pipeline is designed to degrade per section rather than fail as one large batch.
