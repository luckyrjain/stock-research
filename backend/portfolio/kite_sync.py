"""Zerodha Kite Connect sync — the free-tier "Personal API" (holdings,
positions, orders/trades, no live/historical market data needed) into the
Portfolio Aggregator's existing assets/holdings/valuations/transactions
schema. See docs/PRD-gmail-portfolio-intelligence.md Sec 9 Phase 1: this is
the primary ingestion path (structured, authoritative, no LLM, no
extraction error) — Gmail-parsed transactions (Phase 2) are the fallback
for brokers without an API like this one.

Disclosed limitation, same convention as every scraper elsewhere in this
codebase: Kite Connect's exact holdings()/trades() response field names
(tradingsymbol, exchange, isin, quantity, average_price, last_price,
order_timestamp, trade_id, order_id, transaction_type) are taken from
Kite Connect's own published REST API documentation, not verified against
a live response in this sandbox (no outbound internet to kite.trade here).
A field this module expects but a real response doesn't have degrades to
that one holding/trade being skipped (logged), never a fabricated value.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import insert, select, text, update

from core.observability import get_logger, log_event
from db.models import accounts as accounts_t
from db.models import assets as assets_t
from db.models import broker_connections as broker_connections_t
from db.models import holdings as holdings_t
from db.models import transactions as transactions_t
from db.models import valuations as valuations_t

LOGGER = get_logger("portfolio.kite_sync")

BROKER_NAME = "zerodha"
_META_SOURCE = "zerodha_api"


def _get_kite_client(api_key: str, access_token: str | None = None):
    """The one seam tests patch (`unittest.mock.patch("portfolio.kite_sync._get_kite_client")`)
    — lazy import, same convention as this codebase's other optional/heavy
    SDK dependencies (e.g. portfolio_valuation.py's lazy yfinance import),
    so importing this module never requires kiteconnect to be installed
    unless a broker sync is actually attempted."""
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=api_key)
    if access_token:
        kite.set_access_token(access_token)
    return kite


def get_login_url(api_key: str) -> str:
    return _get_kite_client(api_key).login_url()


def exchange_request_token(api_key: str, api_secret: str, request_token: str) -> dict:
    """Returns Kite's session dict (`access_token`, `user_id`, ...) or
    `{"error": ...}` — never raises, matching this codebase's tools/*.py
    convention, even though this isn't itself a `tools/` module."""
    try:
        kite = _get_kite_client(api_key)
        return kite.generate_session(request_token, api_secret=api_secret)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "kite_token_exchange_failed", level="warning", error=str(exc))
        return {"error": str(exc)}


def _to_decimal(value, default=None) -> Decimal | None:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _find_or_create_asset(conn, account_id: int, symbol: str, exchange: str | None) -> int:
    """Reuses csv_import.py's own new-asset-by-symbol lookup shape — a
    `stock` asset for this account matched by `assets.symbol`, created if
    none exists. Deliberately does NOT run tools.securities_master.resolve_symbol()
    here: Kite's own `tradingsymbol` is already the canonical NSE/BSE
    trading symbol (it's what a Kite order actually executes against), so
    there's no broker-internal-code-to-canonical-symbol gap to close the
    way there is for a raw CSV export's tradingsymbol column."""
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
            meta={"source": _META_SOURCE, "exchange": exchange} if exchange else {"source": _META_SOURCE},
        )
    )
    return result.inserted_primary_key[0]


def _upsert_holding(conn, asset_id: int, units: Decimal, avg_cost: Decimal | None) -> None:
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


