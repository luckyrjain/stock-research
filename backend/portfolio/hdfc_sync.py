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

Every endpoint path/param above came from a real, working set of curl
commands (not public docs — developer.hdfcsec.com's own docs site is a
JS-rendered SPA this sandbox can't render). Response *field names* are a
mix of confirmed and inferred:

- **Confirmed against a real account**: step 1's response shape (flat,
  camelCase, no `"data"` wrapper — `{"tokenId": "..."}`, not
  `{"data": {"token_id": ...}}`), and the `portfolio/holdings` endpoint.
- **Inferred, not independently confirmed**: steps 3 and 5's response
  field names (`requestToken`, `accessToken`) — guessed by matching step
  1's confirmed flat/camelCase pattern, not verified live. Each degrades
  to a clean `{"error": ...}` if the guess is wrong (see submit_otp()'s
  and get_access_token()'s own docstrings), never a `KeyError`.
- **Endpoint path confirmed, response shape not**: the tradebook endpoint
  is `GET /oapi/v1/trades` (confirmed against a real account — NOT
  `/portfolio/tradebook`, the original guess, which 404s). Its response
  *field names* (what `_normalize_trade()` reads —
  `trade_id`/`trading_symbol`/`transaction_type`/etc.) are still
  unconfirmed. If wrong, `_normalize_trade()` degrades that one trade to
  skipped (logged), never a fabricated value.

`sync_account()` fetches holdings and trades **independently** — a bad
guess in one (as the original tradebook path was) can never sink the
other; see `sync_account()`'s own docstring.

Spot-check trade normalization and the OTP/token-exchange steps' response
field names against a live account before fully trusting them in
production.
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
from tools.securities_master import get_full_securities_master, resolve_symbol

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
    never raises, matching this codebase's tools/*.py convention.

    Confirmed against a real account: the response is flat, camelCase, no
    `"data"` wrapper — `{"tokenId": "..."}`. Normalized to `token_id` here
    so every caller in this codebase (routes/portfolio_aggregator.py,
    tests) can stay on this module's own snake_case convention rather than
    HDFC's own field-naming style leaking through."""
    try:
        resp = requests.get(
            f"{_API_BASE}/login", params={"api_key": api_key}, headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        token_id = body.get("tokenId")
        if not token_id:
            return {"error": "HDFC login did not return a tokenId"}
        return {"token_id": token_id}
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
    `{"request_token": ...}` or `{"error": ...}`.

    **Not independently confirmed** — unlike start_login()'s response shape
    (confirmed against a real account: flat, camelCase, no `"data"`
    wrapper), this one's exact field name is inferred by matching that same
    pattern (`requestToken`), not verified live. If wrong, this degrades to
    a clean `{"error": ...}` here (never a KeyError) — spot-check against a
    real OTP before relying on this in production."""
    try:
        resp = requests.post(
            f"{_API_BASE}/twofa/validate",
            params={"api_key": api_key, "token_id": token_id},
            json={"answer": otp},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        request_token = body.get("requestToken")
        if not request_token:
            return {"error": "HDFC OTP verification did not return a requestToken"}
        return {"request_token": request_token}
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
    `{"access_token": ...}` or `{"error": ...}`.

    **Not independently confirmed** — same disclosure as submit_otp():
    inferred as `accessToken` by matching start_login()'s confirmed
    flat/camelCase shape, not verified live. Degrades to a clean
    `{"error": ...}` if wrong, never a KeyError — spot-check against a
    real account before relying on this in production."""
    try:
        resp = requests.post(
            f"{_API_BASE}/access-token",
            params={"api_key": api_key, "request_token": request_token},
            json={"apiSecret": api_secret},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        access_token = body.get("accessToken")
        if not access_token:
            return {"error": "HDFC access-token exchange did not return an accessToken"}
        return {"access_token": access_token}
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
    # Endpoint path confirmed against a real account — NOT /portfolio/tradebook
    # (the original guess, 404s), and not under /portfolio/ at all. Response
    # field names (what _normalize_trade() below reads) are still unconfirmed
    # — see module docstring.
    resp = requests.get(
        f"{_API_BASE}/trades", params={"api_key": api_key},
        headers=_headers(api_key, access_token), timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body if isinstance(body, list) else [])


def _resolve_hdfc_symbol(engine, record: dict, master: list[dict]) -> tuple[str, str | None] | None:
    """HDFC's real holdings response (confirmed against a real account, see
    module docstring) has **no trading-symbol field at all** — only `isin`,
    `security_id` (an internal id, often blank per HDFC's own example
    response), and `company_name`. Unlike Kite/Paytm, whose `tradingsymbol`
    is already the canonical NSE/BSE symbol their own order executed
    against (see broker_sync_common.find_or_create_asset()'s own docstring
    on why it deliberately skips ISIN resolution for those two), HDFC needs
    the same isin/broker-code-to-canonical-symbol resolution csv_import.py
    already does for a raw broker CSV export — reused here via
    tools.securities_master.resolve_symbol() rather than reinvented.

    Falls back to the ISIN itself as the symbol (never dropping a real
    holding/trade over an unresolved ISIN) when resolution can't find a
    confident match — logged, not silent. Returns None only when there's
    truly no identifier at all to key an asset on (no isin, no
    security_id)."""
    isin = record.get("isin") or None
    code = record.get("security_id") or ""
    if not isin and not code:
        return None

    resolved = resolve_symbol(engine, code, company_name=record.get("company_name"), isin=isin, master=master)
    if resolved["confidence"] in ("isin", "exact"):
        return resolved["symbol"], resolved["exchange"]

    fallback_symbol = isin or code
    log_event(
        LOGGER, "hdfc_symbol_unresolved", level="warning",
        isin=isin, security_id=code, candidate_name=resolved.get("candidate_name"),
    )
    return fallback_symbol, record.get("exchange")


def _normalize_holding(h: dict, engine, master: list[dict]) -> dict | None:
    resolved = _resolve_hdfc_symbol(engine, h, master)
    quantity = broker_sync_common.to_decimal(h.get("quantity"))
    if resolved is None or quantity is None:
        log_event(LOGGER, "hdfc_holding_skipped", level="warning", holding=str(h)[:200])
        return None
    symbol, exchange = resolved
    return {
        "symbol": symbol,
        "exchange": exchange,
        "quantity": quantity,
        "avg_price": broker_sync_common.to_decimal(h.get("average_price") or h.get("buy_avg_price")),
        "last_price": broker_sync_common.to_decimal(h.get("close_price") or h.get("last_price") or h.get("ltp")),
    }


def _normalize_trade(t: dict, engine, master: list[dict]) -> dict | None:
    # Disclosed limitation: the tradebook response shape is unconfirmed
    # (see module docstring) — this tries the same trading_symbol/symbol
    # fields the original guess assumed FIRST, since holdings' real shape
    # isn't necessarily identical to trades'; only falls through to the
    # same isin/security_id resolution holdings needs if those are absent,
    # so this degrades gracefully either way once a real response is seen.
    trade_id = t.get("trade_id") or t.get("exchange_trade_id")
    symbol = t.get("trading_symbol") or t.get("symbol")
    exchange = t.get("exchange")
    if not symbol:
        resolved = _resolve_hdfc_symbol(engine, t, master)
        if resolved is not None:
            symbol, exchange = resolved
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
        "exchange": exchange, "side": side, "quantity": quantity,
        "price": price, "trade_date": trade_date,
    }


def sync_account(engine, account_id: int, access_token: str, api_key: str, owner: tuple | None = None) -> dict:
    """Syncs one connected HDFC Securities account's holdings + tradebook
    into the existing Portfolio Aggregator schema. Read-only. Returns a
    summary dict; never raises — a broker-API hiccup degrades to an
    {"error": ...} result, same convention as every tools/*.py module.

    Holdings and tradebook are fetched **independently** — the tradebook
    endpoint (`/portfolio/tradebook`) is still an inferred guess (see this
    module's own docstring), unlike the confirmed holdings endpoint. If
    that guess is wrong, trades sync as zero (logged) while holdings —
    already proven to work — still syncs normally; only a genuine failure
    of *both* fetches degrades the whole call to {"error": ...}, since at
    that point there's nothing left to write.

    `api_key` is this connection's own registered app key (broker_connections.api_key),
    never a deployment-wide env var — see db/models.py's broker_connections comment.

    `owner` is optional, passed straight through to
    broker_sync_common.sync_holdings() — see its own docstring."""
    try:
        raw_holdings = broker_sync_common.call_with_backoff(
            lambda: _fetch_holdings(api_key, access_token), broker=BROKER_NAME,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raw_holdings = None
        log_event(LOGGER, "hdfc_holdings_fetch_failed", level="warning",
                   account_id=account_id, error=str(exc))

    try:
        raw_trades = broker_sync_common.call_with_backoff(
            lambda: _fetch_tradebook(api_key, access_token), broker=BROKER_NAME,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raw_trades = None
        log_event(LOGGER, "hdfc_tradebook_fetch_failed", level="warning",
                   account_id=account_id, error=str(exc))

    if raw_holdings is None and raw_trades is None:
        return {"error": "both holdings and tradebook fetches failed"}

    # Loaded once, shared across every holding/trade in this sync — each
    # resolve_symbol() call is a full securities-table scan plus a
    # fuzzy-match candidate rebuild, so this mirrors csv_import.py's own
    # "load once per import, not once per row" convention. Holdings always
    # need it (HDFC's real holdings response has no trading-symbol field at
    # all); only skipped when there's genuinely nothing to resolve.
    master = get_full_securities_master(engine) if (raw_holdings or raw_trades) else None

    today = datetime.now(timezone.utc).date()
    with engine.begin() as conn:
        summary = {}
        summary.update(broker_sync_common.sync_holdings(
            conn, account_id,
            [_normalize_holding(h, engine, master) for h in (raw_holdings or [])],
            _META_SOURCE, today,
            owner=owner,
        ))
        summary.update(broker_sync_common.sync_trades(
            conn, account_id,
            [_normalize_trade(t, engine, master) for t in (raw_trades or [])],
            _META_SOURCE,
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
