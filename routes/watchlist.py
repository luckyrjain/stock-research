"""Cross-mode watchlist endpoints — extracted out of api.py (see CLAUDE.md's
"Watchlist flow" for the full design). Every row is owned by exactly one
identity: either the anonymous per-browser client_id (a UUID the frontend
generates and keeps in localStorage — see frontend/lib/watchlist.ts) or,
once signed in, the account's user_id (see auth.py). A valid session always
wins over client_id when both are present in a request — that's the whole
point of an account: it doesn't depend on which browser sent the request.
Signing in does NOT claim/merge an existing client_id's rows onto the
account (see db/models.py's watchlist_items comment) — a freshly-signed-in
user simply starts seeing whatever their account already owns.

Deliberately imports `api` as a module and reaches into it via dotted
attribute access (`api._get_db_engine()`, not `from api import
_get_db_engine`) rather than importing shared helpers by name. Two reasons:
this avoids a circular-import ordering problem (api.py includes this
router, so this module can't import api.py's names at api.py's own
top-level before they exist yet), and it preserves this app's existing
test-patching convention — `unittest.mock.patch("api._get_db_engine", ...)`
only takes effect on code that looks the name up through the module object
at call time, not on a name already copied by a `from api import X` at
import time.
"""
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

import api
from routes._shared import claim_anonymous_rows_sync, run_owned_db_call

router = APIRouter()

_CLIENT_ID_RE = api.re.compile(r"^[a-zA-Z0-9-]{1,36}$")  # matches watchlist_items.client_id VARCHAR(36)
_MAX_WATCHLIST_ITEMS_PER_CLIENT = 200
_VALID_EXCHANGES = {"NSE", "BSE"}

# (owner_type, owner_value) — owner_type is "user" (owner_value: int user id)
# or "client" (owner_value: str client_id). Never constructed directly outside
# resolve_owner, so owner_type is always one of exactly these two literals
# wherever it's used to pick a column name below. Shared with routes/positions.py,
# which owns the exact same anonymous-client_id-or-account-user_id shape.
WatchlistOwner = tuple


def resolve_owner(token: str | None, client_id: str | None) -> WatchlistOwner:
    """A valid session always takes priority over client_id. Runs inside the
    same executor thread as the caller's other DB work, since checking the
    session also hits the DB. Raises ValueError (-> 422) if neither a valid
    session nor a well-formed client_id is available — this endpoint doesn't
    otherwise require being signed in, so an expired/invalid token just falls
    through to the client_id path rather than surfacing as a 401."""
    if token:
        import auth as _auth
        user = _auth.get_user_for_session(token)
        if user:
            return ("user", user["id"])
    if not client_id or not _CLIENT_ID_RE.match(client_id):
        raise ValueError("Invalid client_id.")
    return ("client", client_id)


def owner_column(owner: WatchlistOwner) -> str:
    return "user_id" if owner[0] == "user" else "client_id"


class WatchlistAddRequest(BaseModel):
    client_id: str | None = None
    symbol: str
    company: str = Field(default="")
    exchange: str = Field(default="NSE")


def _watchlist_rows_sync(owner: WatchlistOwner) -> list[dict]:
    from sqlalchemy import text as _text

    column = owner_column(owner)
    engine = api._get_db_engine()
    with engine.connect() as conn:
        rows = conn.execute(_text(f"""
            SELECT symbol, company, exchange, added_at::text AS "addedAt"
            FROM watchlist_items
            WHERE {column} = :owner_value
            ORDER BY added_at DESC
        """), {"owner_value": owner[1]}).mappings().fetchall()
    return [dict(r) for r in rows]


@router.get("/api/watchlist")
async def get_watchlist(request: Request, client_id: str | None = Query(None)):
    token = api._bearer_token_from_request(request)

    def _sync() -> dict:
        owner = resolve_owner(token, client_id)
        return {"items": _watchlist_rows_sync(owner)}

    return await run_owned_db_call(request, "watchlist_read", 120, _sync, "watchlist_read")


