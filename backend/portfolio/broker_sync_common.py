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


def reconcile_stale_holdings(conn, account_id: int, meta_source: str, seen_symbols: set[str]) -> int:
    """Archives — never deletes, same convention cas_import.py already
    established for a closed folio with real history: XIRR needs the
    transaction trail even after a position is fully closed — a
    broker-sourced asset that used to appear in this connection's holdings
    but doesn't anymore (the account fully sold out of it). Scoped to
    `meta.source == meta_source` so this only ever touches an asset THIS
    broker connection created; a manually-entered or CAS/CSV-sourced asset
    that happens to share a symbol is untouched. `sync_holdings()` calls
    this only when the broker's fetch returned at least one real holding —
    see its own docstring for why a genuinely empty response is treated as
    "nothing to reconcile from" rather than "the user sold everything.\""""
    rows = conn.execute(
        select(assets_t.c.id, assets_t.c.symbol).where(
            assets_t.c.account_id == account_id,
            assets_t.c.type == "stock",
            assets_t.c.archived.is_(False),
            assets_t.c.meta["source"].as_string() == meta_source,
        )
    ).all()
    archived = 0
    for row in rows:
        if row.symbol not in seen_symbols:
            conn.execute(update(assets_t).where(assets_t.c.id == row.id).values(archived=True))
            archived += 1
    return archived


def sync_holdings(conn, account_id: int, normalized_holdings: list[dict | None], meta_source: str, today: date) -> dict:
    """A holdings sync is treated as authoritative for whatever this broker
    connection itself created — a symbol previously synced but absent from
    the latest fetch gets archived (see reconcile_stale_holdings), and a
    previously-archived symbol reappearing (bought back after being fully
    sold) is un-archived here. Deliberately NOT authoritative when the raw
    fetch came back completely empty — a broker API hiccup returning `[]`
    for a reason that isn't "sold everything" must not archive an entire
    portfolio; that failure mode is worse than the stale-ghost-holding
    problem this function exists to fix, so reconciliation is skipped
    (not run at all) on a genuinely empty holdings list."""
    synced, skipped = 0, 0
    seen_symbols: set[str] = set()
    for h in normalized_holdings:
        if h is None:
            skipped += 1
            continue
        asset_id = find_or_create_asset(conn, account_id, h["symbol"], h.get("exchange"), meta_source)
        conn.execute(update(assets_t).where(assets_t.c.id == asset_id).values(archived=False))
        upsert_holding(conn, asset_id, h["quantity"], h.get("avg_price"))
        if h.get("last_price") is not None:
            upsert_valuation(conn, asset_id, today, h["quantity"] * h["last_price"])
        seen_symbols.add(h["symbol"])
        synced += 1

    archived = reconcile_stale_holdings(conn, account_id, meta_source, seen_symbols) if normalized_holdings else 0
    return {"holdings_synced": synced, "holdings_skipped": skipped, "holdings_archived": archived}


def sync_trades(conn, account_id: int, normalized_trades: list[dict | None], meta_source: str) -> dict:
    """The Python-level `seen` pre-check below is the fast path (avoids a
    round trip to the DB's own constraint in the common, single-writer
    case) — but `transactions.uq_transactions_asset_external_ref` (see
    db/models.py) is what actually *guarantees* no duplicate trade under a
    genuine race (two concurrent syncs for the same connection both
    passing this pre-check before either commits). Callers are expected to
    also hold routes/portfolio_aggregator.py's per-(account,broker) sync
    lock, so hitting the constraint here should be rare — when it does
    happen, the whole sync's transaction aborts and surfaces as a clean
    422 to that caller, never a silent duplicate row."""
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
                external_ref=f"{meta_source}:{t['trade_id']}",
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
