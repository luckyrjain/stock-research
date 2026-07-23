import json
import unittest
from unittest.mock import MagicMock, patch

from lxml import etree

from tools.nse_tools import _build_quote_payload, _is_valid_quote, get_mf_holdings, get_stock_quote


class IsValidQuoteTest(unittest.TestCase):
    def test_valid_quote_with_current_price(self) -> None:
        self.assertTrue(_is_valid_quote({"currentPrice": 100, "marketCap": 5_000_000_000}))

    def test_falls_back_to_regular_market_price(self) -> None:
        self.assertTrue(_is_valid_quote({"regularMarketPrice": 100, "marketCap": 5_000_000_000}))

    def test_missing_price_is_invalid(self) -> None:
        self.assertFalse(_is_valid_quote({"marketCap": 5_000_000_000}))

    def test_missing_market_cap_is_invalid(self) -> None:
        self.assertFalse(_is_valid_quote({"currentPrice": 100}))

    def test_absurdly_high_price_is_invalid(self) -> None:
        # yfinance sometimes returns junk data (e.g. a currency-unit mismatch)
        # for delisted/illiquid tickers; guard against treating that as real.
        self.assertFalse(_is_valid_quote({"currentPrice": 600_000, "marketCap": 5_000_000_000}))


class BuildQuotePayloadTest(unittest.TestCase):
    def test_computes_change_pct_from_previous_close(self) -> None:
        payload = _build_quote_payload("TCS", "NSE", {
            "currentPrice": 110, "previousClose": 100, "marketCap": 1e10,
        })
        self.assertEqual(payload["change_pct"], 10.0)
        self.assertEqual(payload["market_cap_cr"], 1000.0)

    def test_missing_previous_close_falls_back_to_price_with_zero_change(self) -> None:
        payload = _build_quote_payload("TCS", "NSE", {"currentPrice": 110, "marketCap": 1e10})
        self.assertEqual(payload["previous_close"], 110)
        self.assertEqual(payload["change_pct"], 0.0)

    def test_dividend_yield_as_decimal_is_converted_to_percent(self) -> None:
        payload = _build_quote_payload("TCS", "NSE", {"currentPrice": 100, "dividendYield": 0.025})
        self.assertEqual(payload["dividend_yield_pct"], 2.5)

    def test_dividend_yield_already_in_percent_form_is_treated_as_bad_data(self) -> None:
        payload = _build_quote_payload("TCS", "NSE", {"currentPrice": 100, "dividendYield": 258.65})
        self.assertEqual(payload["dividend_yield_pct"], 0)

    def test_company_name_prefers_long_name(self) -> None:
        payload = _build_quote_payload("TCS", "NSE", {
            "currentPrice": 100, "longName": "Tata Consultancy Services", "shortName": "TCS",
        })
        self.assertEqual(payload["company_name"], "Tata Consultancy Services")

    def test_about_is_truncated_to_600_chars(self) -> None:
        payload = _build_quote_payload("TCS", "NSE", {
            "currentPrice": 100, "longBusinessSummary": "x" * 1000,
        })
        self.assertEqual(len(payload["about"]), 600)


class GetStockQuoteTest(unittest.TestCase):
    def test_nse_valid_quote_is_primary(self) -> None:
        fake_ticker = MagicMock()
        fake_ticker.info = {"currentPrice": 100, "marketCap": 1e10, "previousClose": 90}
        with patch("yfinance.Ticker", return_value=fake_ticker):
            result = json.loads(get_stock_quote.run(symbol="tcs"))
        self.assertEqual(result["primary_exchange"], "NSE")
        self.assertIn("NSE", result["prices_by_exchange"])
        self.assertIn("BSE", result["prices_by_exchange"])

    def test_nse_invalid_falls_back_to_bse_as_primary(self) -> None:
        def _ticker(sym):
            m = MagicMock()
            if sym.endswith(".NS"):
                m.info = {}  # no usable price -> invalid
            else:
                m.info = {"currentPrice": 50, "marketCap": 1e9}
            return m
        with patch("yfinance.Ticker", side_effect=_ticker):
            result = json.loads(get_stock_quote.run(symbol="smallcap"))
        self.assertEqual(result["primary_exchange"], "BSE")

    def test_both_exchanges_fail_returns_error_payload(self) -> None:
        fake_ticker = MagicMock()
        fake_ticker.info = {}
        with patch("yfinance.Ticker", return_value=fake_ticker):
            result = json.loads(get_stock_quote.run(symbol="nosuch"))
        self.assertIn("error", result)
        self.assertEqual(result["symbol"], "NOSUCH")

    def test_exception_on_one_exchange_does_not_abort_the_other(self) -> None:
        def _ticker(sym):
            if sym.endswith(".NS"):
                raise ConnectionError("boom")
            m = MagicMock()
            m.info = {"currentPrice": 100, "marketCap": 1e10}
            return m
        with patch("yfinance.Ticker", side_effect=_ticker):
            result = json.loads(get_stock_quote.run(symbol="tcs"))
        self.assertEqual(result["primary_exchange"], "BSE")


class GetMfHoldingsTest(unittest.TestCase):
    def test_no_shareholding_records_returns_error(self) -> None:
        sess = MagicMock()
        master_resp = MagicMock()
        master_resp.json.return_value = []
        sess.get.return_value = master_resp
        with patch("tools.nse_tools._nse_session", return_value=sess):
            result = json.loads(get_mf_holdings.run(symbol="TCS"))
        self.assertIn("error", result)

    def test_missing_xbrl_url_returns_error(self) -> None:
        sess = MagicMock()
        master_resp = MagicMock()
        master_resp.json.return_value = [{"date": "2026-01-01", "xbrl": ""}]
        sess.get.return_value = master_resp
        with patch("tools.nse_tools._nse_session", return_value=sess):
            result = json.loads(get_mf_holdings.run(symbol="TCS"))
        self.assertIn("error", result)

    def test_parses_xbrl_mutual_fund_holdings(self) -> None:
        ns_di = "http://xbrl.org/2006/xbrldi"
        xbrl = f'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:di="{ns_di}">
          <xbrli:context id="D_MF1">
            <xbrli:scenario>
              <di:typedMember><MutualFundsMember/></di:typedMember>
            </xbrli:scenario>
          </xbrli:context>
          <NameOfTheShareholder contextRef="D_MF1">Sample Mutual Fund</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="MF1">0.035</ShareholdingAsAPercentageOfTotalNumberOfShares>
        </xbrli:xbrl>'''

        sess = MagicMock()
        master_resp = MagicMock()
        master_resp.json.return_value = [{"date": "2026-01-01", "xbrl": "https://nse.example/x.xml"}]
        sess.get.return_value = master_resp

        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()

        with patch("tools.nse_tools._nse_session", return_value=sess), \
             patch("requests.get", return_value=xbrl_resp):
            result = json.loads(get_mf_holdings.run(symbol="TCS"))

        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(len(result["mutual_funds"]), 1)
        self.assertEqual(result["mutual_funds"][0]["fund"], "Sample Mutual Fund")
        self.assertAlmostEqual(result["mutual_funds"][0]["holding_pct"], 3.5)

    def test_network_failure_returns_error_not_raise(self) -> None:
        with patch("tools.nse_tools._nse_session", side_effect=ConnectionError("boom")):
            result = json.loads(get_mf_holdings.run(symbol="TCS"))
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
