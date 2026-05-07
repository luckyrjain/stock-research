"""
Trendlyne analyst consensus scraper for the market picks pipeline.

Trendlyne aggregates analyst recommendations from 20+ Indian brokerages and
provides consensus ratings, target prices, and upgrade/downgrade alerts.

This scraper targets three distinct signals via GNews:
  1. Analyst upgrades and new BUY initiations (high conviction)
  2. Consensus target price raises (broad analyst agreement)
  3. Trendlyne-cited picks in financial media (validation signal)

Source type: brokerage — aggregated analyst consensus is institutional-grade.
"""

from email.utils import parsedate_to_datetime


def _gnews(query: str, max_results: int = 12) -> list[dict]:
    try:
        from gnews import GNews
        gn   = GNews(language="en", country="IN", period="14d", max_results=max_results)
        arts = gn.get_news(query)
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


_QUERIES = [
    # Analyst upgrades and fresh BUY initiations
    '"upgrades to buy" OR "initiates with buy" OR "initiates coverage" India NSE stock 2026',
    # Consensus target price raises
    '"target price raised" OR "target raised" OR "price target increased" India NSE analyst 2026',
    # Trendlyne-cited picks in financial media
    '"Trendlyne" buy recommendation analyst India NSE stock 2026',
]


def fetch_trendlyne_consensus() -> dict:
    """Fetch analyst upgrades, initiations, and consensus BUY calls via targeted GNews queries."""
    seen_urls: set[str] = set()
    articles:  list[dict] = []

    for query in _QUERIES:
        for art in _gnews(query, max_results=10):
            url = art.get("url", "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            articles.append(art)

    return {"source": "Trendlyne / Analyst Consensus", "type": "brokerage", "articles": articles}


TRENDLYNE_SOURCES = [
    ("Trendlyne / Analyst Consensus", "brokerage", "fetch_trendlyne_consensus"),
]

TRENDLYNE_SCRAPERS: dict = {
    "Trendlyne / Analyst Consensus": fetch_trendlyne_consensus,
}
