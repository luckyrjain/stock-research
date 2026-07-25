"""
Contract normalization layer.

Each task contract defines:
  required  – canonical fields that must be present and non-empty
  normalize – maps raw tool output → canonical dict

Rules:
- Tools own their raw output shape.
- Only normalize() needs updating if a tool renames a field.
- Guardrails, cache, and the analyst prompt all work against the canonical shape.
"""

from typing import Any


def _pick(d: dict, *keys, default: Any = None) -> Any:
    """Return the first key that exists and is not None."""
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return default


def _norm_exchange_quote(d: dict) -> dict:
    return {
        "symbol":             d.get("symbol", ""),
        "exchange":           d.get("exchange", ""),
        "company_name":       d.get("company_name", ""),
        "current_price":      _pick(d, "current_price", "regularMarketPrice"),
        "previous_close":     d.get("previous_close"),
        "change_pct":         d.get("change_pct", 0.0),
        "volume":             d.get("volume"),
        "avg_volume_10d":     d.get("avg_volume_10d"),
        "market_cap_cr":      d.get("market_cap_cr"),
        "pe_ratio":           _pick(d, "pe_ratio", "trailingPE"),
        "eps":                _pick(d, "eps", "trailingEps"),
        "book_value":         d.get("book_value"),
        "price_to_book":      d.get("price_to_book"),
        "52w_high":           _pick(d, "52w_high", "fiftyTwoWeekHigh"),
        "52w_low":            _pick(d, "52w_low", "fiftyTwoWeekLow"),
        "dividend_yield_pct": d.get("dividend_yield_pct", 0),
        "beta":               d.get("beta"),
        "sector":             d.get("sector"),
        "industry":           d.get("industry"),
        "about":              d.get("about", ""),
    }


# ── Per-task contracts ────────────────────────────────────────────────────────

def _norm_stock_info(d: dict) -> dict:
    prices_by_exchange = {
        exchange: _norm_exchange_quote(quote)
        for exchange, quote in (d.get("prices_by_exchange") or {}).items()
        if isinstance(quote, dict)
    }
    primary_exchange = d.get("primary_exchange") or d.get("exchange") or "NSE"
    primary_quote = prices_by_exchange.get(primary_exchange, d)

    return {
        **_norm_exchange_quote(primary_quote),
        "exchange":         primary_exchange,
        "primary_exchange": primary_exchange,
        "prices_by_exchange": prices_by_exchange,
    }


def _norm_research(d: dict) -> dict:
    return {
        "symbol": d.get("symbol", ""),
        "ratios": d.get("ratios", {}),
        "about":  d.get("about", ""),
        "quarterly_trend": d.get("quarterly_trend", {}),
    }


def _norm_news(d: dict) -> dict:
    return {
        "symbol": d.get("symbol", ""),
        "articles": [
            {
                "title":        a.get("title", ""),
                "description":  a.get("description", ""),
                "source":       a.get("source", ""),
                "published_at": a.get("published_at", ""),
                "url":          a.get("url", ""),
            }
            for a in d.get("articles", [])
        ],
    }


def _norm_shareholding(d: dict) -> dict:
    return {
        "symbol":               d.get("symbol", ""),
        "shareholding_pattern": d.get("shareholding_pattern", {}),
        "pledge_pct":           d.get("pledge_pct"),
    }


def _norm_mf_holdings(d: dict) -> dict:
    return {
        "symbol":       d.get("symbol", ""),
        "as_of_date":   d.get("as_of_date", ""),
        "mutual_funds": [
            {
                "fund":        f.get("fund", ""),
                "holding_pct": f.get("holding_pct", 0),
            }
            for f in d.get("mutual_funds", [])
        ],
    }

def _norm_filings(data: dict) -> dict:
    return {
        "filings": data.get("filings", [])
    }

CONTRACTS: dict[str, dict] = {
    # "types" is optional per-contract and only lists container-shaped raw
    # fields (dict/list) whose *type* — not just presence — matters: a field
    # silently changing from a dict to a list (e.g. a scraper's target site
    # restructuring a table) breaks every downstream `.get()`/iteration call
    # on it in a way "required" field presence alone doesn't catch, since
    # the field is still there — schema_drift.py checks this on raw tool
    # output, before normalize() runs. Only checked when the field is
    # present; a field this codebase's own "never invent" convention
    # legitimately omits per-symbol (e.g. a thin quarterly_trend window) is
    # not drift and must not be flagged.
    "stock_info": {
        "required":  ["symbol", "current_price"],
        "types":     {"prices_by_exchange": dict},
        "normalize": _norm_stock_info,
    },
    "research": {
        "required":  ["symbol", "ratios"],
        "types":     {"ratios": dict, "quarterly_trend": dict},
        "normalize": _norm_research,
    },
    "news": {
        "required":  ["articles"],
        "types":     {"articles": list},
        "normalize": _norm_news,
    },
    "shareholding": {
        "required":  ["symbol", "shareholding_pattern"],
        "types":     {"shareholding_pattern": dict},
        "normalize": _norm_shareholding,
    },
    "mf_holdings": {
        "required":  ["symbol", "mutual_funds"],
        "types":     {"mutual_funds": list},
        "normalize": _norm_mf_holdings,
    },
    "filings": {
        "required":  [],
        "types":     {"filings": list},
        "normalize": _norm_filings,
    },
}


# ── Public API ────────────────────────────────────────────────────────────────

def normalize(task_name: str, data: dict) -> dict:
    """Map raw tool output to the canonical shape. Returns data unchanged if no contract."""
    if data.get("error"):
        return data

    contract = CONTRACTS.get(task_name, {})
    normalizer = contract.get("normalize")
    if not normalizer:
        return data
    try:
        return normalizer(data)
    except Exception:
        return data  # degrade gracefully; validation will catch missing fields


def validate(task_name: str, data: dict) -> tuple[bool, str]:
    """
    Check that data satisfies the contract for task_name.
    Returns (True, "") on success, (False, error_message) on failure.
    """
    if data.get("error"):
        return False, f"Tool returned an error: {data['error']}"

    contract = CONTRACTS.get(task_name)
    if not contract:
        return True, ""

    for field in contract["required"]:
        if not data.get(field):
            return False, f"Required field '{field}' is missing or empty"

    return True, ""

