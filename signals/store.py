"""Persistence helpers for signal-engine outputs."""
import json
from pathlib import Path
from datetime import datetime, timedelta

BASE = Path("signals_data")

# Nothing in the codebase reads signals_data/ back — it's a write-only audit
# trail (one file per symbol per day it was analyzed). Without pruning it
# grows forever; 90 days is generous for "look back at what the signal engine
# said recently" while keeping each symbol's directory small.
_RETENTION_DAYS = 90


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

    _prune_old_signals(path)


def _prune_old_signals(symbol_dir: Path, retention_days: int = _RETENTION_DAYS) -> None:
    """Delete this symbol's snapshots older than retention_days. Only scans the
    one symbol's directory just written to, not all of signals_data/ — cheap
    and bounded regardless of how many distinct symbols have been analyzed."""
    cutoff = datetime.now().date() - timedelta(days=retention_days)
    try:
        for f in symbol_dir.glob("*.json"):
            try:
                if datetime.strptime(f.stem, "%Y-%m-%d").date() < cutoff:
                    f.unlink()
            except ValueError:
                continue
    except Exception:
        pass
