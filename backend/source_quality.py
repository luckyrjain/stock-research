"""Per-source signal-quality telemetry for the market-picks pipeline.

Writes one JSON file per pipeline run to output/_source_quality/. Read by
source_quality_report.py to aggregate signal quality across runs. Complements
source_health.py (day-level freshness/volume) and scraper_error_counters.py
(error counting for standalone per-symbol endpoints) — neither gives a
per-run view of "how many articles did source X yield this run, how many
survived to a validated pick."
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from observability import get_logger, log_event

_DIR = Path("output/_source_quality")
_logger = get_logger("source_quality")


def _atomic_write(path: Path, payload: dict) -> None:
    """tempfile + os.replace, same convention as cache.py/source_health.py.
    No lock needed — each run writes its own uniquely-named file, so there's
    no concurrent-writer race on a single path to guard against."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload))
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def record_run(run_id: str, source_stats: dict[str, dict]) -> None:
    """Writes output/_source_quality/<run_id>.json. Never raises — a telemetry
    write failure must never affect a real pipeline run."""
    try:
        payload = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sources": source_stats,
        }
        _atomic_write(_DIR / f"{run_id}.json", payload)
    except Exception as exc:
        log_event(_logger, "source_quality_write_failed", level="warning", run_id=run_id, error=str(exc))
