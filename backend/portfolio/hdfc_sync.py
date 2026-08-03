"""HDFC Securities InvestRight Open API sync — the free-tier "Individual
API" (holdings, tradebook) into the Portfolio Aggregator's schema, same
shape as portfolio/kite_sync.py's Zerodha integration. See
docs/PRD-gmail-portfolio-intelligence.md Sec 9 Phase 1.

Disclosed limitation, same convention as kite_sync.py/paytm_sync.py and
every scraper elsewhere in this codebase: developer.hdfcsec.com blocks
this sandbox's outbound fetches (403), so the exact REST base URL,
endpoint paths, checksum/signing scheme, and holdings/tradebook response
field names below were not verified against a live response in this
sandbox. They follow the general shape confirmed via public documentation
snippets (an `api_key` query parameter on the login redirect, an
`Authorization: access_token <token>` header on every authenticated call,
a documented endpoint list of Login / Fetch Profile / Place Order / Fetch
Order Details / Fetch Tradebook / Positions / Holdings / Funds) plus this
codebase's own observation that Indian broker "Open APIs" (Kite Connect,
Paytm Money, Upstox, Angel One, Dhan) all converge on the same
login-redirect-with-request_token -> checksum-signed-exchange ->
access_token -> REST shape. The SHA-256(api_key + request_token +
api_secret) checksum below mirrors Kite Connect's own published scheme,
which several peer broker APIs also use verbatim — an assumption, not a
confirmation. A field this module expects but a real response doesn't
have degrades that one holding/trade to skipped (logged), never a
fabricated value — spot-check against a live account before this ships
to a real deployment.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import requests
from sqlalchemy import update

from core.observability import get_logger, log_event
from db.models import broker_connections as broker_connections_t
from portfolio import broker_sync_common

LOGGER = get_logger("portfolio.hdfc_sync")

BROKER_NAME = "hdfc_securities"
_META_SOURCE = "hdfc_securities_api"

_LOGIN_URL = "https://developer.hdfcsec.com/login"
_API_BASE = "https://developer.hdfcsec.com/oapi/v1"
_TIMEOUT = 15


def get_login_url(api_key: str) -> str:
    return f"{_LOGIN_URL}?api_key={api_key}"


def exchange_request_token(api_key: str, api_secret: str, request_token: str) -> dict:
    """Returns a session dict with `access_token`, or `{"error": ...}` —
    never raises, matching this codebase's tools/*.py convention."""
    checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()
    try:
        resp = requests.post(
            f"{_API_BASE}/login/v1/session/token",
            json={"api_key": api_key, "request_token": request_token, "checksum": checksum},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", body)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "hdfc_token_exchange_failed", level="warning", error=str(exc))
        return {"error": str(exc)}


def _headers(api_key: str, access_token: str) -> dict:
    return {"api_key": api_key, "Authorization": f"access_token {access_token}"}


def _fetch_holdings(api_key: str, access_token: str) -> list[dict]:
    resp = requests.get(f"{_API_BASE}/holdings", headers=_headers(api_key, access_token), timeout=_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body if isinstance(body, list) else [])


def _fetch_tradebook(api_key: str, access_token: str) -> list[dict]:
    resp = requests.get(f"{_API_BASE}/tradebook", headers=_headers(api_key, access_token), timeout=_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body if isinstance(body, list) else [])


def _normalize_holding(h: dict) -> dict | None:
    symbol = h.get("trading_symbol") or h.get("symbol")
    quantity = broker_sync_common.to_decimal(h.get("quantity"))
    if not symbol or quantity is None:
        log_event(LOGGER, "hdfc_holding_skipped", level="warning", holding=str(h)[:200])
        return None
    return {
        "symbol": symbol,
        "exchange": h.get("exchange"),
        "quantity": quantity,
        "avg_price": broker_sync_common.to_decimal(h.get("average_price") or h.get("buy_avg_price")),
        "last_price": broker_sync_common.to_decimal(h.get("last_price") or h.get("ltp")),
    }


def _normalize_trade(t: dict) -> dict | None:
    trade_id = t.get("trade_id") or t.get("exchange_trade_id")
    symbol = t.get("trading_symbol") or t.get("symbol")
    side = (t.get("transaction_type") or t.get("order_side") or "").lower()
    quantity = broker_sync_common.to_decimal(t.get("quantity") or t.get("traded_quantity"))
    price = broker_sync_common.to_decimal(t.get("price") or t.get("traded_price"))
    ts_raw = t.get("trade_time") or t.get("order_timestamp")
    if not (trade_id and symbol and side in ("buy", "sell") and quantity and price and ts_raw):
        log_event(LOGGER, "hdfc_trade_skipped", level="warning", trade=str(t)[:200])
        return None

    try:
        trade_date = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).date()
    except ValueError:
        log_event(LOGGER, "hdfc_trade_bad_timestamp", level="warning", raw=str(ts_raw))
        return None

    return {
        "trade_id": str(trade_id), "order_id": t.get("order_id"), "symbol": symbol,
        "exchange": t.get("exchange"), "side": side, "quantity": quantity,
        "price": price, "trade_date": trade_date,
    }


def sync_account(engine, account_id: int, access_token: str, api_key: str) -> dict:
    """Syncs one connected HDFC Securities account's holdings + tradebook
    into the existing Portfolio Aggregator schema. Read-only. Returns a
    summary dict; never raises — a broker-API hiccup degrades to an
    {"error": ...} result, same convention as every tools/*.py module.

    `api_key` is this connection's own registered app key (broker_connections.api_key),
    never a deployment-wide env var — see db/models.py's broker_connections comment."""
    try:
        raw_holdings = broker_sync_common.call_with_backoff(
            lambda: _fetch_holdings(api_key, access_token), broker=BROKER_NAME,
        )
        raw_trades = broker_sync_common.call_with_backoff(
            lambda: _fetch_tradebook(api_key, access_token), broker=BROKER_NAME,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "hdfc_sync_fetch_failed", level="warning",
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

    log_event(LOGGER, "hdfc_sync_completed", account_id=account_id, **summary)
    return summary
