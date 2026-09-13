"""Confidence/ranking/recommendation logic for the Market Picks pipeline:
source credibility weights, deterministic trade levels, the net
quality-weighted signal, the 0-100 confidence score, the 4-tier BUY/
WATCHLIST/HOLD/SELL classifier, per-source telemetry aggregation, and
sector-balance reordering. Split out of market_picks_pipeline.py — see that
module for the pipeline itself, which calls these from its scoring phase.
"""

import math
import re

# ── Source credibility weights (0–1) ─────────────────────────────────────────
# Higher = stronger prior that this source's recommendations are actionable.
_SOURCE_CREDIBILITY: dict[str, float] = {
    "Morgan Stanley / JPMorgan":                      1.00,
    "Jefferies / Macquarie / Citi":                   0.95,
    "HSBC / BofA / Bernstein / Investec":             0.95,
    "ShareKhan / Mirae Asset":                        0.80,
    "SMIFS / IDBI Capital / Geojit / Deven Choksey":  0.75,
    "Motilal Oswal / ICICI Direct / Axis Securities": 0.80,
    "HDFC Securities Fundamental":                   0.85,
    "HDFC Securities Technical":                     0.75,
    "Zerodha Z-Connect":                             0.70,
    "GNews — Moneycontrol":                          0.65,
    "GNews — Business Standard":                     0.60,
    "GNews — Financial Express":                     0.55,
    "ET Markets":                                    0.60,
    "LiveMint":                                      0.60,
    "NDTV Profit":                                   0.55,
    "Hindu BusinessLine":                            0.55,
    # ── Institutional activity & fundamental screens ──────────────────────────
    "NSE Bulk/Block Deals":                          0.85,
    "NSE Insider Trades":                            0.85,
    "Screener.in Fundamental Screen":                0.70,
    "Trendlyne / Analyst Consensus":                 0.75,
}
_DEFAULT_CREDIBILITY = 0.50
# Fixed normalization reference for the consensus component.
# Represents a well-covered stock with ~4 non-syndicated brokerage BUY calls at
# credibility 0.80.  Capping here prevents the 3 new high-weight brokerage sources
# (NSE Bulk 0.85, Trendlyne 0.75, Screener 0.70) from inflating max_effective_signal
# and compressing every other stock's consensus score.
_CONSENSUS_REF = 12.0


def _trade_levels(
    si: dict,
    signal_score: float,
) -> tuple[float | None, float | None, float | None]:
    """
    Compute entry / target / stop-loss deterministically — no LLM involved.

    Logic:
      target  = price × (1 + upside),  upside = 10–25 % mapped from signal_score
      stop    = price × (1 − sl_pct),  sl_pct = 7–15 % from 52-week range width
      entry   = current price (slight discount when signal is strongly bullish)
    """
    price = (si or {}).get("current_price")
    if not price:
        return None, None, None

    hi = (si.get("52w_high") or 0)
    lo = (si.get("52w_low") or 0)

    # target: signal_score −1→10 %, 0→17.5 %, +1→25 %
    upside = 0.10 + ((signal_score + 1) / 2) * 0.15
    target = round(price * (1 + upside))

    # stop: volatility proxy from annual 52w range, clamped 7–15 %
    if hi > lo > 0:
        sl_pct = max(0.07, min(0.15, ((hi - lo) / lo) * 0.25))
    else:
        sl_pct = 0.10
    stop = round(price * (1 - sl_pct))

    # entry: 1 % below market for strong buys, at market otherwise
    discount = 0.01 if signal_score >= 0.3 else 0.0
    entry = round(price * (1 - discount))

    return entry, target, stop


def _reason_strength(reason: str) -> float:
    """
    Parse conviction strength from an analyst reason string.
    Returns 0.0 (bare mention) → 1.0 (target + upgrade + explicit buy).

    This strength value is used as a multiplier in _effective_signal so that
    "Morgan Stanley Buy, target ₹3200" outweighs "mentioned as a pick".
    """
    t = reason.lower()
    score = 0
    if re.search(r'target|price\s*target|tp\b|pt\b', t):      score += 2
    if re.search(r'upgrad|initiat|rais\w+ target', t):         score += 2
    if re.search(r'\bbuy\b|outperform|overweight|add\b|accumulate', t): score += 1
    return min(1.0, score / 5)   # normalise to 0–1 range


