# signals/interpreter.py

def interpret(signal_result):
    signals = signal_result.signals

    g = signals.get("growth")
    v = signals.get("valuation")
    vol = signals.get("volume")

    # run_signal_engine() always populates every signals dict key with a
    # real Signal instance (never None -- see signals/engine.py), so a
    # `not g`/`not v` check here is unreachable: a plain dataclass with no
    # custom __bool__ is always truthy. "Not enough data to score" is
    # signalled by g.value/v.value == "UNKNOWN" (this codebase's standard
    # insufficient-data convention across every signal module), which is
    # the actual condition this guard is meant to catch.
    if not g or not v or g.value == "UNKNOWN" or v.value == "UNKNOWN":
        return "Insufficient signal data"

    if g.value.startswith("HIGH") and "OVERVALUED" in v.value:
        return "High-quality company trading at premium. Wait for better entry."

    if v.value == "EXTREME_OVERVALUED" and vol and vol.value == "NORMAL":
        return "No accumulation. Risk of sideways or correction."

    if g.value == "LOW_GROWTH":
        return "Weak fundamentals. Avoid unless turnaround visible."

    return "Neutral setup"