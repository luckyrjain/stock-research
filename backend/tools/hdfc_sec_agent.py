"""
HDFC Securities Research Agent — fundamental + technical scrapers.

Follows the same conventions as market_picks_tools.py:
  - returns {"source": str, "type": str, "articles": list[dict]}
  - each article has title, summary, url, published_at (ISO string | None)
"""

from datetime import datetime, timezone

from tools._gnews_client import fetch_gnews


def _gnews(query: str, max_results: int = 10) -> list[dict]:
    return fetch_gnews(query, period="7d", max_results=max_results, summary_len=400)


def fetch_hdfc_sec_fundamental() -> dict:
    """Earnings reviews, Buy/Add ratings, model portfolio updates."""
    arts = _gnews(
        '"HDFC Securities" buy add rating target price stock Q4 results India',
        max_results=12,
    )
    if not arts:
        arts = _gnews(
            '"HDFC Securities" maintained buy rating target price NSE stock',
            max_results=10,
        )
    if not arts:
        year = datetime.now(timezone.utc).year
        arts = _gnews(
            f'"HDFC Securities" model portfolio stock picks India {year}',
            max_results=8,
        )
    return {"source": "HDFC Securities Fundamental", "type": "brokerage", "articles": arts}


def fetch_hdfc_sec_technical() -> dict:
    """Positional technical calls by Vinay Rajani with entry/target/stop-loss."""
    arts = _gnews(
        '"Vinay Rajani" OR "HDFC Securities technical" buy target stop-loss NSE stock',
        max_results=10,
    )
    if not arts:
        year = datetime.now(timezone.utc).year
        arts = _gnews(
            f'"HDFC Securities" technical picks buy recommendation Nifty stock {year}',
            max_results=8,
        )
    return {"source": "HDFC Securities Technical", "type": "brokerage", "articles": arts}


# Merged into SOURCES in market_picks_tools.py, which derives SCRAPER_FNS
# from the merged list — no separate *_SCRAPERS dict to hand-sync here.
HDFC_SEC_SOURCES = [
    ("HDFC Securities Fundamental", "brokerage", fetch_hdfc_sec_fundamental),
    ("HDFC Securities Technical",   "brokerage", fetch_hdfc_sec_technical),
]
