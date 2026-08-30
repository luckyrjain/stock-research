"""Direct tests for tools/_gnews_client.py::fetch_gnews() — the shared
fetch-and-normalize logic extracted out of tools/market_picks_tools.py,
tools/hdfc_sec_agent.py, tools/trendlyne_agent.py, and
tools/screener_scanner.py's own near-identical `_gnews()`/`_gnews_fallback()`
copies. Consolidates what used to be near-duplicate `GnewsHelperTest`
classes in tests/test_hdfc_sec_agent.py and tests/test_trendlyne_agent.py —
one implementation, one set of tests for it, same instinct as the
consolidation itself.
"""
import unittest
from unittest.mock import MagicMock, patch

from tools._gnews_client import fetch_gnews


def _article(title="Some title", desc="A" * 600, url="https://example.com/a", pub="Mon, 01 Jan 2026 10:00:00 GMT"):
    return {"title": title, "description": desc, "url": url, "published date": pub}


class FetchGnewsTest(unittest.TestCase):
    def test_parses_articles_into_the_normalized_shape(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = [_article(title="t", desc="d", url="https://example.com/a")]
        with patch("gnews.GNews", return_value=fake_gn):
            result = fetch_gnews("query")
        self.assertEqual(result, [{
            "title": "t", "summary": "d", "url": "https://example.com/a",
            "published_at": "2026-01-01T10:00:00+00:00",
        }])

    def test_truncates_summary_to_summary_len(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = [_article(desc="A" * 600)]
        with patch("gnews.GNews", return_value=fake_gn):
            result = fetch_gnews("query", summary_len=400)
        self.assertEqual(len(result[0]["summary"]), 400)

    def test_default_summary_len_is_500(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = [_article(desc="A" * 600)]
        with patch("gnews.GNews", return_value=fake_gn):
            result = fetch_gnews("query")
        self.assertEqual(len(result[0]["summary"]), 500)

    def test_unparseable_date_leaves_published_at_none(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = [_article(pub="not-a-date")]
        with patch("gnews.GNews", return_value=fake_gn):
            result = fetch_gnews("query")
        self.assertIsNone(result[0]["published_at"])

    def test_missing_date_leaves_published_at_none(self) -> None:
        fake_gn = MagicMock()
        arts = [_article()]
        del arts[0]["published date"]
        fake_gn.get_news.return_value = arts
        with patch("gnews.GNews", return_value=fake_gn):
            result = fetch_gnews("query")
        self.assertIsNone(result[0]["published_at"])

    def test_gnews_construction_failure_returns_empty_list(self) -> None:
        with patch("gnews.GNews", side_effect=RuntimeError("boom")):
            result = fetch_gnews("query")
        self.assertEqual(result, [])

    def test_get_news_failure_returns_empty_list(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.side_effect = RuntimeError("boom")
        with patch("gnews.GNews", return_value=fake_gn):
            result = fetch_gnews("query")
        self.assertEqual(result, [])

    def test_period_max_results_and_query_are_passed_through(self) -> None:
        # Each caller relies on its own period/max_results reaching GNews
        # unchanged -- hdfc_sec_agent.py needs "7d", the others need "14d".
        fake_gnews_cls = MagicMock()
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = []
        fake_gnews_cls.return_value = fake_gn
        with patch("gnews.GNews", fake_gnews_cls):
            fetch_gnews("my query", period="7d", max_results=12)
        fake_gnews_cls.assert_called_once_with(language="en", country="IN", period="7d", max_results=12)
        fake_gn.get_news.assert_called_once_with("my query")

    def test_empty_results_returns_empty_list(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = []
        with patch("gnews.GNews", return_value=fake_gn):
            result = fetch_gnews("query")
        self.assertEqual(result, [])
