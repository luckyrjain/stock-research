import json
import logging
import os
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
