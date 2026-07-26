"""'I bought this' positions tracking — extracted out of api.py (see
CLAUDE.md's "Positions" section for the full design). Same ownership shape
as routes/watchlist.py's watchlist_items — an anonymous per-browser
client_id until the user signs in, then the account's user_id — so this
module reuses that module's resolve_owner()/owner_column()/WatchlistOwner
rather than redefining its own copy of identity-resolution logic that isn't
actually watchlist-specific.
"""
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

import api
from routes._shared import claim_anonymous_rows_sync, run_owned_db_call
from routes.watchlist import _CLIENT_ID_RE, _VALID_EXCHANGES, WatchlistOwner, owner_column, resolve_owner

router = APIRouter()

_MAX_POSITIONS_PER_CLIENT = 200


class PositionAddRequest(BaseModel):
    client_id: str | None = None
    symbol: str
    company: str = Field(default="")
    exchange: str = Field(default="NSE")
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None


class PositionSharesRequest(BaseModel):
    client_id: str | None = None
    # None clears a previously-entered share count back to "unknown" — never
    # invented, never defaulted to 0/1.
    shares: float | None = None


def _positions_rows_sync(owner: WatchlistOwner) -> list[dict]:
    from sqlalchemy import text as _text

    column = owner_column(owner)
    engine = api._get_db_engine()
    with engine.connect() as conn:
        rows = conn.execute(_text(f"""
            SELECT symbol, company, exchange,
                   entry_price, target_price, stop_loss, shares,
                   bought_at::text AS "bought_at"
            FROM positions
            WHERE {column} = :owner_value
            ORDER BY bought_at DESC
        """), {"owner_value": owner[1]}).mappings().fetchall()
    return [dict(r) for r in rows]


@router.get("/api/positions")
async def get_positions(request: Request, client_id: str | None = Query(None)):
    token = api._bearer_token_from_request(request)

    def _sync() -> dict:
        owner = resolve_owner(token, client_id)
        return {"items": _positions_rows_sync(owner)}

    return await run_owned_db_call(request, "positions_read", 120, _sync, "positions_read")


@router.post("/api/positions")
async def add_position(request: Request, body: PositionAddRequest):
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
        lock_key = f"positions:{owner[0]}:{owner[1]}"

        engine = api._get_db_engine()
        with engine.begin() as conn:
            # Same advisory-lock-then-count pattern as watchlist's POST, scoped
            # to its own "positions:" lock-key namespace so it can never
            # collide with a concurrent watchlist add for the same owner.
            conn.execute(_text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": lock_key})
            count = conn.execute(_text(
                f"SELECT COUNT(*) FROM positions WHERE {column} = :owner_value"
            ), {"owner_value": owner[1]}).scalar() or 0
            existing = conn.execute(_text(
                f"SELECT 1 FROM positions WHERE {column} = :owner_value AND symbol = :symbol"
            ), {"owner_value": owner[1], "symbol": symbol}).first()
            if count >= _MAX_POSITIONS_PER_CLIENT and not existing:
                raise ValueError(f"Positions are capped at {_MAX_POSITIONS_PER_CLIENT} stocks.")
            # On conflict, refresh the market levels captured at this mark-time
            # but leave `shares` and `bought_at` untouched — a user-entered
            # share count or the original buy timestamp shouldn't be wiped by
            # re-marking a pick as bought (the normal UI flow removes the row
            # first, so this path is mostly a safety net, not the common case).
            conn.execute(_text(f"""
                INSERT INTO positions ({column}, symbol, company, exchange, entry_price, target_price, stop_loss)
                VALUES (:owner_value, :symbol, :company, :exchange, :entry_price, :target_price, :stop_loss)
                ON CONFLICT ({column}, symbol) DO UPDATE SET
                    company = EXCLUDED.company,
                    exchange = EXCLUDED.exchange,
                    entry_price = EXCLUDED.entry_price,
                    target_price = EXCLUDED.target_price,
                    stop_loss = EXCLUDED.stop_loss
            """), {
                "owner_value": owner[1], "symbol": symbol,
                "company": body.company[:200], "exchange": exchange,
                "entry_price": body.entry_price, "target_price": body.target_price, "stop_loss": body.stop_loss,
            })
        return {"items": _positions_rows_sync(owner)}

    return await run_owned_db_call(request, "positions_write", 60, _upsert_sync, "positions_write")


@router.patch("/api/positions/{symbol}")
async def update_position_shares(request: Request, symbol: str, body: PositionSharesRequest):
    """The one field a user fills in after the fact, from the Portfolio page
    (see CLAUDE.md's "Positions" section for why this isn't asked for at
    "I bought this" click-time) — a dedicated endpoint rather than folding
    into POST, since this never touches company/exchange/entry/target/stop."""
    sym = symbol.upper().strip()
    if not api._TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    if body.shares is not None and body.shares < 0:
        raise HTTPException(status_code=422, detail="Shares cannot be negative.")
    token = api._bearer_token_from_request(request)

    def _update_sync() -> dict:
        from sqlalchemy import text as _text

        owner = resolve_owner(token, body.client_id)
        column = owner_column(owner)

        engine = api._get_db_engine()
        with engine.begin() as conn:
            conn.execute(_text(
                f"UPDATE positions SET shares = :shares WHERE {column} = :owner_value AND symbol = :symbol"
            ), {"shares": body.shares, "owner_value": owner[1], "symbol": sym})
        return {"items": _positions_rows_sync(owner)}

    return await run_owned_db_call(request, "positions_write", 60, _update_sync, "positions_write")


@router.delete("/api/positions/{symbol}")
async def remove_position(request: Request, symbol: str, client_id: str | None = Query(None)):
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
                f"DELETE FROM positions WHERE {column} = :owner_value AND symbol = :symbol"
            ), {"owner_value": owner[1], "symbol": sym})
        return {"items": _positions_rows_sync(owner)}

    return await run_owned_db_call(request, "positions_write", 60, _delete_sync, "positions_write")


class ClaimRequest(BaseModel):
    client_id: str


@router.post("/api/positions/claim")
async def claim_positions(request: Request, body: ClaimRequest):
    """Opt-in migration of an anonymous browser's positions onto the account
    that just signed in — same escape hatch as routes/watchlist.py's
    claim_watchlist(), for the same "no migration on sign-in" default (see
    that module's docstring). Requires a valid session for the same reason:
    this endpoint's only caller is the post-sign-in "claim your data"
    prompt, so a missing/expired session is a real 401.
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
            api._get_db_engine(), "positions", "bought_at",
            body.client_id, user_id, _MAX_POSITIONS_PER_CLIENT, "positions_claim",
        )
        return {
            "claimed": claimed,
            "skipped_over_cap": skipped,
            "items": _positions_rows_sync(("user", user_id)),
        }

    return await run_owned_db_call(request, "positions_write", 60, _claim_sync, "positions_claim")
