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

from core.observability import get_logger, log_event
from tools._nse_session import get_nse_session

LOGGER = get_logger("nse_fii_dii_tools")

_FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

# signals/macro.py's ±500/±3000 Cr thresholds assume NSE's `netValue` field
# is already in ₹ Crore, which could not be verified against a live response
# in this sandbox (see this module's own docstring). A single day's net
# FII or DII equity flow beyond this ceiling would be extraordinary for the
# real Indian market — more likely a unit mismatch (e.g. raw rupees instead
# of crore) than a genuine figure — so it's dropped ("never invent") rather
# than fed to the signal engine as if it were trustworthy. Same "bound
# rather than trust an unverified format guess" pattern as
# tools/nse_tools.py's _percent_from_ambiguous_value.
_PLAUSIBLE_MAX_NET_CR = 100_000.0


def _nse_session() -> requests.Session:
    return get_nse_session(timeout=8)


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if abs(parsed) <= _PLAUSIBLE_MAX_NET_CR else None


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
