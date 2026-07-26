"""Per-scraper error/empty-payload counters for the standalone endpoints
outside ALL_DATA_TASKS' own schema-drift coverage (peers, financials,
insider activity, street consensus) — see CLAUDE.md's "Standalone scraper
error counters" section.

Deliberately NOT the same shape as source_health.py's record_and_check():
that module tracks day-level *volume* anomalies across sources with a
genuine "should usually have data" baseline (the 20 Market Picks sources,
the market-wide macro/FII-DII overlay) and is explicitly documented there
as the wrong tool for these four endpoints — most individual stocks
legitimately have zero insider trades or zero Trendlyne coverage on a given
day, so a "3 empty days in a row" alert would just be noise.

What this module counts instead: only genuine `{"error": ...}` scraper
payloads (the "tools must not raise" convention's own signal that something
actually broke), never a legitimate empty result — the two were previously
indistinguishable at these call sites (`fetch_x(...).get("trades", [])`
silently maps both "NSE returned nothing today" and "NSE request failed"
to the same `[]`), which is exactly the "silent layout change degrades
with no log line to grep for" gap this closes.
"""
import json
import os
import tempfile
import threading
from pathlib import Path

from observability import get_logger, log_event

LOGGER = get_logger("scraper_error_counters")

_COUNTERS_DIR = Path("output/_scraper_error_counters")
_lock = threading.Lock()  # in-process only — one counter file per scraper,
                           # per-request-endpoint traffic doesn't need the
                           # cross-process fcntl locking source_health.py
                           # uses for its higher-concurrency batch callers


def _safe_name(scraper_name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in scraper_name).strip("_").lower() or "unknown"


def _path(scraper_name: str) -> Path:
    return _COUNTERS_DIR / f"{_safe_name(scraper_name)}.json"


def record_scraper_error(scraper_name: str, **context) -> None:
    """Call this at a standalone scraper's call site whenever its result
    carries a top-level "error" key — never for a legitimate empty result.
    Increments a small persisted counter (call_count/error_count, same
    tempfile + os.replace atomic-write convention as cache.py::save()) and
    always logs a warning immediately, unlike source_health.py's "wait for
    N bad days" threshold — a single error here already means one real
    user's request degraded, and these are on-demand per-request calls, not
    a scheduled batch where a single bad run is expected background noise.
    Never raises — a broken counter file must not break the request it's
    observing."""
    try:
        _COUNTERS_DIR.mkdir(parents=True, exist_ok=True)
        path = _path(scraper_name)
        with _lock:
            data = {"error_count": 0}
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                except Exception:
                    data = {"error_count": 0}
            data["error_count"] = data.get("error_count", 0) + 1

            fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(json.dumps(data))
                os.replace(tmp_path, path)
            except Exception:
                Path(tmp_path).unlink(missing_ok=True)
                raise
        log_event(
            LOGGER, "scraper_error", level="warning",
            scraper=scraper_name, error_count=data["error_count"], **context,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "scraper_error_counter_failed", level="warning", scraper=scraper_name, error=str(exc))


def get_error_count(scraper_name: str) -> int:
    """Non-mutating read — used by tests and available for a future
    ops/status surface. Returns 0 (never guessed) if this scraper has never
    recorded an error."""
    try:
        path = _path(scraper_name)
        if not path.exists():
            return 0
        return json.loads(path.read_text()).get("error_count", 0)
    except Exception:
        return 0
