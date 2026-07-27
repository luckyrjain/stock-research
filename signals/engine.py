"""Signal engine orchestration for scoring and verdict generation."""

from observability import get_logger, log_event
from signals.features import extract_features
from signals.volume import volume_signal
from signals.valuation import valuation_signal
from signals.models import SignalResult
from signals.growth import growth_signal
from signals.filings import filings_signal
from signals.technical import technical_signal
from signals.macro import macro_signal

LOGGER = get_logger("signals.engine")

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
# info.get("sector")) is assumed to report the same GICS-like taxonomy
# (e.g. "Technology", "Financial Services") it uses for US-listed stocks.
# **Disclosed limitation**: this was not verified against a live yfinance
# response for an NSE/BSE symbol in this sandbox (no outbound internet —
# see CLAUDE.md's repeated disclosure of the same limitation for the
# FII/DII and macro-context scrapers). There is real in-repo evidence this
# assumption could be wrong: existing test fixtures elsewhere in this
# codebase (tests/test_signal_engine.py's own pre-existing
# ExtractFeaturesTest, tests/test_market_picks_scoring.py) use short
# Indian-market-style labels like "IT"/"Banking" as example sector values
# for this exact same field, rather than GICS names — those are unverified
# test-fixture choices too, not confirmed production data, but they're
# reason enough to flag this rather than assume it's correct. If the real
# taxonomy differs, every branch below silently falls through to
# _DEFAULT_WEIGHTS (see `_log_unmatched_sector_once` below), which is safe
# (identical to this engine's pre-existing behavior) but would make this
# feature a no-op for NSE/BSE stocks — worth spot-checking against a live
# response before trusting this to actually tilt production verdicts.
#
# Grouped into three economically-similar buckets rather than one override
# per individual sector — with only six signals and no backtest behind any
# of this, splitting further would read as more empirical precision than
# the underlying judgment actually has. Each override is a deliberate,
# documented tilt layered on top of _DEFAULT_WEIGHTS, not a fabricated
# back-tested calibration: the gap this closes is "a capital-intensive bank
# and an asset-light IT company get identical valuation logic," not
# "empirically optimal weights for every sector." Every override
# reallocates weight from other signals rather than just adding to the
# total, so no group's weights sum meaningfully differs from the baseline's
# 1.55 (which would otherwise skew that sector's final_score magnitude
# against the shared, sector-independent verdict thresholds below).
_RATE_SENSITIVE_SECTORS = {"Financial Services", "Real Estate", "Utilities"}
_GROWTH_SECTORS = {"Technology", "Communication Services", "Healthcare"}
_CYCLICAL_SECTORS = {"Basic Materials", "Energy", "Industrials", "Consumer Cyclical"}

_SECTOR_WEIGHT_OVERRIDES = {
    # Capital-intensive, rate-sensitive: valuation and the macro overlay
    # (FII/DII flow + RBI rate/inflation) matter more; growth is weighted
    # down since these are typically mature, income-oriented businesses
    # rather than high-growth compounders. Deltas from baseline: valuation
    # +0.05, growth -0.10, macro +0.05 -- net 0, preserving the 1.55 sum.
    "rate_sensitive": {"valuation": 0.45, "growth": 0.3, "macro": 0.20},
    # Asset-light, growth-driven: growth carries more weight; the macro
    # overlay (domestic rate/inflation, FII/DII flow) is less directly
    # relevant to export-oriented, globally-priced businesses. Valuation is
    # deliberately left at baseline here (only growth/macro move) -- deltas:
    # growth +0.05, macro -0.05 -- net 0.
    "growth": {"growth": 0.45, "macro": 0.1},
    # Cyclical: price/volume momentum (the technical + volume signals) is
    # more informative for a cyclical business than for a steady compounder,
    # so both are weighted up — offset by weighting valuation/growth down,
    # since a cyclical's steady-state fundamentals matter less than a
    # compounder's. A pure add-without-offset here would silently inflate
    # this group's final_score magnitude relative to the other two groups.
    "cyclical": {"technical": 0.3, "volume": 0.3, "valuation": 0.3, "growth": 0.3},
}

_unmatched_sectors_logged: set[str] = set()


def _log_unmatched_sector_once(sector: str) -> None:
    """One-time-per-process log when a real (non-None) sector value doesn't
    match any override bucket — the cheapest way to validate or invalidate
    the yfinance-taxonomy assumption above against real production traffic
    post-merge, without adding a new metrics dependency. Promoted from
    "debug" to "warning": every non-matching sector silently falls through
    to _DEFAULT_WEIGHTS (safe, but a no-op for the sector-aware tilt this
    engine exists to apply), and a "debug" line is invisible in this
    codebase's default INFO-level deployments — nobody would ever see this
    fire without deliberately turning debug logging on first."""
    if sector in _unmatched_sectors_logged:
        return
    _unmatched_sectors_logged.add(sector)
    log_event(LOGGER, "sector_weight_override_unmatched", level="warning", sector=sector)


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
    elif sector:
        _log_unmatched_sector_once(sector)
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

    # Disclosed limitation: an UNKNOWN signal (score 0 — e.g. technical for
    # a recent IPO with too little price history, or macro when both its
    # fetches failed) still keeps its full weight in this sum, contributing
    # a neutral 0 rather than being excluded and having the remaining
    # signals' weights renormalized. For a stock with more than one
    # UNKNOWN signal, this shrinks the achievable score range below what
    # the fixed, sector-independent BUY/SELL thresholds below assume,
    # systematically biasing such stocks toward HOLD regardless of how
    # strong its other signals (e.g. valuation/growth) actually are. Not
    # fixed here — reweighting on the fly would need its own calibration
    # pass (same "not a back-tested calibration" caveat the sector-weight
    # tilt above already carries), so this is flagged as a known gap
    # rather than silently accepted.
    score = 0
    for name, weight in weights.items():
        sig = signals.get(name)
        if not sig:
            continue
        score += sig.score * weight

    # Clamp to the documented -1..1 contract (docs/output-schema.md, every
    # frontend consumer of `signals.final_score`, and GET /api/v1's
    # external surface all treat this as a hard bound). The per-signal
    # score/weight combination isn't actually symmetric — verified: with
    # the default weights, the achievable maximum is ~1.18 and the
    # achievable minimum is ~-0.89, i.e. structurally easier to reach a
    # strong BUY reading than an equally strong SELL one, since a few
    # signals' positive ceilings (e.g. volume's 1.0, filings' 0.95) are
    # larger in magnitude than their own negative floors (volume's -0.5,
    # filings' -0.15). That asymmetry is a real, disclosed characteristic
    # of this engine's current per-signal magnitudes — fixing it would mean
    # re-calibrating individual signals' score constants, which (like the
    # sector-weight tilt above) would need real backtest data behind it to
    # do responsibly rather than substituting one unverified magnitude
    # guess for another. This clamp only prevents the documented contract
    # itself from being silently violated; it does not correct the
    # underlying skew.
    score = max(-1.0, min(1.0, score))

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
