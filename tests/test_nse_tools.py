import json
import unittest
from unittest.mock import MagicMock, patch

from lxml import etree

from tools.nse_tools import (
    _build_quote_payload,
    _is_valid_quote,
    get_mf_holdings,
    get_nse_basic_ratios,
    get_stock_quote,
)


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

    def test_ambiguous_fraction_close_to_one_is_dropped_not_shown_as_implausible_percent(self) -> None:
        # Regression test: a 0-1 fraction close to 1 used to be multiplied
        # straight into an implausible percent (e.g. a genuine 0.5%-yield
        # value already in percent form, misread as a fraction, would show
        # as a wildly wrong 50%). Rather than trust the format guess, an
        # implausible result is now dropped to 0 ("never invent") instead
        # of displayed as fact.
        payload = _build_quote_payload("TCS", "NSE", {"currentPrice": 100, "dividendYield": 0.5})
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

    def test_malformed_percentage_fact_is_skipped_not_aborting_whole_result(self) -> None:
        # Regression test: one non-numeric percentage fact anywhere in a
        # document with multiple shareholder records used to abort the
        # entire XBRL walk (float() raising, uncaught) — worse than a
        # partial result, since a document can carry dozens of records.
        ns_di = "http://xbrl.org/2006/xbrldi"
        xbrl = f'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:di="{ns_di}">
          <xbrli:context id="D_MF1">
            <xbrli:scenario><di:typedMember><MutualFundsMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <xbrli:context id="D_MF2">
            <xbrli:scenario><di:typedMember><MutualFundsMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <NameOfTheShareholder contextRef="D_MF1">Good Fund</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="MF1">0.035</ShareholdingAsAPercentageOfTotalNumberOfShares>
          <NameOfTheShareholder contextRef="D_MF2">Bad Fund</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="MF2">not-a-number</ShareholdingAsAPercentageOfTotalNumberOfShares>
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

        self.assertEqual(len(result["mutual_funds"]), 1)
        self.assertEqual(result["mutual_funds"][0]["fund"], "Good Fund")

    def test_implausible_holding_pct_is_dropped_from_results(self) -> None:
        # Regression test: same ambiguous-format guard as dividend_yield_pct
        # — a fraction of 0.5 would previously be multiplied into an
        # implausible 50% single-fund holding rather than dropped.
        ns_di = "http://xbrl.org/2006/xbrldi"
        xbrl = f'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:di="{ns_di}">
          <xbrli:context id="D_MF1">
            <xbrli:scenario>
              <di:typedMember><MutualFundsMember/></di:typedMember>
            </xbrli:scenario>
          </xbrli:context>
          <NameOfTheShareholder contextRef="D_MF1">Sample Mutual Fund</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="MF1">0.5</ShareholdingAsAPercentageOfTotalNumberOfShares>
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

        self.assertEqual(result["mutual_funds"], [])


