"""Pure percentile/valuation-anchor math over a get_peer_comparison() result.

Extracted out of api.py (where it originally lived only for the
GET /api/peers/{symbol} endpoint) so market_picks_pipeline.py can reuse the
exact same, already-reviewed math to fold a valuation signal into Market
Picks confidence scoring — rather than duplicating it or having
market_picks_pipeline.py import from api.py (which already imports FROM
market_picks_pipeline.py at module level, so that direction would work
today only by accident of import timing; a shared leaf module avoids
relying on that).
"""
import statistics


def _parse_peer_numeric(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


def compute_peer_percentiles(self_row: dict | None, peer_rows: list[dict]) -> dict[str, float]:
    """For every ratio column present in self_row's values AND at least one peer's
    values, return self's percentile rank (0-100) among [self] + peers for that
    column — a plain rank-based percentile, not a distribution fit. Columns
    Screener doesn't expose for this sector (or that no peer reports) are simply
    absent from the result, never guessed or backfilled."""
    if not self_row or not peer_rows:
        return {}

    percentiles: dict[str, float] = {}
    for col, raw_self in self_row.get("values", {}).items():
        self_num = _parse_peer_numeric(raw_self)
        if self_num is None:
            continue
        peer_nums = [
            n for n in (_parse_peer_numeric(p.get("values", {}).get(col)) for p in peer_rows)
            if n is not None
        ]
        if not peer_nums:
            continue
        all_values = peer_nums + [self_num]
        below = sum(1 for v in all_values if v < self_num)
        equal = sum(1 for v in all_values if v == self_num)
        percentiles[col] = round((below + 0.5 * equal) / len(all_values) * 100, 1)
    return percentiles


def compute_valuation_anchor(self_row: dict | None, valuation_band: dict) -> dict | None:
    """Absolute valuation anchor: where the stock's current P/E sits within its
    OWN last 3-5 years of Screener-published Price to Earning values — a real
    band from real yearly data, not a sector benchmark this codebase has no
    business inventing. None when `self_row` has no parseable current P/E, or
    `valuation_band` has fewer than 3 years on record (see
    tools/screener_tools.py::_extract_valuation_band) — never guessed.

    Percentile math mirrors compute_peer_percentiles' mean-rank formula, but
    the population it ranks against differs on purpose: `current_pe` is
    today's live snapshot, not itself one of the historical yearly
    observations, so it is ranked against `pe_values` alone rather than
    folded into that population the way `self` is folded into the peer set
    above."""
    if not self_row or not valuation_band:
        return None
    pe_values = valuation_band.get("pe") or []
    if len(pe_values) < 3:
        return None

    current_pe = None
    for col, raw in self_row.get("values", {}).items():
        normalized = col.lower().replace(" ", "")
        if "p/e" in normalized or normalized == "pe":
            current_pe = _parse_peer_numeric(raw)
            break
    if current_pe is None:
        return None

    below = sum(1 for v in pe_values if v < current_pe)
    equal = sum(1 for v in pe_values if v == current_pe)
    percentile = round((below + 0.5 * equal) / len(pe_values) * 100, 1)

    return {
        "current_pe": current_pe,
        "years": valuation_band.get("years", []),
        "pe_history": pe_values,
        "low": min(pe_values),
        "median": statistics.median(pe_values),
        "high": max(pe_values),
        "percentile": percentile,
    }
