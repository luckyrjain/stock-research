"""
NSE Bulk Deal and Block Deal scraper for the market picks pipeline.

Bulk deals  — trades ≥ 0.5 % of a company's equity in a single transaction
Block deals — large trades executed via the exchange's 8:45–9:00 AM block window

Both are strong institutional activity signals. Each deal is formatted as a
plain-language article so the extraction LLM can assign BUY/SELL direction.
"""

import logging
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com",
}

_MIN_BULK_QTY  = 50_000   # 50K shares
_MIN_BLOCK_QTY = 100_000  # 1L shares

# NSE's bulk-deals and block-deals endpoints have historically used different
# field-naming conventions (and NSE has changed them across API versions), so
# every field is looked up through a list of known aliases rather than a
# single hard-coded key.
_SYMBOL_KEYS    = ("symbol", "BD_SYMBOL", "BQ_SYMBOL")
_CLIENT_KEYS    = ("clientName", "client_name", "BD_CLIENT_NAME", "BQ_CLIENT_NAME")
_QTY_KEYS       = ("bdQty", "qty", "BD_QTY_TRD", "BQ_QTY_TRD")
_PRICE_KEYS     = ("bdPrice", "price", "BD_TP_WATP", "BQ_TP_WATP")
_BUY_SELL_KEYS  = ("buySell", "buy_sell", "BD_BUY_SELL", "BQ_BUY_SELL")
_DATE_KEYS      = ("date", "deal_date", "BD_DT_DATE", "BQ_DT_DATE")


def _first(deal: dict, keys: tuple) -> object | None:
    for key in keys:
        val = deal.get(key)
        if val not in (None, ""):
            return val
    return None


def _nse_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(_NSE_HEADERS)
    try:
        sess.get("https://www.nseindia.com", timeout=8)
        time.sleep(0.5)
    except Exception:
        pass
    return sess


def _fmt_qty(n: int) -> str:
    if n >= 10_000_000:
        return f"{n / 10_000_000:.1f} Cr"
    if n >= 100_000:
        return f"{n / 100_000:.1f}L"
    return f"{n:,}"


def _parse_deal_row(deal: dict, deal_type: str) -> dict | None:
    """Shared parse step for one bulk/block deal row (no quantity threshold —
    callers apply their own min_qty, same separation the original code had).
    Returns None if malformed. Used by both _deal_to_article() (market-wide
    LLM-article feed, for pick discovery) and fetch_bulk_block_deals_for_symbol()
    (structured, one symbol, for single-stock research)."""
    symbol    = str(_first(deal, _SYMBOL_KEYS) or "").upper().strip()
    client    = str(_first(deal, _CLIENT_KEYS) or "").strip()
    qty_raw   = _first(deal, _QTY_KEYS) or 0
    price_raw = _first(deal, _PRICE_KEYS) or 0
    buy_sell  = str(_first(deal, _BUY_SELL_KEYS) or "").strip().upper()
    date_str  = str(_first(deal, _DATE_KEYS) or "").strip()

    try:
        qty   = int(qty_raw)
        price = float(price_raw)
    except (TypeError, ValueError):
        return None

    if not symbol or not client or qty <= 0 or price <= 0:
        return None

    action = "BUY" if buy_sell.startswith("B") else "SELL" if buy_sell.startswith("S") else None
    if not action:
        return None

    pub_iso: str | None = None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            pub_iso = datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc).isoformat()
            break
        except ValueError:
            pass

    return {
        "symbol":     symbol,
        "client":     client,
        "action":     action,
        "quantity":   qty,
        "price":      price,
        "deal_type":  deal_type,
        "date":       date_str,
        "date_iso":   pub_iso,
    }


