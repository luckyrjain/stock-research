"""Shared helpers for the "single flat JSON-list cache file" pattern used by
tools/sme_tools.py, tools/nifty500_tools.py, and tools/securities_master.py
for their own NSE Emerge/BSE SME, NIFTY 500, and merged-securities-master
list caches respectively — each independently defined the same mtime-based
freshness check, atomic-write save, and fail-soft load trio for one flat
JSON file under output/.

NOT a substitute for core/cache.py, which is a different mechanism (per-
symbol-task cache keyed by task name, with an embedded _meta.fetched_at and
optional Redis write-through).
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.atomic_file import atomic_write_text


def is_fresh(path: Path, ttl_hours: float) -> bool:
    """True if `path` exists and was last written within `ttl_hours`."""
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=ttl_hours)


def save_json(path: Path, data: Any) -> None:
    # Written atomically (tempfile + os.replace), same convention as
    # core/cache.py::save() -- a plain write_text() left a truncated/corrupt file
    # behind on an interrupted write (process killed/OOM/container restart
    # mid-write, or two cron-triggered pipeline runs racing on the same
    # file), which every read site then failed to parse.
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data))


def load_json(
    path: Path,
    logger: logging.Logger | None = None,
    log_label: str = "cache",
) -> Any | None:
    """Read a flat JSON cache file. Returns None (never raises) on any
    parse/read failure (missing file, corrupt JSON, permission error) --
    callers treat None the same as "no usable cache".

    Pass `logger` to log a warning naming the cache on failure (message:
    "<log_label> at <path> is unreadable, treating as absent: <exc>");
    omit it for a silent None.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        if logger is not None:
            logger.warning("%s at %s is unreadable, treating as absent: %s", log_label, path, exc)
        return None
