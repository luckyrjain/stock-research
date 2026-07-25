"""Signal engine orchestration for scoring and verdict generation."""

from signals.features import extract_features
from signals.volume import volume_signal
from signals.valuation import valuation_signal
from signals.models import SignalResult
from signals.growth import growth_signal
from signals.filings import filings_signal
from signals.technical import technical_signal
from signals.macro import macro_signal

# Baseline weights — every signal's contribution to final_score before any
# sector tilt is applied.
_DEFAULT_WEIGHTS = {
    "valuation": 0.4,
    "volume":    0.2,
    "growth":    0.4,
    "filings":   0.2,
    "technical": 0.2,
    "macro":     0.15,
}

# yfinance's `sector` field (see tools/nse_tools.py::get_stock_quote ->
# info.get("sector")) reports a fixed GICS-like taxonomy. Grouped into three
# economically-similar buckets rather than one override per individual
# sector — with only six signals and no backtest behind any of this (see
# CLAUDE.md), splitting further would read as more empirical precision than
# the underlying judgment actually has. Each override is a deliberate,
# documented tilt layered on top of _DEFAULT_WEIGHTS, not a fabricated
# back-tested calibration: the gap this closes is "a capital-intensive bank
# and an asset-light IT company get identical valuation logic," not
# "empirically optimal weights for every sector."
_RATE_SENSITIVE_SECTORS = {"Financial Services", "Real Estate", "Utilities"}
_GROWTH_SECTORS = {"Technology", "Communication Services", "Healthcare"}
_CYCLICAL_SECTORS = {"Basic Materials", "Energy", "Industrials", "Consumer Cyclical"}

_SECTOR_WEIGHT_OVERRIDES = {
    # Capital-intensive, rate-sensitive: valuation and the macro overlay
    # (FII/DII flow + RBI rate/inflation) matter more; growth is weighted
    # down since these are typically mature, income-oriented businesses
    # rather than high-growth compounders.
    "rate_sensitive": {"valuation": 0.45, "growth": 0.3, "macro": 0.25},
    # Asset-light, growth-driven: growth carries more weight; the macro
    # overlay (domestic rate/inflation, FII/DII flow) is less directly
    # relevant to export-oriented, globally-priced businesses.
    "growth": {"growth": 0.45, "valuation": 0.35, "macro": 0.1},
    # Cyclical: price/volume momentum (the technical + volume signals) is
    # more informative for a cyclical business than for a steady compounder.
    "cyclical": {"technical": 0.3, "volume": 0.3},
}


def _weights_for_sector(sector: str | None) -> dict[str, float]:
    """Sector-aware weight overrides layered on top of _DEFAULT_WEIGHTS —
    see the module-level comment above for the reasoning and its limits.
    An unrecognized or missing sector (None — e.g. yfinance didn't report
    one) falls back to the same default weights this engine always used,
    never a guessed sector."""
    weights = dict(_DEFAULT_WEIGHTS)
    if sector in _RATE_SENSITIVE_SECTORS:
        weights.update(_SECTOR_WEIGHT_OVERRIDES["rate_sensitive"])
    elif sector in _GROWTH_SECTORS:
        weights.update(_SECTOR_WEIGHT_OVERRIDES["growth"])
    elif sector in _CYCLICAL_SECTORS:
        weights.update(_SECTOR_WEIGHT_OVERRIDES["cyclical"])
    return weights


def run_signal_engine(symbol: str, all_data: dict) -> SignalResult:
    """Compute weighted signal scores and return a trading verdict.

    `technical` and `macro` are the only signals that do their own I/O —
    every other signal reads from `features` (already-fetched data).
    `technical` fetches its own OHLCV input (see signals/technical.py);
    `macro` fetches (cached, market-wide, not per-symbol) FII/DII flow and
    RBI rate/inflation context (see signals/macro.py). Callers running
    inside an asyncio event loop must invoke this function via an
    executor, not directly, the same as every other blocking call in this
    codebase's SSE paths.
    """
    features = extract_features(all_data)

    signals = {
        "volume": volume_signal(features),
        "valuation": valuation_signal(features),
        "growth": growth_signal(features),
        "filings": filings_signal(features),
        "technical": technical_signal(symbol),
        "macro": macro_signal(),
    }

    weights = _weights_for_sector(features.get("sector"))

    score = 0
    for name, weight in weights.items():
        sig = signals.get(name)
        if not sig:
            continue
        score += sig.score * weight

    if score > 0.5:
        verdict = "BUY"
    elif score > 0.1:
        verdict = "WATCHLIST"
    elif score > -0.3:
        verdict = "HOLD"
    elif score > -0.6:
        verdict = "AVOID"
    else:
        verdict = "SELL"

    return SignalResult(
        symbol=symbol,
        signals=signals,
        final_score=round(score, 2),
        verdict=verdict
    )
