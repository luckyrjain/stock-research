"""Shared plumbing for api.py, routes/watchlist.py, routes/positions.py,
routes/portfolio_aggregator.py, and routes/broker_sync.py — the primitives
these modules need (a cached DB engine, per-IP rate limiting, bearer-token
parsing, the ticker regex, and structured logging), plus the read/write
wrapper the domain route modules build on and the OwnedRequest Pydantic base
their write-endpoint bodies inherit.

These used to be defined in api.py itself and reached into by
routes/watchlist.py and routes/positions.py via `import api` + dotted
attribute access (`api._get_db_engine()`) — a real dependency, but one that
only worked because api.py happened to define all of them before it called
`app.include_router(...)` for those two routers near the bottom of that file,
not because of any actual import direction. A future reorder of api.py could
have silently broken it. They now live here instead, and api.py imports them
FROM this module the same way the two route modules do — this file has no
dependency on api.py at all, so there's no import-order landmine left for
api.py to trip over.
"""
import asyncio
import hmac
import os
import re

from fastapi import HTTPException, Request, UploadFile
from pydantic import BaseModel

from core import rate_limiter
from core.observability import get_logger, log_event

LOGGER = get_logger("api")

_TICKER_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def validate_ticker(symbol: str) -> str:
    """Shared replacement for api.py's own repeated inline
    `sym = symbol.upper().strip(); if not _TICKER_RE.match(sym): raise ...`
    block. Plain function, not a FastAPI dependency — call it explicitly from
    inside each endpoint body, in the same position the inline block used to
    hold relative to that endpoint's own auth/rate-limit calls. (An earlier
    version of this wired it in as `symbol: str = Depends(valid_ticker)`, but
    FastAPI resolves dependencies before the endpoint body runs, which
    silently moved validation ahead of auth/rate-limit checks that need to
    run first.)"""
    sym = symbol.upper().strip()
    if not _TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    return sym


class OwnedRequest(BaseModel):
    """Base for every write-endpoint body across watchlist/positions/
    portfolio_aggregator that carries the anonymous browser identity
    (lib/watchlist.ts's getClientId()) a request resolves against when
    there's no signed-in session (see routes.watchlist.resolve_owner()). A
    shared base rather than each model re-declaring this field means a new
    write-endpoint model inherits it structurally instead of relying on
    every author remembering to add it by hand."""
    client_id: str | None = None


