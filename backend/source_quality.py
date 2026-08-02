"""Per-source signal-quality telemetry for the market-picks pipeline.

Writes one record per pipeline run under the `source_quality` namespace in
`core/state_store.py` (keyed by run id). Read by source_quality_report.py to
aggregate signal quality across runs. Complements source_health.py (day-level
freshness/volume) and scraper_error_counters.py (error counting for standalone
per-symbol endpoints) — neither gives a per-run view of "how many articles did
source X yield this run, how many survived to a validated pick."
"""

from datetime import datetime, timezone

from core import state_store
from core.observability import get_logger, log_event

NAMESPACE = "source_quality"
_RETENTION_DAYS = 90  # well beyond source_quality_report.py's default 14-day lookback
_logger = get_logger("source_quality")


def record_run(run_id: str, source_stats: dict[str, dict]) -> None:
    """Stores this run's per-source stats. No lock needed — each run owns its
    own key, so there's no concurrent-writer race to guard against. Never
    raises: a telemetry write failure must never affect a real pipeline run."""
    try:
        state_store.save(NAMESPACE, run_id, {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sources": source_stats,
        })
        state_store.delete_older_than(NAMESPACE, _RETENTION_DAYS)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(_logger, "source_quality_write_failed", level="warning", run_id=run_id, error=str(exc))
