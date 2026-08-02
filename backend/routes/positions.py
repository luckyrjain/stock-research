"""'I bought this' positions tracking — extracted out of api.py (see
CLAUDE.md's "Positions" section for the full design). Same ownership shape
as routes/watchlist.py's watchlist_items — an anonymous per-browser
client_id until the user signs in, then the account's user_id — so this
module reuses that module's resolve_owner()/owner_column()/WatchlistOwner
rather than redefining its own copy of identity-resolution logic that isn't
actually watchlist-specific.
"""
from concurrent.futures import ThreadPoolExecutor

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

    See `claim_watchlist()`'s own docstring for the disclosed residual risk
    (`client_id` is a grouping key, not a security boundary — this endpoint
    just makes its effect exclusive/permanent rather than merely
    read/write) and why the rate limit below is deliberately much tighter
    than this table's ordinary add/remove endpoints.
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
            body.client_id, user_id, _MAX_POSITIONS_PER_CLIENT, "positions",
        )
        api.log_event(
            api.LOGGER, "positions_claimed", user_id=user_id,
            client_id=body.client_id, claimed=claimed, skipped_over_cap=skipped,
        )
        return {
            "claimed": claimed,
            "skipped_over_cap": skipped,
            "items": _positions_rows_sync(("user", user_id)),
        }

    return await run_owned_db_call(
        request, "positions_claim", 5, _claim_sync, "positions_claim", window_seconds=3600,
    )


_CONCENTRATION_THRESHOLD_PCT = 25.0


def compute_sector_concentration(
    positions: list[dict], live_prices: dict[str, float], sectors: dict[str, str | None],
    threshold_pct: float = _CONCENTRATION_THRESHOLD_PCT,
) -> dict:
    """Capital-weighted sector concentration over tracked positions — same
    "only over what's actually known, never guessed" convention as
    /portfolio's own capital-weighted stats: a position with no share count
    or no resolvable live price/sector simply doesn't contribute, rather
    than being counted at an assumed weight. Display-only; never touches
    market-picks scoring/cache itself."""
    total = 0.0
    value_by_sector: dict[str, float] = {}
    for pos in positions:
        shares = pos.get("shares")
        price = live_prices.get(pos.get("symbol", ""))
        sector = sectors.get(pos.get("symbol", ""))
        if not shares or shares <= 0 or price is None or not sector:
            continue
        value = float(shares) * price
        total += value
        value_by_sector[sector] = value_by_sector.get(sector, 0.0) + value

    if total <= 0:
        return {"by_sector": {}, "concentrated_sectors": []}

    by_sector_pct = {s: round(v / total * 100, 2) for s, v in value_by_sector.items()}
    concentrated = sorted(s for s, pct in by_sector_pct.items() if pct >= threshold_pct)
    return {"by_sector": by_sector_pct, "concentrated_sectors": concentrated}


@router.get("/api/portfolio/concentration")
async def get_portfolio_concentration(request: Request, client_id: str | None = Query(None)):
    """Flags a sector already heavily represented in the caller's tracked
    positions — an overlay Market Picks reads at request time to badge a new
    pick in that same sector, so a user doesn't stack further into a sector
    they're already concentrated in. Computed fresh per request from
    already-cached data only (this table's own rows, GET /api/prices' live
    quote, and the 1h stock_info cache for sector) — never triggers a new
    scrape, and never writes back to market-picks' own scoring/cache."""
    from core import cache as _cache

    token = api._bearer_token_from_request(request)

    def _sync() -> dict:
        owner = resolve_owner(token, client_id)
        positions = _positions_rows_sync(owner)
        symbols = [p["symbol"] for p in positions]

        # Up to _MAX_POSITIONS_PER_CLIENT (200) yfinance calls per request —
        # the same amplification concern GET /api/prices' own docstring
        # flags for its 50-symbol cap, just larger here. That endpoint fans
        # out via asyncio.gather+run_in_executor from the event loop; this
        # one already runs inside a single run_owned_db_call worker thread,
        # so a bounded local ThreadPoolExecutor is the equivalent fan-out
        # from in here (same worker-pool convention peer_analytics.py's
        # _fetch_valuation_percentile() uses for its own per-stock fan-out).
        live_prices: dict[str, float] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            for sym, live in zip(symbols, pool.map(api._fetch_live_price_sync, symbols)):
                if live.get("price"):
                    live_prices[sym] = live["price"]

        # Sector comes only from an already-fresh 1h stock_info cache —
        # never a fresh scrape triggered by this read-only overlay. A
        # symbol whose cache has gone stale/missing simply doesn't
        # contribute a sector this request, same "never invent" instinct
        # as everywhere else this codebase reads an optional field.
        sectors: dict[str, str | None] = {}
        for sym in symbols:
            stock_info = _cache.load(sym, "stock_info")
            if stock_info:
                sectors[sym] = stock_info.get("sector")

        return compute_sector_concentration(positions, live_prices, sectors)

    # Own tighter bucket, not the shared "positions_read" bucket ordinary
    # lightweight position reads use — this does the same per-symbol
    # yfinance fan-out as GET /api/prices, at up to 4x its symbol cap.
    return await run_owned_db_call(request, "portfolio_concentration", 10, _sync, "positions_read")