def _bearer_token_from_request(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        return token or None
    return None


def _fetch_live_price_sync(sym: str) -> dict:
    """LTP + day change% for one NSE/BSE symbol via yfinance, trying the .NS
    then .BO suffix. Returns {} (never raises) if neither resolves — shared by
    GET /api/prices (bulk), GET /api/verdict-history/{symbol} (single-symbol,
    for scoring past verdicts against today's price), and
    GET /api/portfolio/concentration (routes/positions.py)."""
    import yfinance as yf
    for suffix in (".NS", ".BO"):
        # Each suffix attempt is independently guarded — a genuine BSE-only
        # symbol (never listed on NSE, or delisted from it) can make the
        # .NS attempt raise outright rather than just return empty data; a
        # shared try/except around the whole loop would abort before .BO is
        # even tried, silently losing a real, resolvable price.
        try:
            fi = yf.Ticker(sym + suffix).fast_info
            price = getattr(fi, "last_price", None)
            prev  = getattr(fi, "previous_close", None)
            if price and price > 0:
                # None (never a fabricated 0.0 "flat today") when prev isn't
                # available — same "never invent" convention as everywhere
                # else in this codebase; a real flat day and a missing
                # previous-close aren't the same fact.
                chg = round((price - prev) / prev * 100, 2) if prev else None
                return {"price": round(price, 2), "change_pct": chg}
        except Exception:
            continue
    return {}


# ── Cached DB engine ──────────────────────────────────────────────────────────
def _get_db_engine():
    from db.models import get_shared_engine
    return get_shared_engine()


# ── Rate limiting ─────────────────────────────────────────────────────────────
# Sliding-window limiter, keyed by (bucket, client IP). Backed by
# core/rate_limiter.py — Redis-shared across workers when REDIS_URL is set, an
# in-memory per-process counter otherwise.

# Every browser request reaches this backend via the Next.js proxy routes,
# server-to-server (see "Proxy routes" in CLAUDE.md) — so request.client.host
# is always the Next.js server's own IP, never the real visitor's. Left
# unfixed, every one of the per-IP limiters collapses into one shared bucket
# for the whole site, the opposite of what they're for: one abusive visitor
# throttles everyone, and there's no per-visitor signal at all.
# TRUSTED_PROXY_SECRET (also set on the frontend — see
# frontend/lib/proxy-headers.ts) lets a request prove it really came through
# the Next.js proxy layer via X-Internal-Proxy-Secret, in which case the
# X-Forwarded-For value it forwarded is trusted as the real client IP.
# Without a configured secret (the default), or without a match, the header
# is ignored — an untrusted caller could otherwise spoof X-Forwarded-For to
# dodge its own rate limit or frame someone else's IP into being blocked.
_TRUSTED_PROXY_SECRET = os.getenv("TRUSTED_PROXY_SECRET")


def _client_ip(request: Request) -> str:
    secret = request.headers.get("x-internal-proxy-secret", "")
    if _TRUSTED_PROXY_SECRET and hmac.compare_digest(secret, _TRUSTED_PROXY_SECRET):
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in forwarded.split(",")] if forwarded else []
        # A correctly configured single-hop reverse proxy in "replace" mode
        # (see docs/deployment.md) always produces exactly one IP here. More
        # than one usually means an "append" mode misconfiguration (e.g.
        # nginx's $proxy_add_x_forwarded_for) letting a client-supplied
        # X-Forwarded-For survive alongside the real one — and since a
        # browser/curl can set this header directly on a request to the
        # reverse proxy, the leftmost entry in that case would be the
        # attacker's own claimed value, not the one the proxy actually
        # observed. Refuse to trust an ambiguous chain rather than guess
        # which entry is real; this also naturally handles an empty/blank
        # header the same way.
        if len(parts) == 1 and parts[0]:
            return parts[0]
    return request.client.host if request.client else "unknown"


def _check_rate_limit(key: str, max_calls: int, window_seconds: float) -> None:
    if not rate_limiter.is_allowed(key, max_calls, window_seconds):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {max_calls} requests per {int(window_seconds)}s on this endpoint. Try again later.",
        )


def _rate_limit(request: Request, bucket: str, max_calls: int, window_seconds: float) -> None:
    client_ip = _client_ip(request)
    _check_rate_limit(f"{bucket}:{client_ip}", max_calls, window_seconds)


# Applies to every multipart file upload in this app (CAS PDF, broker
# CSV/XLSX import) — these endpoints previously called `await file.read()`
# unconditionally, reading an arbitrarily large request body fully into
# memory (and, for `preview`, doing so on every keystroke of a client
# retrying) before any parsing could reject it. 20 MB comfortably covers a
# real CAS statement or broker tradebook export (typically well under 1 MB)
# while bounding the worst case to a small, fixed amount of memory per
# request.
#
# Disclosed limitation: FastAPI's automatic `UploadFile = File(...)` param
# injection parses the multipart body via Starlette's own `request.form()`
# with its hardcoded default `max_part_size` (1 MB as of the installed
# Starlette version) BEFORE this function — or any endpoint code — ever
# runs, and FastAPI exposes no way to override that default through the
# declarative `File(...)` marker. So for a file between 1 MB and this 20 MB
# cap, Starlette's own parser rejects it first with a generic 400, and this
# function's friendlier 413 never actually fires. Not a security gap (the
# effective ceiling today is *tighter* than 20 MB, not looser) but the 413
# message here is aspirational for that size range until the 3 upload
# endpoints are rewritten to call `request.form(max_part_size=...)`
# manually instead of relying on FastAPI's automatic injection — a larger,
# separate change not attempted here. See docs/backlog.md.
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


