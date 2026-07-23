# signals/growth_signal.py

from signals.models import Signal

def _to_float(x):
    if x is None:
        return None
    if isinstance(x, str):
        x = x.replace("%", "").strip()
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def growth_signal(features: dict) -> Signal:
    ratios = features.get("ratios", {})

    sales_3y = _to_float(ratios.get("Sales growth 3Y"))
    profit_3y = _to_float(ratios.get("Profit growth 3Y"))
    ebitda_margin = _to_float(ratios.get("EBITDA margin"))

    if sales_3y is None or profit_3y is None:
        return Signal("growth", "UNKNOWN", 0, {"raw": ratios})

    # Strong compounder
    if sales_3y > 20 and profit_3y > 20:
        if ebitda_margin and ebitda_margin > 25:
            return Signal("growth", "HIGH_QUALITY_GROWTH", 0.9, {
                "sales": sales_3y,
                "profit": profit_3y,
                "margin": ebitda_margin
            })
        return Signal("growth", "HIGH_GROWTH", 0.7, {
            "sales": sales_3y,
            "profit": profit_3y
        })

    # Moderate growth
    if sales_3y > 10:
        return Signal("growth", "MODERATE_GROWTH", 0.3, {
            "sales": sales_3y
        })

    return Signal("growth", "LOW_GROWTH", -0.5, {
        "sales": sales_3y,
        "profit": profit_3y
    })