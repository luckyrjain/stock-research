"""Shared DB-write helpers for the broker-API sync modules (kite_sync.py,
hdfc_sync.py, paytm_sync.py) — the "get an already-normalized holding/trade
into the assets/holdings/valuations/transactions schema" half of each sync.
Reading + interpreting a broker's own raw JSON shape (field names,
timestamp format — all independently disclosed as unverified per broker)
stays in that broker's own module; this module only ever sees the common
intermediate shape every broker module normalizes into first. Same
"byte-identical logic gets one shared home" instinct as
tools/_nse_session.py consolidating seven near-duplicate NSE-session
helpers.

Normalized holding dict: {"symbol", "exchange" (optional), "quantity"
(Decimal), "avg_price" (Decimal | None), "last_price" (Decimal | None)}.
Normalized trade dict: {"trade_id", "order_id" (optional), "symbol",
"exchange" (optional), "side" ("buy" | "sell"), "quantity" (Decimal),
"price" (Decimal), "trade_date" (date)}. A `None` in either list means
"this raw record was skipped as malformed" — already logged by the
broker module that produced it, counted here, never guessed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import insert, select, text, update

from db.models import assets as assets_t
from db.models import holdings as holdings_t
from db.models import transactions as transactions_t


def to_decimal(value, default=None) -> Decimal | None:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def find_or_create_asset(conn, account_id: int, symbol: str, exchange: str | None, meta_source: str) -> int:
    """Reuses csv_import.py's own new-asset-by-symbol lookup shape — a `stock`
    asset for this account matched by `assets.symbol`, created if none exists.
    Deliberately does NOT run tools.securities_master.resolve_symbol() — every
    broker sync module's own `tradingsymbol`-equivalent field is already the
    canonical NSE/BSE trading symbol (it's what the broker's own order
    actually executed against), so there's no broker-internal-code-to-
    canonical-symbol gap to close the way there is for a raw CSV export."""
    existing = conn.execute(
        select(assets_t.c.id).where(
            assets_t.c.account_id == account_id,
            assets_t.c.symbol == symbol,
            assets_t.c.type == "stock",
        )
    ).first()
    if existing:
        return existing[0]

    result = conn.execute(
        insert(assets_t).values(
            account_id=account_id,
            type="stock",
            name=symbol,
            symbol=symbol,
            meta={"source": meta_source, "exchange": exchange} if exchange else {"source": meta_source},
        )
    )
    return result.inserted_primary_key[0]


def upsert_holding(conn, asset_id: int, units: Decimal, avg_cost: Decimal | None) -> None:
    existing = conn.execute(
        select(holdings_t.c.id).where(holdings_t.c.asset_id == asset_id)
    ).first()
    if existing:
        conn.execute(
            update(holdings_t)
            .where(holdings_t.c.asset_id == asset_id)
            .values(units=units, avg_cost=avg_cost)
        )
    else:
        conn.execute(
            insert(holdings_t).values(asset_id=asset_id, units=units, avg_cost=avg_cost)
        )


def upsert_valuation(conn, asset_id: int, as_of: date, value: Decimal) -> None:
    # Same raw-SQL upsert shape as portfolio_valuation.py::refresh_valuations()
    # — one row per (asset_id, as_of), same-day re-sync updates in place.
    conn.execute(
        text(
            "INSERT INTO valuations (asset_id, as_of, value) VALUES (:asset_id, :as_of, :value) "
            "ON CONFLICT (asset_id, as_of) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"asset_id": asset_id, "as_of": as_of, "value": value},
    )


def existing_trade_ids(conn, account_id: int, meta_source: str) -> set[str]:
    # Dedup key is the broker's own trade/order id, stored in meta — same
    # "meta.source filtered via the JSON comparator" idiom cas_import.py
    # already established, scoped to this account's assets and this broker's
    # own meta_source (so two different brokers connected to the same account
    # can never collide on trade id namespaces).
    rows = conn.execute(
        text(
            "SELECT t.meta->>'trade_id' FROM transactions t "
            "JOIN assets a ON a.id = t.asset_id "
            "WHERE a.account_id = :account_id AND t.meta->>'source' = :source "
            "AND t.meta->>'trade_id' IS NOT NULL"
        ),
        {"account_id": account_id, "source": meta_source},
    ).all()
    return {r[0] for r in rows}


def sync_holdings(conn, account_id: int, normalized_holdings: list[dict | None], meta_source: str, today: date) -> dict:
    synced, skipped = 0, 0
    for h in normalized_holdings:
        if h is None:
            skipped += 1
            continue
        asset_id = find_or_create_asset(conn, account_id, h["symbol"], h.get("exchange"), meta_source)
        upsert_holding(conn, asset_id, h["quantity"], h.get("avg_price"))
        if h.get("last_price") is not None:
            upsert_valuation(conn, asset_id, today, h["quantity"] * h["last_price"])
        synced += 1
    return {"holdings_synced": synced, "holdings_skipped": skipped}


def sync_trades(conn, account_id: int, normalized_trades: list[dict | None], meta_source: str) -> dict:
    synced, skipped, duplicates = 0, 0, 0
    seen = existing_trade_ids(conn, account_id, meta_source)
    for t in normalized_trades:
        if t is None:
            skipped += 1
            continue
        if t["trade_id"] in seen:
            duplicates += 1
            continue

        asset_id = find_or_create_asset(conn, account_id, t["symbol"], t.get("exchange"), meta_source)
        conn.execute(
            insert(transactions_t).values(
                asset_id=asset_id,
                date=t["trade_date"],
                type=t["side"],
                amount=t["quantity"] * t["price"],
                units=t["quantity"],
                meta={
                    "source": meta_source,
                    "trade_id": t["trade_id"],
                    "order_id": t.get("order_id"),
                },
            )
        )
        seen.add(t["trade_id"])
        synced += 1
    return {"trades_synced": synced, "trades_skipped": skipped, "trades_duplicate": duplicates}
