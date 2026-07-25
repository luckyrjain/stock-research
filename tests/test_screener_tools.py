import json
import unittest
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from tools.screener_tools import (
    _clean,
    _extract_compounded_growth,
    _extract_growth_metrics,
    _extract_latest_metric_from_tables,
    _extract_quarterly_trend,
    _extract_valuation_band,
    _parse_peer_table,
    _resolve_screener_slug,
    get_fundamentals,
    get_holdings,
    get_peer_comparison,
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

    def test_includes_quarterly_trend_when_present(self) -> None:
        html = """
        <html><body>
          <ul id="top-ratios"></ul>
          <section id="quarters">
            <table>
              <thead><tr><th></th><th>Mar 2024</th><th>Jun 2024</th></tr></thead>
              <tbody>
                <tr><td>Sales</td><td>1000</td><td>1100</td></tr>
                <tr><td>EPS in Rs</td><td>10</td><td>11</td></tr>
              </tbody>
            </table>
          </section>
        </body></html>
        """
        with patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup(html, "lxml")):
            result_str = get_fundamentals.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertEqual(result["quarterly_trend"]["quarters"], ["Mar 2024", "Jun 2024"])
        self.assertEqual(result["quarterly_trend"]["revenue"], [1000.0, 1100.0])

    def test_omits_quarterly_trend_when_absent(self) -> None:
        html = "<html><body><ul id='top-ratios'></ul></body></html>"
        with patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup(html, "lxml")):
            result_str = get_fundamentals.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertNotIn("quarterly_trend", result)


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

    def test_extracts_pledge_percentage_into_own_field(self) -> None:
        html = """
        <html><body>
          <section id="shareholding">
            <table><tbody>
              <tr><td>Promoters+</td><td>50%</td><td>52.5%</td></tr>
              <tr><td>Pledged percentage</td><td>0%</td><td>3.2%</td></tr>
              <tr><td>FIIs</td><td>10%</td><td>12%</td></tr>
            </tbody></table>
          </section>
        </body></html>
        """
        with patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup(html, "lxml")):
            result_str = get_holdings.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertEqual(result["pledge_pct"], 3.2)
        self.assertNotIn("Pledged percentage", result["shareholding_pattern"])
        self.assertEqual(result["shareholding_pattern"]["Promoters"], 52.5)

    def test_no_pledge_row_omits_pledge_field(self) -> None:
        html = """
        <html><body>
          <section id="shareholding">
            <table><tbody>
              <tr><td>Promoters+</td><td>50%</td></tr>
            </tbody></table>
          </section>
        </body></html>
        """
        with patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup(html, "lxml")):
            result_str = get_holdings.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertNotIn("pledge_pct", result)


