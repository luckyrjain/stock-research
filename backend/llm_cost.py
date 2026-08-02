"""Per-call LLM cost instrumentation and a lightweight running daily total.

Every analysis and every weekly Market Picks batch calls a metered LLM API,
but this codebase had no answer to "what does one analysis actually cost" —
an investor/CTO-lens review flagged this directly: user growth scales a
real cost line against a product that currently monetizes nobody, with no
per-analysis cost instrumentation and no margin model anywhere.

`record_call_cost()` is called once per `litellm.completion()` call from
`crew.py` (every attempt, not just the one that ultimately validates —
a guardrail-retry or a failed failover attempt still cost real tokens).
It logs the call's own cost immediately via `observability.log_event()`
(queryable right away through whatever this deployment already does with
structured logs) and accumulates a running per-day total to a small JSON
file, `output/_llm_cost/<date>.json` — the same "grep-able counter file
plus a log line" convention as `scraper_error_counters.py`/
`source_health.py`, deliberately not a full observability/billing platform.
"""
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from observability import get_logger, log_event

LOGGER = get_logger("llm_cost")

_COST_DIR = Path("output/_llm_cost")


def _today() -> str:
    """Isolated as its own function purely so tests can patch it to
    simulate a specific calendar day without sleeping in real time — same
    convention as source_health.py's own _today()."""
    return datetime.now(timezone.utc).date().isoformat()


def _lock_path(date: str) -> Path:
    return _COST_DIR / f".{date}.lock"


@contextmanager
def _locked(date: str):
    """Advisory, blocking, cross-process exclusive lock guarding one day's
    read-modify-write cycle — same `fcntl.flock` pattern as
    source_health.py's own `_locked()`. Without this, two backend *worker
    processes* (not just threads within one process — a plain
    `threading.Lock` doesn't reach across processes at all) racing to
    record a call on the same UTC day can both read the same prior
    `call_count`, both increment locally, and the second write silently
    overwrites the first — undercounting cost/calls with no warning logged,
    exactly the multi-worker deployment this app's own docs already say
    `REDIS_URL` makes supported. fcntl is POSIX-only, matching this
    codebase's existing Linux-only deployment assumption; imported lazily
    so a non-POSIX environment can still import this module's pure helpers."""
    import fcntl

    _COST_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(_lock_path(date), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def estimate_cost_usd(response, model: str) -> float | None:
    """Estimated USD cost of one completion call, via litellm's own
    pricing table. Never raises and never guesses: litellm doesn't have
    pricing data for every model (a self-hosted Ollama model, a brand-new
    release litellm's pricing table hasn't caught up to yet), and a
    missing price must degrade to None, never a fabricated number that
    looks like a real cost."""
    try:
        import litellm
        return litellm.completion_cost(completion_response=response, model=model)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "llm_cost_estimation_failed", level="warning", model=model, error=str(exc))
        return None


def record_call_cost(
    symbol: str,
    model: str,
    provider: str,
    cost_usd: float | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    run_id: str | None = None,
) -> None:
    """Logs this one call's cost/tokens immediately, then accumulates a
    running per-UTC-day total (call_count, total_cost_usd,
    calls_with_unknown_cost) to a small JSON file — a real, grep-able
    answer to "what's today's total LLM spend so far" without a second
    billing system. Never raises: a broken cost-tracking file must not
    break the analysis request it's observing, the same "tools must not
    raise" instinct this codebase applies to every other piece of
    non-critical observability infrastructure."""
    date = _today()
    try:
        log_event(
            LOGGER, "llm_call_cost", symbol=symbol, model=model, provider=provider,
            cost_usd=cost_usd, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            run_id=run_id,
        )
        _COST_DIR.mkdir(parents=True, exist_ok=True)
        path = _COST_DIR / f"{date}.json"
        with _locked(date):
            data = {"call_count": 0, "total_cost_usd": 0.0, "calls_with_unknown_cost": 0}
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:  # pylint: disable=broad-exception-caught
                    data = {"call_count": 0, "total_cost_usd": 0.0, "calls_with_unknown_cost": 0}
            data["call_count"] = data.get("call_count", 0) + 1
            if cost_usd is not None:
                data["total_cost_usd"] = round(data.get("total_cost_usd", 0.0) + cost_usd, 6)
            else:
                data["calls_with_unknown_cost"] = data.get("calls_with_unknown_cost", 0) + 1

            fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(json.dumps(data))
                os.replace(tmp_path, path)
            except Exception:
                Path(tmp_path).unlink(missing_ok=True)
                raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "llm_cost_tracking_failed", level="warning", error=str(exc))


def get_daily_totals(date: str | None = None) -> dict:
    """Non-mutating read for tests and a future ops surface. `date`
    defaults to today (UTC); never raises, degrading to an all-zero
    result for a day with no recorded calls or a corrupt file."""
    empty = {"call_count": 0, "total_cost_usd": 0.0, "calls_with_unknown_cost": 0}
    try:
        path = _COST_DIR / f"{date or _today()}.json"
        if not path.exists():
            return empty
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # pylint: disable=broad-exception-caught
        return empty
