# signals/interpreter.py

def interpret(signal_result):
    signals = signal_result.signals

    g = signals.get("growth")
    v = signals.get("valuation")
    vol = signals.get("volume")

    if not g or not v:
        return "Insufficient signal data"

    if g.value.startswith("HIGH") and "OVERVALUED" in v.value:
        return "High-quality company trading at premium. Wait for better entry."

    if v.value == "EXTREME_OVERVALUED" and vol and vol.value == "NORMAL":
        return "No accumulation. Risk of sideways or correction."

    if g.value == "LOW_GROWTH":
        return "Weak fundamentals. Avoid unless turnaround visible."

    return "Neutral setup"