class ExtractQuarterlyTrendTest(unittest.TestCase):
    def _table_html(self, sales_row: str = None, eps_row: str = None, opm_row: str = None) -> str:
        rows = []
        if sales_row is not None:
            rows.append(f"<tr><td>Sales</td>{sales_row}</tr>")
        if eps_row is not None:
            rows.append(f"<tr><td>EPS in Rs</td>{eps_row}</tr>")
        if opm_row is not None:
            rows.append(f"<tr><td>OPM %</td>{opm_row}</tr>")
        return f"""
        <section id="quarters">
          <table>
            <thead><tr><th></th><th>Mar 2024</th><th>Jun 2024</th><th>Sep 2024</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </section>
        """

    def test_extracts_aligned_revenue_and_eps_series(self) -> None:
        html = self._table_html(
            sales_row="<td>1000</td><td>1100</td><td>1200</td>",
            eps_row="<td>10.5</td><td>11.0</td><td>11.8</td>",
        )
        soup = BeautifulSoup(html, "lxml")
        trend = _extract_quarterly_trend(soup)
        self.assertEqual(trend["quarters"], ["Mar 2024", "Jun 2024", "Sep 2024"])
        self.assertEqual(trend["revenue"], [1000.0, 1100.0, 1200.0])
        self.assertEqual(trend["eps"], [10.5, 11.0, 11.8])

    def test_caps_to_max_periods(self) -> None:
        html = """
        <section id="quarters">
          <table>
            <thead><tr><th></th><th>Q1</th><th>Q2</th><th>Q3</th><th>Q4</th></tr></thead>
            <tbody>
              <tr><td>Sales</td><td>100</td><td>200</td><td>300</td><td>400</td></tr>
              <tr><td>EPS in Rs</td><td>1</td><td>2</td><td>3</td><td>4</td></tr>
            </tbody>
          </table>
        </section>
        """
        soup = BeautifulSoup(html, "lxml")
        trend = _extract_quarterly_trend(soup, max_periods=2)
        self.assertEqual(trend["quarters"], ["Q3", "Q4"])
        self.assertEqual(trend["revenue"], [300.0, 400.0])
        self.assertEqual(trend["eps"], [3.0, 4.0])

    def test_missing_section_returns_empty_dict(self) -> None:
        soup = BeautifulSoup("<html></html>", "lxml")
        self.assertEqual(_extract_quarterly_trend(soup), {})

    def test_missing_eps_row_returns_empty_dict(self) -> None:
        html = self._table_html(sales_row="<td>1000</td><td>1100</td><td>1200</td>")
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(_extract_quarterly_trend(soup), {})

    def test_unparseable_cell_in_window_returns_empty_dict(self) -> None:
        html = self._table_html(
            sales_row="<td>-</td><td>1100</td><td>1200</td>",
            eps_row="<td>10.5</td><td>11.0</td><td>11.8</td>",
        )
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(_extract_quarterly_trend(soup), {})

    def test_includes_operating_margin_when_present(self) -> None:
        html = self._table_html(
            sales_row="<td>1000</td><td>1100</td><td>1200</td>",
            eps_row="<td>10.5</td><td>11.0</td><td>11.8</td>",
            opm_row="<td>18.5%</td><td>19.2%</td><td>20.1%</td>",
        )
        soup = BeautifulSoup(html, "lxml")
        trend = _extract_quarterly_trend(soup)
        self.assertEqual(trend["operating_margin"], [18.5, 19.2, 20.1])

    def test_omits_operating_margin_when_row_absent(self) -> None:
        # Banks/NBFCs routinely omit an OPM % row entirely — absent rather
        # than guessed, revenue/eps must still come through on their own.
        html = self._table_html(
            sales_row="<td>1000</td><td>1100</td><td>1200</td>",
            eps_row="<td>10.5</td><td>11.0</td><td>11.8</td>",
        )
        soup = BeautifulSoup(html, "lxml")
        trend = _extract_quarterly_trend(soup)
        self.assertNotIn("operating_margin", trend)
        self.assertEqual(trend["revenue"], [1000.0, 1100.0, 1200.0])

    def test_omits_operating_margin_when_unparseable_cell_present(self) -> None:
        html = self._table_html(
            sales_row="<td>1000</td><td>1100</td><td>1200</td>",
            eps_row="<td>10.5</td><td>11.0</td><td>11.8</td>",
            opm_row="<td>-</td><td>19.2%</td><td>20.1%</td>",
        )
        soup = BeautifulSoup(html, "lxml")
        trend = _extract_quarterly_trend(soup)
        self.assertNotIn("operating_margin", trend)
        self.assertEqual(trend["revenue"], [1000.0, 1100.0, 1200.0])

    def test_omits_operating_margin_when_row_shorter_than_window(self) -> None:
        # OPM % row present but with fewer cells than the Sales/EPS window
        # (e.g. Screener only started reporting it partway through this
        # stock's history) — must be omitted entirely rather than risk
        # aligning the wrong quarters against periods_n/revenue_n/eps_n.
        html = """
        <section id="quarters">
          <table>
            <thead><tr><th></th><th>Q1</th><th>Q2</th><th>Q3</th><th>Q4</th></tr></thead>
            <tbody>
              <tr><td>Sales</td><td>100</td><td>200</td><td>300</td><td>400</td></tr>
              <tr><td>EPS in Rs</td><td>1</td><td>2</td><td>3</td><td>4</td></tr>
              <tr><td>OPM %</td><td>14%</td><td>16%</td></tr>
            </tbody>
          </table>
        </section>
        """
        soup = BeautifulSoup(html, "lxml")
        trend = _extract_quarterly_trend(soup)
        self.assertNotIn("operating_margin", trend)
        self.assertEqual(trend["revenue"], [100.0, 200.0, 300.0, 400.0])
        self.assertEqual(trend["eps"], [1.0, 2.0, 3.0, 4.0])

    def test_operating_margin_capped_to_max_periods_window(self) -> None:
        html = """
        <section id="quarters">
          <table>
            <thead><tr><th></th><th>Q1</th><th>Q2</th><th>Q3</th><th>Q4</th></tr></thead>
            <tbody>
              <tr><td>Sales</td><td>100</td><td>200</td><td>300</td><td>400</td></tr>
              <tr><td>EPS in Rs</td><td>1</td><td>2</td><td>3</td><td>4</td></tr>
              <tr><td>OPM %</td><td>10%</td><td>12%</td><td>14%</td><td>16%</td></tr>
            </tbody>
          </table>
        </section>
        """
        soup = BeautifulSoup(html, "lxml")
        trend = _extract_quarterly_trend(soup, max_periods=2)
        self.assertEqual(trend["operating_margin"], [14.0, 16.0])


