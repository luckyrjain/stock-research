"""
Screener.in public fundamental screener for the market picks pipeline.

Queries Screener.in's public /screen/raw/ endpoint with two quality criteria sets:
  1. High ROE, low debt, strong profit/sales growth
  2. High ROCE, reasonable PE, positive earnings growth

Each qualifying stock is formatted as an article so the extraction LLM can
assign a BUY direction based on the fundamental rationale in the summary.

Falls back to GNews when the screener URL is unavailable or requires login.
"""

import logging
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.screener.in",
}

_QUERIES = [
    # Quality growth: high ROE, low debt, strong earnings & sales growth, ≥ ₹1000 Cr cap
    (
        "Return on equity > 15 AND Debt to equity < 1 "
        "AND Profit growth > 15 AND Sales growth > 10 "
        "AND Market Capitalization > 1000",
        "High ROE + low debt + strong growth",
    ),
    # Value quality: high ROCE, undemanding PE, positive earnings growth, ≥ ₹500 Cr cap
    (
        "ROCE > 20 AND PE > 0 AND PE < 35 "
        "AND Profit growth > 10 "
        "AND Market Capitalization > 500",
        "High ROCE + reasonable PE + earnings growth",
    ),
]


def _parse_screen_html(html: str, criterion_label: str) -> list[dict]:
    """Parse Screener's data-table into article dicts."""
    try:
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", class_="data-table") or soup.find("table")
        if not table:
            return []

        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        articles: list[dict] = []

        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            name_cell = cells[0]
            company   = name_cell.get_text(strip=True)
            if not company:
                continue

            link = name_cell.find("a")
            href = (link.get("href") or "") if link else ""
            # A relative href is reconstructed against the known screener.in
            # domain, same "don't trust an arbitrary href" instinct as
            # tools/trendlyne_scraper.py's _is_trendlyne_host — these URLs
            # are only ever returned as article links (rendered client-side,
            # never fetched server-side by this module), but there's no
            # reason to accept an absolute off-domain href here either, for
            # the same "closed set, not raw scraped text" convention this
            # codebase applies to every other scraped URL.
            if href.startswith("/"):
                url = f"https://www.screener.in{href}"
            elif href.startswith("https://www.screener.in/") or href.startswith("http://www.screener.in/"):
                url = href
            else:
                url = "https://www.screener.in"

            metrics = [
                f"{headers[i]}: {cells[i].get_text(strip=True)}"
                for i in range(1, min(len(cells), len(headers)))
                if cells[i].get_text(strip=True)
            ]

            title   = f"{company}: Screener.in fundamental pick ({criterion_label})"
            summary = (
                f"Screener.in fundamental screen: {company} qualifies on '{criterion_label}'. "
                f"{', '.join(metrics[:6])}."
            )
            articles.append({"title": title, "summary": summary, "url": url, "published_at": None})

        return articles
    except Exception:
        return []


def _gnews_fallback() -> list[dict]:
    try:
        import tools._gnews_timeout  # noqa: F401 — sets a socket default timeout for GNews calls below
        from datetime import datetime, timezone
        from gnews import GNews
        from email.utils import parsedate_to_datetime
        year = datetime.now(timezone.utc).year
        gn   = GNews(language="en", country="IN", period="14d", max_results=12)
        arts = gn.get_news(f"screener.in fundamentally strong stock buy NSE India {year}")
        out: list[dict] = []
        for a in arts:
            pub_iso: str | None = None
            try:
                raw = a.get("published date") or ""
                if raw:
                    pub_iso = parsedate_to_datetime(raw).isoformat()
            except Exception:
                pass
            out.append({
                "title":        a.get("title", ""),
                "summary":      (a.get("description") or "")[:500],
                "url":          a.get("url", ""),
                "published_at": pub_iso,
            })
        return out
    except Exception:
        return []


def fetch_screener_scanner() -> dict:
    """Query Screener.in public screener for fundamentally strong stocks."""
    session = requests.Session()
    session.headers.update(_HEADERS)

    articles: list[dict] = []
    seen_titles: set[str] = set()

    screen_worked = False
    for query, label in _QUERIES:
        try:
            url = (
                "https://www.screener.in/screen/raw/?"
                f"sort=&filters=&query={urllib.parse.quote(query)}&limit=50"
            )
            r = session.get(url, timeout=12)
            # Require the actual results-table class, not a bare "<table" match —
            # a login-wall or error page can still contain unrelated <table> markup
            # elsewhere (nav, footer), which would falsely look like a successful screen.
            if r.status_code == 200 and "login" not in r.url and "data-table" in r.text:
                parsed = _parse_screen_html(r.text, label)
                if parsed:
                    screen_worked = True
                for art in parsed:
                    if art["title"] not in seen_titles:
                        seen_titles.add(art["title"])
                        articles.append(art)
        except Exception:
            pass
        time.sleep(0.5)

    if not articles:
        logger.info("Screener.in fundamental screen returned no results; falling back to GNews")
        articles = _gnews_fallback()
        return {"source": "Screener.in Fundamental Screen", "type": "news", "articles": articles}

    logger.debug("Screener.in fundamental screen succeeded: %d picks, real_screen=%s", len(articles), screen_worked)
    return {"source": "Screener.in Fundamental Screen", "type": "brokerage", "articles": articles}


SCREENER_SCAN_SOURCES = [
    ("Screener.in Fundamental Screen", "brokerage", "fetch_screener_scanner"),
]

SCREENER_SCAN_SCRAPERS: dict = {
    "Screener.in Fundamental Screen": fetch_screener_scanner,
}
