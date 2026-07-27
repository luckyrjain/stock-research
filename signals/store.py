"""Persistence helpers for signal-engine outputs."""
import json
from pathlib import Path
from datetime import datetime, timedelta

from observability import get_logger, log_event

LOGGER = get_logger("signals.store")

BASE = Path("signals_data")

# Nothing in the codebase reads signals_data/ back — it's a write-only audit
# trail (one file per symbol per day it was analyzed). Without pruning it
# grows forever; 90 days is generous for "look back at what the signal engine
# said recently" while keeping each symbol's directory small.
_RETENTION_DAYS = 90


def save_signal(result) -> None:
    """Save the computed signal result to a dated JSON file. Best-effort,
    never raises — both main.py and api.py's SSE handler call this
    unguarded, right before the expensive/billed LLM analyst call, for a
    write-only audit trail nothing else in the codebase reads back. Without
    this guard, a filesystem hiccup here (disk full, a read-only mount)
    aborted the entire analysis over a debug artifact, the same class of
    bug already fixed once in main.py's _save_raw_tool_output — matching
    the "best-effort, swallow and log" convention every sibling persistence
    module (verdict_history.py, mf_holdings_history.py, schema_drift.py,
    source_health.py, scraper_error_counters.py) already follows."""
    try:
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
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "save_signal_failed", level="warning", symbol=getattr(result, "symbol", None), error=str(exc))


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