class ExtractValuationBandTest(unittest.TestCase):
    def _ratios_html(self, pe_row: str = None, years: str = None) -> str:
        years = years or "<th>Mar 2020</th><th>Mar 2021</th><th>Mar 2022</th><th>Mar 2023</th><th>Mar 2024</th>"
        rows = f"<tr><td>Price to Earning</td>{pe_row}</tr>" if pe_row is not None else ""
        return f"""
        <section id="ratios">
          <table>
            <thead><tr><th></th>{years}</tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </section>
        """

    def test_extracts_last_five_years(self) -> None:
        html = self._ratios_html(pe_row="<td>18.2</td><td>20.1</td><td>22.5</td><td>19.8</td><td>24.3</td>")
        soup = BeautifulSoup(html, "lxml")
        band = _extract_valuation_band(soup)
        self.assertEqual(band["years"], ["Mar 2020", "Mar 2021", "Mar 2022", "Mar 2023", "Mar 2024"])
        self.assertEqual(band["pe"], [18.2, 20.1, 22.5, 19.8, 24.3])

    def test_caps_to_max_years(self) -> None:
        html = self._ratios_html(pe_row="<td>18.2</td><td>20.1</td><td>22.5</td><td>19.8</td><td>24.3</td>")
        soup = BeautifulSoup(html, "lxml")
        band = _extract_valuation_band(soup, max_years=3)
        self.assertEqual(band["years"], ["Mar 2022", "Mar 2023", "Mar 2024"])
        self.assertEqual(band["pe"], [22.5, 19.8, 24.3])

    def test_missing_section_returns_empty_dict(self) -> None:
        soup = BeautifulSoup("<html></html>", "lxml")
        self.assertEqual(_extract_valuation_band(soup), {})

    def test_missing_pe_row_returns_empty_dict(self) -> None:
        html = self._ratios_html(pe_row=None)
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(_extract_valuation_band(soup), {})

    def test_fewer_than_three_years_returns_empty_dict(self) -> None:
        html = self._ratios_html(
            pe_row="<td>19.8</td><td>24.3</td>",
            years="<th>Mar 2023</th><th>Mar 2024</th>",
        )
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(_extract_valuation_band(soup), {})

    def test_unparseable_cell_in_window_returns_empty_dict(self) -> None:
        html = self._ratios_html(pe_row="<td>-</td><td>20.1</td><td>22.5</td><td>19.8</td><td>24.3</td>")
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(_extract_valuation_band(soup), {})

    def test_row_with_extra_trailing_ttm_column_returns_empty_dict(self) -> None:
        # A trailing "TTM" column only the header (not this data row) carries
        # would otherwise mispair each real P/E with the wrong year once both
        # lists are independently truncated by [-n:] — must bail out instead
        # of silently misaligning years to values.
        html = self._ratios_html(
            years="<th>Mar 2021</th><th>Mar 2022</th><th>Mar 2023</th><th>Mar 2024</th><th>TTM</th>",
            pe_row="<td>18.2</td><td>20.1</td><td>22.5</td><td>19.8</td>",
        )
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(_extract_valuation_band(soup), {})

    def test_row_with_extra_leading_cell_returns_empty_dict(self) -> None:
        html = self._ratios_html(
            pe_row="<td>x</td><td>18.2</td><td>20.1</td><td>22.5</td><td>19.8</td><td>24.3</td>",
        )
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(_extract_valuation_band(soup), {})


