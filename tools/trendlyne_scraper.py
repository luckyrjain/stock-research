"""Real numeric analyst consensus (analyst count / consensus rating / mean
target price) scraped directly from Trendlyne's own public company page.

Additive to tools/trendlyne_agent.py's GNews-based
fetch_trendlyne_consensus_for_symbol(), which explicitly never fabricates
these numbers because it never scrapes trendlyne.com itself — it only
finds articles that mention Trendlyne's consensus, not the consensus
itself. This module closes that gap for real: it hits trendlyne.com
directly, so when the scrape succeeds it has the actual numbers instead of
secondhand article coverage of them.

Never-raise convention, same as every other tools/*.py module: any
failure (network error, symbol-resolution miss, changed page structure)
degrades to a dict with every numeric field None rather than raising or
guessing. `fetch_trendlyne_numeric_consensus()` is never a fourth data
slice — it's fetched on demand alongside the GNews-based articles by
GET /api/street-consensus/{symbol}, same as every other standalone,
on-demand endpoint in this codebase (peers, insider activity).

Disclosed limitation: neither the exact URL pattern Trendlyne uses to
resolve a bare NSE symbol to its own company-page slug, nor the precise
DOM/CSS structure that page renders analyst count / consensus rating /
mean target price under, were verified against a live response in this
sandbox (no outbound internet to non-allowlisted hosts — same disclosure
pattern as the FII/DII/RBI scrapers, the NIFTY 500 constituent list, and
every other "unverified against a live response" scraper already
documented in CLAUDE.md). Parsing is deliberately regex-over-page-text
rather than a narrow set of CSS selectors, since textual labels
("Consensus Recommendation: BUY", "Mean Target Price") are more likely to
survive a markup/class-name change than any specific selector guess would
be — but this is still an unverified assumption, not a confirmed
resilience property. A real-world mismatch on either point degrades every
field to None, the same "wrong guess is worse than missing data" instinct
this codebase applies everywhere else, never a wrong number.

Further disclosed limitation: `_ANALYST_COUNT_RE` searches the whole
page's flattened text for the first "N Analyst(s)" phrase, which is less
label-specific than the other three regexes — a marketing blurb or a
"similar stocks" widget quoting an unrelated count earlier in the DOM
than the real consensus block could in principle produce a wrong-but-
plausible number rather than a `None`. Not verified against a live page
in this sandbox; worth tightening (e.g. requiring proximity to a
"Consensus"/"Rating" anchor) once the real DOM can be inspected.
"""
import re
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

_ALLOWED_HOST = "trendlyne.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_KNOWN_RATINGS = ("STRONG BUY", "BUY", "ACCUMULATE", "HOLD", "REDUCE", "SELL", "STRONG SELL")

_ANALYST_COUNT_RE = re.compile(r"(\d{1,3})\s+Analysts?\b", re.IGNORECASE)
_CONSENSUS_RATING_RE = re.compile(
    r"Consensus\s+(?:Recommendation|Rating)\s*[:\-]?\s*(" + "|".join(_KNOWN_RATINGS) + r")",
    re.IGNORECASE,
)
_MEAN_TARGET_RE = re.compile(
    r"Mean\s+Target(?:\s+Price)?\s*[:\-]?\s*₹?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_UPSIDE_RE = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*%\s*(Upside|Downside)",
    re.IGNORECASE,
)


def _is_trendlyne_host(url: str) -> bool:
    """Guards against following a resolved URL off trendlyne.com — both the
    direct-URL path (a redirect could in principle land anywhere) and the
    search-page fallback (which parses arbitrary anchors out of returned
    HTML) must never hand a cross-domain URL to the follow-up fetch. An
    unvalidated absolute href here would be an SSRF vector: this module
    fetches whatever URL it resolves to with a real browser User-Agent."""
    host = (urlparse(url).netloc or "").lower()
    return host == _ALLOWED_HOST or host.endswith("." + _ALLOWED_HOST)