@router.get("/api/watchlist/calendar")
async def get_watchlist_calendar(request: Request, symbols: str = Query(...)):
    """Upcoming-and-notable roll-up across a set of watched symbols — two
    independent sources, each best-effort and each optional per symbol:

    1. A corporate-action calendar read straight off each symbol's
       already-cached `filings` data, running the same classify_filings()
       classifier main._build_report() already runs per-symbol for the
       single-stock report's "Corporate Filings" card. No new scraping.
    2. A same-day recommendation-change / price-move flag via
       verdict_history.detect_recent_changes() — the same two conditions
       watchlist_alerts.py's daily digest email already computes and sends,
       now also reachable without waiting for that email to arrive. Degrades
       to both `None` (not an error) when DATABASE_URL isn't set, same as
       every other verdict_history-backed read in this app.

    The caller (the watchlist page, which already has its own symbol list
    from GET /api/watchlist) passes the symbols directly rather than this
    endpoint re-querying ownership itself. A symbol contributes an entry
    when it has *something* from either source — no cached filings and no
    notable change means no entry, not an error. Deliberately doesn't go
    through run_owned_db_call — this endpoint needs no DATABASE_URL gate of
    its own (verdict_history's own read already degrades gracefully without
    one) and resolves no owner at all.
    """
    api._rate_limit(request, "watchlist_calendar", max_calls=30, window_seconds=60)
    sym_list = [s.strip().upper() for s in symbols.split(",") if api._TICKER_RE.match(s.strip())][:_MAX_WATCHLIST_ITEMS_PER_CLIENT]
    if not sym_list:
        return {"entries": []}

    def _one(sym: str) -> dict | None:
        import cache
        import verdict_history
        from signals.filings_classifier import classify_filings

        cached = cache.load(sym, "filings")
        filings_list = cached.get("filings", []) if cached else []
        summary = classify_filings(filings_list) if isinstance(filings_list, list) and filings_list else {
            "corporate_actions": [], "rating_action": None, "next_results_date": None,
        }

        changes = verdict_history.detect_recent_changes(sym)

        has_filings_content = bool(summary.get("next_results_date") or summary.get("corporate_actions"))
        has_notable_change = bool(changes["recommendation_change"] or changes["price_move"])
        if not has_filings_content and not has_notable_change:
            return None
        return {
            "symbol": sym,
            **summary,
            "recommendation_change": changes["recommendation_change"],
            "price_move": changes["price_move"],
        }

    loop = api.asyncio.get_running_loop()
    results = await api.asyncio.gather(*[loop.run_in_executor(None, _one, s) for s in sym_list])
    entries = [r for r in results if r]
    # A notable change (something to act on today) sorts first; within each
    # group, symbols with no next_results_date sort after ones that have it
    # rather than first — a corporate-action-only entry (e.g. a dividend
    # filing with no upcoming results date) is still worth surfacing, just
    # not ahead of an actual price move or recommendation flip.
    entries.sort(key=lambda e: (
        0 if (e["recommendation_change"] or e["price_move"]) else 1,
        e.get("next_results_date") or "9999-99-99",
    ))
    return {"entries": entries}


