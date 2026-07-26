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
    e.g. "cap exceeded") to a 422, a raised PermissionError (no/expired
    session on a session-required endpoint, e.g. the claim endpoints below)
    to a 401, and anything else to a sanitized 503 — never leaking raw
    exception text to the caller, logging the real one server-side
    instead."""
    api._rate_limit(request, rate_limit_name, max_calls=max_calls, window_seconds=60)
    if not api.os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")

    loop = api.asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, sync_fn)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        api.log_event(api.LOGGER, f"{event_prefix}_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")


def claim_anonymous_rows_sync(
    engine, table: str, order_column: str, client_id: str, user_id: int, max_per_owner: int, lock_prefix: str,
) -> tuple[int, int]:
    """Opt-in migration of an anonymous browser's rows onto the account that
    just explicitly asked for them — the escape hatch for this codebase's
    deliberate "no migration on sign-in" default (see routes/watchlist.py's
    own docstring), not a reversal of it: this only ever runs when a
    signed-in user clicks a "claim my data" prompt, never automatically.

    `table`/`order_column`/`lock_prefix` are fixed call-site strings, never
    user input — same "closed set, not raw user text" safety as the
    Screener's `sort` column interpolation elsewhere in this codebase, so
    f-string interpolation here is safe.

    A symbol the account already owns keeps the account's existing row; the
    anonymous duplicate is discarded (not left around as clutter — it can
    never be claimed anyway, since uq_{table}_user_symbol forbids two rows
    for the same (user_id, symbol)). Claims are applied oldest-first and
    capped at `max_per_owner` (the same cap this table's own POST endpoint
    already enforces) — rows beyond the account's remaining room are left
    owned by client_id rather than silently exceeding the cap, and reported
    back as `skipped` rather than dropped, so the caller can surface it
    ("N items couldn't be claimed — your watchlist is full") instead of the
    claim silently doing less than it seemed to.

    Returns (claimed, skipped) — never raises; the caller's own executor
    wrapper (run_owned_db_call) is what actually catches and reports errors.
    """
    from sqlalchemy import text as _text

    with engine.begin() as conn:
        # Advisory lock scoped to this account, distinct namespace per table
        # via lock_prefix, so a claim can't race a concurrent add/claim for
        # the same account.
        conn.execute(_text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": f"{lock_prefix}:{user_id}"})

        conn.execute(_text(f"""
            DELETE FROM {table} t1
            WHERE t1.client_id = :client_id
              AND EXISTS (SELECT 1 FROM {table} t2 WHERE t2.user_id = :user_id AND t2.symbol = t1.symbol)
        """), {"client_id": client_id, "user_id": user_id})

        existing_count = conn.execute(_text(
            f"SELECT COUNT(*) FROM {table} WHERE user_id = :user_id"
        ), {"user_id": user_id}).scalar() or 0
        room = max(0, max_per_owner - existing_count)

        result = conn.execute(_text(f"""
            WITH ranked AS (
                SELECT id, ROW_NUMBER() OVER (ORDER BY {order_column} ASC) AS rn
                FROM {table} WHERE client_id = :client_id
            )
            UPDATE {table}
            SET client_id = NULL, user_id = :user_id
            WHERE id IN (SELECT id FROM ranked WHERE rn <= :room)
        """), {"client_id": client_id, "user_id": user_id, "room": room})
        claimed = result.rowcount or 0

        skipped = conn.execute(_text(
            f"SELECT COUNT(*) FROM {table} WHERE client_id = :client_id"
        ), {"client_id": client_id}).scalar() or 0

    return claimed, skipped
