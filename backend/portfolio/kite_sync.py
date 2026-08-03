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

from datetime import datetime, timezone

from sqlalchemy import update

from core.observability import get_logger, log_event
from db.models import broker_connections as broker_connections_t
from portfolio import broker_sync_common

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


def _normalize_holding(h: dict) -> dict | None:
    symbol = h.get("tradingsymbol")
    quantity = broker_sync_common.to_decimal(h.get("quantity"))
    if not symbol or quantity is None:
        log_event(LOGGER, "kite_holding_skipped", level="warning", holding=str(h)[:200])
        return None
    return {
        "symbol": symbol,
        "exchange": h.get("exchange"),
        "quantity": quantity,
        "avg_price": broker_sync_common.to_decimal(h.get("average_price")),
        "last_price": broker_sync_common.to_decimal(h.get("last_price")),
    }


def _normalize_trade(t: dict) -> dict | None:
    trade_id = t.get("trade_id")
    symbol = t.get("tradingsymbol")
    side = (t.get("transaction_type") or "").lower()  # Kite: "BUY" | "SELL"
    quantity = broker_sync_common.to_decimal(t.get("quantity"))
    price = broker_sync_common.to_decimal(t.get("average_price"))
    ts_raw = t.get("order_timestamp") or t.get("fill_timestamp")
    if not (trade_id and symbol and side in ("buy", "sell") and quantity and price and ts_raw):
        log_event(LOGGER, "kite_trade_skipped", level="warning", trade=str(t)[:200])
        return None

    try:
        trade_date = (
            datetime.fromisoformat(str(ts_raw)).date()
            if isinstance(ts_raw, str) else ts_raw.date()
        )
    except (ValueError, AttributeError):
        log_event(LOGGER, "kite_trade_bad_timestamp", level="warning", raw=str(ts_raw))
        return None

    return {
        "trade_id": trade_id, "order_id": t.get("order_id"), "symbol": symbol,
        "exchange": t.get("exchange"), "side": side, "quantity": quantity,
        "price": price, "trade_date": trade_date,
    }


def sync_account(engine, account_id: int, access_token: str, api_key: str) -> dict:
    """Syncs one connected Zerodha account's holdings + today's executed
    trades into the existing Portfolio Aggregator schema. Read-only against
    Kite (holdings/positions/trades — never places an order). Returns a
    summary dict; never raises — a broker-API hiccup degrades to an
    {"error": ...} result, same convention as every tools/*.py module,
    even though this lives under portfolio/ rather than tools/.

    `api_key` is this connection's own registered app key (broker_connections.api_key),
    never a deployment-wide env var — see db/models.py's broker_connections comment
    for why a single global KITE_API_KEY wouldn't work across different accounts."""
    try:
        kite = _get_kite_client(api_key, access_token=access_token)
        raw_holdings = kite.holdings()
        raw_trades = kite.trades()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "kite_sync_fetch_failed", level="warning",
                   account_id=account_id, error=str(exc))
        return {"error": str(exc)}

    today = datetime.now(timezone.utc).date()
    with engine.begin() as conn:
        summary = {}
        summary.update(broker_sync_common.sync_holdings(
            conn, account_id, [_normalize_holding(h) for h in (raw_holdings or [])], _META_SOURCE, today,
        ))
        summary.update(broker_sync_common.sync_trades(
            conn, account_id, [_normalize_trade(t) for t in (raw_trades or [])], _META_SOURCE,
        ))
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
