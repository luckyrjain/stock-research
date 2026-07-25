"""
Fetchers for Indian SME stocks:
  - NSE Emerge:  /api/live-analysis-emerge (539 stocks, series SM)
  - BSE SME:     BSE public API (best-effort; skipped gracefully if unavailable)

NSE list is cached for 24 h under output/.

Deduplication: primarily by ISIN, but the NSE Emerge endpoint doesn't return
one (every NSE record has isin=None), so ISIN matching alone never catches a
company dual-listed on both exchanges. As a fallback, get_all_sme_stocks()
also fuzzy-matches normalized company names (rapidfuzz, high score cutoff to
avoid merging genuinely different companies) — NSE Emerge names are already
enriched via Screener.in by fetch_nse_emerge_stocks(), so this is usually
available. A company whose NSE and BSE names diverge enough to miss both
checks (or whose Screener.in name lookup failed) will still appear twice;
this is a best-effort dedup, not a guarantee.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import requests
from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

# Deliberately narrower than market_picks_pipeline.py's _norm_company (which
# this was originally copied from). That version is used to widen a ticker
# *search* — over-stripping there is safe because a live-price check catches
# a wrong match afterward. Here the match result conclusively merges two
# records, so only genuinely empty legal-entity terms are stripped. Words
# like "international"/"group"/"systems"/"technologies"/"corporation" are
# deliberately NOT stripped: Indian conglomerates commonly list multiple
# separate companies sharing a family name root (e.g. "Tata Motors" vs
# "Tata Technologies" vs "Tata International" are different companies) —
# stripping those words would erase the only text that distinguishes them.
_COMPANY_SUFFIXES = re.compile(
    r'\b(limited|ltd|private|pvt|company|co)\b',
    re.IGNORECASE,
)
_NAME_MATCH_SCORE_CUTOFF = 90  # high bar — a miss is cheaper than a wrong merge
_MIN_NORMALIZED_NAME_LEN = 3   # skip fuzzy matching on near-empty normalized names


def _norm_company_name(name: str | None) -> str:
    """Strip common suffixes/punctuation for cleaner company-name comparison."""
    if not name:
        return ""
    n = _COMPANY_SUFFIXES.sub("", name)
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip().upper()
    return n

_NSE_EMERGE_CACHE = Path("output/_nse_emerge_master.json")
_BSE_SME_CACHE    = Path("output/_bse_sme_master.json")
_CACHE_TTL_HOURS  = 24
# A truncated-but-nonempty response (partial download, rate-limited/paginated
# reply) is otherwise silently cached and treated as complete — worse than
# the already-handled zero-rows case, since downstream code has no way to
# tell "the real universe is this small" apart from "the fetch got cut off."
# Set well below each list's real size (this module's own docstring cites
# ~539 for NSE Emerge) to tolerate genuine listing/delisting drift.
_NSE_EMERGE_MIN_COUNT = 150
_BSE_SME_MIN_COUNT    = 30

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

    failures = 0
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
            else:
                failures += 1
        except Exception:
            failures += 1
        time.sleep(0.1)

    if failures:
        # Not fatal (the ISIN-dedup fallback in get_all_sme_stocks() degrades
        # gracefully to a name-less record), but silent per-stock failures
        # would otherwise weaken that fallback with no way to notice —
        # matching this codebase's "no silent caps" convention.
        logger.warning(
            "SME name enrichment: %d/%d lookups failed or returned nothing",
            failures, len(to_fetch),
        )

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
        if len(stocks) >= _NSE_EMERGE_MIN_COUNT:
            logger.info("NSE Emerge: fetched %d stocks, enriching names…", len(stocks))
            stocks = _enrich_names(stocks)
            _save_cache(_NSE_EMERGE_CACHE, stocks)
            logger.info("NSE Emerge: cached %d stocks with names", len(stocks))
            return stocks
        if stocks:
            logger.warning(
                "NSE Emerge: fetch returned suspiciously few rows (%d, expected at least %d) — "
                "treating as a failed fetch rather than caching a truncated universe",
                len(stocks), _NSE_EMERGE_MIN_COUNT,
            )
        else:
            logger.warning("NSE Emerge: fetch returned no usable rows")
    except Exception as exc:
        logger.warning("NSE Emerge fetch failed: %s", exc)

    if _NSE_EMERGE_CACHE.exists():
        logger.warning("NSE Emerge: using stale cache")
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
            skipped = 0
            for item in rows:
                # A single malformed row (not a dict, or missing fields) must
                # not abort the rest of this group's rows — it used to raise
                # out of this loop entirely, discarding every remaining row
                # in the group via the outer except, reported only as an
                # indistinguishable "group fetch failed" warning.
                try:
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
                except Exception:
                    skipped += 1
            if skipped:
                logger.warning("BSE SME Group=%s: skipped %d malformed row(s)", group, skipped)
            logger.info("BSE SME Group=%s: %d stocks", group, len(rows) if isinstance(rows, list) else 0)
        except Exception as exc:
            logger.warning("BSE SME Group=%s fetch failed: %s", group, exc)

    if len(stocks) >= _BSE_SME_MIN_COUNT:
        _save_cache(_BSE_SME_CACHE, stocks)
        logger.info("BSE SME: fetched %d total stocks", len(stocks))
        return stocks
    if stocks:
        logger.warning(
            "BSE SME: fetch returned suspiciously few rows (%d, expected at least %d) — "
            "treating as a failed fetch rather than caching a truncated universe",
            len(stocks), _BSE_SME_MIN_COUNT,
        )

    if _BSE_SME_CACHE.exists():
        logger.warning("BSE SME: using stale cache")
        return json.loads(_BSE_SME_CACHE.read_text())
    return []


def get_all_sme_stocks(force: bool = False) -> list[dict]:
    """
    Merged NSE Emerge + BSE SME list, deduplicated by ISIN first, then by a
    high-confidence company-name match (see module docstring for why both are
    needed). NSE records are preferred over BSE — yfinance's .NS suffix is
    more reliable than .BO for these thinly-traded names.
    """
    nse = fetch_nse_emerge_stocks(force=force)
    bse = fetch_bse_sme_stocks(force=force)

    nse_isins = {s["isin"] for s in nse if s.get("isin")}

    nse_names: list[str] = []
    for s in nse:
        norm = _norm_company_name(s.get("name"))
        if len(norm) >= _MIN_NORMALIZED_NAME_LEN:
            nse_names.append(norm)

    merged = list(nse)
    isin_dupes = 0
    name_dupes = 0
    for s in bse:
        if s.get("isin") and s["isin"] in nse_isins:
            isin_dupes += 1
            continue

        bse_name = _norm_company_name(s.get("name"))
        if len(bse_name) >= _MIN_NORMALIZED_NAME_LEN and nse_names:
            match = process.extractOne(
                bse_name, nse_names, scorer=fuzz.token_sort_ratio,
                score_cutoff=_NAME_MATCH_SCORE_CUTOFF,
            )
            if match:
                name_dupes += 1
                continue

        merged.append(s)

    logger.info(
        "SME master: %d total (%d NSE Emerge, %d BSE-only; "
        "skipped %d ISIN-matched + %d name-matched BSE duplicates)",
        len(merged), len(nse), len(merged) - len(nse), isin_dupes, name_dupes,
    )
    return merged
