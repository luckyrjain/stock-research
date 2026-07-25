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

from observability import get_logger, log_event

LOGGER = get_logger("macro_context_tools")

_RBI_HOMEPAGE_URL = "https://www.rbi.org.in/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

_PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")


def _parse_percent(text: str) -> float | None:
    match = _PERCENT_RE.search(text or "")
    return float(match.group(1)) if match else None


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
                repo_rate = _parse_percent(cells[1])
            elif "cpi" in label and cpi is None:
                cpi = _parse_percent(cells[1])

        return {"repo_rate_pct": repo_rate, "cpi_inflation_pct": cpi}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "macro_context_fetch_failed", level="warning", error=str(exc))
        return {"error": str(exc)}
