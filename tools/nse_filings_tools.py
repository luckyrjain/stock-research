# tools/nse_filings_tools.py

import requests
from datetime import datetime, timedelta
import time

BASE_URL = "https://www.nseindia.com/api/corporate-announcements"


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

        to_date = datetime.today()
        from_date = to_date - timedelta(days=days)
        issuer_param = f"&issuer={issuer.replace(' ', '%20')}" if issuer else ""

        url = (
            f"{BASE_URL}?"
            f"index=equities"
            f"&symbol={symbol}"
            f"{issuer_param}"
            f"&from_date={from_date.strftime('%d-%m-%Y')}"
            f"&to_date={to_date.strftime('%d-%m-%Y')}"
            f"&reqXbrl=false"
        )

        resp = session.get(url, timeout=10)

        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "symbol": symbol}

        if "application/json" not in resp.headers.get("Content-Type", ""):
            return {
                "error": "Blocked / Non-JSON",
                "symbol": symbol,
                "raw": resp.text[:200]
            }

        data = resp.json()

        filings = []
        for item in data:
            filings.append({
                "title": item.get("subject"),
                "desc": item.get("description"),
                "date": item.get("date"),
                "category": item.get("category"),
                "attachment": item.get("attchmntFile"),
            })

        return {
            "symbol": symbol,
            "count": len(filings),
            "filings": filings
        }

    except Exception as e:
        return {"error": str(e), "symbol": symbol}