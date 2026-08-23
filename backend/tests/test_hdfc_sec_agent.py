import unittest
from unittest.mock import patch

from tools.hdfc_sec_agent import fetch_hdfc_sec_fundamental, fetch_hdfc_sec_technical


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
