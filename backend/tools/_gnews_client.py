"""Shared GNews fetch-and-normalize helper.

tools/market_picks_tools.py, tools/hdfc_sec_agent.py, tools/trendlyne_agent.py,
and tools/screener_scanner.py (its `_gnews_fallback()`) each independently
built a near-identical GNews fetch: same `GNews(...)` construction, same
try/except-empty-list convention, same `email.utils.parsedate_to_datetime`
article-date normalization, differing only in incidental defaults (period,
summary-truncation length, and whether the query is caller-supplied or
baked in). This is the same drift `tools/_nse_session.py` already fixed once
for NSE session-priming — this module is the equivalent single place for
GNews.

tools/news_tools.py::get_latest_news() also calls GNews directly but is
deliberately NOT folded in here — it's a `@tool`-decorated CrewAI tool with a
genuinely different output contract (a JSON string, `description`/`source`
fields instead of `summary`, no ISO-normalized `published_at`), feeding the
six-task `ALL_DATA_TASKS` pipeline rather than the Market Picks source list.
Forcing it through this shape would mean overfitting `fetch_gnews()` to a
consumer it doesn't actually share a contract with.

The 3 `_gnews(query, max_results=N)` callers each keep their own local thin
wrapper (same name, same call signature they already had) that delegates
here with its own period/summary_len needs — deliberate, not an oversight:
every existing test patches that per-module function name directly (e.g.
`patch("tools.market_picks_tools._gnews", ...)`), so keeping a thin local
wrapper means this consolidation doesn't also force a rewrite of every test
file's patch targets. See `tools/_nse_session.py`'s own docstring for the
identical reasoning. `screener_scanner.py::_gnews_fallback()` has no
`query` parameter of its own (single hardcoded query, single call site), so
it calls `fetch_gnews()` directly rather than through an extra wrapper.

`tools._gnews_timeout`'s socket-default-timeout side effect only needs to
fire from wherever `GNews(...)` is actually constructed — now here only, so
none of the 4 callers above import it themselves any more.
"""
import tools._gnews_timeout  # noqa: F401 — sets a socket default timeout for the GNews call below


def fetch_gnews(query: str, period: str = "14d", max_results: int = 10, summary_len: int = 500) -> list[dict]:
    """Fetch up to `max_results` GNews articles for `query`, normalized to
    `{title, summary, url, published_at}` (`published_at` an ISO string or
    None). Never raises — returns `[]` on any failure, matching every other
    `tools/*.py` module's never-raise convention."""
    try:
        from gnews import GNews
        gn = GNews(language="en", country="IN", period=period, max_results=max_results)
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
                "summary":      (a.get("description") or "")[:summary_len],
                "url":          a.get("url", ""),
                "published_at": pub_iso,
            })
        return results
    except Exception:
        return []
