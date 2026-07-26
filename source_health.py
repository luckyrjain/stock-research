"""Lightweight per-source freshness/volume monitoring.

Complements schema_drift.py, which only catches *type* drift on the six
ALL_DATA_TASKS fields — it has nothing to say about a source that's still
returning well-shaped data but has silently gone quiet (0 results every
run), since an empty result isn't a shape mismatch. That failure mode is
otherwise invisible: `_SOURCE_CREDIBILITY` weights every market-picks
source into confidence scoring, so a dead source doesn't error, it just
quietly stops contributing.

Deliberately scoped to sources with a genuine "should usually have data"
expectation: the 20 market-picks SOURCES (aggregate across every symbol,
run on a fixed weekly/on-demand cadence — see market_picks_pipeline.py's
_phase_scrape) and the two market-wide macro-overlay fetches
(fii_dii_flow, macro_context — see signals/macro.py). Deliberately NOT
applied to the three genuinely per-symbol standalone endpoints (peers,
insider-activity, street-consensus): most individual stocks legitimately
have zero insider trades or zero Trendlyne-cited coverage on a given
day — this codebase's own documented "expected common case" everywhere
else in CLAUDE.md, not a source-health anomaly. Applying a volume-anomaly
heuristic there would just be noise.

State is a small per-source JSON file under output/_source_health/ (same
"cache" directory convention as everything else in this codebase) holding
a rolling window of recent ok/not-ok results — no database needed for
something this lightweight.
"""
import json
from pathlib import Path

from observability import get_logger, log_event

LOGGER = get_logger("source_health")

_HEALTH_DIR = Path("output/_source_health")
_MAX_HISTORY = 20                # runs retained per source
_MIN_HISTORY_FOR_BASELINE = 5    # need this many prior runs before alerting —
                                  # a brand-new source with little history
                                  # shouldn't trip a false alarm
_CONSECUTIVE_FAILURES_TO_ALERT = 3


def _safe_name(source_name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in source_name).strip("_").lower() or "unknown"


def _path(source_name: str) -> Path:
    return _HEALTH_DIR / f"{_safe_name(source_name)}.json"


def record_and_check(source_name: str, ok: bool, **context) -> None:
    """Append this run's ok/not-ok result for `source_name`, then warn if a
    source with an established healthy baseline has now failed
    _CONSECUTIVE_FAILURES_TO_ALERT runs in a row. Never raises — a broken
    health-tracking file must not break the scrape/pipeline run it's
    trying to observe, the same "tools must not raise" convention this
    codebase applies everywhere else."""
    try:
        path = _path(source_name)
        prior: list[bool] = []
        if path.exists():
            try:
                prior = json.loads(path.read_text()).get("runs", [])[-_MAX_HISTORY:]
            except Exception:
                prior = []

        # A source only has an established "should usually have data"
        # baseline once it has enough history AND at least one of those
        # prior runs actually succeeded — a source that's simply always
        # been empty (e.g. genuinely no coverage) shouldn't alert either.
        was_healthy_baseline = len(prior) >= _MIN_HISTORY_FOR_BASELINE and any(prior)

        history = (prior + [ok])[-_MAX_HISTORY:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"runs": history}))

        recent = history[-_CONSECUTIVE_FAILURES_TO_ALERT:]
        if (
            was_healthy_baseline
            and len(recent) == _CONSECUTIVE_FAILURES_TO_ALERT
            and not any(recent)
        ):
            log_event(
                LOGGER, "source_health_anomaly", level="warning",
                source=source_name,
                consecutive_failures=_CONSECUTIVE_FAILURES_TO_ALERT,
                **context,
            )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "source_health_tracking_failed", level="warning", source=source_name, error=str(exc))
