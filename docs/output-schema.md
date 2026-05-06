# Output Schema

## Per-symbol cache files

Each symbol gets its own folder under `output/<SYMBOL>/`:

```text
output/TCS/
├── stock_info.json
├── research.json
├── news.json
├── shareholding.json
├── mf_holdings.json
├── filings.json
├── analysis.json
└── report_2026-05-06.json
```

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

The merged `report_<DATE>.json` strips `_meta` from all nested sections.

---

## Merged report shape

```json
{
  "symbol": "TCS",
  "generated_at": "2026-05-06",
  "analysis": {},
  "signals": {},
  "stock_info": {},
  "research": {},
  "news": [],
  "holdings": {}
}
```

### Top-level fields

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Uppercased stock symbol |
| `generated_at` | string | Run date in `YYYY-MM-DD` format |
| `analysis` | object | Final LLM analyst output |
| `signals` | object | Quantitative signal engine output |
| `stock_info` | object | Quote and company information |
| `research` | object | Fundamental ratios and about text |
| `news` | array | News article list |
| `holdings` | object | Shareholding pattern + MF holdings |

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

---

### `signals`

```json
{
  "final_score": 0.42,
  "verdict": "BUY",
  "signals": {
    "valuation": {
      "name": "valuation",
      "value": "Fairly Valued",
      "score": 0.3,
      "meta": { "pe_ratio": 17.6, "industry_pe": 22.0 }
    },
    "growth": { "name": "growth", "value": "Strong", "score": 0.8, "meta": {} },
    "volume":  { "name": "volume",  "value": "Above Average", "score": 0.5, "meta": {} },
    "filings": { "name": "filings", "value": "Neutral", "score": 0.0, "meta": {} }
  }
}
```

- `final_score`: –1 (strong sell) to +1 (strong buy)
- `verdict`: `BUY` | `HOLD` | `SELL`

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
  "about": "Company description..."
}
```

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
  "mutual_funds": [
    { "fund": "SBI Nifty 50 ETF", "holding_pct": 1.25 }
  ]
}
```

---

## Market picks output

The market picks result is cached at `output/_market_picks/picks.json`:

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
| `mention_count` | number | Total source mentions |
| `sources` | array | See below |
| `confidence_score` | number | 0–100 |
| `action_score` | number | 0–1 directional conviction magnitude |
| `signal_score` | number | –1 to +1 (quant signal engine) |
| `signal_verdict` | string | `BUY` / `HOLD` / `SELL` |
| `recommendation` | string | `BUY` / `WATCHLIST` / `HOLD` / `SELL` |
| `trend` | string | `rising` / `falling` / `stable` / `new` |
| `trend_delta` | number\|null | Confidence delta vs prior 3-day average |
| `current_price` | number\|null | Last traded price |
| `change_pct` | number | % change today |
| `pe_ratio` | number\|null | Trailing P/E |
| `market_cap_cr` | number\|null | Market cap in crores |
| `summary` | string | LLM-generated investment thesis |
| `bull_factors` | string[] | Specific positive catalysts |
| `bear_factors` | string[] | Key risks |
| `entry_price` | number\|null | Suggested entry (deterministic) |
| `target_price` | number\|null | Analyst target or formula-derived |
| `stop_loss` | number\|null | Formula-derived stop (7–15 % range) |
| `upside_pct` | number\|null | `(target - price) / price × 100` |
| `ranking_reasons` | string[] | Up to 4 plain-English reasons for the rank |
| `is_recent_ipo` | boolean | Listed < 8 months ago |

#### `sources` items

| Field | Type | Description |
|---|---|---|
| `name` | string | Source name (e.g. `Morgan Stanley / JPMorgan`) |
| `type` | string | `news` or `brokerage` |
| `url` | string | Article URL |
| `headline` | string | Analyst reason / rating text |
| `direction` | string | `BUY` / `SELL` / `NEUTRAL` |

---

## Pipeline-internal cache files

| Path | TTL | Key | Description |
|---|---|---|---|
| `output/_extract_cache/<hash>.json` | 6 h | SHA-256 of source name + article titles/URLs | LLM extraction result per source |
| `output/_history/<YYYY-MM-DD>.json` | Permanent | Date | Daily pick snapshot (symbol, confidence, effective_signal, mention_count) |
| `output/_nse_master.txt` | 24 h | — | Newline-separated set of valid NSE equity symbols from EQUITY_L.csv |

---

## Error payloads

If a task fails, the tool returns an error object:

```json
{
  "error": "No market data found",
  "symbol": "TCS"
}
```

Other tasks can still succeed. The pipeline degrades per-section rather than failing as a whole batch.