async def read_upload_capped(file: UploadFile, max_bytes: int = _MAX_UPLOAD_BYTES) -> bytes:
    """Reads an UploadFile's body, rejecting (413) anything over `max_bytes`
    rather than buffering an unbounded amount of it first. Reads one byte
    past the cap so a file exactly at the limit isn't misreported as over
    it, without ever holding more than `max_bytes + 1` bytes in memory.

    See this module's own disclosed limitation above: for files between
    Starlette's own default per-part limit (1 MB) and `max_bytes`,
    Starlette's multipart parser rejects the upload before this function
    ever runs, with a less specific 400 rather than this function's 413.

    Prefer `rate_limited_upload()` below over calling this directly for a
    new upload endpoint — it couples the rate-limit check to the read so
    the two can't be separated by a future edit."""
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {max_bytes // (1024 * 1024)} MB).",
        )
    return data


async def rate_limited_upload(
    request, rate_limit_name: str, max_calls: int, file: UploadFile,
    max_bytes: int = _MAX_UPLOAD_BYTES, window_seconds: float = 60,
) -> bytes:
    """Rate-limits `request`, then reads `file`'s capped body — always in
    that order, as one call, for every upload endpoint. An earlier version
    of the 3 upload endpoints below called `api._rate_limit()` and
    `read_upload_capped()` as two separate statements, ahead of a
    `run_owned_db_call(..., skip_rate_limit=True)`. An adversarial-review
    pass on that version confirmed no live bug in the 3 endpoints it
    touched, but flagged the shape itself as a footgun for a *future*
    upload endpoint: the unsafe combination (an explicit `_rate_limit()`
    call copy-pasted in, `skip_rate_limit` left at its default `False`)
    needs no deliberate action to reach, while the safe one requires
    remembering an extra kwarg at a call site physically far from the
    `_rate_limit()` line it depends on — silently halving that new
    endpoint's rate limit via a double-counted bucket, the exact bug this
    whole mechanism exists to prevent. This function exists so a caller
    literally cannot do one step without the other. Callers still pass
    `skip_rate_limit=True` to `run_owned_db_call()` afterward — this
    function only replaces the read_upload_capped()-plus-a-separate-
    _rate_limit()-call pair, not `run_owned_db_call()` itself."""
    _rate_limit(request, rate_limit_name, max_calls=max_calls, window_seconds=window_seconds)
    return await read_upload_capped(file, max_bytes)


