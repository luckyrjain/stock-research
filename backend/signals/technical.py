"""Technical (RSI14 + EMA20/50 posture) signal for the main NSE/BSE stock
analysis flow — the same momentum-screener math pipelines/sme_ema_pipeline.py already
computes for SME stocks, applied here to whatever symbol the six-task
pipeline is analysing. Unlike the other signals in this package, this one
needs a daily-close OHLCV series the six data-fetch tasks don't include, so
it fetches its own input (via tools.price_history_tools.get_price_series,
the same 6h-cached series backing the sparkline endpoint) rather than
reading from the `features` dict extract_features() builds.
"""
import pandas as pd

from signals.indicators import MIN_HISTORY_DAYS, compute_ema, compute_rsi
from signals.models import Signal
from tools.price_history_tools import get_price_series

# Same value as sme_ema_pipeline._MIN_HISTORY_DAYS, both now sourced from
# signals.indicators.MIN_HISTORY_DAYS — kept as a local alias since it's this
# module's own name for the constant.
_MIN_CLOSES = MIN_HISTORY_DAYS
_compute_rsi = compute_rsi  # alias: see signals/indicators.py for the formula


def technical_signal(symbol: str) -> Signal:
    """RSI14 + EMA20/50 trend posture. UNKNOWN (score 0) when there isn't
    enough price history yet to trust EMA50 convergence."""
    series = get_price_series(symbol)
    closes = series.get("closes") or []
    if len(closes) < _MIN_CLOSES:
        return Signal("technical", "UNKNOWN", 0, {"closes_available": len(closes)})

    close = pd.Series(closes)
    ema20 = compute_ema(close, 20)
    ema50 = compute_ema(close, 50)
    rsi = _compute_rsi(close)

    latest_rsi = round(float(rsi.iloc[-1]), 1)
    bullish_trend = bool(ema20.iloc[-1] > ema50.iloc[-1])
    meta = {"rsi14": latest_rsi, "ema20_above_ema50": bullish_trend}

    if bullish_trend and latest_rsi >= 70:
        return Signal("technical", "OVERBOUGHT_UPTREND", 0.3, meta)
    if bullish_trend and latest_rsi <= 30:
        return Signal("technical", "OVERSOLD_IN_UPTREND", 0.6, meta)
    if bullish_trend:
        return Signal("technical", "BULLISH_TREND", 0.5, meta)
    if latest_rsi <= 30:
        return Signal("technical", "OVERSOLD_DOWNTREND", -0.2, meta)
    if latest_rsi >= 70:
        return Signal("technical", "OVERBOUGHT_DOWNTREND", -0.3, meta)
    return Signal("technical", "BEARISH_TREND", -0.4, meta)
