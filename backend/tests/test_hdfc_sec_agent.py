import unittest
from unittest.mock import MagicMock, patch

from tools.hdfc_sec_agent import _gnews, fetch_hdfc_sec_fundamental, fetch_hdfc_sec_technical


def _article(title="Some title", desc="A" * 500, url="https://example.com/a", pub="Mon, 01 Jan 2026 10:00:00 GMT"):
    return {"title": title, "description": desc, "url": url, "published date": pub}


class GnewsHelperTest(unittest.TestCase):
    def test_parses_articles_and_truncates_summary(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = [_article()]
        with patch("tools.hdfc_sec_agent.GNews", return_value=fake_gn):
            result = _gnews("some query")
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["summary"]), 400)
        self.assertTrue(result[0]["published_at"].startswith("2026-01-01"))

    def test_unparseable_date_leaves_published_at_none(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = [_article(pub="not-a-date")]
        with patch("tools.hdfc_sec_agent.GNews", return_value=fake_gn):
            result = _gnews("some query")
        self.assertIsNone(result[0]["published_at"])

    def test_gnews_exception_returns_empty_list(self) -> None:
        with patch("tools.hdfc_sec_agent.GNews", side_effect=RuntimeError("boom")):
            result = _gnews("some query")
        self.assertEqual(result, [])


class FetchHdfcSecFundamentalTest(unittest.TestCase):
    def test_uses_first_query_results_when_available(self) -> None:
        with patch("tools.hdfc_sec_agent._gnews", return_value=[{"title": "a"}]) as mocked:
            result = fetch_hdfc_sec_fundamental()
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result["source"], "HDFC Securities Fundamental")
        self.assertEqual(result["type"], "brokerage")
        self.assertEqual(len(result["articles"]), 1)

    def test_falls_back_through_all_three_queries_when_empty(self) -> None:
        with patch("tools.hdfc_sec_agent._gnews", side_effect=[[], [], [{"title": "c"}]]) as mocked:
            result = fetch_hdfc_sec_fundamental()
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(len(result["articles"]), 1)

    def test_all_queries_empty_returns_empty_articles(self) -> None:
        with patch("tools.hdfc_sec_agent._gnews", return_value=[]):
            result = fetch_hdfc_sec_fundamental()
        self.assertEqual(result["articles"], [])


class FetchHdfcSecTechnicalTest(unittest.TestCase):
    def test_uses_first_query_results_when_available(self) -> None:
        with patch("tools.hdfc_sec_agent._gnews", return_value=[{"title": "a"}]) as mocked:
            result = fetch_hdfc_sec_technical()
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result["source"], "HDFC Securities Technical")

    def test_falls_back_to_second_query_when_first_empty(self) -> None:
        with patch("tools.hdfc_sec_agent._gnews", side_effect=[[], [{"title": "b"}]]) as mocked:
            result = fetch_hdfc_sec_technical()
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(len(result["articles"]), 1)


if __name__ == "__main__":
    unittest.main()
