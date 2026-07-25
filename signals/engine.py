"""Signal engine orchestration for scoring and verdict generation."""

from signals.features import extract_features
from signals.volume import volume_signal
from signals.valuation import valuation_signal
from signals.models import SignalResult
from signals.growth import growth_signal
from signals.filings import filings_signal
from signals.technical import technical_signal

def run_signal_engine(symbol: str, all_data: dict) -> SignalResult:
    """Compute weighted signal scores and return a trading verdict.

    Every signal but `technical` reads from `features` (already-fetched
    data, no I/O of its own). `technical` fetches its own OHLCV input (see
    signals/technical.py) — callers running inside an asyncio event loop
    must invoke this function via an executor, not directly, the same as
    every other blocking call in this codebase's SSE paths.
    """
    features = extract_features(all_data)

    signals = {
        "volume": volume_signal(features),
        "valuation": valuation_signal(features),
        "growth": growth_signal(features),
        "filings": filings_signal(features),
        "technical": technical_signal(symbol),
    }

    weights = {
        "valuation": 0.4,
        "volume": 0.2,
        "growth": 0.4,
        "filings": 0.2,
        "technical": 0.2,
    }

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
