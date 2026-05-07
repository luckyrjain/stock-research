"""Scrapers for Indian financial platforms to collect weekly stock picks."""

import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_CUTOFF_DAYS = 14  # look back 2 weeks to capture more articles

from tools.hdfc_sec_agent import HDFC_SEC_SOURCES
from tools.nse_bulk_block_deals import NSE_BULK_SOURCES
from tools.screener_scanner import SCREENER_SCAN_SOURCES
from tools.trendlyne_agent import TRENDLYNE_SOURCES

# Registry used by the pipeline to run all scrapers
SOURCES = [
    # ── News RSS feeds ──────────────────────────────────────────────────────────
    ("ET Markets",                          "news",      "fetch_et_markets"),
    ("LiveMint",                            "news",      "fetch_livemint"),
    ("NDTV Profit",                         "news",      "fetch_ndtv_profit"),
    ("Hindu BusinessLine",                  "news",      "fetch_hindu_bl"),
    ("Zerodha Z-Connect",                   "brokerage", "fetch_zerodha"),
    ("GNews — Moneycontrol",                "news",      "fetch_gnews_moneycontrol"),
    # ── Global bulge-bracket & research firms ───────────────────────────────────
    ("Morgan Stanley / JPMorgan",           "brokerage", "fetch_gnews_ms_jpm"),
    ("Jefferies / Macquarie / Citi",        "brokerage", "fetch_gnews_jefferies_mac_citi"),
    ("HSBC / BofA / Bernstein / Investec",  "brokerage", "fetch_gnews_intl_banks"),
    # ── India-focused brokerages ────────────────────────────────────────────────
    ("ShareKhan / Mirae Asset",             "brokerage", "fetch_gnews_sharekhan_mirae"),
    ("SMIFS / IDBI Capital / Geojit / Deven Choksey", "brokerage", "fetch_gnews_india_brokers"),
] + HDFC_SEC_SOURCES + NSE_BULK_SOURCES + SCREENER_SCAN_SOURCES + TRENDLYNE_SOURCES


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    return s


