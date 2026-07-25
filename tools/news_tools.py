import json
from crewai.tools import tool


@tool("Get Latest Stock News")
def get_latest_news(query: str) -> str:
    """Fetch the 5 most recent news articles about an Indian stock or company from Google News.
    Input: search query string, e.g. 'Reliance Industries NSE India' or 'TCS earnings Q4'.
    Returns articles with title, description, source, published date, and URL."""
    try:
        from gnews import GNews
        gn = GNews(language="en", country="IN", period="7d", max_results=5)
        articles = gn.get_news(query)
        results = []
        for a in articles:
            # One malformed article (e.g. a present-but-None "publisher",
            # or a non-dict entry) must not discard the other good results
            # for this query — skip just the bad item instead of letting
            # the whole batch abort to the outer except.
            if not isinstance(a, dict):
                continue
            try:
                publisher = a.get("publisher")
                results.append({
                    "title": a.get("title") or "",
                    "description": (a.get("description") or "")[:200],
                    "source": (publisher or {}).get("title", "") if isinstance(publisher, dict) else "",
                    "published_at": a.get("published date", ""),
                    "url": a.get("url", ""),
                })
            except Exception:
                continue
        return json.dumps({"query": query, "articles": results})
    except ImportError:
        return json.dumps({"error": "gnews package not installed. Run: pip install gnews", "query": query})
    except Exception as e:
        return json.dumps({"error": str(e), "query": query})