class ParsePeerTableTest(unittest.TestCase):
    def test_parses_headers_and_rows_generically(self) -> None:
        html = """
        <section id="peers">
          <table>
            <thead><tr><th></th><th>Name</th><th>P/E</th><th>ROCE %</th></tr></thead>
            <tbody>
              <tr><td>1.</td><td><a href="/company/TCS/">TCS</a></td><td>28</td><td>52</td></tr>
              <tr><td>2.</td><td><a href="/company/INFY/">Infosys</a></td><td>25</td><td>32</td></tr>
            </tbody>
          </table>
        </section>
        """
        soup = BeautifulSoup(html, "lxml")
        result = _parse_peer_table(soup)
        self.assertEqual(result["columns"], ["", "Name", "P/E", "ROCE %"])
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["name"], "TCS")
        self.assertEqual(result["rows"][0]["slug"], "TCS")
        self.assertEqual(result["rows"][0]["values"]["P/E"], "28")

    def test_row_with_mismatched_cell_count_is_skipped(self) -> None:
        html = """
        <section id="peers">
          <table>
            <thead><tr><th>Name</th><th>P/E</th></tr></thead>
            <tbody><tr><td>Only one cell</td></tr></tbody>
          </table>
        </section>
        """
        soup = BeautifulSoup(html, "lxml")
        result = _parse_peer_table(soup)
        self.assertEqual(result["rows"], [])

    def test_no_peers_section_returns_empty(self) -> None:
        soup = BeautifulSoup("<html></html>", "lxml")
        self.assertEqual(_parse_peer_table(soup), {"columns": [], "rows": []})

    def test_no_table_in_section_returns_empty(self) -> None:
        soup = BeautifulSoup("<section id='peers'></section>", "lxml")
        self.assertEqual(_parse_peer_table(soup), {"columns": [], "rows": []})


