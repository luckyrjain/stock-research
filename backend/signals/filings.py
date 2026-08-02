import re

from signals.filings_classifier import classify_rating_action
from signals.models import Signal

KEYWORDS = [
    "order", "contract", "agreement", "deal",
    "partnership", "award", "client"
]

# "order" alone is ambiguous -- NSE filing boilerplate overwhelmingly uses it
# as filler ("in order to comply with...") rather than a genuine business
# order (a purchase/export/work order). Same class of substring false
# positive already fixed once for "care" in
# filings_classifier.py::_find_rating_agency() -- strip just this specific
# filler phrase before checking for "order" specifically, rather than
# dropping the keyword entirely, so a real "received an order worth ₹50 Cr"
# filing still counts.
_ORDER_FILLER_RE = re.compile(r"\bin order to\b")

# A credit-rating change is a small confirmation nudge on top of the
# keyword-hit score below, not a primary driver — mirrors the
# "confirmation signal layered on top" pattern _compute_confidence()'s
# valuation nudge already established in market_picks_pipeline.py. Only
# upgrade/downgrade move the score; "reaffirmed" is neutral (the rating
# didn't change, so it carries no new directional information).
_RATING_NUDGE = 0.15


def filings_signal(features: dict) -> Signal:
    filings = features.get("filings", [])

    hits = []
    for f in filings:
        # A filing's title/desc can be present-but-None (e.g. a scraper
        # passing through a null field) rather than simply absent, in which
        # case f.get(key, "") returns None, not the default — guard with
        # `or ""` so this never raises on a malformed/None text field.
        text = ((f.get("title") or "") + " " + (f.get("desc") or "")).lower()
        order_check_text = _ORDER_FILLER_RE.sub("", text)
        for k in KEYWORDS:
            haystack = order_check_text if k == "order" else text
            if k in haystack:
                hits.append(k)

    if len(hits) >= 2:
        base_score, label = 0.8, "STRONG_DEAL_FLOW"
    elif hits:
        base_score, label = 0.3, "WEAK_SIGNAL"
    else:
        base_score, label = 0.0, "NONE"

    rating_action = classify_rating_action(filings)
    meta = {"hits": hits}
    if rating_action:
        meta["rating_action"] = rating_action
        # Only relabel when the rating action is what actually moved the
        # score off zero (no keyword hits) — a keyword-driven label
        # (STRONG_DEAL_FLOW/WEAK_SIGNAL) still correctly names the
        # dominant driver even with a rating nudge layered on top, so it's
        # left as-is. Without this, a downgrade-only filing set rendered
        # as `Signal(value="NONE", score=-0.15)` — a frontend badge
        # literally labeled "NONE" while still colored red/green off the
        # nonzero score.
        if rating_action["action"] == "upgrade":
            base_score = min(1.0, base_score + _RATING_NUDGE)
            if label == "NONE":
                label = "RATING_UPGRADE"
        elif rating_action["action"] == "downgrade":
            base_score = max(-1.0, base_score - _RATING_NUDGE)
            if label == "NONE":
                label = "RATING_DOWNGRADE"

    return Signal("filings", label, base_score, meta)