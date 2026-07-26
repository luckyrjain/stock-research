import unittest
from unittest.mock import MagicMock, patch

from tools.trendlyne_scraper import (
    _parse_numeric_consensus,
    _resolve_trendlyne_url,
    fetch_trendlyne_numeric_consensus,
)


def _resp(status_code=200, text="", url="https://trendlyne.com/equity/1/TCS/tata-consultancy/"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.url = url
    resp.raise_for_status = MagicMock()
    return resp


class ParseNumericConsensusTest(unittest.TestCase):
    def test_extracts_all_fields_when_cleanly_present(self) -> None:
        text = (
            "TCS Overview 28 Analysts have covered this stock. "
            "Consensus Recommendation: BUY. Mean Target Price ₹4,250.50. "
            "12.3 % Upside from current price."
        )
        fields = _parse_numeric_consensus(text)
        self.assertEqual(fields["analyst_count"], 28)
        self.assertEqual(fields["consensus_rating"], "BUY")
        self.assertAlmostEqual(fields["mean_target_price"], 4250.50)
        self.assertAlmostEqual(fields["target_upside_pct"], 12.3)

    def test_downside_is_negated(self) -> None:
        fields = _parse_numeric_consensus("8.5 % Downside expected")
        self.assertAlmostEqual(fields["target_upside_pct"], -8.5)

    def test_negative_upside_value_is_not_double_negated(self) -> None:
        # If the page itself already renders a signed negative number
        # alongside "Downside", trust that value rather than flipping it.
        fields = _parse_numeric_consensus("-8.5 % Downside expected")
        self.assertAlmostEqual(fields["target_upside_pct"], -8.5)

    def test_all_fields_none_when_nothing_matches(self) -> None:
        fields = _parse_numeric_consensus("This page has no analyst data at all.")
        self.assertIsNone(fields["analyst_count"])
        self.assertIsNone(fields["consensus_rating"])
        self.assertIsNone(fields["mean_target_price"])
        self.assertIsNone(fields["target_upside_pct"])

    def test_partial_page_yields_partial_result(self) -> None:
        # A rating shown but no target price must not discard the rating.
        fields = _parse_numeric_consensus("Consensus Recommendation: HOLD only, nothing else here.")
        self.assertEqual(fields["consensus_rating"], "HOLD")
        self.assertIsNone(fields["mean_target_price"])

    def test_strong_buy_and_strong_sell_are_not_shadowed_by_bare_buy_sell(self) -> None:
        self.assertEqual(_parse_numeric_consensus("Consensus Recommendation: STRONG BUY")["consensus_rating"], "STRONG BUY")
        self.assertEqual(_parse_numeric_consensus("Consensus Recommendation: STRONG SELL")["consensus_rating"], "STRONG SELL")


class ResolveTrendlyneUrlTest(unittest.TestCase):
    def test_direct_url_used_when_it_resolves_to_an_equity_page(self) -> None:
        with patch("tools.trendlyne_scraper.requests.get", return_value=_resp(200)) as mocked:
            url = _resolve_trendlyne_url("TCS")
        self.assertEqual(url, "https://trendlyne.com/equity/1/TCS/tata-consultancy/")
        mocked.assert_called_once()

    def test_falls_back_to_search_when_direct_url_404s(self) -> None:
        direct_404 = _resp(404)
        search_html = '<a href="/equity/2/INFY/infosys/">Infosys</a>'
        search_ok = _resp(200, text=search_html)
        with patch("tools.trendlyne_scraper.requests.get", side_effect=[direct_404, search_ok]):
            url = _resolve_trendlyne_url("INFY")
        self.assertEqual(url, "https://trendlyne.com/equity/2/INFY/infosys/")

    def test_returns_none_when_neither_direct_nor_search_resolves(self) -> None:
        with patch("tools.trendlyne_scraper.requests.get", return_value=_resp(404)):
            url = _resolve_trendlyne_url("NOSUCHSTOCK")
        self.assertIsNone(url)

    def test_network_failure_on_direct_url_falls_through_to_search(self) -> None:
        search_html = '<a href="/equity/3/WIPRO/wipro/">Wipro</a>'
        with patch(
            "tools.trendlyne_scraper.requests.get",
            side_effect=[ConnectionError("boom"), _resp(200, text=search_html)],
        ):
            url = _resolve_trendlyne_url("WIPRO")
        self.assertEqual(url, "https://trendlyne.com/equity/3/WIPRO/wipro/")

    def test_network_failure_on_both_returns_none_not_raise(self) -> None:
        with patch("tools.trendlyne_scraper.requests.get", side_effect=ConnectionError("boom")):
            url = _resolve_trendlyne_url("TCS")
        self.assertIsNone(url)


class FetchTrendlyneNumericConsensusTest(unittest.TestCase):
    def test_full_success_path(self) -> None:
        page = _resp(200, text="<html><body>30 Analysts. Consensus Recommendation: BUY. Mean Target Price ₹1000.</body></html>")
        with patch("tools.trendlyne_scraper._resolve_trendlyne_url", return_value="https://trendlyne.com/equity/1/TCS/tcs/"), \
             patch("tools.trendlyne_scraper.requests.get", return_value=page):
            result = fetch_trendlyne_numeric_consensus("tcs")

        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(result["analyst_count"], 30)
        self.assertEqual(result["consensus_rating"], "BUY")
        self.assertAlmostEqual(result["mean_target_price"], 1000.0)
        self.assertEqual(result["source_url"], "https://trendlyne.com/equity/1/TCS/tcs/")
        self.assertNotIn("error", result)

    def test_unresolvable_symbol_returns_all_none_not_an_error(self) -> None:
        with patch("tools.trendlyne_scraper._resolve_trendlyne_url", return_value=None):
            result = fetch_trendlyne_numeric_consensus("NOSUCHSTOCK")

        self.assertEqual(result["symbol"], "NOSUCHSTOCK")
        self.assertIsNone(result["analyst_count"])
        self.assertIsNone(result["consensus_rating"])
        self.assertIsNone(result["mean_target_price"])
        self.assertIsNone(result["target_upside_pct"])
        self.assertIsNone(result["source_url"])
        self.assertNotIn("error", result)

    def test_page_fetch_failure_degrades_to_all_none_with_error_key(self) -> None:
        with patch("tools.trendlyne_scraper._resolve_trendlyne_url", return_value="https://trendlyne.com/equity/1/TCS/tcs/"), \
             patch("tools.trendlyne_scraper.requests.get", side_effect=ConnectionError("connection refused")):
            result = fetch_trendlyne_numeric_consensus("TCS")

        self.assertIsNone(result["analyst_count"])
        self.assertIsNone(result["mean_target_price"])
        self.assertIn("error", result)

    def test_symbol_is_uppercased_and_stripped(self) -> None:
        with patch("tools.trendlyne_scraper._resolve_trendlyne_url", return_value=None):
            result = fetch_trendlyne_numeric_consensus("  infy  ")
        self.assertEqual(result["symbol"], "INFY")

    def test_non_string_symbol_does_not_raise(self) -> None:
        result = fetch_trendlyne_numeric_consensus(None)
        self.assertEqual(result["symbol"], "")
        self.assertIn("error", result)

    def test_never_raises_on_unexpected_exception(self) -> None:
        with patch("tools.trendlyne_scraper._resolve_trendlyne_url", side_effect=RuntimeError("boom")):
            result = fetch_trendlyne_numeric_consensus("TCS")
        self.assertIn("error", result)
        self.assertIsNone(result["consensus_rating"])


if __name__ == "__main__":
    unittest.main()
