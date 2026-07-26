"""Shared plumbing for routes/watchlist.py and routes/positions.py — the two
domains that share the exact same anonymous-client_id-or-account-user_id
ownership shape (see routes/watchlist.py's own docstring for the full
reasoning) and, until this module existed, each independently repeated the
same rate-limit → DB-configured-check → run_in_executor → sanitize-error
wrapper around every read/write.
"""
from fastapi import HTTPException

import api


async def run_owned_db_call(request, rate_limit_name: str, max_calls: int, sync_fn, event_prefix: str):
    """Runs `sync_fn` (a zero-arg callable doing the actual DB work) off the
    event loop, with the exact shape every watchlist/positions endpoint
    needs: rate limit this call, 503 immediately if no DATABASE_URL is
    configured, then translate a raised ValueError (validation failure,
    e.g. "cap exceeded") to a 422 and anything else to a sanitized 503 —
    never leaking raw exception text to the caller, logging the real one
    server-side instead."""
    api._rate_limit(request, rate_limit_name, max_calls=max_calls, window_seconds=60)
    if not api.os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")

    loop = api.asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, sync_fn)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        api.log_event(api.LOGGER, f"{event_prefix}_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")
