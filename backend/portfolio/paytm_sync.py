"""Paytm Money Open API sync — the free-tier Open API (holdings, orders)
into the Portfolio Aggregator's schema, same shape as
portfolio/kite_sync.py's Zerodha integration. See
docs/PRD-gmail-portfolio-intelligence.md Sec 9 Phase 1.

Paytm Money's own published Python client (github.com/paytmmoney/pyPMClient)
confirms the same login -> request_token -> generate_session -> access_token
flow Kite Connect uses (the two APIs are close cousins — Paytm Money's Open
API launched explicitly as a Kite Connect-style alternative). This module
talks to the underlying REST endpoints directly via `requests` rather than
depending on pyPMClient itself: that package has no working PyPI release
(`pip install pyPMClient` fails — "Could not find a version that satisfies
the requirement" — confirmed via its own community forum), so adding it as
a `requirements.txt` dependency would mean vendoring GitHub source for a
thin wrapper, which this codebase's dependency policy ("prefer the standard
library and already-installed dependencies over new ones") advises against.

Disclosed limitation, same convention as kite_sync.py and every scraper
elsewhere in this codebase: developer.paytmmoney.com blocks this sandbox's
outbound fetches (403), so the exact REST base URL, endpoint paths, and
holdings/orders response field names below were not verified against a
live response in this sandbox. They follow the shape pyPMClient's own
README documents (`login(state_key)` -> a browser login URL;
`generate_session(request_token=...)` -> an access token; the response
holds `user_holdings_data()`/`order_book()` calls) plus the same
`request_token` naming Kite Connect uses (Paytm Money's own README uses
that exact term). A field this module expects but a real response doesn't
have degrades that one holding/trade to skipped (logged), never a
fabricated value — spot-check against a live account before this ships to
a real deployment.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests
from sqlalchemy import update

from core.observability import get_logger, log_event
from db.models import broker_connections as broker_connections_t
from portfolio import broker_sync_common

LOGGER = get_logger("portfolio.paytm_sync")

BROKER_NAME = "paytm_money"
_META_SOURCE = "paytm_money_api"

_LOGIN_URL = "https://login.paytmmoney.com/merchant-login"
_API_BASE = "https://developer.paytmmoney.com/accounts/v2"
_TIMEOUT = 15

# Only a FILLED/EXECUTED order is a real trade — most rows in an order book
# are pending/rejected/cancelled, which is the expected common case, not a
# data problem, so those are silently excluded rather than logged as skipped.
_FILLED_STATUSES = {"EXECUTED", "COMPLETE", "FILLED"}


def get_login_url(api_key: str) -> str:
    return f"{_LOGIN_URL}?apiKey={api_key}"


def exchange_request_token(api_key: str, api_secret: str, request_token: str) -> dict:
    """Returns a session dict with `access_token`, or `{"error": ...}` —
    never raises, matching this codebase's tools/*.py convention."""
    try:
        resp = requests.post(
            f"{_API_BASE}/session/token",
            json={"apiKey": api_key, "apiSecretKey": api_secret, "requestToken": request_token},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", body)
        if "access_token" not in data and "accessToken" in data:
            data["access_token"] = data["accessToken"]
        return data
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "paytm_token_exchange_failed", level="warning", error=str(exc))
        return {"error": str(exc)}


def _headers(api_key: str, access_token: str) -> dict:
    return {"x-jwt-token": access_token, "x-api-key": api_key}


def _fetch_holdings(api_key: str, access_token: str) -> list[dict]:
    resp = requests.get(f"{_API_BASE}/holdings", headers=_headers(api_key, access_token), timeout=_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body if isinstance(body, list) else [])


def _fetch_orders(api_key: str, access_token: str) -> list[dict]:
    resp = requests.get(f"{_API_BASE}/order/book", headers=_headers(api_key, access_token), timeout=_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body if isinstance(body, list) else [])


