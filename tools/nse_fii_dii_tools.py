"""Daily FII/DII (Foreign / Domestic Institutional Investor) net equity
flow — NSE's own end-of-day provisional trading-activity report. Market-
wide, not per-symbol, so it's not part of the six-task analysis pipeline
(`ALL_DATA_TASKS`) — `signals/macro.py` fetches and caches it once per TTL
window under a fixed pseudo-symbol, the same convention
`GET /api/market-picks/history` already uses to cache the Nifty benchmark
series under a `"NSEI"` pseudo-symbol.

NOTE: This module's scrape target (NSE's FII/DII trade-activity endpoint)
could not be verified against a live response in this sandbox — no
outbound internet (see CLAUDE.md's disclosed limitation). Parsing follows
the same defensive, never-raise convention as every other `tools/*.py`
module: a shape mismatch (NSE changed its response format) degrades to an
`{"error": ...}` dict exactly like a network failure would, rather than
raising into the signal engine.
"""
import requests

from observability import get_logger, log_event

LOGGER = get_logger("nse_fii_dii_tools")

_FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com",
}


def _nse_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(_NSE_HEADERS)
    try:
        sess.get("https://www.nseindia.com", timeout=8)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return sess


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def get_fii_dii_flow() -> dict:
    """Most recent day's net FII/DII equity flow (₹ Cr) from NSE.

    Returns {"date", "fii_net_cr", "dii_net_cr"} — either net figure is
    None (never guessed) if that category's row isn't present in NSE's
    response. Returns {"error": ...} only on a total fetch/parse failure.
    """
    try:
        sess = _nse_session()
        resp = sess.get(_FII_DII_URL, timeout=10)
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            return {"error": "no FII/DII rows returned"}

        fii_row = next((r for r in rows if str(r.get("category", "")).upper().startswith("FII")), None)
        dii_row = next((r for r in rows if str(r.get("category", "")).upper().startswith("DII")), None)
        if not fii_row and not dii_row:
            return {"error": "FII/DII rows not found in response"}

        date = (fii_row or dii_row or {}).get("date")
        return {
            "date": date,
            "fii_net_cr": _to_float((fii_row or {}).get("netValue")),
            "dii_net_cr": _to_float((dii_row or {}).get("netValue")),
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "fii_dii_fetch_failed", level="warning", error=str(exc))
        return {"error": str(exc)}
