"""Feature extraction for signal computations."""

def extract_features(all_data: dict) -> dict:
    """Extract normalized features used by signal calculators."""
    stock = all_data.get("stock_info", {})
    research = all_data.get("research", {})

    return {
        "price": stock.get("current_price"),
        "volume": stock.get("volume"),
        "avg_volume": stock.get("avg_volume_10d"),
        "pe": stock.get("pe_ratio"),
        "market_cap": stock.get("market_cap_cr"),
        "sector": stock.get("sector"),
        "ratios": research.get("ratios", {}),
        "filings": all_data.get("filings", {}).get("filings", [])
    }
