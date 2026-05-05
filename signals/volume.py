"""Volume-based signal computation."""

from signals.models import Signal

def volume_signal(features: dict) -> Signal:
    """Return a volume signal based on current vs average volume ratio."""
    vol = features.get("volume")
    avg = features.get("avg_volume")

    if not vol or not avg:
        return Signal("volume", "UNKNOWN", 0, {})

    ratio = vol / avg

    if ratio > 3:
        return Signal("volume", "STRONG_ACCUMULATION", 1.0, {"ratio": ratio})
    if ratio > 2:
        return Signal("volume", "ACCUMULATION", 0.7, {"ratio": ratio})
    if ratio < 0.5:
        return Signal("volume", "DRYING_VOLUME", -0.5, {"ratio": ratio})

    return Signal("volume", "NORMAL", 0, {"ratio": ratio})
