"""Mirrors a synced broker holding into `positions` — the separate manual
"I bought this" Market Picks tracker's own table, a different feature with
its own ownership shape (see routes/positions.py). Extracted out of
portfolio/broker_sync_common.py, whose own docstring scopes it to "the
assets/holdings/valuations/transactions schema" — writing into `positions`
is a real cross-feature dependency that module's own comments already
disclosed. This move gives that dependency a name and a rightful owner
(this module writes `positions`, broker_sync_common.py doesn't) — it does
NOT eliminate the dependency itself: broker_sync_common.py::sync_holdings()
still directly imports and calls this function from inside its own
per-holding loop, and always will, since the mirror write has to run inside
the same atomic transaction as the holdings/trades sync, wrapped in its own
SAVEPOINT (see sync_holdings()'s own docstring for why). A caller-supplied
callback would remove that import edge entirely, but isn't worth it for
this module's one real caller today.
"""

from decimal import Decimal

from sqlalchemy import text

from routes.watchlist import owner_column


def upsert_position_from_holding(
    conn, owner: tuple[str, str | int], symbol: str, exchange: str | None,
    quantity: Decimal, avg_price: Decimal | None,
) -> None:
    """See routes/portfolio_aggregator.py's broker_sync() for why `owner`
    only ever comes from the request that kicked off the sync, never stored
    on accounts/profiles themselves (Portfolio Aggregator has no owner
    concept at all otherwise).

    `owner` is a resolved routes.watchlist.WatchlistOwner tuple
    (`("user", user_id)` or `("client", client_id)`), not a raw client_id —
    written to whichever column GET /api/positions actually reads for that
    same caller (routes.watchlist.resolve_owner prefers a valid session
    over client_id). Writing every synced position under client_id
    unconditionally would make it invisible on /portfolio for anyone
    signed in, since that page then reads by user_id.

    `owner_column()` (routes/watchlist.py — the same helper
    routes/positions.py::add_position() already uses for this identical
    table) is what routes.watchlist.resolve_owner()'s own return-type
    guarantee makes safe to interpolate into the column-name f-string
    below: it only ever returns exactly "user_id" or "client_id", never raw
    user input, same closed-set-substitution convention as
    routes/_shared.py::claim_anonymous_rows_sync's own comment about why
    that's safe.

    Only `entry_price`/`shares`/`exchange` are overwritten on every sync —
    `target_price`/`stop_loss` (manual, no broker equivalent) and
    `bought_at` (first-seen timestamp) are left untouched by the `DO
    UPDATE`, whether this row started as a manual entry or a previous
    sync's. `company` is left NULL: none of the three brokers' normalized
    holding dicts carry a company name today (see
    portfolio/broker_sync_common.py's own module docstring for the
    normalized shape), and guessing one from the symbol isn't worth the
    drift risk for a field the Positions UI already treats as optional."""
    column = owner_column(owner)
    conn.execute(
        text(
            f"INSERT INTO positions ({column}, symbol, exchange, entry_price, shares) "
            f"VALUES (:owner_value, :symbol, :exchange, :entry_price, :shares) "
            f"ON CONFLICT ({column}, symbol) DO UPDATE SET "
            f"exchange = EXCLUDED.exchange, entry_price = EXCLUDED.entry_price, shares = EXCLUDED.shares"
        ),
        {"owner_value": owner[1], "symbol": symbol, "exchange": exchange, "entry_price": avg_price, "shares": quantity},
    )
