"""Symbol/company-name matching and dedup helpers for the Market Picks
pipeline: text-similarity dedup key primitives, NSE equity-master
validation, analyst-target price selection/parsing, and fuzzy ticker
resolution. Split out of market_picks_pipeline.py — see that module for the
pipeline itself, which calls these from its extraction and consolidation
phases.
"""

import re

from sqlalchemy import select

from db.models import get_engine, securities
from pipelines.market_picks_scoring import _DEFAULT_CREDIBILITY

# ── Company-name suffixes to strip before ticker lookup ──────────────────────
_COMPANY_SUFFIXES = re.compile(
    r'\b(limited|ltd|industries|industry|technologies|technology|'
    r'enterprises|solutions|holdings|group|corporation|corp|'
    r'international|india|infotech|infosystems|systems|services|'
    r'finance|financial|bank|insurance)\b',
    re.IGNORECASE,
)


def _title_words(title: str) -> frozenset[str]:
    """Normalize a headline to a word-set for Jaccard dedup."""
    _STOP = frozenset({
        'a', 'an', 'the', 'to', 'in', 'of', 'and', 'or', 'is', 'are',
        'was', 'for', 'with', 'at', 'by', 'on', 'as', 'this', 'that',
        'it', 'its', 'be', 'has', 'have', 'had', 'from', 'but', 'not', 'its',
    })
    words = re.sub(r'[^a-z0-9 ]', '', title.lower()).split()
    return frozenset(w for w in words if w not in _STOP and len(w) > 2)


# ── NSE equity master (hard symbol validation) ────────────────────────────────


def _load_nse_symbol_universe() -> set[str]:
    """Official set of NSE equity symbols, read from the `securities` table
    (populated nightly by pipelines/eod_prices_pipeline.py::refresh_securities_master
    from NSE's own EQUITY_L.csv — no live fetch here). Fails open (empty set)
    when DATABASE_URL is unset or the query fails, so a DB hiccup never
    silently blocks all validation."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(select(securities.c.symbol)).fetchall()
        return {r[0].strip().upper() for r in rows if r[0] and r[0].strip()}
    except Exception:
        return set()  # empty = allow all (fail open)


# A regex-parsed "target ₹X" match out of a source's free-text reason string
# is unvalidated against the stock's own price — a false-positive match (e.g.
# "target 2027 revenue growth" capturing "2027" as a target price) or a
# genuine but stale/wrong figure can land arbitrarily far from the current
# price, breaking the entry < target invariant _trade_levels' own formula
# path guarantees (target is always 10-25% above price). Any analyst target
# outside this multiple-of-price band is treated as unparseable (falls back
# to the deterministic formula) rather than trusted at face value.
_ANALYST_TARGET_MIN_PRICE_MULT = 0.5
_ANALYST_TARGET_MAX_PRICE_MULT = 3.0


def _select_target_price(
    analyst_target: float | None,
    formula_target: float | None,
    price: float | None,
) -> float | None:
    """
    Prefer a real, regex-parsed analyst target price over the deterministic
    formula target — but only when it's within a plausible multiple of the
    current price (_ANALYST_TARGET_MIN_PRICE_MULT.._ANALYST_TARGET_MAX_PRICE_MULT).
    A target outside that band is more likely a false-positive regex match
    (e.g. "target 2027 revenue growth" capturing "2027" as a price) or a
    stale/wrong figure than a real one, and trusting it at face value would
    violate the stop < entry < target invariant the formula path guarantees.
    Falls back to `formula_target` (which may itself be None) in that case.
    """
    if (
        analyst_target is not None
        and analyst_target > 0
        and price is not None
        and price > 0
        and _ANALYST_TARGET_MIN_PRICE_MULT * price <= analyst_target <= _ANALYST_TARGET_MAX_PRICE_MULT * price
    ):
        return analyst_target
    return formula_target


def _dedup_key(ticker: str, company: str) -> str:
    """
    Group key for consolidating raw LLM-extracted picks that refer to the
    same stock, before any NSE/ticker validation has happened. Prefers a
    real ticker when the LLM provided one; falls back to a normalized
    company name (uppercased, alphanumeric-only) when it didn't.

    Previously truncated the normalized-company-name fallback to 12
    characters. Two different companies whose normalized names happen to
    share the same first 12 characters (e.g. two subsidiaries of the same
    group with a long shared name prefix) would silently collide onto the
    same group and have their source mentions merged — with no length-
    related reason for the cap in the first place (this key is only ever
    used as a dict lookup, where key length doesn't matter). Uses the full
    normalized name instead.
    """
    if ticker:
        return ticker
    return re.sub(r"[^A-Z0-9]", "", company.upper())


def _resolve_symbol_via_fuzzy_match(norm: str, symbols: list[dict]) -> dict | None:
    """
    Resolve `norm` (a normalized company name) against NSE autocomplete's
    `symbols` results via rapidfuzz fuzzy matching. Only called from
    consolidation's Path B2 — when there's no exact ticker match among the
    candidates and more than one candidate exists to disambiguate between.
    Returns None (never guessed) when rapidfuzz isn't installed, there's
    only one candidate, or nothing clears the score cutoff.
    """
    if len(symbols) <= 1:
        return None
    try:
        from rapidfuzz import process as _rfprocess, fuzz as _rffuzz
    except ImportError:
        return None

    sym_names = [
        (s.get("symbol", ""), s.get("symbol_info") or s.get("company", ""))
        for s in symbols
    ]
    best_match = _rfprocess.extractOne(
        norm,
        [n for _, n in sym_names],
        scorer=_rffuzz.token_set_ratio,
        score_cutoff=70,
    )
    if not best_match:
        return None
    # extractOne's own 3rd tuple element is the matched choice's index in
    # the `choices` list passed to it -- using this directly (rather than
    # re-deriving it via `[...].index(best_match[0])`, which silently finds
    # the FIRST list entry with that matched display-name string) avoids
    # resolving to the WRONG symbol whenever two different entries share the
    # same display name (e.g. two differently-suffixed listings of a
    # similarly named company).
    return symbols[best_match[2]]


def _parse_targets_from_sources(sources: list[dict]) -> float | None:
    """
    Extract credibility-weighted analyst target price from source reason strings.
    Returns None when no parseable target is found.
    """
    weighted: list[tuple[float, float]] = []
    for s in sources:
        m = re.search(
            r'(?:target|tp\b|pt\b)\s*(?:₹|₨|[Rr][Ss]?\.?|[Ii][Nn][Rr])?\s*([\d,]{3,})',
            s.get("reason", ""),
            re.IGNORECASE,
        )
        if m:
            try:
                t = float(m.group(1).replace(",", ""))
                weighted.append((t, s.get("credibility", _DEFAULT_CREDIBILITY)))
            except Exception:
                pass
    if not weighted:
        return None
    total_cred = sum(c for _, c in weighted)
    return round(sum(t * c for t, c in weighted) / total_cred) if total_cred > 0 else None