class GetPeerComparisonTest(unittest.TestCase):
    def test_resolves_slug_only_once_and_passes_it_to_fetch(self) -> None:
        # Regression test: get_peer_comparison used to resolve the Screener slug
        # both explicitly and again inside _fetch_soup(), wasting a request and
        # risking a self-row mismatch if the two resolutions ever disagreed.
        html = """
        <section id="peers">
          <table>
            <thead><tr><th>Name</th><th>P/E</th></tr></thead>
            <tbody><tr><td><a href="/company/TCS/">TCS</a></td><td>28</td></tr></tbody>
          </table>
        </section>
        """
        resolve_mock = MagicMock(return_value="TCS")
        fetch_soup_mock = MagicMock(return_value=BeautifulSoup(html, "lxml"))
        with patch("tools.screener_tools._resolve_screener_slug", resolve_mock), \
             patch("tools.screener_tools._fetch_soup", fetch_soup_mock):
            get_peer_comparison.run(symbol="TCS")
        resolve_mock.assert_called_once_with("TCS")
        fetch_soup_mock.assert_called_once_with("TCS", slug="TCS")

    def test_splits_self_median_and_peers(self) -> None:
        html = """
        <section id="peers">
          <table>
            <thead><tr><th>Name</th><th>P/E</th><th>ROCE %</th></tr></thead>
            <tbody>
              <tr><td><a href="/company/TCS/">TCS</a></td><td>28</td><td>52</td></tr>
              <tr><td><a href="/company/INFY/">Infosys</a></td><td>25</td><td>32</td></tr>
              <tr><td><a href="/company/WIPRO/">Wipro</a></td><td>20</td><td>18</td></tr>
              <tr><td>Median</td><td>22</td><td>28</td></tr>
            </tbody>
          </table>
        </section>
        """
        with patch("tools.screener_tools._resolve_screener_slug", return_value="TCS"), \
             patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup(html, "lxml")):
            result_str = get_peer_comparison.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(result["self"]["name"], "TCS")
        self.assertEqual(result["sector_median"]["values"]["P/E"], "22")
        self.assertEqual(len(result["peers"]), 2)
        self.assertNotIn("TCS", [p["name"] for p in result["peers"]])

    def test_caps_peers_at_five(self) -> None:
        rows_html = "".join(
            f'<tr><td><a href="/company/PEER{i}/">Peer {i}</a></td><td>{20 + i}</td></tr>'
            for i in range(7)
        )
        html = f"""
        <section id="peers">
          <table>
            <thead><tr><th>Name</th><th>P/E</th></tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </section>
        """
        with patch("tools.screener_tools._resolve_screener_slug", return_value="TCS"), \
             patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup(html, "lxml")):
            result_str = get_peer_comparison.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertEqual(len(result["peers"]), 5)

    def test_no_peers_section_returns_empty_lists(self) -> None:
        with patch("tools.screener_tools._resolve_screener_slug", return_value="TCS"), \
             patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup("<html></html>", "lxml")):
            result_str = get_peer_comparison.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertIsNone(result["self"])
        self.assertIsNone(result["sector_median"])
        self.assertEqual(result["peers"], [])

    def test_fetch_failure_returns_error_payload_not_raise(self) -> None:
        with patch("tools.screener_tools._fetch_soup", side_effect=ConnectionError("boom")):
            result_str = get_peer_comparison.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertIn("error", result)
        self.assertEqual(result["symbol"], "TCS")

    def test_includes_valuation_band_when_ratios_section_present(self) -> None:
        html = """
        <section id="peers">
          <table>
            <thead><tr><th>Name</th><th>P/E</th></tr></thead>
            <tbody><tr><td><a href="/company/TCS/">TCS</a></td><td>28</td></tr></tbody>
          </table>
        </section>
        <section id="ratios">
          <table>
            <thead><tr><th></th><th>Mar 2022</th><th>Mar 2023</th><th>Mar 2024</th></tr></thead>
            <tbody><tr><td>Price to Earning</td><td>22.5</td><td>19.8</td><td>24.3</td></tr></tbody>
          </table>
        </section>
        """
        with patch("tools.screener_tools._resolve_screener_slug", return_value="TCS"), \
             patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup(html, "lxml")):
            result_str = get_peer_comparison.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertEqual(result["valuation_band"]["pe"], [22.5, 19.8, 24.3])

    def test_omits_valuation_band_when_ratios_section_absent(self) -> None:
        html = """
        <section id="peers">
          <table>
            <thead><tr><th>Name</th><th>P/E</th></tr></thead>
            <tbody><tr><td><a href="/company/TCS/">TCS</a></td><td>28</td></tr></tbody>
          </table>
        </section>
        """
        with patch("tools.screener_tools._resolve_screener_slug", return_value="TCS"), \
             patch("tools.screener_tools._fetch_soup", return_value=BeautifulSoup(html, "lxml")):
            result_str = get_peer_comparison.run(symbol="TCS")
        result = json.loads(result_str)
        self.assertNotIn("valuation_band", result)


if __name__ == "__main__":
    unittest.main()