def _deal_to_article(deal: dict, deal_type: str) -> dict | None:
    parsed = _parse_deal_row(deal, deal_type)
    if not parsed:
        return None
    symbol, client, action, qty, price, date_str = (
        parsed["symbol"], parsed["client"], parsed["action"],
        parsed["quantity"], parsed["price"], parsed["date"],
    )

    verb = "bought" if action == "BUY" else "sold"
    title = (
        f"{client} {verb} {_fmt_qty(qty)} shares of {symbol} "
        f"at ₹{price:,.0f} (NSE {deal_type})"
    )
    summary = (
        f"NSE {deal_type}: {client} ({action}) {_fmt_qty(qty)} shares of {symbol} "
        f"at ₹{price:,.2f}. "
        f"{'Institutional buy signal.' if action == 'BUY' else 'Large institutional sell.'} "
        f"Date: {date_str}."
    )

    return {
        "title":        title,
        "summary":      summary,
        "url":          "https://www.nseindia.com/companies-listing/corporate-filings-bulk-block-deals",
        "published_at": parsed["date_iso"],
    }


def _fetch_deal_rows(sess: requests.Session, endpoint: str) -> list[dict]:
    try:
        r = sess.get(f"https://www.nseindia.com/api/{endpoint}", timeout=10)
        r.raise_for_status()
        return r.json().get("data") or []
    except Exception:
        return []


def _fetch_deals(sess: requests.Session, endpoint: str, min_qty: int, deal_type: str) -> list[dict]:
    raw = _fetch_deal_rows(sess, endpoint)

    articles: list[dict] = []
    seen: set[str] = set()
    for deal in raw:
        try:
            qty = int(_first(deal, _QTY_KEYS) or 0)
            if qty < min_qty:
                continue
            art = _deal_to_article(deal, deal_type)
        except Exception as exc:
            logger.debug("Skipping malformed %s row: %s", deal_type, exc)
            continue
        if not art:
            continue
        key = art["title"]
        if key in seen:
            continue
        seen.add(key)
        articles.append(art)

    if raw and not articles:
        logger.warning(
            "%s: NSE returned %d raw rows but 0 parsed as articles — "
            "field names may have drifted from the expected schema",
            deal_type, len(raw),
        )

    return articles


def fetch_nse_bulk_block_deals() -> dict:
    """Fetch NSE bulk deals and block deals (recent trading days)."""
    sess = _nse_session()
    articles = (
        _fetch_deals(sess, "bulk-deals",  _MIN_BULK_QTY,  "Bulk Deal") +
        _fetch_deals(sess, "block-deals", _MIN_BLOCK_QTY, "Block Deal")
    )
    return {"source": "NSE Bulk/Block Deals", "type": "brokerage", "articles": articles}


def fetch_bulk_block_deals_for_symbol(symbol: str) -> dict:
    """Structured (not LLM-article) bulk/block deals for one symbol, for the
    single-stock research flow — same NSE endpoints and quantity thresholds
    as fetch_nse_bulk_block_deals() (market-wide, for pick discovery), just
    filtered to one symbol. NSE's own bulk-deals/block-deals endpoints only
    ever return "recent trading days" (no date-range parameter to widen),
    unlike the PIT insider-trades endpoint — so unlike
    fetch_insider_trades_for_symbol(), there's no wider lookback to request
    here; most stocks simply won't have a recent deal, which is the expected
    common case, not an error. Returns {"symbol", "deals": []} (never
    raises)."""
    sym = symbol.upper().strip()
    sess = _nse_session()
    deals: list[dict] = []
    for endpoint, min_qty, deal_type in (
        ("bulk-deals",  _MIN_BULK_QTY,  "Bulk Deal"),
        ("block-deals", _MIN_BLOCK_QTY, "Block Deal"),
    ):
        raw = _fetch_deal_rows(sess, endpoint)
        for deal in raw:
            try:
                qty = int(_first(deal, _QTY_KEYS) or 0)
                if qty < min_qty:
                    continue
                parsed = _parse_deal_row(deal, deal_type)
            except Exception as exc:
                logger.debug("Skipping malformed %s row: %s", deal_type, exc)
                continue
            if not parsed or parsed["symbol"] != sym:
                continue
            deals.append({k: v for k, v in parsed.items() if k != "symbol"})

    deals.sort(key=lambda d: d["date_iso"] or "", reverse=True)
    return {"symbol": sym, "deals": deals}


NSE_BULK_SOURCES = [
    ("NSE Bulk/Block Deals", "brokerage", "fetch_nse_bulk_block_deals"),
]

NSE_BULK_SCRAPERS: dict = {
    "NSE Bulk/Block Deals": fetch_nse_bulk_block_deals,
}
