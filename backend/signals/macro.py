"""Market-wide macro overlay signal — FII/DII net institutional equity flow
plus RBI repo rate / CPI inflation context. Unlike every other signal in
this package, this one is identical for every stock analysed on a given
day, so its inputs are cached under a fixed pseudo-symbol ("_MACRO") — one
cached fetch per TTL window (see cache.TTL_HOURS's fii_dii_flow/
macro_context entries) serves every symbol's analysis, not one fetch per
symbol. Same pattern GET /api/market-picks/history already uses to cache
the Nifty benchmark series under a "NSEI" pseudo-symbol.
"""
from datetime import datetime, timezone

from core import cache
import source_health
from signals.models import Signal
from tools.macro_context_tools import get_macro_context
from tools.nse_fii_dii_tools import get_fii_dii_flow

_MACRO_PSEUDO_SYMBOL = "_MACRO"

# core/cache.py's own freshness check only knows when the HTTP call *succeeded*
# (_meta.fetched_at) — not whether NSE's response actually carried the
# current trading session's numbers. NSE publishes this report once per
# day; if it hasn't published yet (e.g. early in the day) or a scrape
# genuinely returns a stale row, the fetch still "succeeds" and gets cached
# as fresh for the full 24h TTL, silently blending yesterday's — or a
# stale Friday's — flow into every stock's score. 4 calendar days covers a
# 3-day long weekend (Fri close -> Mon fetch) plus one day of slack; never
# guessed further than that — an unparseable or missing date simply isn't
# checked for staleness at all (fail open on the existing behavior, not a
# fabricated "definitely stale" claim).
_MAX_FLOW_STALE_DAYS = 4
_FLOW_DATE_FORMATS = ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y")


def _parse_flow_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    for fmt in _FLOW_DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None

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
    # Only recorded on an actual fetch (not a cache hit) — this is a daily-
    # cadence source, so one health record per real fetch already matches
    # its own refresh rate; recording on every cache hit too would just
    # inflate the rolling window with duplicate "ok" entries for the same
    # underlying fetch.
    source_health.record_and_check(
        "fii_dii_flow", flow.get("fii_net_cr") is not None or flow.get("dii_net_cr") is not None,
    )
    cache.save(_MACRO_PSEUDO_SYMBOL, "fii_dii_flow", flow)
    return flow


def _cached_macro_context() -> dict:
    cached = cache.load(_MACRO_PSEUDO_SYMBOL, "macro_context")
    if cached is not None:
        return cached
    context = get_macro_context()
    source_health.record_and_check(
        "macro_context", context.get("repo_rate_pct") is not None or context.get("cpi_inflation_pct") is not None,
    )
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

    # The flow's own as-of date, not just when the HTTP call happened to
    # succeed — see _MAX_FLOW_STALE_DAYS's own comment above. A stale flow
    # figure is excluded from scoring entirely (never blended in as if it
    # were current) but still surfaced in meta so a stale-data situation is
    # visible in the signal's own diagnostic tooltip, not silently dropped.
    # A plain string (not a bool) so this fits the same meta value type
    # (number | string | None) every other signal's meta already uses —
    # frontend/types/index.ts's SignalItem.meta doesn't need widening.
    flow_date = _parse_flow_date(flow.get("date"))
    flow_stale = bool(flow_date and (datetime.now(timezone.utc) - flow_date).days > _MAX_FLOW_STALE_DAYS)
    if flow_stale:
        fii_net = None
        dii_net = None

    if fii_net is None and dii_net is None and repo_rate is None and cpi is None:
        return Signal(
            "macro", "UNKNOWN", 0,
            {"flow_date": flow.get("date"), "flow_status": "stale, excluded from score"} if flow_stale else {},
        )

    score = 0.0
    meta: dict = {}
    if flow_stale:
        meta["flow_date"] = flow.get("date")
        meta["flow_status"] = "stale, excluded from score"

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
