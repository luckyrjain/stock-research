"""Per-task cache with TTL-based freshness checks.

Each task's output is stored at output/<SYMBOL>/<task_name>.json
with a top-level _meta.fetched_at timestamp.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path("output")

# How long each task's data stays fresh (hours)
TTL_HOURS: dict[str, float] = {
    "stock_info":     1,     # price data — refresh every hour
    "research":       24,    # fundamentals — refresh daily
    "news":           1,     # headlines — refresh every hour
    "shareholding":   168,   # quarterly filings — 7 days
    "mf_holdings":    168,   # quarterly filings — 7 days
    "analysis":       24,    # re-analyse daily (or when any input changes)
    "price_history":  6,     # daily-close series for sparklines — doesn't move fast
}


def cache_path(symbol: str, task_name: str) -> Path:
    return CACHE_DIR / symbol.upper() / f"{task_name}.json"


def _is_failed_payload(data: object) -> bool:
    # `_degraded` marks a safe-fallback payload (e.g. crew._safe_analysis_fallback) —
    # it has no "error" key of its own, but must not be cached as if it were a
    # genuine result, or it'd be served as fresh for the rest of the TTL window.
    return isinstance(data, dict) and bool(data.get("error") or data.get("_degraded"))


def load(symbol: str, task_name: str) -> dict | None:
    """Return cached data if it exists, succeeded, and is within TTL, else None."""
    p = cache_path(symbol, task_name)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if _is_failed_payload(data):
            return None
        fetched_at_str = data.get("_meta", {}).get("fetched_at")
        if not fetched_at_str:
            return None
        fetched_at = datetime.fromisoformat(fetched_at_str)
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        return data if age_hours <= TTL_HOURS.get(task_name, 1) else None
    except Exception:
        return None


def save(symbol: str, task_name: str, data: dict) -> None:
    """Write a copy of data to cache, stamping _meta.fetched_at."""
    if _is_failed_payload(data):
        return
    p = cache_path(symbol, task_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["_meta"] = {"fetched_at": datetime.now(timezone.utc).isoformat()}
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def is_fresh(symbol: str, task_name: str) -> bool:
    p = cache_path(symbol, task_name)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if _is_failed_payload(data):
            return False
    except Exception:
        return False
    return load(symbol, task_name) is not None


def status(symbol: str) -> dict[str, str]:
    """Return a human-readable freshness label for every task."""
    result = {}
    for name in TTL_HOURS:
        p = cache_path(symbol, name)
        if not p.exists():
            result[name] = "missing"
            continue
        data = load(symbol, name)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            ts = raw.get("_meta", {}).get("fetched_at", "")[:16]
        except Exception:
            ts = "?"
        result[name] = f"fresh  (at {ts})" if data is not None else f"stale  (at {ts})"
    return result