class GetNseBasicRatiosTest(unittest.TestCase):
    def _filings_session(self, filings: list) -> MagicMock:
        sess = MagicMock()
        resp = MagicMock()
        resp.json.return_value = filings
        sess.get.return_value = resp
        return sess

    def test_no_filings_returns_empty_dict(self) -> None:
        with patch("tools.nse_tools._nse_session", return_value=self._filings_session([])):
            self.assertEqual(get_nse_basic_ratios("TCS"), {})

    def test_non_list_response_returns_empty_dict(self) -> None:
        sess = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"error": "blocked"}
        sess.get.return_value = resp
        with patch("tools.nse_tools._nse_session", return_value=sess):
            self.assertEqual(get_nse_basic_ratios("TCS"), {})

    def test_no_xbrl_attachment_returns_empty_dict(self) -> None:
        filings = [{"date": "2026-01-01", "desc": "Financial Results", "attchmntFile": "notice.pdf"}]
        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)):
            self.assertEqual(get_nse_basic_ratios("TCS"), {})

    def test_network_failure_returns_empty_dict_not_raise(self) -> None:
        with patch("tools.nse_tools._nse_session", side_effect=ConnectionError("boom")):
            self.assertEqual(get_nse_basic_ratios("TCS"), {})

    def test_parses_eps_from_xbrl_financial_results_filing(self) -> None:
        filings = [
            {"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://nse.example/results.xml"},
        ]
        xbrl = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <BasicEarningsPerEquityShare contextRef="D1" unitRef="INR">42.5</BasicEarningsPerEquityShare>
        </xbrli:xbrl>'''
        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()

        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", return_value=xbrl_resp):
            result = get_nse_basic_ratios("TCS")

        self.assertEqual(result["eps"], 42.5)
        self.assertEqual(result["source"], "nse_xbrl")
        self.assertEqual(result["as_of_date"], "2026-01-01")

    def test_prefers_financial_results_filing_over_other_categories(self) -> None:
        filings = [
            {"date": "2026-02-01", "desc": "Board Meeting Intimation", "xbrl": "https://nse.example/board.xml"},
            {"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://nse.example/results.xml"},
        ]
        xbrl = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <BasicEarningsPerEquityShare contextRef="D1">10.0</BasicEarningsPerEquityShare>
        </xbrli:xbrl>'''
        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()

        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", return_value=xbrl_resp) as mock_get:
            result = get_nse_basic_ratios("TCS")

        self.assertEqual(result["eps"], 10.0)
        mock_get.assert_called_once_with(
            "https://nse.example/results.xml",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=20,
        )

    def test_unrecognized_tag_names_return_empty_dict(self) -> None:
        filings = [{"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://nse.example/results.xml"}]
        xbrl = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <SomeUnrelatedFact contextRef="D1">99</SomeUnrelatedFact>
        </xbrli:xbrl>'''
        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()

        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", return_value=xbrl_resp):
            self.assertEqual(get_nse_basic_ratios("TCS"), {})

    def test_non_numeric_eps_fact_does_not_raise(self) -> None:
        filings = [{"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://nse.example/results.xml"}]
        xbrl = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <BasicEarningsPerEquityShare contextRef="D1">not-a-number</BasicEarningsPerEquityShare>
        </xbrli:xbrl>'''
        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()

        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", return_value=xbrl_resp):
            self.assertEqual(get_nse_basic_ratios("TCS"), {})

    def test_xbrl_fetch_failure_returns_empty_dict_not_raise(self) -> None:
        filings = [{"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://nse.example/results.xml"}]
        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", side_effect=ConnectionError("boom")):
            self.assertEqual(get_nse_basic_ratios("TCS"), {})

    def test_picks_the_chronologically_newest_filing_not_lexically_last(self) -> None:
        # Regression test: NSE's own dd-Mon-yyyy date format does not sort
        # lexically in calendar order (same drift nse_insider_trades.py's
        # _parse_pit_date already documents/fixes for its own date field).
        # "05-Feb-2026" is chronologically newer than "20-Jan-2026" but
        # lexically SMALLER ("0" < "2"), so a raw string sort would
        # (wrongly) treat the January filing as more recent.
        filings = [
            {"date": "20-Jan-2026", "desc": "Financial Results", "xbrl": "https://nse.example/jan.xml"},
            {"date": "05-Feb-2026", "desc": "Financial Results", "xbrl": "https://nse.example/feb.xml"},
        ]
        xbrl = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <BasicEarningsPerEquityShare contextRef="D1">99.0</BasicEarningsPerEquityShare>
        </xbrli:xbrl>'''
        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()

        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", return_value=xbrl_resp) as mock_get:
            result = get_nse_basic_ratios("TCS")

        self.assertEqual(result["as_of_date"], "05-Feb-2026")
        mock_get.assert_called_once_with(
            "https://nse.example/feb.xml",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=20,
        )

    def test_ampersand_in_symbol_is_url_encoded(self) -> None:
        # Regression test: a real NSE ticker like "M&M" must not corrupt
        # the query string — an unescaped "&" would silently truncate the
        # "symbol" param and/or inject a bogus extra query parameter.
        sess = self._filings_session([])
        with patch("tools.nse_tools._nse_session", return_value=sess):
            get_nse_basic_ratios("M&M")

        requested_url = sess.get.call_args[0][0]
        self.assertIn("symbol=M%26M", requested_url)
        self.assertNotIn("symbol=M&M&", requested_url)


if __name__ == "__main__":
    unittest.main()
