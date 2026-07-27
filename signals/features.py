"""Feature extraction for signal computations."""

def extract_features(all_data: dict) -> dict:
    """Extract normalized features used by signal calculators."""
    stock = all_data.get("stock_info", {})
    research = all_data.get("research", {})

    # A scraped nested field can be present with the WRONG TYPE rather than
    # simply absent (schema drift, a malformed cache entry) -- schemas.py's
    # own contract check only validates presence, not type (see
    # schema_drift.py's own disclosed "often silently breaks downstream"
    # caveat). ratios/filings feed every signal module's own .get()/
    # iteration calls, which assume dict/list respectively; a wrong type
    # here must degrade to an empty default the same way missing data
    # already does, rather than crashing run_signal_engine() (and, via
    # api.py's call site having no local try/except around it, the entire
    # /api/analyse/{symbol} request).
    ratios = research.get("ratios", {})
    if not isinstance(ratios, dict):
        ratios = {}

    filings = all_data.get("filings", {}).get("filings", [])
    if not isinstance(filings, list):
        filings = []

    return {
        "price": stock.get("current_price"),
        "volume": stock.get("volume"),
        "avg_volume": stock.get("avg_volume_10d"),
        "change_pct": stock.get("change_pct"),
        "pe": stock.get("pe_ratio"),
        "market_cap": stock.get("market_cap_cr"),
        "sector": stock.get("sector"),
        "ratios": ratios,
        "filings": filings,
    }
