"""
NSE Bulk Deal and Block Deal scraper for the market picks pipeline.

Bulk deals  — trades ≥ 0.5 % of a company's equity in a single transaction
Block deals — large trades executed via the exchange's 8:45–9:00 AM block window

Both are strong institutional activity signals. Each deal is formatted as a
plain-language article so the extraction LLM can assign BUY/SELL direction.
"""

import time
from datetime import datetime, timezone

import requests

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com",
}

_MIN_BULK_QTY  = 50_000   # 50K shares
_MIN_BLOCK_QTY = 100_000  # 1L shares


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


def _deal_to_article(deal: dict, deal_type: str) -> dict | None:
    symbol    = (deal.get("symbol") or "").upper().strip()
    client    = (deal.get("clientName") or deal.get("client_name") or "").strip()
    qty_raw   = deal.get("bdQty") or deal.get("qty") or 0
    price_raw = deal.get("bdPrice") or deal.get("price") or 0
    buy_sell  = (deal.get("buySell") or deal.get("buy_sell") or "").strip().upper()
    date_str  = (deal.get("date") or deal.get("deal_date") or "").strip()

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

    pub_iso: str | None = None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            pub_iso = datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc).isoformat()
            break
        except ValueError:
            pass

    return {
        "title":        title,
        "summary":      summary,
        "url":          "https://www.nseindia.com/companies-listing/corporate-filings-bulk-block-deals",
        "published_at": pub_iso,
    }


def _fetch_deals(sess: requests.Session, endpoint: str, min_qty: int, deal_type: str) -> list[dict]:
    try:
        r = sess.get(f"https://www.nseindia.com/api/{endpoint}", timeout=10)
        r.raise_for_status()
        raw = r.json().get("data", [])
    except Exception:
        return []

    articles: list[dict] = []
    seen: set[str] = set()
    for deal in raw:
        try:
            qty = int(deal.get("bdQty") or deal.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if qty < min_qty:
            continue
        art = _deal_to_article(deal, deal_type)
        if not art:
            continue
        key = art["title"]
        if key in seen:
            continue
        seen.add(key)
        articles.append(art)

    return articles


def fetch_nse_bulk_block_deals() -> dict:
    """Fetch NSE bulk deals and block deals (recent trading days)."""
    sess = _nse_session()
    articles = (
        _fetch_deals(sess, "bulk-deals",  _MIN_BULK_QTY,  "Bulk Deal") +
        _fetch_deals(sess, "block-deals", _MIN_BLOCK_QTY, "Block Deal")
    )
    return {"source": "NSE Bulk/Block Deals", "type": "brokerage", "articles": articles}


NSE_BULK_SOURCES = [
    ("NSE Bulk/Block Deals", "brokerage", "fetch_nse_bulk_block_deals"),
]

NSE_BULK_SCRAPERS: dict = {
    "NSE Bulk/Block Deals": fetch_nse_bulk_block_deals,
}
