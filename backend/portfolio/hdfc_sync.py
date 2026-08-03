"""HDFC Securities InvestRight Open API sync — the free-tier "Individual
API" (holdings, tradebook) into the Portfolio Aggregator's schema, same
shape as portfolio/kite_sync.py's Zerodha integration. See
docs/PRD-gmail-portfolio-intelligence.md Sec 9 Phase 1.

Unlike Kite Connect/Paytm Money, HDFC's real login has **no browser
redirect at all** — it's a direct, multi-step API flow that this app itself
must drive, collecting the user's HDFC username/password and OTP directly
rather than sending the browser to HDFC's own login page:

1. `GET /oapi/v1/login?api_key=` — returns a `token_id` scoping this one
   login attempt.
2. `POST /oapi/v1/login/validate?api_key=&token_id=` with `{username,
   password}` in the body — HDFC's own username/password check.
3. `POST /oapi/v1/twofa/validate?api_key=&token_id=` with `{"answer": otp}`
   — the OTP HDFC sends the user out-of-band. Returns `request_token`.
4. `GET /oapi/v1/authorise?api_key=&token_id=&consent=true&request_token=`
   — explicit consent, no further user input needed.
5. `POST /oapi/v1/access-token?api_key=&request_token=` with
   `{"apiSecret": ...}` in the body (not a checksum — HDFC's exchange step
   takes the app secret directly) — returns the actual `access_token`.

`routes/portfolio_aggregator.py`'s `login-start`/`verify-otp` endpoints
drive steps 1-2 and 3-5 respectively (steps 4-5 need no further user input
once the OTP is in, so they're chained server-side rather than making the
user click twice more). `broker_connections.pending_token_id` carries the
`token_id` between those two calls — see its own column comment in
db/models.py for why this is the one broker needing that.

Every field/endpoint above came from a real, working set of curl commands
(not public docs — developer.hdfcsec.com's own docs site is a JS-rendered
SPA this sandbox can't render), confirmed working through the `portfolio/
holdings` endpoint. The tradebook endpoint below (`portfolio/tradebook`)
was **not** independently confirmed — inferred from the same `/portfolio/`
prefix `holdings` actually uses, since no working tradebook call was
supplied. If that guess is wrong, `_fetch_tradebook`'s own `raise_for_status()`
turns it into a clean, logged `{"error": ...}` (via `call_with_backoff`'s
catch-all in `sync_account` below), never a silent wrong result — spot-check
against a live account before relying on trade sync specifically.
`Authorization: {access_token}` (no `Bearer`/`access_token` prefix) and a
real browser `User-Agent` are both required — the confirmed curls send both
on every call, including the unauthenticated login step.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests
from sqlalchemy import update

from core.observability import get_logger, log_event
from db.models import broker_connections as broker_connections_t
from portfolio import broker_sync_common

LOGGER = get_logger("portfolio.hdfc_sync")

BROKER_NAME = "hdfc_securities"
_META_SOURCE = "hdfc_securities_api"

_API_BASE = "https://developer.hdfcsec.com/oapi/v1"
_TIMEOUT = 15
# HDFC's endpoint rejects requests with no browser-shaped User-Agent (every
# confirmed-working curl sends one) — a generic requests/python UA gets
# nowhere. This exact string is a snapshot, not a live-negotiated value;
# it doesn't need to track a real Chrome release, just look like one.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
}


def start_login(api_key: str) -> dict:
    """Step 1: GET /login. Returns `{"token_id": ...}` or `{"error": ...}` —
    never raises, matching this codebase's tools/*.py convention."""
    try:
        resp = requests.get(
            f"{_API_BASE}/login", params={"api_key": api_key}, headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", body)
        if not data.get("token_id"):
            return {"error": "HDFC login did not return a token_id"}
        return data
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "hdfc_start_login_failed", level="warning", error=str(exc))
        return {"error": str(exc)}


def submit_credentials(api_key: str, token_id: str, username: str, password: str) -> dict:
    """Step 2: POST /login/validate with {username, password}. The password
    is only ever in-flight here — never logged, never persisted (see
    routes/portfolio_aggregator.py's login-start, which reads it out of the
    request body and never writes it to broker_connections). Returns the
    raw response body, or {"error": ...}; the caller only needs to know
    this didn't fail, the actual OTP prompt is a fixed next step."""
    try:
        resp = requests.post(
            f"{_API_BASE}/login/validate",
            params={"api_key": api_key, "token_id": token_id},
            json={"username": username, "password": password},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "hdfc_submit_credentials_failed", level="warning", error=str(exc))
        return {"error": str(exc)}


def submit_otp(api_key: str, token_id: str, otp: str) -> dict:
    """Step 3: POST /twofa/validate with {"answer": otp}. Returns
    `{"request_token": ...}` or `{"error": ...}`."""
    try:
        resp = requests.post(
            f"{_API_BASE}/twofa/validate",
            params={"api_key": api_key, "token_id": token_id},
            json={"answer": otp},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", body)
        if not data.get("request_token"):
            return {"error": "HDFC OTP verification did not return a request_token"}
        return data
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "hdfc_submit_otp_failed", level="warning", error=str(exc))
        return {"error": str(exc)}


def authorise(api_key: str, token_id: str, request_token: str) -> dict:
    """Step 4: GET /authorise?consent=true. No user input needed — this is
    a fixed continuation once the OTP step returns a request_token, so
    routes/portfolio_aggregator.py's verify-otp calls this and
    get_access_token() back to back. Returns {"error": ...} on failure."""
    try:
        resp = requests.get(
            f"{_API_BASE}/authorise",
            params={"api_key": api_key, "token_id": token_id, "consent": "true", "request_token": request_token},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "hdfc_authorise_failed", level="warning", error=str(exc))
        return {"error": str(exc)}


def get_access_token(api_key: str, api_secret: str, request_token: str) -> dict:
    """Step 5: POST /access-token with {"apiSecret": ...} in the body — not
    a checksum, unlike Kite Connect's own scheme. Returns
    `{"access_token": ...}` or `{"error": ...}`."""
    try:
        resp = requests.post(
            f"{_API_BASE}/access-token",
            params={"api_key": api_key, "request_token": request_token},
            json={"apiSecret": api_secret},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", body)
        if not data.get("access_token"):
            return {"error": "HDFC access-token exchange did not return an access_token"}
        return data
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "hdfc_get_access_token_failed", level="warning", error=str(exc))
        return {"error": str(exc)}


def _headers(api_key: str, access_token: str) -> dict:
    return {**_HEADERS, "api_key": api_key, "Authorization": access_token}


def _fetch_holdings(api_key: str, access_token: str) -> list[dict]:
    resp = requests.get(
        f"{_API_BASE}/portfolio/holdings", params={"api_key": api_key},
        headers=_headers(api_key, access_token), timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body if isinstance(body, list) else [])


def _fetch_tradebook(api_key: str, access_token: str) -> list[dict]:
    # Disclosed limitation: not independently confirmed — see module
    # docstring. Inferred from the confirmed holdings path's own
    # /portfolio/ prefix.
    resp = requests.get(
        f"{_API_BASE}/portfolio/tradebook", params={"api_key": api_key},
        headers=_headers(api_key, access_token), timeout=_TIMEOUT,
    )
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
