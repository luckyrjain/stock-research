"""Per-scraper error/empty-payload counters for the standalone endpoints
outside ALL_DATA_TASKS' own schema-drift coverage (peers, financials,
insider activity, street consensus) — see CLAUDE.md's "Standalone scraper
error counters" section.

Deliberately NOT the same shape as telemetry/source_health.py's record_and_check():
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
from core import state_store
from core.observability import get_logger, log_event

LOGGER = get_logger("scraper_error_counters")

_NAMESPACE = "scraper_errors"


def _safe_name(scraper_name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in scraper_name).strip("_").lower() or "unknown"


def record_scraper_error(scraper_name: str, **context) -> None:
    """Call this at a standalone scraper's call site whenever its result
    carries a top-level "error" key — never for a legitimate empty result.
    Increments a persisted per-scraper counter and always logs a warning
    immediately, unlike telemetry/source_health.py's "wait for N bad days" threshold —
    a single error here already means one real user's request degraded, and
    these are on-demand per-request calls, not a scheduled batch where a
    single bad run is expected background noise. Never raises — broken
    counting must not break the request it's observing.

    The counter used to be a JSON file guarded by an `fcntl.flock` advisory
    lock, because two worker processes handling two different requests can
    race on the *same* scraper's counter, each read the same prior
    `error_count`, and the second write silently clobber the first —
    permanently undercounting with no warning logged, undermining the one
    thing this module exists to get right. `state_store.mutate()` keeps that
    guarantee with a row lock, which also holds across separate hosts."""
    try:
        updated = state_store.mutate(
            _NAMESPACE, _safe_name(scraper_name),
            lambda data: {**data, "error_count": data.get("error_count", 0) + 1},
            {"error_count": 0},
        )
        log_event(
            LOGGER, "scraper_error", level="warning",
            scraper=scraper_name,
            # None when there's no DATABASE_URL to count against — the error
            # itself still gets its warning line, which is the half of this
            # that matters most; a guessed count would be worse than none.
            error_count=(updated or {}).get("error_count"),
            **context,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "scraper_error_counter_failed", level="warning", scraper=scraper_name, error=str(exc))


def get_error_count(scraper_name: str) -> int:
    """Non-mutating read — used by tests and available for a future
    ops/status surface. Returns 0 (never guessed) if this scraper has never
    recorded an error."""
    return (state_store.load(_NAMESPACE, _safe_name(scraper_name)) or {}).get("error_count", 0)
