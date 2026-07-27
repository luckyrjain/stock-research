"""
NSE Bulk Deal and Block Deal scraper for the market picks pipeline.

Bulk deals  — trades ≥ 0.5 % of a company's equity in a single transaction
Block deals — large trades executed via the exchange's 8:45–9:00 AM block window

Both are strong institutional activity signals. Each deal is formatted as a
plain-language article so the extraction LLM can assign BUY/SELL direction.
"""

import logging
from datetime import datetime, timezone

import requests

from tools._nse_session import get_nse_session

logger = logging.getLogger(__name__)

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


def _to_int(value: object) -> int:
    """NSE's quantity fields can come back comma-formatted (e.g. "5,00,000")
    — stripping commas before int() avoids silently misreading an otherwise
    valid row as unparseable (a bare int()/float() call raises on a comma).
    Deliberately does NOT swallow a genuine parse failure — callers already
    wrap this in their own try/except and log+skip a truly malformed row,
    the same convention as elsewhere in this module."""
    return int(float(str(value if value is not None else 0).replace(",", "")))


def _nse_session() -> requests.Session:
    return get_nse_session(timeout=8)


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
        qty   = int(float(str(qty_raw).replace(",", "")))
        price = float(str(price_raw).replace(",", ""))
    except (TypeError, ValueError, AttributeError):
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
        except (ValueError, TypeError):
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


def _article_from_parsed_deal(parsed: dict, deal_type: str) -> dict:
    """Formats an already-_parse_deal_row()-parsed deal as a plain-language
    LLM-extraction article. Split out of _deal_to_article() below so
    _fetch_deals() can parse+dedup once (on the real underlying fields — see
    its own dedup comment) and only then build the article, instead of
    building the article first and deduping on its own (coarser) title
    string."""
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


def _deal_to_article(deal: dict, deal_type: str) -> dict | None:
    parsed = _parse_deal_row(deal, deal_type)
    if not parsed:
        return None
    return _article_from_parsed_deal(parsed, deal_type)


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
    # Dedups on the same real-world-event fields fetch_bulk_block_deals_for_symbol()
    # already dedups on (client, action, quantity, price, deal_type, date),
    # not on the built article's own title string — the title omits date
    # entirely and rounds quantity via _fmt_qty() for large values, so two
    # genuinely distinct same-client/same-symbol deals on different dates (or
    # with a similar-but-not-identical quantity that rounds the same) could
    # otherwise collide on an identical title and the second would be
    # silently dropped.
    seen: set[tuple] = set()
    for deal in raw:
        try:
            qty = _to_int(_first(deal, _QTY_KEYS))
            if qty < min_qty:
                continue
            parsed = _parse_deal_row(deal, deal_type)
        except Exception as exc:
            logger.debug("Skipping malformed %s row: %s", deal_type, exc)
            continue
        if not parsed:
            continue
        key = (
            parsed["symbol"], parsed["client"], parsed["action"],
            parsed["quantity"], parsed["price"], parsed["deal_type"], parsed["date"],
        )
        if key in seen:
            continue
        seen.add(key)
        articles.append(_article_from_parsed_deal(parsed, deal_type))

    if raw and not articles:
        # debug, not warning — a routine day where every returned deal fell
        # below min_qty (small deals are common) looks identical to a real
        # schema drift from this check's own perspective; nse_insider_trades.py's
        # equivalent check already uses debug for the same reason (see its
        # own comment) — matches that established "don't manufacture noise
        # from the expected common case" convention.
        logger.debug(
            "%s: NSE returned %d raw rows but 0 parsed as articles "
            "(likely filtered out by the min_qty threshold)",
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
    sym = symbol.upper().strip() if isinstance(symbol, str) else ""
    sess = _nse_session()
    deals: list[dict] = []
    # _fetch_deals() (market-wide) dedupes by article title — an
    # undocumented divergence otherwise, since NSE's bulk/block-deals
    # endpoints can return the same deal more than once. This dedups on the
    # same real-world-event fields the market-wide title already bakes in
    # (client, action, quantity, price, deal_type, date).
    seen: set[tuple] = set()
    for endpoint, min_qty, deal_type in (
        ("bulk-deals",  _MIN_BULK_QTY,  "Bulk Deal"),
        ("block-deals", _MIN_BLOCK_QTY, "Block Deal"),
    ):
        raw = _fetch_deal_rows(sess, endpoint)
        for deal in raw:
            try:
                qty = _to_int(_first(deal, _QTY_KEYS))
                if qty < min_qty:
                    continue
                parsed = _parse_deal_row(deal, deal_type)
            except Exception as exc:
                logger.debug("Skipping malformed %s row: %s", deal_type, exc)
                continue
            if not parsed or parsed["symbol"] != sym:
                continue
            key = (
                parsed["client"], parsed["action"], parsed["quantity"],
                parsed["price"], parsed["deal_type"], parsed["date"],
            )
            if key in seen:
                continue
            seen.add(key)
            deals.append({k: v for k, v in parsed.items() if k != "symbol"})

    deals.sort(key=lambda d: d["date_iso"] or "", reverse=True)
    return {"symbol": sym, "deals": deals}


NSE_BULK_SOURCES = [
    ("NSE Bulk/Block Deals", "brokerage", "fetch_nse_bulk_block_deals"),
]

NSE_BULK_SCRAPERS: dict = {
    "NSE Bulk/Block Deals": fetch_nse_bulk_block_deals,
}
