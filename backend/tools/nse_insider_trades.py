"""
NSE insider trading (PIT / SAST disclosure) scraper for the market picks pipeline.

Promoter and director buys are among the strongest conviction signals — insiders
commit their own capital. Each qualifying disclosure is formatted as a
plain-language article so the extraction LLM can assign BUY/SELL direction.

Filters applied to cut ESOP/pledge noise:
- person category must be a promoter or director
- transaction must be a market/off-market buy or sell (no pledges, gifts, ESOPs)
- transaction value must be at least _MIN_VALUE_INR
"""

import logging
from datetime import date, datetime, timedelta, timezone

import requests

from tools._nse_session import get_nse_session

logger = logging.getLogger(__name__)

_MIN_VALUE_INR = 2_500_000  # ₹25L — drops small ESOP-style trades
_LOOKBACK_DAYS = 14

_INSIDER_CATEGORIES = ("promoter", "director")
_EXCLUDED_MODES = ("esop", "pledge", "gift", "bonus", "rights", "inter-se")


def _nse_session() -> requests.Session:
    return get_nse_session(timeout=8)


def _fmt_value(v: float) -> str:
    if v >= 10_000_000:
        return f"₹{v / 10_000_000:.1f} Cr"
    return f"₹{v / 100_000:.1f}L"


def _parse_pit_row(row: dict) -> dict | None:
    """Shared parse+noise-filter step for one NSE PIT disclosure row. Returns
    None if malformed or filtered out (wrong person category, an excluded
    acquisition mode, or below the minimum value). Used by both
    fetch_insider_trades() (market-wide LLM-article feed, for pick discovery)
    and fetch_insider_trades_for_symbol() (structured, one symbol, for
    single-stock research) so the two call sites can't drift on what counts
    as a "real" insider trade."""
    symbol   = (row.get("symbol") or "").upper().strip()
    person   = (row.get("acqName") or "").strip()
    category = (row.get("personCategory") or "").strip()
    txn_type = (row.get("tdpTransactionType") or row.get("transactionType") or "").strip()
    acq_mode = (row.get("acqMode") or "").strip()
    date_str = (row.get("intimDt") or row.get("date") or "").strip()

    try:
        qty   = int(float(str(row.get("secAcq") or 0).replace(",", "")))
        value = float(str(row.get("secVal") or 0).replace(",", ""))
    except (TypeError, ValueError, AttributeError):
        return None

    if not symbol or not person or qty <= 0 or value < _MIN_VALUE_INR:
        return None
    if not any(term in category.lower() for term in _INSIDER_CATEGORIES):
        return None
    if any(term in acq_mode.lower() for term in _EXCLUDED_MODES):
        return None

    txn = txn_type.lower()
    if txn.startswith(("buy", "acquisition")):
        action = "BUY"
    elif txn.startswith(("sell", "disposal", "sale")):
        action = "SELL"
    else:
        return None

    # NSE's own date_str formats aren't lexically sortable (month abbreviations
    # don't sort like calendar order) — date_iso exists purely so callers can
    # sort chronologically.
    return {
        "symbol":   symbol,
        "person":   person,
        "category": category,
        "action":   action,
        "quantity": qty,
        "value":    value,
        "date":     date_str,
        "date_iso": _parse_pit_date(date_str),
    }


def _article_from_parsed(parsed: dict) -> dict:
    """Formats an already-_parse_pit_row()-parsed disclosure as a
    plain-language LLM-extraction article. Split out of _trade_to_article()
    below so fetch_insider_trades() can parse+dedup once (on the real
    underlying fields — see its own dedup comment) and only then build the
    article, instead of building the article first and deduping on its own
    (coarser) title string."""
    symbol, person, category, action, qty, value, date_str = (
        parsed["symbol"], parsed["person"], parsed["category"], parsed["action"],
        parsed["quantity"], parsed["value"], parsed["date"],
    )

    verb = "bought" if action == "BUY" else "sold"
    title = (
        f"{person} ({category}) {verb} {_fmt_value(value)} worth of {symbol} "
        f"(NSE insider disclosure)"
    )
    summary = (
        f"NSE insider trading disclosure: {person}, {category} of {symbol}, "
        f"({action}) {qty:,} shares worth {_fmt_value(value)}. "
        f"{'Insider buying is a strong conviction signal.' if action == 'BUY' else 'Insider selling.'} "
        f"Date: {date_str}."
    )

    pub_iso = _parse_pit_date(date_str)

    return {
        "title":        title,
        "summary":      summary,
        "url":          "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading",
        "published_at": pub_iso,
    }


def _trade_to_article(row: dict) -> dict | None:
    parsed = _parse_pit_row(row)
    if not parsed:
        return None
    return _article_from_parsed(parsed)


