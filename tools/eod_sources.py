"""EOD data source fetchers: NSE bhavcopy, NSE equity master, AMFI NAV.

All public functions return dicts and never raise (repo tool convention).
"""

import csv
import io
import logging
from datetime import date, datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_BHAVCOPY_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{stamp}.csv"
_EQUITY_MASTER_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

_ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "output" / "_bhavcopy"
_ALLOWED_SERIES = {"EQ", "BE", "BZ", "SM", "ST"}  # SM/ST = NSE Emerge (SME) main + ITP
_TIMEOUT = 20


def make_nse_session() -> requests.Session:
    """Fresh session with NSE headers and a primed cookie jar. Never raises."""
    sess = requests.Session()
    sess.headers.update(_NSE_HEADERS)
    try:
        sess.get("https://www.nseindia.com", timeout=6)
    except Exception:
        pass
    return sess


def _archive_path(trade_date: date) -> Path:
    return _ARCHIVE_DIR / f"sec_bhavdata_full_{trade_date.strftime('%d%m%Y')}.csv"


def _looks_like_html(text: str) -> bool:
    return text.lstrip()[:1] == "<"


def _has_bhavcopy_header(text: str) -> bool:
    """A real bhavcopy's first line is the CSV header containing SYMBOL.
    A 200-with-empty/garbage bot-block body fails this check."""
    stripped = text.strip()
    if not stripped:
        return False
    return "SYMBOL" in stripped.splitlines()[0]


def download_bhavcopy(trade_date: date, session: requests.Session) -> dict:
    """Fetch one day's full bhavcopy CSV; archive to output/_bhavcopy/.

    Returns {"status": "ok", "csv": ...} | {"status": "missing"} (404 = holiday
    or not yet published) | {"status": "error", "error": ...}.
    Replays from the local archive when present, without hitting NSE — unless
    the archived body fails the header check, in which case it's ignored and
    a fresh download is attempted.
    """
    archive = _archive_path(trade_date)
    try:
        if archive.exists():
            cached = archive.read_text()
            if _has_bhavcopy_header(cached):
                return {"status": "ok", "csv": cached}
            logger.warning("bhavcopy archive for %s failed header check; re-downloading", trade_date)
    except Exception as exc:
        logger.warning("bhavcopy archive read failed for %s: %s", trade_date, exc)

    url = _BHAVCOPY_URL.format(stamp=trade_date.strftime("%d%m%Y"))
    try:
        resp = session.get(url, timeout=_TIMEOUT)
        if resp.status_code == 404:
            return {"status": "missing"}
        if resp.status_code == 200 and _looks_like_html(resp.text):
            # Bot-block: retry once with a fresh session.
            resp = make_nse_session().get(url, timeout=_TIMEOUT)
            if resp.status_code == 200 and _looks_like_html(resp.text):
                return {"status": "error", "error": f"bot-block on {url}"}
            if resp.status_code == 404:
                return {"status": "missing"}
        if resp.status_code != 200:
            return {"status": "error", "error": f"HTTP {resp.status_code} on {url}"}
        if not _has_bhavcopy_header(resp.text):
            return {"status": "error", "error": f"unexpected bhavcopy body on {url}"}
        try:
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_text(resp.text)
        except Exception as exc:
            logger.warning("bhavcopy archive write failed for %s: %s", trade_date, exc)
        return {"status": "ok", "csv": resp.text}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _num(val: str | None) -> float | None:
    if val is None:
        return None
    val = val.strip()
    if val in ("", "-", "N.A."):
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _intval(val: str | None) -> int | None:
    f = _num(val)
    return int(f) if f is not None else None


def parse_bhavcopy(csv_text: str) -> dict:
    """Parse sec_bhavdata_full CSV. Keeps only EQ/BE/BZ/SM/ST series.

    Returns {"rows": [...], "skipped_series": n, "malformed": n}.
    Malformed rows are counted and skipped, never abort the file.
    """
    rows: list[dict] = []
    skipped_series = 0
    malformed = 0
    reader = csv.DictReader(io.StringIO(csv_text))
    for raw in reader:
        try:
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            series = row.get("SERIES", "")
            if series not in _ALLOWED_SERIES:
                skipped_series += 1
                continue
            close = _num(row.get("CLOSE_PRICE"))
            prev_close = _num(row.get("PREV_CLOSE"))
            trade_date = datetime.strptime(row["DATE1"], "%d-%b-%Y").date()
            if not row.get("SYMBOL") or close is None or prev_close is None:
                malformed += 1
                continue
            rows.append({
                "symbol":        row["SYMBOL"],
                "series":        series,
                "trade_date":    trade_date,
                "open":          _num(row.get("OPEN_PRICE")),
                "high":          _num(row.get("HIGH_PRICE")),
                "low":           _num(row.get("LOW_PRICE")),
                "close":         close,
                "prev_close":    prev_close,
                "avg_price":     _num(row.get("AVG_PRICE")),
                "volume":        _intval(row.get("TTL_TRD_QNTY")),
                "turnover_lacs": _num(row.get("TURNOVER_LACS")),
                "trades":        _intval(row.get("NO_OF_TRADES")),
                "delivery_qty":  _intval(row.get("DELIV_QTY")),
                "delivery_pct":  _num(row.get("DELIV_PER")),
            })
        except Exception:
            malformed += 1
    return {"rows": rows, "skipped_series": skipped_series, "malformed": malformed}


