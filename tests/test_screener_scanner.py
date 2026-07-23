import unittest
from unittest.mock import MagicMock, patch

from tools.screener_scanner import _gnews_fallback, _parse_screen_html, fetch_screener_scanner

_TABLE_HTML = """
<table class="data-table">
  <tr><th>Name</th><th>ROE</th><th>PE</th></tr>
  <tr><td><a href="/company/TCS/">TCS</a></td><td>45%</td><td>28</td></tr>
  <tr><td><a href="https://external.example/x">External Co</a></td><td>20%</td><td>15</td></tr>
  <tr><td></td><td>-</td><td>-</td></tr>
</table>
"""


class ParseScreenHtmlTest(unittest.TestCase):
    def test_parses_rows_into_articles(self) -> None:
        articles = _parse_screen_html(_TABLE_HTML, "High ROE")
        self.assertEqual(len(articles), 2)
        self.assertIn("TCS", articles[0]["title"])
        self.assertEqual(articles[0]["url"], "https://www.screener.in/company/TCS/")
        self.assertIn("ROE: 45%", articles[0]["summary"])

    def test_absolute_href_is_used_as_is(self) -> None:
        articles = _parse_screen_html(_TABLE_HTML, "High ROE")
        self.assertEqual(articles[1]["url"], "https://external.example/x")

    def test_row_with_empty_company_name_is_skipped(self) -> None:
        articles = _parse_screen_html(_TABLE_HTML, "High ROE")
        self.assertEqual(len(articles), 2)  # the blank third row is dropped

    def test_no_table_returns_empty_list(self) -> None:
        self.assertEqual(_parse_screen_html("<div>no table here</div>", "label"), [])

    def test_malformed_html_returns_empty_list_not_raise(self) -> None:
        self.assertEqual(_parse_screen_html(None, "label"), [])


class GnewsFallbackTest(unittest.TestCase):
    def test_parses_articles_on_success(self) -> None:
        fake_gn = MagicMock()
        fake_gn.get_news.return_value = [{
            "title": "t", "description": "d", "url": "https://example.com",
            "published date": "Mon, 01 Jan 2026 10:00:00 GMT",
        }]
        with patch("gnews.GNews", return_value=fake_gn):
            result = _gnews_fallback()
        self.assertEqual(len(result), 1)

    def test_failure_returns_empty_list(self) -> None:
        with patch("gnews.GNews", side_effect=RuntimeError("boom")):
            result = _gnews_fallback()
        self.assertEqual(result, [])


class FetchScreenerScannerTest(unittest.TestCase):
    def _resp(self, status_code=200, text="", url="https://www.screener.in/screen/raw/"):
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        resp.url = url
        return resp

    def test_successful_screen_returns_brokerage_type(self) -> None:
        with patch("requests.Session.get", return_value=self._resp(text=_TABLE_HTML)), \
             patch("time.sleep"):
            result = fetch_screener_scanner()
        self.assertEqual(result["type"], "brokerage")
        self.assertGreater(len(result["articles"]), 0)

    def test_login_wall_response_is_ignored(self) -> None:
        resp = self._resp(text=_TABLE_HTML, url="https://www.screener.in/login/?next=/screen/raw/")
        with patch("requests.Session.get", return_value=resp), \
             patch("time.sleep"), \
             patch("tools.screener_scanner._gnews_fallback", return_value=[]):
            result = fetch_screener_scanner()
        self.assertEqual(result["type"], "news")
        self.assertEqual(result["articles"], [])

    def test_no_results_falls_back_to_gnews(self) -> None:
        with patch("requests.Session.get", return_value=self._resp(text="<html>nothing</html>")), \
             patch("time.sleep"), \
             patch("tools.screener_scanner._gnews_fallback", return_value=[{"title": "fallback"}]):
            result = fetch_screener_scanner()
        self.assertEqual(result["type"], "news")
        self.assertEqual(result["articles"], [{"title": "fallback"}])

    def test_network_exception_on_one_query_does_not_abort_the_other(self) -> None:
        with patch("requests.Session.get", side_effect=[ConnectionError("boom"), self._resp(text=_TABLE_HTML)]), \
             patch("time.sleep"):
            result = fetch_screener_scanner()
        self.assertGreater(len(result["articles"]), 0)

    def test_deduplicates_across_the_two_queries(self) -> None:
        with patch("requests.Session.get", return_value=self._resp(text=_TABLE_HTML)), \
             patch("time.sleep"):
            result = fetch_screener_scanner()
        titles = [a["title"] for a in result["articles"]]
        self.assertEqual(len(titles), len(set(titles)))


if __name__ == "__main__":
    unittest.main()
