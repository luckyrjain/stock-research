"""Valuation-based signal computation."""
from signals.models import Signal

def valuation_signal(features: dict) -> Signal:
    """Return a valuation signal based on the P/E ratio."""
    pe = features.get("pe")

    if not pe or pe < 0:
        # A negative P/E means the company is loss-making — the ratio itself
        # is not a meaningful valuation signal in that case (it says nothing
        # about over/undervaluation), so this degrades to UNKNOWN rather
        # than being classified by raw magnitude, which would otherwise
        # satisfy `pe < 15` for any negative number and misread a
        # loss-making company as UNDERVALUED.
        return Signal("valuation", "UNKNOWN", 0, {})

    if pe > 60:
        return Signal("valuation", "EXTREME_OVERVALUED", -1.0, {"pe": pe})
    if pe > 40:
        return Signal("valuation", "OVERVALUED", -0.6, {"pe": pe})
    if pe < 15:
        return Signal("valuation", "UNDERVALUED", 0.6, {"pe": pe})

    return Signal("valuation", "FAIR", 0, {"pe": pe})
