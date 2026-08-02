"""Volume-based signal computation."""

from signals.models import Signal

def volume_signal(features: dict) -> Signal:
    """Return a volume signal based on current vs average volume ratio.

    A high-volume day alone doesn't say which direction the volume was on —
    heavy buying and heavy panic-selling/distribution both show up as an
    above-average volume ratio. `change_pct` (today's price move) breaks
    that tie: a volume spike on a down day is distribution, not
    accumulation, and scores negatively instead of positively. Only flips
    direction when change_pct is genuinely known and negative — a missing/
    None change_pct (e.g. a caller that never passed it) preserves the
    previous "assume accumulation on a spike" behavior rather than guessing
    a price direction that isn't there, matching this codebase's
    "never invent" convention.
    """
    vol = features.get("volume")
    avg = features.get("avg_volume")
    change_pct = features.get("change_pct")

    if not vol or not avg:
        return Signal("volume", "UNKNOWN", 0, {})

    ratio = vol / avg
    is_down_day = change_pct is not None and change_pct < 0

    if ratio > 3:
        if is_down_day:
            return Signal("volume", "DISTRIBUTION", -1.0, {"ratio": ratio, "change_pct": change_pct})
        return Signal("volume", "STRONG_ACCUMULATION", 1.0, {"ratio": ratio})
    if ratio > 2:
        if is_down_day:
            return Signal("volume", "DISTRIBUTION", -0.7, {"ratio": ratio, "change_pct": change_pct})
        return Signal("volume", "ACCUMULATION", 0.7, {"ratio": ratio})
    if ratio < 0.5:
        return Signal("volume", "DRYING_VOLUME", -0.5, {"ratio": ratio})

    return Signal("volume", "NORMAL", 0, {"ratio": ratio})
