"""Shared NSE session-priming helper.

Every NSE-touching module in tools/ (nse_tools.py, nse_filings_tools.py,
nse_insider_trades.py, nse_bulk_block_deals.py, nse_fii_dii_tools.py,
sme_tools.py, nifty500_tools.py) independently builds a requests.Session,
sets near-identical headers, and "primes" it with a GET to nseindia.com's
homepage before the real API/CSV call — NSE rejects a cold request with no
prior cookie. Three of those seven modules had a byte-identical priming
function; the other four each drifted slightly (different helper names,
timeouts, User-Agent strings, and inconsistently either swallowing or
propagating a priming failure). This module is the single place that logic
now lives.

Each caller keeps its own local wrapper function (same name, same call
signature callers already had — e.g. tools/nse_tools.py still defines
_nse_session(), tools/nse_filings_tools.py still defines _get_session())
that delegates here with its own timeout/header needs. This is deliberate,
not an oversight: every module's existing tests patch that per-module
function name directly (e.g. patch("tools.nse_tools._nse_session", ...)),
so keeping a thin local wrapper means the consolidation doesn't also force
a rewrite of every test file's patch targets.

Resilience is standardized across all callers here (this is the
"...+ Screener.in fallback resilience" half of this pass, not just
deduplication): every priming attempt is wrapped in a swallow-and-continue
try/except and followed by a short sleep on success. Previously two of the
seven modules (nse_tools.py, nse_filings_tools.py) let a priming failure
propagate uncaught into the caller's own broad except, and nifty500_tools.py
folded a priming failure into the same log line as a real data-fetch
failure — none of that distinguishing behavior was intentional, it was
drift, so standardizing here is a genuine resilience improvement rather
than a behavior change worth preserving per-module.
"""
import time

import requests

NSE_BASE_URL = "https://www.nseindia.com"

_DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def get_nse_session(
    timeout: float = 8.0,
    accept: str = "application/json",
    extra_headers: dict | None = None,
    sleep_after_prime: float = 0.5,
) -> requests.Session:
    """Build a fresh requests.Session, set NSE-friendly headers on it, and
    prime it with a cookie-fetching visit to nseindia.com. Never raises —
    a priming failure is logged nowhere and simply leaves the session
    un-primed; the caller's own real request will fail on its own terms
    (and every caller already has a broad except around its whole fetch)
    rather than this helper deciding what "failure" means for a module it
    doesn't own.

    `accept`/`extra_headers` let callers with non-default needs (a CSV
    fetch, an extra Accept-Language header) opt in without duplicating the
    rest of this function."""
    session = requests.Session()
    headers = {
        "User-Agent": _DEFAULT_USER_AGENT,
        "Accept": accept,
        "Referer": NSE_BASE_URL,
    }
    if extra_headers:
        headers.update(extra_headers)
    session.headers.update(headers)

    try:
        session.get(NSE_BASE_URL, timeout=timeout)
        if sleep_after_prime:
            time.sleep(sleep_after_prime)
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    return session
