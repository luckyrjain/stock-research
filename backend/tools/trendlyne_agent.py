"""
Trendlyne analyst consensus source for the market picks pipeline.

Trendlyne aggregates analyst recommendations from 20+ Indian brokerages and
provides consensus ratings, target prices, and upgrade/downgrade alerts.
This module does not scrape trendlyne.com directly — it searches GNews for
articles that explicitly cite Trendlyne, so every query requires the
"Trendlyne" term to avoid duplicating the generic brokerage-upgrade queries
already covered by other sources in market_picks_tools.py:
  1. Trendlyne-cited analyst upgrades and new BUY initiations (high conviction)
  2. Trendlyne-cited consensus target price raises (broad analyst agreement)
  3. Trendlyne-cited picks in financial media generally (validation signal)

Source type: brokerage — aggregated analyst consensus is institutional-grade.
"""

from datetime import datetime, timezone

from tools._gnews_client import fetch_gnews


def _gnews(query: str, max_results: int = 12) -> list[dict]:
    return fetch_gnews(query, period="14d", max_results=max_results, summary_len=500)


def _queries() -> list[str]:
    # Computed per-call (not a module constant) so the query stays accurate
    # if this long-running process happens to cross a year boundary,
    # instead of baking in whatever year it started in.
    year = datetime.now(timezone.utc).year
    return [
        # Trendlyne-cited analyst upgrades and fresh BUY initiations
        f'"Trendlyne" ("upgrades to buy" OR "initiates with buy" OR "initiates coverage") India NSE stock {year}',
        # Trendlyne-cited consensus target price raises
        f'"Trendlyne" ("target price raised" OR "target raised" OR "price target increased") India NSE {year}',
        # Trendlyne-cited picks in financial media
        f'"Trendlyne" buy recommendation analyst India NSE stock {year}',
    ]


def fetch_trendlyne_consensus() -> dict:
    """Fetch analyst upgrades, initiations, and consensus BUY calls via targeted GNews queries."""
    seen_urls: set[str] = set()
    articles:  list[dict] = []

    for query in _queries():
        for art in _gnews(query, max_results=10):
            url = art.get("url", "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            articles.append(art)

    return {"source": "Trendlyne / Analyst Consensus", "type": "brokerage", "articles": articles}


def fetch_trendlyne_consensus_for_symbol(symbol: str, max_results: int = 10) -> dict:
    """Recent Trendlyne-cited analyst commentary for one stock — a structured
    article list (title/summary/url/published_at), scoped to this symbol the
    same way fetch_trendlyne_consensus() scopes to "Trendlyne" market-wide.
    Deliberately never a fabricated consensus rating or target price number —
    this module searches GNews for articles that cite Trendlyne, it doesn't
    scrape trendlyne.com's own numeric estimates, so a "12 analysts rate this
    BUY, target ₹X" figure isn't data this module actually has; inventing one
    would violate this codebase's "never invent" convention just as much as
    guessing a missing scraped field would.
    """
    sym = symbol.upper().strip() if isinstance(symbol, str) else ""
    query = f'"Trendlyne" "{sym}" (buy OR upgrade OR "target price") NSE stock'

    seen_urls: set[str] = set()
    articles:  list[dict] = []
    for art in _gnews(query, max_results=max_results):
        url = art.get("url", "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        articles.append(art)

    return {"symbol": sym, "articles": articles}


# Merged into SOURCES in market_picks_tools.py, which derives SCRAPER_FNS
# from the merged list — no separate *_SCRAPERS dict to hand-sync here.
TRENDLYNE_SOURCES = [
    ("Trendlyne / Analyst Consensus", "brokerage", fetch_trendlyne_consensus),
]