def _normalize_holding(h: dict) -> dict | None:
    symbol = h.get("tradingSymbol") or h.get("symbol")
    quantity = broker_sync_common.to_decimal(h.get("quantity") or h.get("sellableQuantity"))
    if not symbol or quantity is None:
        log_event(LOGGER, "paytm_holding_skipped", level="warning", holding=str(h)[:200])
        return None
    return {
        "symbol": symbol,
        "exchange": h.get("exchange"),
        "quantity": quantity,
        "avg_price": broker_sync_common.to_decimal(h.get("averagePrice") or h.get("buyAvgPrice")),
        "last_price": broker_sync_common.to_decimal(h.get("lastTradedPrice") or h.get("ltp")),
    }


def _normalize_trade(o: dict) -> dict | None:
    """Each row is an order, not a discrete fill — pyPMClient exposes a
    separate `trade_details(order_no, leg_no, segment)` for per-fill data,
    but that needs a per-order follow-up call this module doesn't make (see
    module docstring); the filled order's own id stands in as the dedup key
    instead, a disclosed simplification versus a genuine trade id. Callers
    are expected to have already filtered to `_FILLED_STATUSES` — an
    unfilled order isn't a malformed trade, so it must never reach here and
    inflate the "skipped" count with ordinary order-book noise."""
    order_id = o.get("orderNo") or o.get("order_no")
    symbol = o.get("tradingSymbol") or o.get("symbol")
    side = (o.get("transactionType") or o.get("txnType") or "").lower()
    quantity = broker_sync_common.to_decimal(o.get("quantity"))
    price = broker_sync_common.to_decimal(o.get("avgTradedPrice") or o.get("price"))
    ts_raw = o.get("orderTimestamp") or o.get("exchangeTimestamp")
    if not (order_id and symbol and side in ("buy", "sell") and quantity and price and ts_raw):
        log_event(LOGGER, "paytm_trade_skipped", level="warning", trade=str(o)[:200])
        return None

    try:
        trade_date = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).date()
    except ValueError:
        log_event(LOGGER, "paytm_trade_bad_timestamp", level="warning", raw=str(ts_raw))
        return None

    return {
        "trade_id": str(order_id), "order_id": str(order_id), "symbol": symbol,
        "exchange": o.get("exchange"), "side": side, "quantity": quantity,
        "price": price, "trade_date": trade_date,
    }


def sync_account(engine, account_id: int, access_token: str, api_key: str) -> dict:
    """Syncs one connected Paytm Money account's holdings + filled orders
    into the existing Portfolio Aggregator schema. Read-only. Returns a
    summary dict; never raises — a broker-API hiccup degrades to an
    {"error": ...} result, same convention as every tools/*.py module.

    `api_key` is this connection's own registered app key (broker_connections.api_key),
    never a deployment-wide env var — see db/models.py's broker_connections comment."""
    try:
        raw_holdings = broker_sync_common.call_with_backoff(
            lambda: _fetch_holdings(api_key, access_token), broker=BROKER_NAME,
        )
        raw_orders = broker_sync_common.call_with_backoff(
            lambda: _fetch_orders(api_key, access_token), broker=BROKER_NAME,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "paytm_sync_fetch_failed", level="warning",
                   account_id=account_id, error=str(exc))
        return {"error": str(exc)}

    filled_orders = [o for o in (raw_orders or []) if (o.get("status") or "").upper() in _FILLED_STATUSES]

    today = datetime.now(timezone.utc).date()
    with engine.begin() as conn:
        summary = {}
        summary.update(broker_sync_common.sync_holdings(
            conn, account_id, [_normalize_holding(h) for h in (raw_holdings or [])], _META_SOURCE, today,
        ))
        summary.update(broker_sync_common.sync_trades(
            conn, account_id, [_normalize_trade(o) for o in filled_orders], _META_SOURCE,
        ))
        conn.execute(
            update(broker_connections_t)
            .where(
                broker_connections_t.c.account_id == account_id,
                broker_connections_t.c.broker == BROKER_NAME,
            )
            .values(last_synced_at=datetime.now(timezone.utc))
        )

    log_event(LOGGER, "paytm_sync_completed", account_id=account_id, **summary)
    return summary
