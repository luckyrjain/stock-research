"""Shared stateless indicator math for RSI(14) and EMA20/50, previously
defined independently (and identically) in pipelines/sme_ema_pipeline.py and
signals/technical.py. Each caller's own event/trend logic on top of these
stays in its own module — this file holds only the formulas."""
import numpy as np
import pandas as pd

RSI_PERIOD = 14
# With adjust=False, an "EMA50" computed on too few bars is really just a
# recency-weighted average of all of them, not a converged 50-day EMA — a
# series with less history than this is UNKNOWN/unconverged, never guessed
# (e.g. a recent IPO). A healthy margin above the 50-span, not merely past it.
MIN_HISTORY_DAYS = 75


def compute_ema(closes: pd.Series, span: int) -> pd.Series:
    """EMA over `closes` with the given span."""
    return closes.ewm(span=span, adjust=False).mean()


def compute_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """RSI(14), Wilder-style exponential smoothing via pandas ewm — standard
    momentum-screener confirmation alongside an EMA cross. Note this isn't a
    bit-exact match to textbook Wilder's method (which seeds avg_gain/avg_loss
    with a plain mean of the first `period` deltas before switching to
    smoothing; ewm(adjust=False) instead seeds recursively from the very
    first delta) — the difference only affects the first handful of
    post-warmup values and has fully decayed away by the time either caller
    persists/reads anything derived from it.
    NaN for the first `period` rows (not enough history to smooth over yet);
    a completely flat price (no gains or losses at all, vanishingly rare for
    a real stock) is treated as neutral (50), not undefined, since a
    straight-up (avg_loss == 0, avg_gain > 0) or straight-down (avg_gain ==
    0, avg_loss > 0) move already resolves correctly to 100/0 through plain
    float division.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    # avg_loss is 0 wherever avg_gain is also 0 (flat price, masked back to
    # 50 below) or on a pure uptrend (masked away by clip(lower=0) elsewhere
    # never applying here) — either way the resulting inf/nan from this
    # divide is benign and immediately overwritten by the .where() below, but
    # numpy still raises a RuntimeWarning for it; only sme_ema_pipeline's
    # original had this suppression, technical.py's twin didn't — folding it
    # into the shared function is strictly a no-op on values either way.
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return rsi