async def run_owned_db_call(
    request, rate_limit_name: str, max_calls: int, sync_fn, event_prefix: str, window_seconds: float = 60,
    skip_rate_limit: bool = False,
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
    share the same generous per-minute budget as an ordinary star/unstar.

    `skip_rate_limit=True` is for a caller that already called
    `_rate_limit(request, rate_limit_name, ...)` itself earlier in the
    request — e.g. the CAS/CSV upload endpoints, which need the rate-limit
    check to run BEFORE `read_upload_capped()` reads the file, not after
    (see `rate_limited_upload()` above). Calling `_rate_limit()` a second
    time here with the *same* bucket name would silently consume two slots
    from one sliding window per request, halving the effective limit —
    this flag exists so that never happens."""
    if not skip_rate_limit:
        _rate_limit(request, rate_limit_name, max_calls=max_calls, window_seconds=window_seconds)
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")

    loop = asyncio.get_running_loop()
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
        log_event(LOGGER, f"{event_prefix}_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")


async def run_db_call(
    request, rate_limit_name: str, max_calls: int, sync_fn, event_prefix: str, window_seconds: float = 60,
    db_missing_detail: str = "DATABASE_URL not configured.", extra_log_fields: dict | None = None,
):
    """For an ownership-scoped endpoint, use run_owned_db_call() instead — this
    is its counterpart for a PUBLIC data endpoint with no owner/client_id
    concept at all (SME Signals, Screener): rate limit this call, 503
    immediately if no DATABASE_URL is configured (`db_missing_detail`
    customizes that message — e.g. pointing at which pipeline to run first —
    since the two current callers each have their own wording), then run
    `sync_fn` (a zero-arg callable doing the actual DB work) off the event
    loop, translating any exception it raises into a sanitized 503 rather
    than leaking raw exception text (which can carry a DSN/credentials) to
    the caller — the real one is logged server-side as
    `{event_prefix}_failed` instead. `extra_log_fields`, when given, is
    merged into that log call (e.g. the symbol a per-symbol endpoint was
    querying) without changing what the caller ever sees.

    Unlike run_owned_db_call(), there's no ValueError/PermissionError/
    HTTPException special-casing here — no current public endpoint's
    sync_fn raises any of those, so this stays the plain shape the three
    read endpoints actually had before this was extracted."""
    _rate_limit(request, rate_limit_name, max_calls=max_calls, window_seconds=window_seconds)
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail=db_missing_detail)

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, sync_fn)
    except Exception as exc:
        log_event(LOGGER, f"{event_prefix}_failed", level="error", error=str(exc), **(extra_log_fields or {}))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")


async def run_locked_refresh(
    request, lock_name: str, lock_ttl_seconds: int,
    rate_limit_name: str, rate_limit_max_calls: int, rate_limit_window_seconds: float,
    pipeline_run_fn, event_prefix: str, unhealthy_detail: str,
    db_missing_detail: str = "DATABASE_URL not configured.",
) -> dict:
    """For an ownership-scoped endpoint, use run_owned_db_call() instead — this
    covers the other public-endpoint plumbing shape, the single-flight
    "kick off a background pipeline run" one shared by the SME and screener
    refresh endpoints: 503 if no DATABASE_URL, then atomically claim
    `lock_name` (409 if a refresh is already running), then rate-limit
    (429) — checked in that exact order, deliberately, matching what both
    refresh endpoints already did before this was extracted: an in-progress
    refresh (409) is a fact about server state that costs nothing to check
    and answers a different question than this caller's own rate limit
    (429), so it's checked first. A rate-limit rejection releases the lock
    it just claimed (in the `except` below) so a request that never actually
    starts a refresh doesn't leave the lock stuck against everyone else.

    `pipeline_run_fn` is a zero-arg callable that imports and runs the
    pipeline, returning its health bool (e.g. a closure around
    `pipelines.sme_ema_pipeline.run`) — kept as a plain callable rather than
    a module path so the import stays lazy exactly as it was inline, without
    this function needing to know how to import anything. `unhealthy_detail`
    is the pipeline-specific explanation logged (as a warning) when
    `pipeline_run_fn()` returns a falsy health flag.

    Dispatches the actual run onto a background asyncio task (never awaited
    by this call) so the endpoint can return 202 immediately; the lock is
    released in a `finally` inside that task regardless of outcome."""
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail=db_missing_detail)
    if not rate_limiter.try_acquire_lock(lock_name, lock_ttl_seconds):
        raise HTTPException(status_code=409, detail="A refresh is already running.")
    try:
        _rate_limit(request, rate_limit_name, max_calls=rate_limit_max_calls, window_seconds=rate_limit_window_seconds)
    except HTTPException:
        rate_limiter.release_lock(lock_name)
        raise

    loop = asyncio.get_running_loop()

    def _run_pipeline():
        try:
            healthy = pipeline_run_fn()
            if not healthy:
                log_event(LOGGER, f"{event_prefix}_unhealthy", level="warning", detail=unhealthy_detail)
        except Exception as exc:
            log_event(LOGGER, f"{event_prefix}_failed", level="error", error=str(exc))
        finally:
            rate_limiter.release_lock(lock_name)

    async def _launch():
        await loop.run_in_executor(None, _run_pipeline)

    asyncio.create_task(_launch())
    log_event(LOGGER, f"{event_prefix}_started")
    return {"started": True}


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
