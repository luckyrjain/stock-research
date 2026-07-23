import json
import unittest
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from tools.screener_tools import (
    _clean,
    _extract_compounded_growth,
    _extract_growth_metrics,
    _extract_latest_metric_from_tables,
    _resolve_screener_slug,
    get_fundamentals,
    get_holdings,
)


class CleanTest(unittest.TestCase):
    def test_strips_currency_symbol_and_commas(self) -> None:
        self.assertEqual(_clean(" ₹1,234.50 "), "1234.50")


class ExtractCompoundedGrowthTest(unittest.TestCase):
    def test_extracts_percentage_after_period_label(self) -> None:
        text = "Compounded Sales Growth 10 Years: 12% 5 Years: 18% 3 Years: 25%"
        self.assertEqual(_extract_compounded_growth(text, "Compounded Sales Growth", "3 Years"), "25%")

    def test_negative_percentage(self) -> None:
        text = "Compounded Profit Growth 3 Years: -8.5%"
        self.assertEqual(_extract_compounded_growth(text, "Compounded Profit Growth", "3 Years"), "-8.5%")

    def test_no_match_returns_empty_string(self) -> None:
        self.assertEqual(_extract_compounded_growth("nothing here", "Compounded Sales Growth", "3 Years"), "")


class ExtractGrowthMetricsTest(unittest.TestCase):
    def test_extracts_from_dedicated_block(self) -> None:
        html = """
        <div>Compounded Sales Growth 3 Years: 22% 5 Years: 19%</div>
        <div>Compounded Profit Growth 3 Years: 30% 5 Years: 28%</div>
        """
        soup = BeautifulSoup(html, "lxml")
        metrics = _extract_growth_metrics(soup)
        self.assertEqual(metrics["Sales growth 3Y"], "22%")
        self.assertEqual(metrics["Sales growth 5Y"], "19%")
        self.assertEqual(metrics["Profit growth 3Y"], "30%")

    def test_missing_block_falls_back_to_full_text_scan(self) -> None:
        html = "<p>Random text. Compounded Sales Growth: 3 Years : 14%</p>"
        soup = BeautifulSoup(html, "lxml")
        metrics = _extract_growth_metrics(soup)
        self.assertEqual(metrics.get("Sales growth 3Y"), "14%")

    def test_no_data_returns_empty_dict(self) -> None:
        soup = BeautifulSoup("<p>nothing relevant</p>", "lxml")
        self.assertEqual(_extract_growth_metrics(soup), {})


class ExtractLatestMetricFromTablesTest(unittest.TestCase):
    def test_returns_last_nonempty_value_in_row(self) -> None:
        html = """
        <table><tr><th>EBITDA Margin</th><td>18%</td><td>20%</td><td>-</td></tr></table>
        """
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(_extract_latest_metric_from_tables(soup, ("EBITDA Margin",)), "20%")

    def test_matches_alias_label(self) -> None:
        html = "<table><tr><th>OPM %</th><td>15%</td></tr></table>"
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(_extract_latest_metric_from_tables(soup, ("EBITDA Margin", "OPM %")), "15%")

    def test_no_matching_row_returns_empty_string(self) -> None:
        soup = BeautifulSoup("<table><tr><th>Revenue</th><td>100</td></tr></table>", "lxml")
        self.assertEqual(_extract_latest_metric_from_tables(soup, ("EBITDA Margin",)), "")


class ResolveScreenerSlugTest(unittest.TestCase):
    def test_direct_url_200_uses_symbol_as_slug(self) -> None:
        resp = MagicMock(status_code=200)
        with patch("requests.get", return_value=resp):
            self.assertEqual(_resolve_screener_slug("tcs"), "TCS")

    def test_404_falls_back_to_search_and_extracts_slug(self) -> None:
        direct_resp = MagicMock(status_code=404)
        search_resp = MagicMock(ok=True)
        search_resp.json.return_value = [{"url": "/company/500325/reliance-industries/"}]
        with patch("requests.get", side_effect=[direct_resp, search_resp]):
            self.assertEqual(_resolve_screener_slug("reliance"), "500325")

    def test_404_and_no_search_results_returns_original_symbol(self) -> None:
        direct_resp = MagicMock(status_code=404)
        search_resp = MagicMock(ok=True)
        search_resp.json.return_value = []
        with patch("requests.get", side_effect=[direct_resp, search_resp]):
            self.assertEqual(_resolve_screener_slug("nosuch"), "NOSUCH")


class GetFundamentalsTest(unittest.TestCase):
    def test_parses_ratios_growth_and_about(self) -> None:
        html = """
        <html><body>
          <ul id="top-ratios">
            <li><span class="name">Market Cap</span><span class="number">₹1,20,000 Cr</span></li>
            <li><span class="name">P/E</span><span class="number">28.5</span></li>
          </ul>
          <div>Compounded Sales Growth 3 Years: 20%</div>
          <table><tr><th>OPM %</th><td>22%</td></tr></table>
          <div class="company-profile"><p>Leading IT services company.</p></div>
        </body></html>
        """
        with patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup(html, "lxml")):
            result_str = get_fundamentals.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(result["ratios"]["P/E"], "28.5")
        self.assertEqual(result["ratios"]["Sales growth 3Y"], "20%")
        self.assertEqual(result["ratios"]["EBITDA margin"], "22%")
        self.assertIn("IT services", result["about"])

    def test_fetch_failure_returns_error_payload_not_raise(self) -> None:
        with patch("tools.screener_tools._fetch_soup", side_effect=ConnectionError("boom")):
            result_str = get_fundamentals.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertIn("error", result)
        self.assertEqual(result["symbol"], "TCS")


class GetHoldingsTest(unittest.TestCase):
    def test_parses_latest_quarter_shareholding(self) -> None:
        html = """
        <html><body>
          <section id="shareholding">
            <table><tbody>
              <tr><td>Promoters+</td><td>50%</td><td>52.5%</td></tr>
              <tr><td>FIIs</td><td>10%</td><td>12%</td></tr>
              <tr><td>No. of Shareholders</td><td>1000</td><td>1200</td></tr>
            </tbody></table>
          </section>
        </body></html>
        """
        with patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup(html, "lxml")):
            result_str = get_holdings.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertEqual(result["shareholding_pattern"]["Promoters"], 52.5)
        self.assertEqual(result["shareholding_pattern"]["FIIs"], 12.0)
        self.assertNotIn("No. of Shareholders", result["shareholding_pattern"])

    def test_missing_shareholding_section_returns_empty_dict(self) -> None:
        with patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup("<html></html>", "lxml")):
            result_str = get_holdings.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertEqual(result["shareholding_pattern"], {})

    def test_fetch_failure_returns_error_payload_not_raise(self) -> None:
        with patch("tools.screener_tools._fetch_soup", side_effect=ConnectionError("boom")):
            result_str = get_holdings.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