def _build_ranking_reasons(
    sources: list[dict],
    signal_score: float,
    quant_verdict: str,
    net_signal: float,
    confidence: float,
    upside_pct: float | None,
) -> list[str]:
    """Return up to 4 plain-English strings explaining why this stock ranked here."""
    reasons: list[str] = []

    # Brokerage BUY calls (non-syndicated)
    brokerage_buys = [
        s for s in sources
        if s.get("source_type") == "brokerage"
        and s.get("direction", "BUY").upper() == "BUY"
        and not s.get("syndicated", False)
    ]
    if len(brokerage_buys) >= 3:
        reasons.append(f"{len(brokerage_buys)} brokerage BUY calls")
    elif len(brokerage_buys) == 2:
        names = " & ".join({s["name"] for s in brokerage_buys})
        reasons.append(f"BUY rated by {names}")
    elif len(brokerage_buys) == 1:
        s = brokerage_buys[0]
        r = s.get("reason", "")
        reasons.append(r[:70] if r else f"BUY rated by {s['name']}")

    # Signal engine conviction
    if signal_score >= 0.5:
        reasons.append(f"Strong quant signal ({round(signal_score, 2)})")
    elif quant_verdict == "BUY":
        reasons.append("Signal engine: BUY")

    # Upside potential
    if upside_pct is not None and upside_pct >= 12:
        reasons.append(f"{upside_pct:.0f}% upside to target")

    # Fresh high-credibility coverage
    fresh = [s for s in sources if (s.get("article_age_days") or 999) <= 1]
    if fresh:
        top_cred = max(s.get("credibility", 0) for s in fresh)
        label = "Fresh high-credibility coverage today" if top_cred >= 0.80 else (
            f"Fresh coverage: {len(fresh)} article{'s' if len(fresh) > 1 else ''} today"
        )
        reasons.append(label)

    # High-credibility source not already captured above
    already_named = {s["name"] for s in brokerage_buys}
    top_src = max(
        (s for s in sources if s["name"] not in already_named),
        key=lambda s: s.get("credibility", 0),
        default=None,
    )
    if top_src and top_src.get("credibility", 0) >= 0.95:
        r = top_src.get("reason", "")
        if r:
            reasons.append(r[:70])

    return reasons[:4]


# ── Confidence scoring ────────────────────────────────────────────────────────

def _effective_signal(sources: list[dict]) -> float:
    """
    Net quality-weighted signal (positive = bullish, can go negative with SELL signals).

    Per-source base weight:
      brokerage, non-syndicated → 3.0
      brokerage, syndicated     → 1.0
      news,      non-syndicated → 1.0
      news,      syndicated     → 0.3

    Final weight = base × credibility × (1 + 0.3 × reason_strength)

    SELL / DOWNGRADE directions flip the sign.
    Story-clusters take their max-absolute-weight member (one wire story = one signal).
    """
    if not sources:
        return 0.0
    story_clusters: dict[str, list[float]] = {}
    for i, s in enumerate(sources):
        is_brokerage  = s.get("source_type", "news") == "brokerage"
        is_syndicated = s.get("syndicated", False)
        credibility   = s.get("credibility", _DEFAULT_CREDIBILITY)
        strength      = _reason_strength(s.get("reason", ""))
        direction     = (s.get("direction") or "BUY").upper()

        base = (3.0 if is_brokerage else 1.0) if not is_syndicated else (1.0 if is_brokerage else 0.3)
        weight = base * credibility * (1.0 + 0.3 * strength)
        if direction == "SELL":
            weight = -weight
        elif direction == "NEUTRAL":
            weight = 0.0  # NEUTRAL = no conviction either way

        cluster_key = s.get("story_cluster", str(i))
        story_clusters.setdefault(cluster_key, []).append(weight)

    # Per cluster: keep the item with max absolute value (preserves sign)
    total = 0.0
    for weights in story_clusters.values():
        dominant = max(weights, key=abs)
        total += dominant
    return total


_VALUATION_NUDGE = 3.0       # small confirmation nudge, not a primary driver
_CHEAP_PERCENTILE_MAX = 33.0  # vs. own P/E history — mirrors the ≤33rd/≥67th
_EXPENSIVE_PERCENTILE_MIN = 67.0  # thresholds ResultsDashboard's ValuationAnchorBadge already uses


def _compute_confidence(
    signal_score: float,
    sources: list[dict],
    max_effective_signal: float,
    stock_info: dict,
    valuation_percentile: float | None = None,
) -> float:
    """
    Return 0–100 confidence score.

    The signal engine already captures fundamentals and momentum,
    so the formula avoids double-counting:

      50 % signal engine  (quant: valuation + growth + volume + filings)
      30 % consensus      (source breadth + credibility + conviction strength)
      20 % timing         (recency — credibility-weighted mean, not min)

    `valuation_percentile` (0-100, where this stock's current P/E sits
    within its own last 3-5 years — see _fetch_valuation_percentile) is a
    confirmation signal layered on top, not a fourth primary component: it
    can only nudge the already-computed 0-100 score by ±_VALUATION_NUDGE
    points before the final clamp, rather than reallocating weight from the
    three components above. Absent (None) when Screener didn't have a
    parseable valuation band for this stock — contributes no nudge, not a
    guessed neutral one.
    """
    # 50 % — quant signal engine (-1..1 → 0..50)
    signal_comp = ((signal_score + 1) / 2) * 50

    # 30 % — consensus (log-scaled, normalized against a fixed reference ceiling)
    eff   = max(_effective_signal(sources), 0.0)
    denom = math.log1p(max(min(max_effective_signal, _CONSENSUS_REF), 1.0))
    mention_comp = min(30.0, (math.log1p(eff) / denom) * 30)

    # 20 % — timing: credibility-weighted mean of recency scores
    # exp(-age/3): day-0→1.0, day-3→0.37, day-7→0.10
    recency_comp = 4.0  # baseline for missing dates
    try:
        pairs = [
            (s["article_age_days"], s.get("credibility", _DEFAULT_CREDIBILITY))
            for s in sources
            if s.get("article_age_days") is not None
        ]
        if pairs:
            total_cred = sum(c for _, c in pairs)
            if total_cred > 0:
                weighted_recency = sum(math.exp(-a / 3) * c for a, c in pairs) / total_cred
                recency_comp = weighted_recency * 20
    except Exception:
        pass

    valuation_nudge = 0.0
    if valuation_percentile is not None:
        if valuation_percentile <= _CHEAP_PERCENTILE_MAX:
            valuation_nudge = _VALUATION_NUDGE
        elif valuation_percentile >= _EXPENSIVE_PERCENTILE_MIN:
            valuation_nudge = -_VALUATION_NUDGE

    return round(min(100.0, max(0.0, signal_comp + mention_comp + recency_comp + valuation_nudge)), 1)


