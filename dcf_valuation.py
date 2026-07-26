"""A simple, disclosed-assumptions two-stage DCF, computed deterministically
from tools/screener_tools.py::get_financial_statements()'s cash_flow table —
no LLM involved, same "deterministic, not model-generated" convention as
market_picks_pipeline.py's entry/target/stop-loss levels.

This is a genuinely different lens from the two valuation views this app
already has (api.py's peer-relative percentile, and the absolute P/E-history
anchor in _compute_valuation_anchor): those both answer "cheap/expensive
compared to what" (peers, or its own trading history); this answers "cheap/
expensive compared to what the business's own cash flows are worth" — the
one lens neither of those covers.

**Deliberate simplifications, disclosed rather than hidden**:
1. Screener's cash-flow table has no cleanly-labelled, sector-independent
   Capex row to net against Operating Cash Flow — so this treats OCF itself
   as the Free-Cash-Flow-to-equity proxy. That overstates true FCF for a
   capital-intensive business and roughly approximates it for an
   asset-light one; this is a real, named limitation, not swept under a
   generic disclaimer.
2. The discount rate and terminal growth rate are fixed, market-wide
   assumptions (see _DISCOUNT_RATE / _TERMINAL_GROWTH below) — not adjusted
   per-company for beta, leverage, or sector, unlike a real equity-research
   DCF. A single shared rate is a coarse approximation across every stock
   this runs for.
3. Historical OCF growth (used to project the next _PROJECTION_YEARS) is
   clamped to _GROWTH_CLAMP — a handful of noisy cash-flow years can produce
   an extreme CAGR that would make the projection worse than a flat
   estimate, so it's bounded rather than trusted outright.

Returns `None` (never a guessed number) when there's too little cash-flow
history, the latest OCF isn't positive (a DCF on negative/zero cash flow
isn't meaningful), or share count can't be derived from market cap + price.
"""

_DISCOUNT_RATE    = 0.12   # rough Indian-equity cost-of-equity assumption — same for every stock
_TERMINAL_GROWTH  = 0.05   # long-run terminal growth, held below _DISCOUNT_RATE by construction
_PROJECTION_YEARS = 5
_MIN_HISTORY_YEARS = 3     # fewer OCF data points than this is too thin a sample to extrapolate from
_GROWTH_CLAMP = (-0.20, 0.25)   # clamps the historical CAGR used to project future OCF


def _find_operating_cash_flow_row(cash_flow: dict) -> list[float | None] | None:
    for row in cash_flow.get("rows", []):
        label = str(row.get("label", "")).lower()
        if "operating activit" in label:
            return row.get("values")
    return None


def _historical_cagr(values: list[float | None]) -> float | None:
    """CAGR from the earliest to the latest non-null value in the series,
    clamped to _GROWTH_CLAMP. None if fewer than _MIN_HISTORY_YEARS non-null
    points exist, or the earliest usable value isn't positive (a CAGR off a
    non-positive base isn't meaningful)."""
    points = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(points) < _MIN_HISTORY_YEARS:
        return None
    (first_i, first_v), (last_i, last_v) = points[0], points[-1]
    years_spanned = last_i - first_i
    if years_spanned <= 0 or first_v is None or first_v <= 0:
        return None
    cagr = (last_v / first_v) ** (1 / years_spanned) - 1
    return max(_GROWTH_CLAMP[0], min(_GROWTH_CLAMP[1], cagr))


def compute_dcf_estimate(
    cash_flow: dict | None,
    current_price: float | None,
    market_cap_cr: float | None,
) -> dict | None:
    """Returns {fair_value_per_share, current_price, upside_pct, verdict,
    growth_rate_used, discount_rate, terminal_growth, latest_ocf_cr} or None.
    `market_cap_cr` + `current_price` derive an implied share count — this
    codebase doesn't otherwise fetch shares-outstanding directly anywhere."""
    if not cash_flow or current_price is None or not current_price:
        return None
    if market_cap_cr is None or market_cap_cr <= 0:
        return None

    ocf_series = _find_operating_cash_flow_row(cash_flow)
    if not ocf_series:
        return None
    latest_ocf = next((v for v in reversed(ocf_series) if v is not None), None)
    if latest_ocf is None or latest_ocf <= 0:
        return None

    growth_rate = _historical_cagr(ocf_series)
    if growth_rate is None:
        return None

    # Two-stage: explicit projection years at the clamped historical growth
    # rate, then a Gordon-growth terminal value off the final projected year.
    discounted_sum = 0.0
    projected = latest_ocf
    for year in range(1, _PROJECTION_YEARS + 1):
        projected *= (1 + growth_rate)
        discounted_sum += projected / ((1 + _DISCOUNT_RATE) ** year)

    terminal_value = (projected * (1 + _TERMINAL_GROWTH)) / (_DISCOUNT_RATE - _TERMINAL_GROWTH)
    discounted_terminal = terminal_value / ((1 + _DISCOUNT_RATE) ** _PROJECTION_YEARS)

    equity_value_cr = discounted_sum + discounted_terminal
    # market_cap_cr is in ₹ Cr, current_price in plain ₹ — implied shares
    # outstanding, then fair value per share in the same plain-₹ units as
    # current_price so the two are directly comparable.
    shares_outstanding = (market_cap_cr * 1_00_00_000) / current_price
    if shares_outstanding <= 0:
        return None
    fair_value_per_share = (equity_value_cr * 1_00_00_000) / shares_outstanding

    upside_pct = round((fair_value_per_share - current_price) / current_price * 100, 1)
    verdict = "Undervalued" if upside_pct >= 20 else "Overvalued" if upside_pct <= -20 else "Fair"

    return {
        "fair_value_per_share": round(fair_value_per_share, 2),
        "current_price":        round(current_price, 2),
        "upside_pct":           upside_pct,
        "verdict":              verdict,
        "growth_rate_used":     round(growth_rate * 100, 1),
        "discount_rate":        round(_DISCOUNT_RATE * 100, 1),
        "terminal_growth":      round(_TERMINAL_GROWTH * 100, 1),
        "latest_ocf_cr":        round(latest_ocf, 1),
    }