def _empty_result(symbol: str, error: str | None = None) -> dict:
    result = {
        "symbol":              symbol,
        "analyst_count":       None,
        "consensus_rating":    None,
        "mean_target_price":   None,
        "target_upside_pct":   None,
        "source_url":          None,
    }
    if error:
        result["error"] = error
    return result


def _resolve_trendlyne_url(symbol: str) -> str | None:
    """Best-effort resolution of a bare NSE symbol to its Trendlyne company
    page. Tries the direct /equity/{symbol}/ path first (Trendlyne is known
    to redirect a bare symbol to its full /equity/<id>/<symbol>/<slug>/
    URL); falls back to Trendlyne's own search page and takes the first
    result link that looks like a company page. Every candidate URL is
    host-checked against trendlyne.com (_is_trendlyne_host) before being
    accepted — a redirect or a parsed anchor pointing off-domain is treated
    as unresolved, never followed. Returns None (never a guessed URL) if
    nothing resolves."""
    direct_url = f"https://trendlyne.com/equity/{quote(symbol)}/"
    try:
        resp = requests.get(direct_url, headers=_HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code == 200 and "/equity/" in resp.url and _is_trendlyne_host(resp.url):
            return resp.url
    except Exception:
        pass

    try:
        search_resp = requests.get(
            f"https://trendlyne.com/search-new/{quote(symbol)}/",
            headers=_HEADERS,
            timeout=10,
        )
        if search_resp.status_code != 200:
            return None
        soup = BeautifulSoup(search_resp.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/equity/" not in href:
                continue
            resolved = href if href.startswith("http") else f"https://trendlyne.com{href}"
            if _is_trendlyne_host(resolved):
                return resolved
    except Exception:
        pass

    return None


def _parse_numeric_consensus(page_text: str) -> dict:
    """Extracts analyst_count / consensus_rating / mean_target_price /
    target_upside_pct from the page's own text via label-anchored regexes
    rather than narrow CSS selectors (see module docstring). Every field is
    independently None when its own label/value pair isn't cleanly
    present — a partial page (e.g. a rating shown but no target price)
    yields a partial result, not a discarded one."""
    fields: dict = {
        "analyst_count":      None,
        "consensus_rating":   None,
        "mean_target_price":  None,
        "target_upside_pct":  None,
    }

    count_match = _ANALYST_COUNT_RE.search(page_text)
    if count_match:
        fields["analyst_count"] = int(count_match.group(1))

    rating_match = _CONSENSUS_RATING_RE.search(page_text)
    if rating_match:
        fields["consensus_rating"] = rating_match.group(1).upper()

    target_match = _MEAN_TARGET_RE.search(page_text)
    if target_match:
        try:
            fields["mean_target_price"] = float(target_match.group(1).replace(",", ""))
        except ValueError:
            pass

    upside_match = _UPSIDE_RE.search(page_text)
    if upside_match:
        try:
            pct = float(upside_match.group(1))
            if upside_match.group(2).lower() == "downside" and pct > 0:
                pct = -pct
            fields["target_upside_pct"] = pct
        except ValueError:
            pass

    return fields


def fetch_trendlyne_numeric_consensus(symbol: str) -> dict:
    """Real analyst_count / consensus_rating / mean_target_price /
    target_upside_pct for one symbol, scraped directly from Trendlyne's own
    company page — never invented. Every field is None (not guessed) when
    the page can't be resolved or a value isn't cleanly present in it.
    Never raises: any failure returns the same all-None shape as a clean
    "no data" result, with an added `error` key for observability."""
    sym = symbol.upper().strip() if isinstance(symbol, str) else ""
    if not sym:
        return _empty_result(sym, error="empty symbol")

    try:
        url = _resolve_trendlyne_url(sym)
        if not url:
            return _empty_result(sym)

        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        page_text = " ".join(soup.stripped_strings)

        result = {"symbol": sym, "source_url": url}
        result.update(_parse_numeric_consensus(page_text))
        return result
    except Exception as exc:
        return _empty_result(sym, error=str(exc))
