"""Market-wide macro overlay signal — FII/DII net institutional equity flow
plus RBI repo rate / CPI inflation context. Unlike every other signal in
this package, this one is identical for every stock analysed on a given
day, so its inputs are cached under a fixed pseudo-symbol ("_MACRO") — one
cached fetch per TTL window (see cache.TTL_HOURS's fii_dii_flow/
macro_context entries) serves every symbol's analysis, not one fetch per
symbol. Same pattern GET /api/market-picks/history already uses to cache
the Nifty benchmark series under a "NSEI" pseudo-symbol.
"""
import cache
from signals.models import Signal
from tools.macro_context_tools import get_macro_context
from tools.nse_fii_dii_tools import get_fii_dii_flow

_MACRO_PSEUDO_SYMBOL = "_MACRO"

# Net FII+DII flow thresholds (₹ Cr) for a "meaningful" vs. "strong" tilt —
# round numbers, not derived from any backtest; a coarse overlay, not a
# precise model.
_STRONG_FLOW_THRESHOLD_CR = 3000
_MILD_FLOW_THRESHOLD_CR = 500

# India's RBI inflation target band is 2-6%; above the upper bound is
# conventionally read as elevated enough to pressure valuations (higher
# odds of further rate tightening), below the lower half of the band as
# supportive.
_ELEVATED_CPI_PCT = 6.0
_LOW_CPI_PCT = 4.0


def _cached_fii_dii_flow() -> dict:
    cached = cache.load(_MACRO_PSEUDO_SYMBOL, "fii_dii_flow")
    if cached is not None:
        return cached
    flow = get_fii_dii_flow()
    cache.save(_MACRO_PSEUDO_SYMBOL, "fii_dii_flow", flow)
    return flow


def _cached_macro_context() -> dict:
    cached = cache.load(_MACRO_PSEUDO_SYMBOL, "macro_context")
    if cached is not None:
        return cached
    context = get_macro_context()
    cache.save(_MACRO_PSEUDO_SYMBOL, "macro_context", context)
    return context


def macro_signal() -> Signal:
    """UNKNOWN (score 0) when neither the flow nor the macro fetch produced
    any usable field — e.g. both are down, or this is the very first call
    before either cache is warm and both fetches failed."""
    flow = _cached_fii_dii_flow()
    macro = _cached_macro_context()

    fii_net = flow.get("fii_net_cr")
    dii_net = flow.get("dii_net_cr")
    repo_rate = macro.get("repo_rate_pct")
    cpi = macro.get("cpi_inflation_pct")

    if fii_net is None and dii_net is None and repo_rate is None and cpi is None:
        return Signal("macro", "UNKNOWN", 0, {})

    score = 0.0
    meta: dict = {}

    if fii_net is not None or dii_net is not None:
        net_flow = (fii_net or 0.0) + (dii_net or 0.0)
        meta["net_institutional_flow_cr"] = round(net_flow, 2)
        if net_flow > _STRONG_FLOW_THRESHOLD_CR:
            flow_score = 0.6
        elif net_flow > _MILD_FLOW_THRESHOLD_CR:
            flow_score = 0.3
        elif net_flow < -_STRONG_FLOW_THRESHOLD_CR:
            flow_score = -0.6
        elif net_flow < -_MILD_FLOW_THRESHOLD_CR:
            flow_score = -0.3
        else:
            flow_score = 0.0
        score += flow_score * 0.6

    if repo_rate is not None or cpi is not None:
        meta["repo_rate_pct"] = repo_rate
        meta["cpi_inflation_pct"] = cpi
        macro_component = 0.0
        if cpi is not None and cpi > _ELEVATED_CPI_PCT:
            macro_component -= 0.4
        elif cpi is not None and cpi < _LOW_CPI_PCT:
            macro_component += 0.2
        score += macro_component * 0.4

    score = round(score, 2)
    if score > 0.15:
        value = "SUPPORTIVE"
    elif score < -0.15:
        value = "HEADWIND"
    else:
        value = "NEUTRAL"
    return Signal("macro", value, score, meta)
