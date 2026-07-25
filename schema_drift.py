"""Lightweight schema-drift detection for the six scraped data slices.

Tools in tools/*.py never raise (see CLAUDE.md's "Tools must not raise") —
a scraped source's HTML/JSON structure changing underneath us (Screener.in
restructuring a table, NSE renaming a field) doesn't crash the fetch; it
just silently returns something under the expected key that's no longer
the expected shape. schemas.CONTRACTS' "required" list only checks
presence, and this codebase's own "never invent" convention means most
optional fields are legitimately absent per-symbol anyway — so a naive
"did the key set change" check would be constant false-positive noise.

What actually indicates real drift, without that noise: a *present*
container field (dict/list) whose *type* flips — e.g. `ratios` coming back
as a list instead of a dict. That's never a legitimate "this symbol simply
doesn't have this field" case; every downstream `.get()`/iteration call on
it was written for the declared shape in schemas.CONTRACTS[task]["types"],
so a flip breaks something further down the pipeline, often silently.

This is a monitoring signal only — check_drift()/log_drift_if_any() never
raise and never alter the payload; a detected drift is logged via
observability.log_event() at "warning" (not "error", so it doesn't
page through the Phase 15 Sentry hook — this needs a human to look at the
scraper, not an on-call page) and the pipeline proceeds exactly as it
would have without this module.
"""
from typing import Any

from observability import get_logger, log_event
from schemas import CONTRACTS

LOGGER = get_logger("schema_drift")


def check_drift(task_name: str, raw_data: Any) -> list[str]:
    """Compare raw_data's present container fields against
    schemas.CONTRACTS[task_name]["types"]. Returns a list of human-readable
    drift descriptions (empty if none, or if there's nothing to check)."""
    if not isinstance(raw_data, dict) or raw_data.get("error"):
        return []

    expected_types = CONTRACTS.get(task_name, {}).get("types", {})
    problems = []
    for field, expected_type in expected_types.items():
        if field not in raw_data:
            continue  # legitimately absent for this symbol — not drift
        value = raw_data[field]
        if value is not None and not isinstance(value, expected_type):
            problems.append(
                f"'{field}' expected {expected_type.__name__}, got {type(value).__name__}"
            )
    return problems


def log_drift_if_any(task_name: str, raw_data: Any, **context: Any) -> None:
    """Best-effort wrapper around check_drift() — never raises, so a bug
    here can never break the fetch that called it. **context is forwarded
    as extra log_event fields (e.g. run_id, symbol)."""
    try:
        problems = check_drift(task_name, raw_data)
        if problems:
            log_event(
                LOGGER,
                "schema_drift_detected",
                level="warning",
                task=task_name,
                problems=problems,
                **context,
            )
    except Exception:  # pylint: disable=broad-exception-caught
        pass
