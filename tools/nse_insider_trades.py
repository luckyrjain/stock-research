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
import time
from datetime import date, datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com",
}

_MIN_VALUE_INR = 2_500_000  # ₹25L — drops small ESOP-style trades
_LOOKBACK_DAYS = 14

_INSIDER_CATEGORIES = ("promoter", "director")
_EXCLUDED_MODES = ("esop", "pledge", "gift", "bonus", "rights", "inter-se")


def _nse_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(_NSE_HEADERS)
    try:
        sess.get("https://www.nseindia.com", timeout=8)
        time.sleep(0.5)
    except Exception:
        pass
    return sess


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
        qty   = int(float(row.get("secAcq") or 0))
        value = float(row.get("secVal") or 0)
    except (TypeError, ValueError):
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


def _trade_to_article(row: dict) -> dict | None:
    parsed = _parse_pit_row(row)
    if not parsed:
        return None
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


def _parse_pit_date(date_str: str) -> str | None:
    for fmt in ("%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
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
    seen: set[str] = set()
    for row in raw:
        try:
            art = _trade_to_article(row)
        except Exception as exc:
            logger.debug("Skipping malformed insider-trade row: %s", exc)
            continue
        if not art or art["title"] in seen:
            continue
        seen.add(art["title"])
        articles.append(art)

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
    for row in raw:
        try:
            parsed = _parse_pit_row(row)
        except Exception as exc:
            logger.debug("Skipping malformed insider-trade row: %s", exc)
            continue
        if not parsed or parsed["symbol"] != sym:
            continue
        trades.append({k: v for k, v in parsed.items() if k != "symbol"})

    trades.sort(key=lambda t: t["date_iso"] or "", reverse=True)
    return {"symbol": sym, "trades": trades}


INSIDER_SOURCES = [
    ("NSE Insider Trades", "brokerage", "fetch_insider_trades"),
]

INSIDER_SCRAPERS: dict = {
    "NSE Insider Trades": fetch_insider_trades,
}