@router.post("/api/watchlist")
async def add_to_watchlist(request: Request, body: WatchlistAddRequest):
    symbol = body.symbol.upper().strip()
    if not api._TICKER_RE.match(symbol):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    exchange = body.exchange.upper().strip()
    if exchange not in _VALID_EXCHANGES:
        raise HTTPException(status_code=422, detail="Invalid exchange.")
    token = api._bearer_token_from_request(request)

    def _upsert_sync() -> dict:
        from sqlalchemy import text as _text

        owner = resolve_owner(token, body.client_id)
        column = owner_column(owner)
        # Distinct lock-key namespace per owner type so a client_id string and
        # a user_id integer can never collide on the same advisory lock.
        lock_key = f"watchlist:{owner[0]}:{owner[1]}"

        engine = api._get_db_engine()
        with engine.begin() as conn:
            # Advisory lock scoped to this transaction (released automatically on
            # commit/rollback) serializes concurrent adds for the same owner, so
            # the count-then-insert below can't race past _MAX_WATCHLIST_ITEMS_PER_CLIENT.
            conn.execute(_text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": lock_key})
            count = conn.execute(_text(
                f"SELECT COUNT(*) FROM watchlist_items WHERE {column} = :owner_value"
            ), {"owner_value": owner[1]}).scalar() or 0
            if count >= _MAX_WATCHLIST_ITEMS_PER_CLIENT:
                raise ValueError(f"Watchlist is capped at {_MAX_WATCHLIST_ITEMS_PER_CLIENT} stocks.")
            conn.execute(_text(f"""
                INSERT INTO watchlist_items ({column}, symbol, company, exchange)
                VALUES (:owner_value, :symbol, :company, :exchange)
                ON CONFLICT ({column}, symbol) DO NOTHING
            """), {
                "owner_value": owner[1], "symbol": symbol,
                "company": body.company[:200], "exchange": exchange,
            })
        return {"items": _watchlist_rows_sync(owner)}

    return await run_owned_db_call(request, "watchlist_write", 60, _upsert_sync, "watchlist_write")


@router.delete("/api/watchlist/{symbol}")
async def remove_from_watchlist(request: Request, symbol: str, client_id: str | None = Query(None)):
    sym = symbol.upper().strip()
    if not api._TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    token = api._bearer_token_from_request(request)

    def _delete_sync() -> dict:
        from sqlalchemy import text as _text

        owner = resolve_owner(token, client_id)
        column = owner_column(owner)

        engine = api._get_db_engine()
        with engine.begin() as conn:
            conn.execute(_text(
                f"DELETE FROM watchlist_items WHERE {column} = :owner_value AND symbol = :symbol"
            ), {"owner_value": owner[1], "symbol": sym})
        return {"items": _watchlist_rows_sync(owner)}

    return await run_owned_db_call(request, "watchlist_write", 60, _delete_sync, "watchlist_write")


class ClaimRequest(BaseModel):
    client_id: str


@router.post("/api/watchlist/claim")
async def claim_watchlist(request: Request, body: ClaimRequest):
    """Opt-in migration of an anonymous browser's watchlist onto the account
    that just signed in — the escape hatch for this module's own "no
    migration on sign-in" default (see the module docstring above), not a
    reversal of it. Requires a valid session: unlike every other endpoint
    in this module, this one has exactly one caller (the post-sign-in
    "claim your data" prompt, which only ever fires once a session is
    already known to exist), so an absent/expired session here is a real
    401, not a silent fall-through to "nothing to do."

    **Disclosed residual risk**: `client_id` was never a secret — any
    request that knows it can already read/add/delete that browser's rows
    (see `resolve_owner()` above), the same "grouping key, not a security
    boundary" design this table has always had. This endpoint's effect is
    more severe than a plain read/write, though: it *exclusively*
    reassigns those rows to whichever account calls it, permanently
    cutting off the original anonymous browser's access — a signed-in
    attacker who obtains someone else's `client_id` (e.g. leaked in a
    shared screenshot, a URL in browser history, or a server access log
    entry — `client_id` already appears in plaintext query strings on
    every other endpoint in this module) could claim their watchlist for
    themselves. A tight, dedicated rate limit (below) meaningfully bounds
    automated/brute-force abuse of this; it does not eliminate a single
    targeted guess of a specific leaked ID, which would require proof of
    possession (e.g. a signed token minted into the anonymous browser
    itself) to fully close — a larger change not attempted in this pass.
    """
    if not body.client_id or not _CLIENT_ID_RE.match(body.client_id):
        raise HTTPException(status_code=422, detail="Invalid client_id.")
    token = api._bearer_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in required to claim anonymous data.")

    def _claim_sync() -> dict:
        import auth as _auth

        user = _auth.get_user_for_session(token)
        if not user:
            raise PermissionError("Your session has expired. Sign in again to claim this data.")
        user_id = user["id"]

        claimed, skipped = claim_anonymous_rows_sync(
            api._get_db_engine(), "watchlist_items", "added_at",
            body.client_id, user_id, _MAX_WATCHLIST_ITEMS_PER_CLIENT, "watchlist",
        )
        # Audit trail distinct from run_owned_db_call's own failure-only
        # logging — a real, if imperfect, forensic signal for the residual
        # risk disclosed above (e.g. spotting one account claiming an
        # unusual number of distinct client_ids in a short window).
        api.log_event(
            api.LOGGER, "watchlist_claimed", user_id=user_id,
            client_id=body.client_id, claimed=claimed, skipped_over_cap=skipped,
        )
        return {
            "claimed": claimed,
            "skipped_over_cap": skipped,
            "items": _watchlist_rows_sync(("user", user_id)),
        }

    # Dedicated, tight rate limit — not the same generous per-minute budget
    # as an ordinary star/unstar (see this endpoint's own docstring above
    # for why an exclusive-reassignment operation warrants a much smaller
    # one, same per-address-not-just-per-IP precedent as the magic-link
    # request-link endpoint's 5/hour).
    return await run_owned_db_call(
        request, "watchlist_claim", 5, _claim_sync, "watchlist_claim", window_seconds=3600,
    )
