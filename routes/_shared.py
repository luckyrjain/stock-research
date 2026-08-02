"""Shared plumbing for routes/watchlist.py and routes/positions.py — the two
domains that share the exact same anonymous-client_id-or-account-user_id
ownership shape (see routes/watchlist.py's own docstring for the full
reasoning) and, until this module existed, each independently repeated the
same rate-limit → DB-configured-check → run_in_executor → sanitize-error
wrapper around every read/write.
"""
from fastapi import HTTPException

import api


async def run_owned_db_call(
    request, rate_limit_name: str, max_calls: int, sync_fn, event_prefix: str, window_seconds: float = 60,
):
    """Runs `sync_fn` (a zero-arg callable doing the actual DB work) off the
    event loop, with the exact shape every watchlist/positions endpoint
    needs: rate limit this call, 503 immediately if no DATABASE_URL is
    configured, then translate a raised ValueError (validation failure,
    e.g. "cap exceeded") to a 422, a raised PermissionError (no/expired
    session on a session-required endpoint, e.g. the claim endpoints below)
    to a 401, and anything else to a sanitized 503 — never leaking raw
    exception text to the caller, logging the real one server-side
    instead. `window_seconds` defaults to the same 60s every ordinary
    read/write here already used; the claim endpoints pass a much longer
    window with a much lower cap (same per-address-not-just-per-IP
    precedent as the magic-link request-link endpoint's 5/hour) — a
    sensitive, low-frequency, exclusive-reassignment operation shouldn't
    share the same generous per-minute budget as an ordinary star/unstar."""
    api._rate_limit(request, rate_limit_name, max_calls=max_calls, window_seconds=window_seconds)
    if not api.os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")

    loop = api.asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, sync_fn)
    except HTTPException:
        # sync_fn raised a status code it already knows is correct (e.g. a
        # 404 for a missing id) — re-raise as-is rather than falling through
        # to the generic-503 branch below, which would otherwise silently
        # swallow a deliberate, specific status/detail into an opaque
        # "Database error" response. Added alongside routes/
        # portfolio_aggregator.py, whose sync_fn closures are this wrapper's
        # first real callers that raise HTTPException directly (404/409/422
        # for missing ids, duplicate names, invalid asset-type combinations)
        # — every existing caller (watchlist/positions) still routes its own
        # "not found" cases around this wrapper entirely, so this remains a
        # pure addition for them, not a behavior change.
        raise
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

    `lock_prefix` MUST be the exact same string the table's own add endpoint
    uses for its own advisory lock (e.g. `"watchlist"`, matching
    `routes/watchlist.py::add_to_watchlist`'s `f"watchlist:{owner[0]}:{owner[1]}"`)
    — an earlier version of this function used a distinct `"<table>_claim"`
    prefix, which looked like an intentional "own lock-key namespace" choice
    but actually meant a concurrent claim and add for the same account took
    two different advisory locks and never serialized against each other at
    all, letting both read the same pre-claim/pre-add row count and both
    commit, silently exceeding `max_per_owner`.

    A second advisory lock, scoped to the *source* `client_id` rather than
    the target account, is also taken (see below) — without it, two
    different accounts racing to claim the identical `client_id` (a leaked
    id claimed by two people, or the same browser signing into two accounts
    in quick succession) take two different user-scoped locks and never
    serialize against each other at all. The final UPDATE below matches by
    row `id` (computed from an earlier, unlocked snapshot of which rows
    currently have this client_id), not by a live re-check of `client_id` —
    so a second transaction that starts after the first has already
    reassigned those same row ids still matches them by id and blindly
    re-assigns them again, silently overwriting the first claim. Both
    transactions report `claimed=1`; the true final owner is whichever
    committed last — a false-positive success for the loser, verified
    against a real concurrent-transaction repro. Locking the client_id too
    forces the second transaction's own `ranked` CTE to re-read the table
    only after the first has committed, at which point it correctly finds
    zero remaining rows for that client_id.

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
        # Advisory lock scoped to this account — same key format and same
        # "user" owner-type segment the add endpoint's own lock uses (see
        # this function's docstring above), so a claim and a concurrent add
        # for the same account actually serialize against each other rather
        # than silently racing past max_per_owner.
        conn.execute(_text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": f"{lock_prefix}:user:{user_id}"})
        # Second advisory lock scoped to the *source* client_id (see this
        # function's docstring above) — serializes two different accounts
        # racing to claim the same anonymous identity. Always acquired in
        # this fixed order (user lock, then client lock) by every caller of
        # this function, so this can never deadlock against another call
        # acquiring the same two lock types in the opposite order.
        conn.execute(_text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": f"{lock_prefix}:client:{client_id}"})

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
