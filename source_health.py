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
a rolling window of recent per-CALENDAR-DAY ok/not-ok results — no
database needed for something this lightweight.

Two correctness properties that a naive read-modify-write can't provide,
both addressed below:

- **Concurrency safety**: several callers can race to update the same
  source's file at once (e.g. market_picks_pipeline.py's _phase_scrape
  workers, or several ThreadPoolExecutor workers in signals/macro.py all
  missing the "_MACRO" pseudo-symbol cache at once — see CLAUDE.md's
  "Shared state and queues" section for that exact scenario). A plain
  read-then-write is a classic lost-update race: two callers can both read
  the same prior history, then each write their own updated version, with
  the second write silently clobbering the first — corrupting the file if
  the writes interleave, or silently dropping an update (and, worse,
  resetting the rolling baseline) even when they don't. `_locked()` below
  makes the whole read-modify-write cycle for one source's file mutually
  exclusive.
- **Time-normalized cadence**: the alert threshold ("N bad runs in a
  row") must mean N bad *days*, not N raw calls — otherwise a burst of
  same-hour force-refresh retries could trip the threshold in minutes,
  while the intended weekly-cron cadence would need N *weeks* to ever
  flag a dead source. Several calls for the same source on the same UTC
  calendar day collapse into one entry (keeping the latest result for
  that day) rather than each counting as its own data point.
"""
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from observability import get_logger, log_event

LOGGER = get_logger("source_health")

_HEALTH_DIR = Path("output/_source_health")
_MAX_HISTORY = 20                # distinct days retained per source
_MIN_HISTORY_FOR_BASELINE = 5    # need this many prior days before alerting —
                                  # a brand-new source with little history
                                  # shouldn't trip a false alarm
_CONSECUTIVE_FAILURES_TO_ALERT = 3


def _safe_name(source_name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in source_name).strip("_").lower() or "unknown"


def _path(source_name: str) -> Path:
    return _HEALTH_DIR / f"{_safe_name(source_name)}.json"


def _lock_path(source_name: str) -> Path:
    return _HEALTH_DIR / f".{_safe_name(source_name)}.lock"


def _today() -> str:
    """Isolated as its own function purely so tests can patch it to
    simulate distinct calendar days without sleeping in real time."""
    return datetime.now(timezone.utc).date().isoformat()


@contextmanager
def _locked(source_name: str):
    """Advisory, blocking, cross-process exclusive lock guarding one
    source's entire read-modify-write cycle. fcntl.flock is POSIX-only —
    matches this codebase's existing Linux-only deployment assumption
    (no Windows-specific fallback exists elsewhere in this repo either).
    Imported lazily so a Windows dev environment can still import this
    module (e.g. for its pure helpers) without failing at import time."""
    import fcntl

    _HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(_lock_path(source_name), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _atomic_write(path: Path, payload: dict) -> None:
    """Same tempfile + os.replace convention as cache.py::save() — matters
    less here than the _locked() wrapper above (which already serializes
    writers), but keeps a torn/partial write off disk even if something
    outside this module ever reads the file without taking the lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload))
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def record_and_check(source_name: str, ok: bool, *, date: str | None = None, **context) -> None:
    """Record this run's ok/not-ok result for `source_name` under today's
    UTC calendar day, then warn if a source with an established healthy
    baseline has now failed _CONSECUTIVE_FAILURES_TO_ALERT distinct days
    in a row. Never raises — a broken health-tracking file must not break
    the scrape/pipeline run it's trying to observe, the same "tools must
    not raise" convention this codebase applies everywhere else.

    `date` is normally left as None (defaults to _today()) — it exists so
    tests can simulate distinct calendar days without sleeping in real
    time. It's an explicit parameter rather than a patched module global
    specifically because several tests exercise concurrent callers, and a
    process-wide monkeypatch of a shared function is itself racy across
    threads (one thread's patched return value can leak into another's
    call) in a way an explicit per-call argument isn't."""
    try:
        with _locked(source_name):
            path = _path(source_name)
            prior_days: list[dict] = []
            if path.exists():
                try:
                    prior_days = json.loads(path.read_text()).get("days", [])[-_MAX_HISTORY:]
                except Exception:
                    prior_days = []

            # A source only has an established "should usually have data"
            # baseline once it has enough distinct-day history AND at
            # least one of those prior days actually succeeded — a source
            # that's simply always been empty (e.g. genuinely no
            # coverage) shouldn't alert either.
            was_healthy_baseline = (
                len(prior_days) >= _MIN_HISTORY_FOR_BASELINE
                and any(d.get("ok") for d in prior_days)
            )

            today = date or _today()
            if prior_days and prior_days[-1].get("date") == today:
                days = prior_days[:-1] + [{"date": today, "ok": ok}]
            else:
                days = prior_days + [{"date": today, "ok": ok}]
            days = days[-_MAX_HISTORY:]

            _atomic_write(path, {"days": days})

            recent = days[-_CONSECUTIVE_FAILURES_TO_ALERT:]
            should_alert = (
                was_healthy_baseline
                and len(recent) == _CONSECUTIVE_FAILURES_TO_ALERT
                and not any(d.get("ok") for d in recent)
            )

        # Logging deliberately happens outside the lock — log_event() is
        # independent I/O (stdout/Sentry) unrelated to this source's file,
        # and holding the file lock across it would only widen the window
        # other callers block on for no benefit.
        if should_alert:
            log_event(
                LOGGER, "source_health_anomaly", level="warning",
                source=source_name,
                consecutive_failures=_CONSECUTIVE_FAILURES_TO_ALERT,
                **context,
            )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "source_health_tracking_failed", level="warning", source=source_name, error=str(exc))