def download_equity_master(session: requests.Session) -> dict:
    """Fetch EQUITY_L.csv (symbol master: names, ISIN, listing date, face value)."""
    try:
        resp = session.get(_EQUITY_MASTER_URL, timeout=_TIMEOUT)
        if resp.status_code == 200 and _looks_like_html(resp.text):
            return {"status": "error", "error": f"bot-block or HTTP {resp.status_code} on equity master"}
        if resp.status_code != 200:
            return {"status": "error", "error": f"HTTP {resp.status_code} on equity master"}
        return {"status": "ok", "csv": resp.text}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def parse_equity_master(csv_text: str) -> list[dict]:
    """Parse EQUITY_L.csv into securities-master rows. Malformed rows skipped."""
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for raw in reader:
        try:
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            if not row.get("SYMBOL"):
                continue
            listing_date: date | None
            try:
                listing_date = datetime.strptime(row.get("DATE OF LISTING", ""), "%d-%b-%Y").date()
            except ValueError:
                listing_date = None
            rows.append({
                "symbol":       row["SYMBOL"],
                "company_name": row.get("NAME OF COMPANY") or None,
                "series":       row.get("SERIES") or None,
                "listing_date": listing_date,
                "isin":         row.get("ISIN NUMBER") or None,
                "face_value":   _num(row.get("FACE VALUE")),
            })
        except Exception:
            continue
    return rows


# ── AMFI mutual fund NAV ──────────────────────────────────────────────────────

_NAV_ALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
_NAV_HISTORY_URL = "https://api.mfapi.in/mf/{scheme_code}"   # JSON mirror of AMFI history


def fetch_nav_all() -> dict:
    """Fetch AMFI's daily NAVAll.txt (all ~40k schemes, semicolon-separated)."""
    try:
        resp = requests.get(_NAV_ALL_URL, timeout=30)
        if resp.status_code != 200:
            return {"status": "error", "error": f"HTTP {resp.status_code} on NAVAll.txt"}
        return {"status": "ok", "text": resp.text}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def parse_nav_all(text: str, scheme_codes: set[str]) -> list[dict]:
    """Extract NAV rows for the given scheme codes from NAVAll.txt.

    Format per data line:
    Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date
    Section headers and blank lines carry no semicolons (or too few fields) and are skipped.
    """
    rows: list[dict] = []
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) < 6:
            continue
        code = parts[0].strip()
        if code not in scheme_codes:
            continue
        nav = _num(parts[4])
        if nav is None:
            continue
        try:
            nav_date = datetime.strptime(parts[5].strip(), "%d-%b-%Y").date()
        except ValueError:
            continue
        rows.append({
            "scheme_code": code,
            "nav_date":    nav_date,
            "nav":         nav,
            "scheme_name": parts[3].strip() or None,
        })
    return rows


def fetch_scheme_history(scheme_code: str, since: date) -> dict:
    """Full NAV history for one scheme via api.mfapi.in, filtered to >= since."""
    try:
        resp = requests.get(_NAV_HISTORY_URL.format(scheme_code=scheme_code), timeout=30)
        if resp.status_code != 200:
            return {"status": "error", "error": f"HTTP {resp.status_code} for scheme {scheme_code}"}
        payload = resp.json()
        name = (payload.get("meta") or {}).get("scheme_name")
        rows: list[dict] = []
        for item in payload.get("data", []):
            try:
                nav_date = datetime.strptime(item["date"], "%d-%m-%Y").date()
            except (KeyError, ValueError):
                continue
            if nav_date < since:
                continue
            nav = _num(item.get("nav"))
            if nav is None:
                continue
            rows.append({
                "scheme_code": scheme_code,
                "nav_date":    nav_date,
                "nav":         nav,
                "scheme_name": name,
            })
        return {"status": "ok", "rows": rows}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
