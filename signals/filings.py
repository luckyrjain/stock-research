from signals.filings_classifier import classify_rating_action
from signals.models import Signal

KEYWORDS = [
    "order", "contract", "agreement", "deal",
    "partnership", "award", "client"
]

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
        for k in KEYWORDS:
            if k in text:
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
        if rating_action["action"] == "upgrade":
            base_score = min(1.0, base_score + _RATING_NUDGE)
        elif rating_action["action"] == "downgrade":
            base_score = max(-1.0, base_score - _RATING_NUDGE)

    return Signal("filings", label, base_score, meta)