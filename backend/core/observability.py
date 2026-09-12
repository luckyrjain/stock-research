import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from core import error_tracking


def _configure_root_logger() -> None:
    root = logging.getLogger("stock_research")
    if root.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)
    root.propagate = False


def get_logger(name: str = "app") -> logging.Logger:
    _configure_root_logger()
    return logging.getLogger(f"stock_research.{name}")


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: str = "info",
    exc: BaseException | None = None,
    **fields: Any,
) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    getattr(logger, level.lower(), logger.info)(json.dumps(payload, ensure_ascii=False, default=str))

    if level.lower() == "error":
        # Forward to the optional Sentry-style hook (see core/error_tracking.py) —
        # a no-op unless SENTRY_DSN is configured. Wrapped defensively so a
        # broken/unreachable error-tracking backend can never break the
        # primary structured-logging path this function exists for.
        try:
            error_tracking.capture_error(event, fields, exc=exc)
        except Exception:  # pylint: disable=broad-exception-caught
            pass


# Shared registry backing warn_once() below: {event: {keys already warned
# about}}. Keyed by event name (rather than one lock+set per call site) so
# two independent "warn once per key" guardrails elsewhere in this codebase
# can share this one mechanism while staying separately telemetered — each
# gets its own key-set here, so their counts/keys can never collide, even
# though the lock is shared. A single process-wide lock (rather than a lock
# per event) keeps this simple: the critical section is just a dict lookup
# plus a membership check, cheap enough that cross-event contention is a
# non-issue in practice. Left as a plain module-level dict (not hidden
# behind a class) so a test can seed `_warn_once_seen[event]` with a custom
# set-like object to force a deterministic race on the check-then-add
# sequence below.
_warn_once_lock = threading.Lock()
_warn_once_seen: dict[str, set[str]] = {}


def warn_once(logger: logging.Logger, event: str, key: str, **fields: Any) -> None:
    """Log a "warning"-level event at most once per process for a given
    (event, key) pair, thread-safely (acquire lock -> check membership ->
    add -> release lock -> log outside the lock, so log_event() never runs
    while holding the lock)."""
    with _warn_once_lock:
        seen = _warn_once_seen.setdefault(event, set())
        if key in seen:
            return
        seen.add(key)
    log_event(logger, event, level="warning", **fields)
