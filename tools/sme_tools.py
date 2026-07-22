"""
Fetchers for Indian SME stocks:
  - NSE Emerge:  /api/live-analysis-emerge (539 stocks, series SM)
  - BSE SME:     BSE public API (best-effort; skipped gracefully if unavailable)

NSE list is cached for 24 h under output/.
Deduplication by ISIN where available; NSE records preferred over BSE.

KNOWN LIMITATION: the NSE Emerge endpoint (fetch_nse_emerge_stocks) does not
return an ISIN, so every NSE record has isin=None. Dedup therefore never
matches an NSE record against a BSE one in practice — a company listed on
both NSE Emerge and BSE SME will currently appear twice in get_all_sme_stocks.
Fixing this needs a separate ISIN lookup (e.g. NSE equity master or Screener.in)
cross-referenced by symbol/name; deferred as out of scope for the initial
golden-cross screener.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_NSE_EMERGE_CACHE = Path("output/_nse_emerge_master.json")
_BSE_SME_CACHE    = Path("output/_bse_sme_master.json")
_CACHE_TTL_HOURS  = 24

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.nseindia.com",
    "Accept":     "application/json",
}
_SCREENER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
_BSE_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":         "https://www.bseindia.com/",
    "Origin":          "https://www.bseindia.com",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=_CACHE_TTL_HOURS)


def _save_cache(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _enrich_names(stocks: list[dict]) -> list[dict]:
    """Fill in missing names via Screener.in sequentially. Returns same list."""
    import time

    to_fetch = [s for s in stocks if not s.get("name")]
    if not to_fetch:
        return stocks

    for s in to_fetch:
        try:
            r = requests.get(
                "https://www.screener.in/api/company/search/",
                params={"q": s["symbol"]},
                headers=_SCREENER_HEADERS,
                timeout=8,
            )
            results = r.json() if r.ok else []
            if results:
                s["name"] = results[0].get("name") or None
        except Exception:
            pass
        time.sleep(0.1)

    return stocks


def fetch_nse_emerge_stocks(force: bool = False) -> list[dict]:
    """Return all NSE Emerge (SME) stocks via NSE's live-analysis API. Cached 24 h."""
    if not force and _is_fresh(_NSE_EMERGE_CACHE):
        return json.loads(_NSE_EMERGE_CACHE.read_text())

    sess = requests.Session()
    sess.headers.update(_NSE_HEADERS)
    # Prime NSE session cookie
    try:
        sess.get("https://www.nseindia.com", timeout=6)
    except Exception:
        pass

    try:
        r = sess.get(
            "https://www.nseindia.com/api/live-analysis-emerge",
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        rows = data.get("data", [])
        stocks = [
            {
                "symbol":   row["symbol"].strip().upper(),
                "name":     None,   # not provided by this endpoint
                "isin":     None,
                "series":   row.get("series", "SM").strip().upper(),
                "exchange": "NSE",
            }
            for row in rows
            if row.get("symbol", "").strip()
        ]
        if stocks:
            logger.info("NSE Emerge: fetched %d stocks, enriching names…", len(stocks))
            stocks = _enrich_names(stocks)
            _save_cache(_NSE_EMERGE_CACHE, stocks)
            logger.info("NSE Emerge: cached %d stocks with names", len(stocks))
        return stocks
    except Exception as exc:
        logger.warning("NSE Emerge fetch failed: %s", exc)
        if _NSE_EMERGE_CACHE.exists():
            return json.loads(_NSE_EMERGE_CACHE.read_text())
        return []


def fetch_bse_sme_stocks(force: bool = False) -> list[dict]:
    """Return all BSE SME stocks (Groups M + MS). Cached 24 h."""
    if not force and _is_fresh(_BSE_SME_CACHE):
        return json.loads(_BSE_SME_CACHE.read_text())

    base_url = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w/"
    base_params = "Scripcode=&segment=Equity&status=Active&industrycode=&isincode=&maskstatus="

    stocks: list[dict] = []
    seen_codes: set[str] = set()

    for group in ["M", "MS"]:
        try:
            r = requests.get(f"{base_url}?{base_params}&Group={group}", headers=_BSE_HEADERS, timeout=15, allow_redirects=False)
            r.raise_for_status()
            rows = r.json()
            if not isinstance(rows, list):
                rows = rows.get("Table", []) if isinstance(rows, dict) else []
            for item in rows:
                code = str(item.get("SCRIP_CD", "")).strip()
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)
                stocks.append({
                    "symbol":   code,
                    "name":     str(item.get("Scrip_Name", "")).strip() or None,
                    "isin":     str(item.get("ISIN_NUMBER", "")).strip() or None,
                    "series":   group,
                    "exchange": "BSE",
                })
            logger.info("BSE SME Group=%s: %d stocks", group, len(rows) if isinstance(rows, list) else 0)
        except Exception as exc:
            logger.warning("BSE SME Group=%s fetch failed: %s", group, exc)

    if stocks:
        _save_cache(_BSE_SME_CACHE, stocks)
        logger.info("BSE SME: fetched %d total stocks", len(stocks))
        return stocks

    if _BSE_SME_CACHE.exists():
        logger.warning("BSE SME: using stale cache")
        return json.loads(_BSE_SME_CACHE.read_text())
    return []


def get_all_sme_stocks(force: bool = False) -> list[dict]:
    """
    Merged NSE Emerge + BSE SME list, deduplicated by ISIN.
    NSE records are preferred over BSE when ISIN matches (yfinance .NS is more reliable).

    NOTE: NSE Emerge records never have an ISIN (see module docstring), so this
    dedup currently only ever compares BSE records against each other — dual-listed
    NSE Emerge + BSE SME companies are not merged and will appear twice.
    """
    nse = fetch_nse_emerge_stocks(force=force)
    bse = fetch_bse_sme_stocks(force=force)

    nse_isins = {s["isin"] for s in nse if s.get("isin")}

    merged = list(nse)
    for s in bse:
        # Skip BSE record if NSE already has this ISIN
        if s.get("isin") and s["isin"] in nse_isins:
            continue
        merged.append(s)

    logger.info(
        "SME master: %d total (%d NSE Emerge, %d BSE-only)",
        len(merged), len(nse), len(merged) - len(nse),
    )
    return merged
