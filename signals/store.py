"""Persistence helpers for signal-engine outputs."""
import json
from pathlib import Path
from datetime import datetime

BASE = Path("signals_data")

def save_signal(result):
    """Save the computed signal result to a dated JSON file."""
    path = BASE / result.symbol
    path.mkdir(parents=True, exist_ok=True)

    file = path / f"{datetime.now().date()}.json"

    with open(file, "w", encoding="utf-8") as f:
        json.dump({
            "score": result.final_score,
            "verdict": result.verdict,
            "signals": {
                k: v.__dict__ for k, v in result.signals.items()
            }
        }, f, indent=2)
