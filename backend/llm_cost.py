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
structured logs) and accumulates a running per-day total under the
`llm_cost` namespace in `state_store.py` (one record per UTC day) — the same
"one counter plus a log line" convention as `scraper_error_counters.py`/
`source_health.py`, deliberately not a full observability/billing platform.

That counter used to be a JSON file, `output/_llm_cost/<date>.json`, guarded
by an `fcntl.flock` advisory lock so two worker *processes* couldn't both
read the same prior `call_count` and have the second silently overwrite the
first. `state_store.mutate()` does that with a row lock instead, which also
holds across separate hosts — see its docstring.
"""
from datetime import datetime, timezone

import state_store
from observability import get_logger, log_event

LOGGER = get_logger("llm_cost")

_NAMESPACE = "llm_cost"
_EMPTY = {"call_count": 0, "total_cost_usd": 0.0, "calls_with_unknown_cost": 0}


def _today() -> str:
    """Isolated as its own function purely so tests can patch it to
    simulate a specific calendar day without sleeping in real time — same
    convention as source_health.py's own _today()."""
    return datetime.now(timezone.utc).date().isoformat()


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
    calls_with_unknown_cost) — a real answer to "what's today's total LLM
    spend so far" without a second billing system. Never raises: broken cost
    tracking must not break the analysis request it's observing, the same
    "tools must not raise" instinct this codebase applies to every other
    piece of non-critical observability infrastructure."""
    try:
        log_event(
            LOGGER, "llm_call_cost", symbol=symbol, model=model, provider=provider,
            cost_usd=cost_usd, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            run_id=run_id,
        )

        def _accumulate(data: dict) -> dict:
            data["call_count"] = data.get("call_count", 0) + 1
            if cost_usd is not None:
                data["total_cost_usd"] = round(data.get("total_cost_usd", 0.0) + cost_usd, 6)
            else:
                data["calls_with_unknown_cost"] = data.get("calls_with_unknown_cost", 0) + 1
            return data

        state_store.mutate(_NAMESPACE, _today(), _accumulate, dict(_EMPTY))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "llm_cost_tracking_failed", level="warning", error=str(exc))


def get_daily_totals(date: str | None = None) -> dict:
    """Non-mutating read for tests and a future ops surface. `date`
    defaults to today (UTC); never raises, degrading to an all-zero
    result for a day with no recorded calls."""
    return state_store.load(_NAMESPACE, date or _today()) or dict(_EMPTY)
