"""
Securities master + broker-code resolver.

Combines three registries into one symbol lookup:
  - NSE main-board: db.models.securities table (populated nightly by
    eod_prices_pipeline.py from NSE's EQUITY_L.csv — no fetch needed here).
  - BSE main-board:  BSE public API (this module; groups A/B/T/Z/X/XT/P/MT/TS).
  - NSE Emerge + BSE SME: tools/sme_tools.py (unchanged).

resolve_symbol() lets a broker's internal stock code be matched against a
real trading symbol via ISIN, exact code (with a known suffix stripped), or
fuzzy company-name match. Consumed by csv_import.py's broker CSV import
(new-asset resolution) — see that module for the integration.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import requests
from rapidfuzz import fuzz, process, utils as _rf_utils
from sqlalchemy import select

from db.models import securities as _securities_t
from tools.sme_tools import get_all_sme_stocks

_BSE_MAIN_CACHE = Path("output/_bse_main_master.json")
_CACHE_TTL_HOURS = 24
_BSE_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":         "https://www.bseindia.com/",
    "Origin":          "https://www.bseindia.com",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
_BSE_MAIN_GROUPS = ["A", "B", "T", "Z", "X", "XT", "P", "MT", "TS"]
_SUFFIXES = ("EQ", "SM", "ST", "BE", "BZ", "IV")


def load_nse_main_board(engine) -> list[dict]:
    """NSE main-board securities with a known company name (enriched rows only —
    a row without one is a pre-EQUITY_L.csv-join miss in the EOD pipeline and
    isn't useful for name-fuzzy matching)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(_securities_t.c.symbol, _securities_t.c.isin,
                   _securities_t.c.company_name, _securities_t.c.series)
            .where(_securities_t.c.company_name.isnot(None))
        ).mappings().fetchall()
    return [
        {"symbol": r["symbol"], "name": r["company_name"], "isin": r["isin"],
         "exchange": "NSE", "series": r["series"]}
        for r in rows
    ]


def _is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=_CACHE_TTL_HOURS)


def _save_cache(path: Path, data: list[dict]) -> None:
    # Atomic write (tempfile + os.replace) — same convention as
    # cache.py::save()/tools/sme_tools.py::_save_cache, so an interrupted
    # write never leaves a corrupt file behind for the next read to choke on.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data))
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _load_cache(path: Path) -> list[dict] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def fetch_bse_main_board(force: bool = False) -> list[dict]:
    """Return all BSE main-board stocks across the main equity groups. Cached 24 h.
    Never raises — a fetch failure degrades to a stale cache, then to []."""
    if not force and _is_fresh(_BSE_MAIN_CACHE):
        cached = _load_cache(_BSE_MAIN_CACHE)
        if cached is not None:
            return cached

    base_url = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w/"
    base_params = "Scripcode=&segment=Equity&status=Active&industrycode=&isincode=&maskstatus="

    stocks: list[dict] = []
    seen_codes: set[str] = set()

    for group in _BSE_MAIN_GROUPS:
        try:
            r = requests.get(f"{base_url}?{base_params}&Group={group}",
                              headers=_BSE_HEADERS, timeout=15, allow_redirects=False)
            r.raise_for_status()
            rows = r.json()
            if not isinstance(rows, list):
                rows = rows.get("Table", []) if isinstance(rows, dict) else []
            for item in rows:
                try:
                    code = str(item.get("SCRIP_CD", "")).strip()
                    if not code or code in seen_codes:
                        continue
                    seen_codes.add(code)
                    symbol = str(item.get("scrip_id", "")).strip().upper() or code
                    stocks.append({
                        "symbol":   symbol,
                        "name":     str(item.get("Scrip_Name", "")).strip() or None,
                        "isin":     str(item.get("ISIN_NUMBER", "")).strip() or None,
                        "exchange": "BSE",
                        "series":   group,
                    })
                except Exception:
                    continue
        except Exception:
            continue

    if stocks:
        _save_cache(_BSE_MAIN_CACHE, stocks)
        return stocks

    if _BSE_MAIN_CACHE.exists():
        cached = _load_cache(_BSE_MAIN_CACHE)
        if cached is not None:
            return cached
    return []


