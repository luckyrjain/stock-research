# tools/nse_filings_tools.py

import requests
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo
import time

BASE_URL = "https://www.nseindia.com/api/corporate-announcements"
_IST = ZoneInfo("Asia/Kolkata")


def _get_session():
    session = requests.Session()

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com",
        "Accept-Language": "en-US,en;q=0.9",
    }

    session.headers.update(headers)

    # 🔥 mandatory cookie init
    session.get("https://www.nseindia.com", timeout=5)
    time.sleep(0.5)

    return session


def get_nse_filings(symbol: str, issuer: str = "", days: int = 30) -> dict:
    try:
        session = _get_session()
        sym = symbol.upper().strip()

        # NSE's trading day is IST, not this process's local timezone — most
        # cloud hosts default to UTC, which for ~5.5h after UTC midnight is
        # still "yesterday" in IST, silently shifting this window by a day.
        to_date = datetime.now(_IST)
        from_date = to_date - timedelta(days=days)
        issuer_param = f"&issuer={quote(issuer)}" if issuer else ""

        url = (
            f"{BASE_URL}?"
            f"index=equities"
            f"&symbol={quote(sym)}"
            f"{issuer_param}"
            f"&from_date={from_date.strftime('%d-%m-%Y')}"
            f"&to_date={to_date.strftime('%d-%m-%Y')}"
            f"&reqXbrl=false"
        )

        resp = session.get(url, timeout=10)

        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "symbol": sym}

        if "application/json" not in resp.headers.get("Content-Type", ""):
            return {
                "error": "Blocked / Non-JSON",
                "symbol": sym,
                "raw": resp.text[:200]
            }

        data = resp.json()
        if not isinstance(data, list):
            return {"error": "Unexpected response shape (expected a list)", "symbol": sym}

        filings = []
        for item in data:
            if not isinstance(item, dict):
                continue
            filings.append({
                # Never a bare None for a text field, matching every other
                # tool's convention — NSE sometimes omits/nulls these, and a
                # None here used to crash signals/filings.py's string
                # concatenation with a TypeError, taking down the whole
                # analysis for that symbol.
                "title": item.get("subject") or "",
                "desc": item.get("description") or "",
                "date": item.get("date"),
                "category": item.get("category"),
                "attachment": item.get("attchmntFile"),
            })

        return {
            "symbol": sym,
            "count": len(filings),
            "filings": filings
        }

    except Exception as e:
        return {"error": str(e), "symbol": symbol}