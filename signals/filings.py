from signals.models import Signal

KEYWORDS = [
    "order", "contract", "agreement", "deal",
    "partnership", "award", "client"
]

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
        return Signal("filings", "STRONG_DEAL_FLOW", 0.8, {"hits": hits})

    if hits:
        return Signal("filings", "WEAK_SIGNAL", 0.3, {"hits": hits})

    return Signal("filings", "NONE", 0, {})