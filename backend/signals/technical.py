"""Technical (RSI14 + EMA20/50 posture) signal for the main NSE/BSE stock
analysis flow — the same momentum-screener math sme_ema_pipeline.py already
computes for SME stocks, applied here to whatever symbol the six-task
pipeline is analysing. Unlike the other signals in this package, this one
needs a daily-close OHLCV series the six data-fetch tasks don't include, so
it fetches its own input (via tools.price_history_tools.get_price_series,
the same 6h-cached series backing the sparkline endpoint) rather than
reading from the `features` dict extract_features() builds.
"""
import pandas as pd

from signals.models import Signal
from tools.price_history_tools import get_price_series

_RSI_PERIOD = 14
# With adjust=False, an "EMA50" computed on too few bars is really just a
# recency-weighted average of all of them, not a converged 50-day EMA — a
# stock with less history than this is UNKNOWN, never guessed (e.g. a
# recent IPO). Set to the same value as sme_ema_pipeline._MIN_HISTORY_DAYS
# (75) for the same reason: a healthy margin above the 50-span, not merely
# past it.
_MIN_CLOSES = 75


def _compute_rsi(close: pd.Series) -> pd.Series:
    """RSI(14) via pandas ewm — see sme_ema_pipeline._compute_rsi for the
    same formula and the note on why this isn't a bit-exact match to
    textbook Wilder's method (immaterial once past the warmup window)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / _RSI_PERIOD, adjust=False, min_periods=_RSI_PERIOD).mean()
    avg_loss = loss.ewm(alpha=1 / _RSI_PERIOD, adjust=False, min_periods=_RSI_PERIOD).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return rsi


def technical_signal(symbol: str) -> Signal:
    """RSI14 + EMA20/50 trend posture. UNKNOWN (score 0) when there isn't
    enough price history yet to trust EMA50 convergence."""
    series = get_price_series(symbol)
    closes = series.get("closes") or []
    if len(closes) < _MIN_CLOSES:
        return Signal("technical", "UNKNOWN", 0, {"closes_available": len(closes)})

    close = pd.Series(closes)
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
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