def _parse_rss(url: str, max_articles: int = 20, delay: float = 0.0) -> list[dict]:
    """Fetch and parse an RSS feed, returning articles within the cutoff window."""
    if delay:
        time.sleep(delay)
    try:
        r = _session().get(url, timeout=12)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        cutoff = datetime.now(timezone.utc) - timedelta(days=_CUTOFF_DAYS)
        articles: list[dict] = []
        for entry in feed.entries:
            try:
                pd = entry.get("published_parsed")
                if pd:
                    pub = datetime(*pd[:6], tzinfo=timezone.utc)
                    if pub < cutoff:
                        continue
            except Exception:
                pass  # missing or unparseable date → include anyway
            title = entry.get("title", "").strip()
            if not title:
                continue
            pub_iso: str | None = None
            try:
                if pd:
                    pub_iso = datetime(*pd[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
            articles.append({
                "title":        title,
                "summary":      (entry.get("summary") or entry.get("description") or "")[:500].strip(),
                "url":          entry.get("link", ""),
                "published_at": pub_iso,
            })
            if len(articles) >= max_articles:
                break
        return articles
    except Exception:
        return []


def _gnews(query: str, max_results: int = 10) -> list[dict]:
    try:
        from gnews import GNews
        gn = GNews(language="en", country="IN", period="14d", max_results=max_results)
        arts = gn.get_news(query)
        results = []
        for a in arts:
            pub_iso: str | None = None
            try:
                raw_date = a.get("published date") or ""
                if raw_date:
                    from email.utils import parsedate_to_datetime
                    pub_iso = parsedate_to_datetime(raw_date).isoformat()
            except Exception:
                pass
            results.append({
                "title":        a.get("title", ""),
                "summary":      (a.get("description") or "")[:500],
                "url":          a.get("url", ""),
                "published_at": pub_iso,
            })
        return results
    except Exception:
        return []


# ── Individual source scrapers ────────────────────────────────────────────────

def fetch_et_markets() -> dict:
    # Primary: ET Markets stocks feed; fallback: ET general markets
    arts = _parse_rss("https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms")
    if not arts:
        arts = _parse_rss("https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms")
    return {"source": "ET Markets", "type": "news", "articles": arts}


def fetch_livemint() -> dict:
    arts = _parse_rss("https://www.livemint.com/rss/markets")
    if not arts:
        arts = _parse_rss("https://www.livemint.com/rss/money")
    return {"source": "LiveMint", "type": "news", "articles": arts}


def fetch_ndtv_profit() -> dict:
    arts = _parse_rss("https://feeds.feedburner.com/ndtvprofit-latest", max_articles=20)
    return {"source": "NDTV Profit", "type": "news", "articles": arts}


def fetch_hindu_bl() -> dict:
    arts = _parse_rss("https://www.thehindubusinessline.com/markets/stocks/?service=rss")
    if not arts:
        arts = _parse_rss("https://www.thehindubusinessline.com/markets/?service=rss")
    return {"source": "Hindu BusinessLine", "type": "news", "articles": arts}


def fetch_zerodha() -> dict:
    # Zerodha Z-Connect blog RSS has ~50 relevant investment articles
    arts = _parse_rss("https://zerodha.com/z-connect/feed", max_articles=20)
    return {"source": "Zerodha Z-Connect", "type": "brokerage", "articles": arts}


def fetch_gnews_moneycontrol() -> dict:
    arts = _gnews("moneycontrol buy recommendation target price India stock", max_results=15)
    return {"source": "GNews — Moneycontrol", "type": "news", "articles": arts}


def fetch_gnews_ms_jpm() -> dict:
    arts = _gnews('"Morgan Stanley" OR "JPMorgan" India stock buy overweight target 2026', max_results=15)
    return {"source": "Morgan Stanley / JPMorgan", "type": "brokerage", "articles": arts}


def fetch_gnews_jefferies_mac_citi() -> dict:
    arts = _gnews('"Jefferies" OR "Macquarie" OR "Citi" India stock buy outperform target 2026', max_results=15)
    return {"source": "Jefferies / Macquarie / Citi", "type": "brokerage", "articles": arts}


def fetch_gnews_intl_banks() -> dict:
    arts = _gnews('"HSBC" OR "Bank of America" OR "Bernstein" OR "Investec" India stock buy outperform 2026', max_results=15)
    return {"source": "HSBC / BofA / Bernstein / Investec", "type": "brokerage", "articles": arts}


def fetch_gnews_sharekhan_mirae() -> dict:
    arts = _gnews('"ShareKhan" OR "Mirae Asset" India stock buy recommendation NSE target 2026', max_results=15)
    return {"source": "ShareKhan / Mirae Asset", "type": "brokerage", "articles": arts}


def fetch_gnews_india_brokers() -> dict:
    arts = _gnews('"SMIFS" OR "IDBI Capital" OR "Geojit" OR "Deven Choksey" India stock buy recommendation 2026', max_results=15)
    return {"source": "SMIFS / IDBI Capital / Geojit / Deven Choksey", "type": "brokerage", "articles": arts}


from tools.hdfc_sec_agent import HDFC_SEC_SOURCES, HDFC_SEC_SCRAPERS
from tools.nse_bulk_block_deals import NSE_BULK_SCRAPERS
from tools.screener_scanner import SCREENER_SCAN_SCRAPERS
from tools.trendlyne_agent import TRENDLYNE_SCRAPERS

# Map source name → function (used by pipeline)
SCRAPER_FNS: dict = {
    "ET Markets":                                     fetch_et_markets,
    "LiveMint":                                       fetch_livemint,
    "NDTV Profit":                                    fetch_ndtv_profit,
    "Hindu BusinessLine":                             fetch_hindu_bl,
    "Zerodha Z-Connect":                              fetch_zerodha,
    "GNews — Moneycontrol":                           fetch_gnews_moneycontrol,
    "Morgan Stanley / JPMorgan":                      fetch_gnews_ms_jpm,
    "Jefferies / Macquarie / Citi":                   fetch_gnews_jefferies_mac_citi,
    "HSBC / BofA / Bernstein / Investec":             fetch_gnews_intl_banks,
    "ShareKhan / Mirae Asset":                        fetch_gnews_sharekhan_mirae,
    "SMIFS / IDBI Capital / Geojit / Deven Choksey":  fetch_gnews_india_brokers,
    **HDFC_SEC_SCRAPERS,
    **NSE_BULK_SCRAPERS,
    **SCREENER_SCAN_SCRAPERS,
    **TRENDLYNE_SCRAPERS,
}
