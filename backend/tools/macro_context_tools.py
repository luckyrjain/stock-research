"""Macro context — RBI policy repo rate and CPI inflation — a market-wide
overlay on the valuation signal, not per-symbol. Same pseudo-symbol caching
convention as tools/nse_fii_dii_tools.py (see signals/macro.py).

NOTE: RBI's website structure was not verified against a live response in
this sandbox — no outbound internet (see CLAUDE.md's disclosed
limitation). The parser is written defensively (BeautifulSoup + explicit
None checks, never raises) so a page-layout change degrades to
{"error": ...} exactly like a network failure, rather than crashing the
signal engine — same "tools must not raise" convention as every other
tools/*.py module. CPI inflation in particular is often unavailable this
way (RBI's homepage doesn't always carry a single easily-parseable CPI
figure) and is left None rather than guessed, per this codebase's "never
invent" convention — same as fii_net_cr/dii_net_cr in
tools/nse_fii_dii_tools.py when a row is missing.
"""
import re

import requests
from bs4 import BeautifulSoup

from core.observability import get_logger, log_event

LOGGER = get_logger("macro_context_tools")

_RBI_HOMEPAGE_URL = "https://www.rbi.org.in/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

_PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")

# The `table tr` selector below scans every table on RBI's homepage, not
# just the "Current Rates" widget this module's docstring describes — its
# exact HTML structure couldn't be verified against a live response in this
# sandbox (see module docstring). A percentage from an unrelated table
# elsewhere on the page (a promo, an unrelated stat) could false-positive
# match before the real rate. Bounding each field to its real-world
# plausible range is a cheap, verification-independent guard against
# trusting such a match blindly — same "bound rather than trust an
# unscoped/unverified match" pattern as tools/nse_fii_dii_tools.py's
# _PLAUSIBLE_MAX_NET_CR. India's repo rate has stayed within roughly this
# band for decades; CPI inflation is given a wider band to allow for real
# extreme prints while still rejecting an obviously-unrelated percentage.
_REPO_RATE_RANGE = (2.0, 15.0)
_CPI_RANGE = (-5.0, 20.0)


def _parse_percent(text: str, plausible_range: tuple[float, float] = (-1000.0, 1000.0)) -> float | None:
    match = _PERCENT_RE.search(text or "")
    if not match:
        return None
    value = float(match.group(1))
    lo, hi = plausible_range
    return value if lo <= value <= hi else None


def get_macro_context() -> dict:
    """Best-effort {"repo_rate_pct", "cpi_inflation_pct"} snapshot scraped
    from RBI's own "Current Rates" table. Either field is None (never
    guessed) if the page doesn't clearly expose it. Returns {"error": ...}
    only on a total fetch failure."""
    try:
        resp = requests.get(_RBI_HOMEPAGE_URL, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        repo_rate = None
        cpi = None
        for row in soup.select("table tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            label = cells[0].lower()
            if "repo rate" in label and repo_rate is None:
                repo_rate = _parse_percent(cells[1], _REPO_RATE_RANGE)
            elif "cpi" in label and cpi is None:
                cpi = _parse_percent(cells[1], _CPI_RANGE)

        return {"repo_rate_pct": repo_rate, "cpi_inflation_pct": cpi}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "macro_context_fetch_failed", level="warning", error=str(exc))
        return {"error": str(exc)}