def get_full_securities_master(engine, force: bool = False) -> list[dict]:
    """Merge NSE main-board + BSE main-board + NSE Emerge + BSE SME.
    Dedup by ISIN; earlier sources (NSE main-board first) win on collision."""
    try:
        nse_rows = load_nse_main_board(engine)
    except Exception:
        nse_rows = []  # DB error: NSE main-board contributes nothing this call

    merged: list[dict] = []
    seen_isins: set[str] = set()
    for group in (nse_rows, fetch_bse_main_board(force=force), get_all_sme_stocks(force=force)):
        for s in group:
            isin = s.get("isin")
            if isin and isin in seen_isins:
                continue
            if isin:
                seen_isins.add(isin)
            merged.append(s)
    return merged


def resolve_symbol(engine, code: str, company_name: str | None = None,
                    isin: str | None = None, master: list[dict] | None = None) -> dict:
    """Resolve a broker's internal stock code to a verified symbol.
    Tiers: ISIN exact > code exact (one suffix stripped) > fuzzy company
    name (rapidfuzz, threshold 85, case/punctuation-insensitive via
    rapidfuzz's default_process) > unresolved.

    Pass `master` (from get_full_securities_master(engine)) when resolving
    many codes in a loop — each self-load is a full securities-table scan
    plus a fuzzy-match candidate rebuild, so callers looping over rows
    should load once and share it."""
    if master is None:
        master = get_full_securities_master(engine)
    code = (code or "").strip().upper()

    if isin:
        for s in master:
            if s.get("isin") == isin:
                return {"symbol": s["symbol"], "exchange": s["exchange"],
                        "confidence": "isin", "candidate_name": s.get("name")}

    # Built preferring the FIRST source that claims a given symbol string,
    # not the last — `master`'s ISIN-based dedup (get_full_securities_master)
    # already documents "earlier sources (NSE main-board first) win on
    # collision", but a naive {s["symbol"]: s for s in master} dict
    # comprehension is last-key-wins, silently reversing that priority for
    # any exact-symbol-string collision across sources (e.g. an SME scrip
    # code that happens to match an unrelated NSE main-board ticker).
    by_symbol: dict[str, dict] = {}
    for s in master:
        sym = s.get("symbol")
        if sym and sym not in by_symbol:
            by_symbol[sym] = s
    if code in by_symbol:
        s = by_symbol[code]
        return {"symbol": s["symbol"], "exchange": s["exchange"],
                "confidence": "exact", "candidate_name": s.get("name")}
    for suffix in _SUFFIXES:
        if code.endswith(suffix):
            stripped = code[:-len(suffix)]
            if stripped in by_symbol:
                s = by_symbol[stripped]
                return {"symbol": s["symbol"], "exchange": s["exchange"],
                        "confidence": "exact", "candidate_name": s.get("name")}
            break

    if company_name:
        names = [s.get("name") or "" for s in master]
        best = process.extractOne(company_name, names, scorer=fuzz.token_set_ratio,
                                   processor=_rf_utils.default_process, score_cutoff=85)
        if best:
            s = master[best[2]]
            return {"symbol": s["symbol"], "exchange": s["exchange"],
                    "confidence": "fuzzy", "candidate_name": s.get("name")}
        best_any = process.extractOne(company_name, names, scorer=fuzz.token_set_ratio,
                                       processor=_rf_utils.default_process)
        return {"symbol": None, "exchange": None, "confidence": "unresolved",
                "candidate_name": best_any[0] if best_any else None}

    return {"symbol": None, "exchange": None, "confidence": "unresolved",
            "candidate_name": None}