_BUY_THRESHOLD       = 0.35
_WATCHLIST_THRESHOLD = 0.15
_SELL_THRESHOLD      = -0.30
_BUY_SIGNAL_GATE     = -0.30  # a BUY additionally needs the quant signal to not be bearish


def _classify_recommendation(net_signal: float, signal_score: float) -> tuple[str, float]:
    """
    4-tier recommendation (BUY / WATCHLIST / HOLD / SELL), thresholded on
    `combined_dir` — a clamped blend of source consensus and the quant
    signal engine, range ≈ -1..1+. Returns `(recommendation, combined_dir)`;
    the caller also needs `combined_dir` for `action_score`, so it's
    returned rather than silently recomputed a second time at the call site.

    `signal_score >= _BUY_SIGNAL_GATE` is BUY's own quant veto — a strong
    source consensus alone can't earn a BUY if the quant signal is bearish.
    An earlier version of this function also had a second, explicit
    "demote BUY to WATCHLIST when signal_score < -0.3" block below the
    branches — that was dead code, since the BUY branch's own guard already
    makes `signal_score < _BUY_SIGNAL_GATE and rec == "BUY"` impossible.
    Removed as an adversarial-review finding rather than left as misleading,
    unreachable code; this docstring is what's left of the explanation.
    """
    consensus_norm = min(1.0, max(-1.0, net_signal / 5.0))
    combined_dir   = 0.55 * consensus_norm + 0.45 * signal_score

    if combined_dir >= _BUY_THRESHOLD and signal_score >= _BUY_SIGNAL_GATE:
        rec = "BUY"
    elif combined_dir >= _WATCHLIST_THRESHOLD:
        rec = "WATCHLIST"
    elif combined_dir <= _SELL_THRESHOLD:
        rec = "SELL"
    else:
        rec = "HOLD"

    return rec, combined_dir


def _aggregate_source_stats(
    raw_sources: dict,
    raw_picks: list[dict],
    consolidated: list[dict],
) -> dict[str, dict]:
    """Tallies per-source telemetry for source_quality.record_run(): articles
    fetched (phase 1), picks the LLM extracted (phase 2), and picks that
    survived NSE-symbol validation (phase 3 — every item in `consolidated`
    passed validation by definition, so a source's validated count is how
    many of its picks fed a surviving consolidated group)."""
    from tools.market_picks_tools import SOURCES

    stats: dict[str, dict] = {
        name: {"articles_fetched": 0, "picks_extracted": 0, "picks_validated": 0}
        for name, _type, _fn in SOURCES
    }

    for name, data in raw_sources.items():
        if name in stats:
            stats[name]["articles_fetched"] = len(data.get("articles", []))

    for pick in raw_picks:
        name = pick.get("source")
        if name in stats:
            stats[name]["picks_extracted"] += 1

    for item in consolidated:
        for src in item.get("sources", []):
            name = src.get("name")
            if name in stats:
                stats[name]["picks_validated"] += 1

    return stats


_MAX_PICKS_PER_SECTOR = 2


def _apply_sector_balance(picks: list[dict], max_per_sector: int = _MAX_PICKS_PER_SECTOR) -> list[dict]:
    """Promote up to `max_per_sector` picks per sector (in existing rank order)
    to the front of the list, deferring the rest to the end — keeps the
    primary list from being dominated by one hot sector. Each pick must
    already carry a `sector` key; this only reorders, it never removes it."""
    sector_counts: dict[str, int] = {}
    primary:  list[dict] = []
    deferred: list[dict] = []
    for pick in picks:
        sector = pick.get("sector", "Unknown")
        if sector_counts.get(sector, 0) < max_per_sector:
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            primary.append(pick)
        else:
            deferred.append(pick)
    return primary + deferred