def _upsert_valuation(conn, asset_id: int, as_of: date, value: Decimal) -> None:
    # Same raw-SQL upsert shape as portfolio_valuation.py::refresh_valuations()
    # — one row per (asset_id, as_of), same-day re-sync updates in place.
    conn.execute(
        text(
            "INSERT INTO valuations (asset_id, as_of, value) VALUES (:asset_id, :as_of, :value) "
            "ON CONFLICT (asset_id, as_of) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"asset_id": asset_id, "as_of": as_of, "value": value},
    )


def _sync_holdings(conn, account_id: int, raw_holdings: list[dict]) -> dict:
    synced, skipped = 0, 0
    today = datetime.now(timezone.utc).date()
    for h in raw_holdings:
        symbol = h.get("tradingsymbol")
        quantity = _to_decimal(h.get("quantity"))
        if not symbol or quantity is None:
            skipped += 1
            log_event(LOGGER, "kite_holding_skipped", level="warning", holding=str(h)[:200])
            continue
        avg_cost = _to_decimal(h.get("average_price"))
        last_price = _to_decimal(h.get("last_price"))

        asset_id = _find_or_create_asset(conn, account_id, symbol, h.get("exchange"))
        _upsert_holding(conn, asset_id, quantity, avg_cost)
        if last_price is not None:
            _upsert_valuation(conn, asset_id, today, quantity * last_price)
        synced += 1
    return {"holdings_synced": synced, "holdings_skipped": skipped}


def _existing_trade_ids(conn, account_id: int) -> set[str]:
    # Dedup key is the broker's own trade_id, stored in meta — same
    # "meta.source filtered via the JSON comparator" idiom cas_import.py
    # already established, scoped to this account's assets only.
    rows = conn.execute(
        text(
            "SELECT t.meta->>'trade_id' FROM transactions t "
            "JOIN assets a ON a.id = t.asset_id "
            "WHERE a.account_id = :account_id AND t.meta->>'source' = :source "
            "AND t.meta->>'trade_id' IS NOT NULL"
        ),
        {"account_id": account_id, "source": _META_SOURCE},
    ).all()
    return {r[0] for r in rows}


def _sync_trades(conn, account_id: int, raw_trades: list[dict]) -> dict:
    synced, skipped, duplicates = 0, 0, 0
    seen = _existing_trade_ids(conn, account_id)
    for t in raw_trades:
        trade_id = t.get("trade_id")
        symbol = t.get("tradingsymbol")
        side = (t.get("transaction_type") or "").lower()  # Kite: "BUY" | "SELL"
        quantity = _to_decimal(t.get("quantity"))
        price = _to_decimal(t.get("average_price"))
        ts_raw = t.get("order_timestamp") or t.get("fill_timestamp")
        if not (trade_id and symbol and side in ("buy", "sell") and quantity and price and ts_raw):
            skipped += 1
            log_event(LOGGER, "kite_trade_skipped", level="warning", trade=str(t)[:200])
            continue
        if trade_id in seen:
            duplicates += 1
            continue

        try:
            trade_date = (
                datetime.fromisoformat(str(ts_raw)).date()
                if isinstance(ts_raw, str) else ts_raw.date()
            )
        except (ValueError, AttributeError):
            skipped += 1
            log_event(LOGGER, "kite_trade_bad_timestamp", level="warning", raw=str(ts_raw))
            continue

        asset_id = _find_or_create_asset(conn, account_id, symbol, t.get("exchange"))
        conn.execute(
            insert(transactions_t).values(
                asset_id=asset_id,
                date=trade_date,
                type=side,
                amount=quantity * price,
                units=quantity,
                meta={
                    "source": _META_SOURCE,
                    "trade_id": trade_id,
                    "order_id": t.get("order_id"),
                },
            )
        )
        seen.add(trade_id)
        synced += 1
    return {"trades_synced": synced, "trades_skipped": skipped, "trades_duplicate": duplicates}


def sync_account(engine, account_id: int, access_token: str, api_key: str | None = None) -> dict:
    """Syncs one connected Zerodha account's holdings + today's executed
    trades into the existing Portfolio Aggregator schema. Read-only against
    Kite (holdings/positions/trades — never places an order). Returns a
    summary dict; never raises — a broker-API hiccup degrades to an
    {"error": ...} result, same convention as every tools/*.py module,
    even though this lives under portfolio/ rather than tools/."""
    api_key = api_key or os.environ.get("KITE_API_KEY", "")
    try:
        kite = _get_kite_client(api_key, access_token=access_token)
        raw_holdings = kite.holdings()
        raw_trades = kite.trades()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "kite_sync_fetch_failed", level="warning",
                   account_id=account_id, error=str(exc))
        return {"error": str(exc)}

    with engine.begin() as conn:
        summary = {}
        summary.update(_sync_holdings(conn, account_id, raw_holdings or []))
        summary.update(_sync_trades(conn, account_id, raw_trades or []))
        conn.execute(
            update(broker_connections_t)
            .where(
                broker_connections_t.c.account_id == account_id,
                broker_connections_t.c.broker == BROKER_NAME,
            )
            .values(last_synced_at=datetime.now(timezone.utc))
        )

    log_event(LOGGER, "kite_sync_completed", account_id=account_id, **summary)
    return summary
