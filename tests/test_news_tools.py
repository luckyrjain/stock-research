import json
import unittest
from unittest.mock import MagicMock, patch

from tools.news_tools import get_latest_news


class GetLatestNewsTest(unittest.TestCase):
    def test_parses_articles_and_truncates_description(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = [{
            "title": "TCS beats estimates",
            "description": "x" * 500,
            "publisher": {"title": "Economic Times"},
            "published date": "Mon, 01 Jan 2026 10:00:00 GMT",
            "url": "https://example.com/a",
        }]
        with patch("gnews.GNews", return_value=fake_gn):
            result = json.loads(get_latest_news.run(query="TCS earnings"))

        self.assertEqual(result["query"], "TCS earnings")
        self.assertEqual(len(result["articles"]), 1)
        self.assertEqual(len(result["articles"][0]["description"]), 200)
        self.assertEqual(result["articles"][0]["source"], "Economic Times")

    def test_missing_gnews_package_returns_error_payload(self) -> None:
        with patch("gnews.GNews", side_effect=ImportError("no module")):
            result = json.loads(get_latest_news.run(query="TCS"))
        self.assertIn("error", result)
        self.assertEqual(result["query"], "TCS")

    def test_generic_exception_returns_error_payload_not_raise(self) -> None:
        with patch("gnews.GNews", side_effect=RuntimeError("rate limited")):
            result = json.loads(get_latest_news.run(query="TCS"))
        self.assertIn("error", result)
        self.assertEqual(result["error"], "rate limited")

    def test_empty_results_returns_empty_articles_list(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = []
        with patch("gnews.GNews", return_value=fake_gn):
            result = json.loads(get_latest_news.run(query="NoSuchCompany"))
        self.assertEqual(result["articles"], [])


if __name__ == "__main__":
    unittest.main()
