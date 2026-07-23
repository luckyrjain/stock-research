import unittest
from unittest.mock import MagicMock, patch

from tools.trendlyne_agent import _gnews, fetch_trendlyne_consensus


def _article(title="t", desc="d", url="https://example.com/a", pub="Mon, 01 Jan 2026 10:00:00 GMT"):
    return {"title": title, "description": desc, "url": url, "published date": pub}


class GnewsHelperTest(unittest.TestCase):
    def test_parses_articles(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = [_article()]
        with patch("gnews.GNews", return_value=fake_gn):
            result = _gnews("query")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "t")

    def test_import_or_network_failure_returns_empty_list(self) -> None:
        with patch("gnews.GNews", side_effect=RuntimeError("boom")):
            result = _gnews("query")
        self.assertEqual(result, [])


class FetchTrendlyneConsensusTest(unittest.TestCase):
    def test_dedupes_articles_by_url_across_queries(self) -> None:
        dup = {"title": "a", "url": "https://example.com/dup", "summary": "", "published_at": None}
        unique = {"title": "b", "url": "https://example.com/unique", "summary": "", "published_at": None}
        with patch("tools.trendlyne_agent._gnews", side_effect=[[dup], [dict(dup)], [unique]]):
            result = fetch_trendlyne_consensus()
        urls = [a["url"] for a in result["articles"]]
        self.assertEqual(urls.count("https://example.com/dup"), 1)
        self.assertIn("https://example.com/unique", urls)

    def test_articles_without_url_are_not_deduped_away(self) -> None:
        no_url = {"title": "a", "url": "", "summary": "", "published_at": None}
        with patch("tools.trendlyne_agent._gnews", side_effect=[[no_url], [dict(no_url)], []]):
            result = fetch_trendlyne_consensus()
        self.assertEqual(len(result["articles"]), 2)

    def test_source_and_type_metadata(self) -> None:
        with patch("tools.trendlyne_agent._gnews", return_value=[]):
            result = fetch_trendlyne_consensus()
        self.assertEqual(result["source"], "Trendlyne / Analyst Consensus")
        self.assertEqual(result["type"], "brokerage")

    def test_queries_all_three_configured_searches(self) -> None:
        with patch("tools.trendlyne_agent._gnews", return_value=[]) as mocked:
            fetch_trendlyne_consensus()
        self.assertEqual(mocked.call_count, 3)


if __name__ == "__main__":
    unittest.main()
