"""NSE corporate actions: fetch and PURPOSE-string parsing.

All public functions return dicts / lists and never raise (repo convention).
Never guess a ratio: unparseable purposes become type "other" with a NULL
price_factor — a silently wrong factor is the worst failure mode here.
"""

import logging
import re
from datetime import date, datetime

_CA_URL = "https://www.nseindia.com/api/corporates-corporateActions"
_TIMEOUT = 20

logger = logging.getLogger(__name__)

_BONUS_RE = re.compile(r"BONUS\s+(\d+)\s*:\s*(\d+)")
_SPLIT_RE = re.compile(
    r"FROM\s+R[SE]\.?\s*(\d+(?:\.\d+)?)\s*(?:/-)?\s*(?:PER\s+SHARE|EACH)?\s*"
    r"TO\s+R[SE]\.?\s*(\d+(?:\.\d+)?)"
)
_DIV_RE = re.compile(r"DIVIDEND[^0-9]*R[SE]\.?\s*(\d+(?:\.\d+)?)")


def parse_purpose(purpose: str) -> dict:
    """Classify one PURPOSE string and compute its price factor / amount.

    bonus A:B -> factor B/(A+B); split old_fv->new_fv -> factor new_fv/old_fv.
    Anything unparseable is type "other" with NULL factor — never guess.
    """
    p = " ".join((purpose or "").upper().split())
    if "BONUS" in p:
        m = _BONUS_RE.search(p)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > 0 and b > 0:
                return {"type": "bonus", "price_factor": round(b / (a + b), 6), "amount": None}
        return {"type": "other", "price_factor": None, "amount": None}
    if "SPLIT" in p or "SUB-DIVISION" in p or "SUBDIVISION" in p:
        m = _SPLIT_RE.search(p)
        if m:
            old, new = float(m.group(1)), float(m.group(2))
            if old > 0 and new > 0 and new != old:
                return {"type": "split", "price_factor": round(new / old, 6), "amount": None}
        return {"type": "other", "price_factor": None, "amount": None}
    if "RIGHTS" in p:
        return {"type": "rights", "price_factor": None, "amount": None}
    if "BUYBACK" in p or "BUY BACK" in p or "BUY-BACK" in p:
        return {"type": "buyback", "price_factor": None, "amount": None}
    if "DIVIDEND" in p:
        m = _DIV_RE.search(p)
        amount = float(m.group(1)) if m else None
        return {"type": "dividend", "price_factor": None, "amount": amount}
    return {"type": "other", "price_factor": None, "amount": None}


def fetch_corporate_actions(from_date: date, to_date: date, session) -> dict:
    """Fetch corporate actions for a date window from the NSE API."""
    try:
        resp = session.get(
            _CA_URL,
            params={
                "index": "equities",
                "from_date": from_date.strftime("%d-%m-%Y"),
                "to_date": to_date.strftime("%d-%m-%Y"),
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return {"status": "error", "error": f"HTTP {resp.status_code} on corporate actions"}
        if resp.text.lstrip()[:1] == "<":
            return {"status": "error", "error": "bot-block on corporate actions"}
        data = resp.json()
        if isinstance(data, dict):
            data = data.get("data", [])
        if not isinstance(data, list):
            return {"status": "error", "error": "unexpected corporate actions payload"}
        return {"status": "ok", "raw": data}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _parse_nse_date(val: str | None) -> date | None:
    try:
        return datetime.strptime((val or "").strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def parse_corporate_actions(raw: list) -> list[dict]:
    """Convert raw NSE rows to corporate_actions-shaped rows.

    Rows without a symbol or a parseable ex-date are skipped (logged at debug
    level); an action we cannot date cannot be applied safely.
    """
    rows: list[dict] = []
    for item in raw:
        try:
            symbol = (item.get("symbol") or "").strip().upper()
            ex_date = _parse_nse_date(item.get("exDate"))
            if not symbol or ex_date is None:
                logger.debug("corporate action skipped (symbol/exDate): %r", item)
                continue
            purpose = (item.get("subject") or "").strip()
            parsed = parse_purpose(purpose)
            rows.append({
                "symbol":       symbol,
                "ex_date":      ex_date,
                "type":         parsed["type"],
                "purpose_raw":  purpose[:300],
                "price_factor": parsed["price_factor"],
                "amount":       parsed["amount"],
                "record_date":  _parse_nse_date(item.get("recDate")),
            })
        except Exception:
            continue
    return rows