def _parse_pit_date(date_str: str) -> str | None:
    for fmt in ("%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc).isoformat()
        except (ValueError, TypeError):
            pass
    return None


def _fetch_pit_rows(sess: requests.Session, lookback_days: int) -> list[dict]:
    to_d   = date.today()
    from_d = to_d - timedelta(days=lookback_days)
    try:
        r = sess.get(
            "https://www.nseindia.com/api/corporates-pit",
            params={
                "index":     "equities",
                "from_date": from_d.strftime("%d-%m-%Y"),
                "to_date":   to_d.strftime("%d-%m-%Y"),
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("data") or []
    except Exception:
        return []


def fetch_insider_trades() -> dict:
    """Fetch NSE promoter/director insider trades from the last two weeks,
    market-wide — feeds the market-picks discovery pipeline as one more LLM
    extraction source."""
    sess = _nse_session()
    raw = _fetch_pit_rows(sess, _LOOKBACK_DAYS)

    articles: list[dict] = []
    # Dedups on the same real-world-event fields fetch_insider_trades_for_symbol()
    # already dedups on (person, action, quantity, value, date), not on the
    # built article's own title string. The title format
    # (f"{person} ({category}) {verb} {_fmt_value(value)} worth of {symbol}...")
    # deliberately omits quantity and date for readability — it rounds value
    # to one decimal place in Cr/L buckets — so two genuinely distinct same-
    # symbol, same-person, same-day-category disclosures (e.g. two separate
    # purchases of ₹1.05 Cr and ₹1.08 Cr, both formatting as "₹1.1 Cr") would
    # otherwise collide on an identical title and the second would be
    # silently dropped, losing a real "insider is buying more" signal this
    # module exists to surface.
    seen: set[tuple] = set()
    for row in raw:
        try:
            parsed = _parse_pit_row(row)
        except Exception as exc:
            logger.debug("Skipping malformed insider-trade row: %s", exc)
            continue
        if not parsed:
            continue
        key = (parsed["symbol"], parsed["person"], parsed["action"], parsed["quantity"], parsed["value"], parsed["date"])
        if key in seen:
            continue
        seen.add(key)
        articles.append(_article_from_parsed(parsed))

    if raw and not articles:
        logger.debug(
            "NSE insider trades: %d raw rows returned but 0 parsed as articles "
            "(likely filtered out by category/mode/value thresholds)",
            len(raw),
        )

    return {"source": "NSE Insider Trades", "type": "brokerage", "articles": articles}


_SYMBOL_LOOKBACK_DAYS = 90  # wider than the 14d market-wide window: a single
                            # stock's insider activity is comparatively sparse,
                            # so a shorter window would too often show nothing


def fetch_insider_trades_for_symbol(symbol: str, lookback_days: int = _SYMBOL_LOOKBACK_DAYS) -> dict:
    """Structured (not LLM-article) insider-trading disclosures for one
    symbol, for the single-stock research flow — same NSE PIT feed and noise
    filters as fetch_insider_trades() (market-wide, for pick discovery), just
    scoped to one symbol over a longer window. Returns {"symbol", "trades": []}
    (never raises) if NSE has nothing, the request fails, or every row for
    this symbol was filtered out as noise — absent rather than guessed, same
    convention as every other tool in this codebase."""
    sym = symbol.upper().strip() if isinstance(symbol, str) else ""
    sess = _nse_session()
    raw = _fetch_pit_rows(sess, lookback_days)

    trades: list[dict] = []
    # fetch_insider_trades() (market-wide) dedupes by article title — an
    # undocumented divergence otherwise, since NSE's PIT feed can return the
    # same disclosure more than once (e.g. an amended/re-filed row). This
    # dedups on the same real-world-event fields the market-wide title
    # already bakes in (person, action, quantity, value, date).
    seen: set[tuple] = set()
    for row in raw:
        try:
            parsed = _parse_pit_row(row)
        except Exception as exc:
            logger.debug("Skipping malformed insider-trade row: %s", exc)
            continue
        if not parsed or parsed["symbol"] != sym:
            continue
        key = (parsed["person"], parsed["action"], parsed["quantity"], parsed["value"], parsed["date"])
        if key in seen:
            continue
        seen.add(key)
        trades.append({k: v for k, v in parsed.items() if k != "symbol"})

    trades.sort(key=lambda t: t["date_iso"] or "", reverse=True)
    return {"symbol": sym, "trades": trades}


INSIDER_SOURCES = [
    ("NSE Insider Trades", "brokerage", "fetch_insider_trades"),
]

INSIDER_SCRAPERS: dict = {
    "NSE Insider Trades": fetch_insider_trades,
}
