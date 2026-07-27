"""Feature extraction for signal computations."""

def extract_features(all_data: dict) -> dict:
    """Extract normalized features used by signal calculators."""
    # Every one of these top-level containers can itself be present with the
    # WRONG TYPE, not just the nested fields read out of them below -- a
    # non-dict here (e.g. a malformed cache entry that stored a list) would
    # otherwise raise AttributeError on the very first .get() call against
    # it, before any of the nested-field isinstance guards below ever run.
    # Same "degrade to empty default, never crash the caller" convention as
    # the nested-field guards.
    stock = all_data.get("stock_info", {})
    if not isinstance(stock, dict):
        stock = {}

    research = all_data.get("research", {})
    if not isinstance(research, dict):
        research = {}

    filings_task = all_data.get("filings", {})
    if not isinstance(filings_task, dict):
        filings_task = {}

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

    filings = filings_task.get("filings", [])
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
