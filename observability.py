import json
import logging
import os
from datetime import datetime, timezone
from typing import Any


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
    **fields: Any,
) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    getattr(logger, level.lower(), logger.info)(json.dumps(payload, ensure_ascii=False, default=str))
