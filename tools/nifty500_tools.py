"""
NIFTY 500 constituent list fetcher — the "main NSE/BSE universe" bound for
screener_pipeline.py's stored-metrics table (GET /api/screener). NIFTY 500
(NSE's own published index membership) is used rather than the full NSE
equity master (_nse_master.txt, ~2000 symbols, already used elsewhere in this
codebase for symbol validation): a daily per-stock yfinance .info scrape —
this codebase's heaviest documented per-symbol call, see sme_ema_pipeline.py's
own note on why it deliberately avoids that call for "hundreds of SME
stocks" — is only reasonable at a bounded, curated scale. NIFTY 500 already
covers the vast majority of stocks anyone would realistically screen for.

Cached 24 h, same convention as tools/sme_tools.py's stock-list fetchers.

**Disclosed limitation**: the exact NSE archive URL and CSV column layout for
the NIFTY 500 constituent list was not verified against a live response in
this sandbox (no outbound internet — same disclosure pattern already used for
the other NSE/BSE scrapers in this codebase, e.g. tools/sme_tools.py,
tools/nse_fii_dii_tools.py). Parsing is defensive: a missing/renamed column or
an unreachable URL degrades to an empty list (never a partial or guessed
universe), so worth spot-checking against a live response before this ships
to a real deployment.
"""

import csv
import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests

from tools._nse_session import get_nse_session

logger = logging.getLogger(__name__)

_CACHE_PATH = Path("output/_nifty500_master.json")
_CACHE_TTL_HOURS = 24
# NIFTY 500 has ~500 constituents by construction. A truncated-but-nonempty
# response (a partial download, a paginated/rate-limited NSE response) would
# otherwise be silently cached and treated as complete — worse than the
# already-handled zero-rows case, since a screener built on a truncated
# universe looks correct while quietly missing most of the market. Set well
# below 500 to tolerate real index-reconstitution drift.
_MIN_PLAUSIBLE_COUNT = 400

_NIFTY500_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"


def _nse_session() -> requests.Session:
    return get_nse_session(timeout=10, accept="text/csv,application/json,*/*")


def _is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=_CACHE_TTL_HOURS)


def _save_cache(data: list[dict]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(data))


def get_nifty500_constituents(force: bool = False) -> list[dict]:
    """Return NIFTY 500 constituent stocks as
    [{"symbol", "company_name", "industry", "isin"}, ...]. Never raises —
    returns [] (or a stale cache, logged as such) on any failure. Cached 24 h.
    """
    if not force and _is_fresh(_CACHE_PATH):
        return json.loads(_CACHE_PATH.read_text())

    try:
        session = _nse_session()
        resp = session.get(_NIFTY500_CSV_URL, timeout=15)
        resp.raise_for_status()

        reader = csv.DictReader(io.StringIO(resp.text))
        stocks = []
        for row in reader:
            symbol = (row.get("Symbol") or "").strip().upper()
            if not symbol:
                continue
            stocks.append({
                "symbol":       symbol,
                "company_name": (row.get("Company Name") or "").strip() or None,
                "industry":     (row.get("Industry") or "").strip() or None,
                "isin":         (row.get("ISIN Code") or "").strip() or None,
            })

        if len(stocks) >= _MIN_PLAUSIBLE_COUNT:
            _save_cache(stocks)
            logger.info("NIFTY 500: fetched %d constituents", len(stocks))
            return stocks
        if stocks:
            logger.warning(
                "NIFTY 500: fetch returned suspiciously few rows (%d, expected at least %d) — "
                "treating as a failed fetch rather than caching a truncated universe",
                len(stocks), _MIN_PLAUSIBLE_COUNT,
            )
        else:
            logger.warning("NIFTY 500: fetch returned no usable rows")
    except Exception as exc:
        logger.warning("NIFTY 500 fetch failed: %s", exc)

    if _CACHE_PATH.exists():
        logger.warning("NIFTY 500: using stale cache")
        return json.loads(_CACHE_PATH.read_text())
    return []
