import json
import unittest
from unittest.mock import MagicMock, patch

from lxml import etree

from tools.nse_tools import (
    _build_quote_payload,
    _humanize_category,
    _is_nse_host,
    _is_valid_quote,
    _num,
    _screener_fallback_quote,
    _stockanalysis_extra_fields,
    get_mf_holdings,
    get_nse_basic_ratios,
    get_shareholding_detail,
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
        with patch("yfinance.Ticker", return_value=fake_ticker), \
             patch("tools.nse_tools._screener_fallback_quote", return_value=None):
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

    def test_both_exchanges_fail_falls_back_to_screener_quote(self) -> None:
        # Thinly-traded stocks yfinance has no quote for on either exchange,
        # but Screener.in still has a price for — must not hard-fail.
        fake_ticker = MagicMock()
        fake_ticker.info = {}
        fallback = {
            "symbol": "CHANDAN", "exchange": "NSE", "company_name": "Chandan Steel",
            "current_price": 45.0, "previous_close": None, "change_pct": 0.0,
            "volume": None, "avg_volume_10d": None, "market_cap_cr": 120.0,
            "pe_ratio": 12.0, "eps": 3.75, "book_value": 30.0, "price_to_book": 1.5,
            "52w_high": None, "52w_low": None, "dividend_yield_pct": 1.2,
            "beta": None, "sector": None, "industry": None, "about": "",
        }
        with patch("yfinance.Ticker", return_value=fake_ticker), \
             patch("tools.nse_tools._screener_fallback_quote", return_value=fallback):
            result = json.loads(get_stock_quote.run(symbol="chandan"))
        self.assertEqual(result["primary_exchange"], "NSE")
        self.assertEqual(result["current_price"], 45.0)
        self.assertEqual(result["prices_by_exchange"]["NSE"]["pe_ratio"], 12.0)


class ScreenerFallbackQuoteTest(unittest.TestCase):
    def _soup_with_ratios(self, ratios: dict, company_name: str = "Chandan Steel"):
        from bs4 import BeautifulSoup
        lis = "".join(
            f'<li><span class="name">{k}</span><span class="number">{v}</span></li>'
            for k, v in ratios.items()
        )
        html = f"<html><body><h1>{company_name}</h1><ul id='top-ratios'>{lis}</ul></body></html>"
        return BeautifulSoup(html, "lxml")

    def test_returns_none_when_no_current_price(self) -> None:
        with patch("tools.screener_tools._fetch_soup", return_value=self._soup_with_ratios({})), \
             patch("tools.nse_tools._stockanalysis_extra_fields", return_value={}):
            self.assertIsNone(_screener_fallback_quote("NOSUCH"))

    def test_derives_eps_and_price_to_book_from_price_and_ratios(self) -> None:
        soup = self._soup_with_ratios({
            "Current Price": "120", "Stock P/E": "10", "Book Value": "60", "Market Cap": "500",
        })
        with patch("tools.screener_tools._fetch_soup", return_value=soup), \
             patch("tools.nse_tools._stockanalysis_extra_fields", return_value={}):
            result = _screener_fallback_quote("CHANDAN")
        self.assertEqual(result["current_price"], 120.0)
        self.assertEqual(result["eps"], 12.0)
        self.assertEqual(result["price_to_book"], 2.0)
        self.assertEqual(result["company_name"], "Chandan Steel")

    def test_stockanalysis_fields_override_derived_values(self) -> None:
        soup = self._soup_with_ratios({"Current Price": "120", "Stock P/E": "10"})
        with patch("tools.screener_tools._fetch_soup", return_value=soup), \
             patch("tools.nse_tools._stockanalysis_extra_fields",
                   return_value={"eps": 13.5, "52w_high": 200.0, "52w_low": 90.0, "volume": 12345}):
            result = _screener_fallback_quote("CHANDAN")
        self.assertEqual(result["eps"], 13.5)
        self.assertEqual(result["52w_high"], 200.0)
        self.assertEqual(result["volume"], 12345)

    def test_returns_none_on_fetch_exception(self) -> None:
        with patch("tools.screener_tools._fetch_soup", side_effect=ConnectionError("boom")):
            self.assertIsNone(_screener_fallback_quote("NOSUCH"))


class StockanalysisExtraFieldsTest(unittest.TestCase):
    def test_parses_eps_range_and_volume(self) -> None:
        fake_resp = MagicMock()
        fake_resp.text = (
            "<html><body><table>"
            "<tr><td>EPS</td><td>12.5</td></tr>"
            "<tr><td>52-Week Range</td><td>80.00 - 150.00</td></tr>"
            "<tr><td>Volume</td><td>1,234,567</td></tr>"
            "</table></body></html>"
        )
        fake_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=fake_resp):
            result = _stockanalysis_extra_fields("TCS")
        self.assertEqual(result["eps"], 12.5)
        self.assertEqual(result["52w_low"], 80.0)
        self.assertEqual(result["52w_high"], 150.0)
        self.assertEqual(result["volume"], 1234567)

    def test_returns_empty_dict_on_request_failure(self) -> None:
        with patch("requests.get", side_effect=ConnectionError("boom")):
            self.assertEqual(_stockanalysis_extra_fields("TCS"), {})


class IsNseHostTest(unittest.TestCase):
    def test_accepts_bare_and_subdomain_hosts(self) -> None:
        self.assertTrue(_is_nse_host("https://nseindia.com/x.xml"))
        self.assertTrue(_is_nse_host("https://www.nseindia.com/x.xml"))
        self.assertTrue(_is_nse_host("https://nsearchives.nseindia.com/x.xml"))

    def test_rejects_lookalike_and_off_domain_hosts(self) -> None:
        self.assertFalse(_is_nse_host("https://evilnseindia.com/x.xml"))
        self.assertFalse(_is_nse_host("https://attacker.com/nseindia.com"))
        self.assertFalse(_is_nse_host("https://nseindia.com.attacker.com/x.xml"))

    def test_rejects_non_http_schemes(self) -> None:
        self.assertFalse(_is_nse_host("file:///etc/passwd"))
        self.assertFalse(_is_nse_host("ftp://nseindia.com/x.xml"))


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

    def test_picks_chronologically_latest_record_not_lexically_last(self) -> None:
        # Regression test: NSE's real date format (dd-Mon-yyyy) does not
        # sort lexically in calendar order — "01-Jan-2026" sorts BEFORE
        # "15-Dec-2025" as plain strings ('0' < '1'), which would silently
        # pick the older Dec-2025 filing as "latest". Two records here: an
        # older one with a lexically-larger date string, and a genuinely
        # newer one with a lexically-smaller date string.
        ns_di = "http://xbrl.org/2006/xbrldi"
        older_xbrl = f'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:di="{ns_di}">
          <xbrli:context id="D_MF1">
            <xbrli:scenario><di:typedMember><MutualFundsMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <NameOfTheShareholder contextRef="D_MF1">Old Quarter Fund</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="MF1">0.01</ShareholdingAsAPercentageOfTotalNumberOfShares>
        </xbrli:xbrl>'''
        newer_xbrl = f'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:di="{ns_di}">
          <xbrli:context id="D_MF2">
            <xbrli:scenario><di:typedMember><MutualFundsMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <NameOfTheShareholder contextRef="D_MF2">New Quarter Fund</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="MF2">0.02</ShareholdingAsAPercentageOfTotalNumberOfShares>
        </xbrli:xbrl>'''

        sess = MagicMock()
        master_resp = MagicMock()
        master_resp.json.return_value = [
            {"date": "15-Dec-2025", "xbrl": "https://nsearchives.nseindia.com/older.xml"},
            {"date": "01-Jan-2026", "xbrl": "https://nsearchives.nseindia.com/newer.xml"},
        ]
        sess.get.return_value = master_resp

        def _fake_get(url, **kwargs):
            resp = MagicMock()
            if url == "https://nsearchives.nseindia.com/newer.xml":
                resp.content = newer_xbrl.encode()
            else:
                resp.content = older_xbrl.encode()
            resp.url = url
            return resp

        with patch("tools.nse_tools._nse_session", return_value=sess), \
             patch("requests.get", side_effect=_fake_get):
            result = json.loads(get_mf_holdings.run(symbol="TCS"))

        self.assertEqual(result["as_of_date"], "01-Jan-2026")
        self.assertEqual(result["mutual_funds"][0]["fund"], "New Quarter Fund")

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
        master_resp.json.return_value = [{"date": "2026-01-01", "xbrl": "https://nsearchives.nseindia.com/x.xml"}]
        sess.get.return_value = master_resp

        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()
        xbrl_resp.url = "https://nsearchives.nseindia.com/x.xml"

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
        master_resp.json.return_value = [{"date": "2026-01-01", "xbrl": "https://nsearchives.nseindia.com/x.xml"}]
        sess.get.return_value = master_resp

        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()
        xbrl_resp.url = "https://nsearchives.nseindia.com/x.xml"

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
        master_resp.json.return_value = [{"date": "2026-01-01", "xbrl": "https://nsearchives.nseindia.com/x.xml"}]
        sess.get.return_value = master_resp

        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()
        xbrl_resp.url = "https://nsearchives.nseindia.com/x.xml"

        with patch("tools.nse_tools._nse_session", return_value=sess), \
             patch("requests.get", return_value=xbrl_resp):
            result = json.loads(get_mf_holdings.run(symbol="TCS"))

        self.assertEqual(result["mutual_funds"], [])

    def test_xbrl_url_off_nseindia_host_is_rejected(self) -> None:
        # SSRF regression: the xbrl field comes straight out of NSE's own
        # API response, not a hardcoded endpoint — must never be fetched
        # blind.
        sess = MagicMock()
        master_resp = MagicMock()
        master_resp.json.return_value = [{"date": "2026-01-01", "xbrl": "https://evil.example/x.xml"}]
        sess.get.return_value = master_resp
        with patch("tools.nse_tools._nse_session", return_value=sess), \
             patch("requests.get") as mock_get:
            result = json.loads(get_mf_holdings.run(symbol="TCS"))
        mock_get.assert_not_called()
        self.assertIn("error", result)

    def test_xbrl_redirect_off_nseindia_host_is_rejected(self) -> None:
        # SSRF regression: even a legitimately-hosted URL must be re-checked
        # after following redirects — a redirect at fetch time could
        # otherwise land off nseindia.com with no check at all.
        sess = MagicMock()
        master_resp = MagicMock()
        master_resp.json.return_value = [{"date": "2026-01-01", "xbrl": "https://nsearchives.nseindia.com/x.xml"}]
        sess.get.return_value = master_resp

        xbrl_resp = MagicMock()
        xbrl_resp.url = "https://evil.example/x.xml"

        with patch("tools.nse_tools._nse_session", return_value=sess), \
             patch("requests.get", return_value=xbrl_resp):
            result = json.loads(get_mf_holdings.run(symbol="TCS"))
        self.assertIn("error", result)


class HumanizeCategoryTest(unittest.TestCase):
    def test_splits_pascal_case_words(self) -> None:
        self.assertEqual(_humanize_category("ForeignPortfolioInvestorsCategoryI"), "Foreign Portfolio Investors Category I")

    def test_single_word_is_unchanged(self) -> None:
        self.assertEqual(_humanize_category("MutualFunds"), "Mutual Funds")

    def test_empty_string_falls_back_to_itself(self) -> None:
        self.assertEqual(_humanize_category(""), "")

    def test_acronym_categories_are_not_split_into_single_letters(self) -> None:
        # Regression test for an adversarial-review finding: NSE shareholding
        # categories commonly use short acronyms verbatim (FII, NRI, HUF,
        # IEPF) — a naive "space before every capital" split would break
        # these into "F I I" instead of keeping the acronym intact.
        self.assertEqual(_humanize_category("FIIMember"), "FII")
        self.assertEqual(_humanize_category("NRIMember"), "NRI")
        self.assertEqual(_humanize_category("HUFMember"), "HUF")
        self.assertEqual(_humanize_category("IEPFMember"), "IEPF")

    def test_acronym_followed_by_titlecase_word_still_splits(self) -> None:
        self.assertEqual(_humanize_category("NRIRepatriableMember"), "NRI Repatriable")


class GetShareholdingDetailTest(unittest.TestCase):
    def _mock_session_and_xbrl(self, xbrl: str):
        sess = MagicMock()
        master_resp = MagicMock()
        master_resp.json.return_value = [{"date": "2026-01-01", "xbrl": "https://nsearchives.nseindia.com/x.xml"}]
        sess.get.return_value = master_resp

        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()
        xbrl_resp.url = "https://nsearchives.nseindia.com/x.xml"
        return sess, xbrl_resp

    def test_no_shareholding_records_returns_error(self) -> None:
        sess = MagicMock()
        master_resp = MagicMock()
        master_resp.json.return_value = []
        sess.get.return_value = master_resp
        with patch("tools.nse_tools._nse_session", return_value=sess):
            result = json.loads(get_shareholding_detail.run(symbol="TCS"))
        self.assertIn("error", result)

    def test_network_failure_returns_error_not_raise(self) -> None:
        with patch("tools.nse_tools._nse_session", side_effect=ConnectionError("boom")):
            result = json.loads(get_shareholding_detail.run(symbol="TCS"))
        self.assertIn("error", result)

    def test_xbrl_url_off_nseindia_host_is_rejected(self) -> None:
        # SSRF regression: same shared _fetch_shareholding_xbrl helper
        # get_mf_holdings already relies on — must reject before fetching.
        sess = MagicMock()
        master_resp = MagicMock()
        master_resp.json.return_value = [{"date": "2026-01-01", "xbrl": "https://evil.example/x.xml"}]
        sess.get.return_value = master_resp
        with patch("tools.nse_tools._nse_session", return_value=sess), \
             patch("requests.get") as mock_get:
            result = json.loads(get_shareholding_detail.run(symbol="TCS"))
        mock_get.assert_not_called()
        self.assertIn("error", result)

    def test_promoter_mutual_fund_and_other_category_are_grouped_correctly(self) -> None:
        ns_di = "http://xbrl.org/2006/xbrldi"
        xbrl = f'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:di="{ns_di}">
          <xbrli:context id="D_P1">
            <xbrli:scenario><di:typedMember><IndividualPromotersMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <xbrli:context id="D_MF1">
            <xbrli:scenario><di:typedMember><MutualFundsMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <xbrli:context id="D_FPI1">
            <xbrli:scenario><di:typedMember><ForeignPortfolioInvestorsMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <NameOfTheShareholder contextRef="D_P1">Founder Family Trust</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="P1">0.55</ShareholdingAsAPercentageOfTotalNumberOfShares>
          <NameOfTheShareholder contextRef="D_MF1">Sample Mutual Fund</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="MF1">0.035</ShareholdingAsAPercentageOfTotalNumberOfShares>
          <NameOfTheShareholder contextRef="D_FPI1">Sample Foreign Fund</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="FPI1">0.021</ShareholdingAsAPercentageOfTotalNumberOfShares>
        </xbrli:xbrl>'''
        sess, xbrl_resp = self._mock_session_and_xbrl(xbrl)

        with patch("tools.nse_tools._nse_session", return_value=sess), \
             patch("requests.get", return_value=xbrl_resp):
            result = json.loads(get_shareholding_detail.run(symbol="TCS"))

        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(result["as_of_date"], "2026-01-01")

        self.assertEqual(len(result["promoters"]), 1)
        self.assertEqual(result["promoters"][0]["name"], "Founder Family Trust")
        self.assertAlmostEqual(result["promoters"][0]["holding_pct"], 55.0)

        categories = {c["category"]: c["holders"] for c in result["shareholder_categories"]}
        self.assertIn("Mutual Funds", categories)
        self.assertEqual(categories["Mutual Funds"][0]["name"], "Sample Mutual Fund")
        self.assertAlmostEqual(categories["Mutual Funds"][0]["holding_pct"], 3.5)
        self.assertIn("Foreign Portfolio Investors", categories)
        self.assertEqual(categories["Foreign Portfolio Investors"][0]["name"], "Sample Foreign Fund")
        self.assertAlmostEqual(categories["Foreign Portfolio Investors"][0]["holding_pct"], 2.1)

    def test_promoter_can_hold_up_to_100_percent_but_other_categories_capped_at_30(self) -> None:
        # A promoter/promoter-group entity can plausibly hold a very large
        # stake; any other single named holder above ~30% would be
        # extraordinary and is dropped as an implausible format guess,
        # same "never invent" reasoning get_mf_holdings already applies.
        ns_di = "http://xbrl.org/2006/xbrldi"
        xbrl = f'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:di="{ns_di}">
          <xbrli:context id="D_P1">
            <xbrli:scenario><di:typedMember><IndividualPromotersMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <xbrli:context id="D_MF1">
            <xbrli:scenario><di:typedMember><MutualFundsMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <NameOfTheShareholder contextRef="D_P1">Majority Promoter</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="P1">71.77</ShareholdingAsAPercentageOfTotalNumberOfShares>
          <NameOfTheShareholder contextRef="D_MF1">Implausible Fund</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="MF1">71.77</ShareholdingAsAPercentageOfTotalNumberOfShares>
        </xbrli:xbrl>'''
        sess, xbrl_resp = self._mock_session_and_xbrl(xbrl)

        with patch("tools.nse_tools._nse_session", return_value=sess), \
             patch("requests.get", return_value=xbrl_resp):
            result = json.loads(get_shareholding_detail.run(symbol="TCS"))

        self.assertEqual(len(result["promoters"]), 1)
        self.assertAlmostEqual(result["promoters"][0]["holding_pct"], 71.77)
        # The implausible 71.77% mutual fund holding is dropped, leaving no
        # Mutual Funds category at all rather than a fabricated entry.
        categories = {c["category"] for c in result["shareholder_categories"]}
        self.assertNotIn("Mutual Funds", categories)

    def test_no_typed_members_returns_empty_not_error(self) -> None:
        # A filing with no named-shareholder XBRL facts at all (or a
        # category-tagging scheme this generalized walk doesn't recognize)
        # degrades to an empty result, not an error — same "legitimately
        # nothing here" distinction as every other standalone endpoint.
        xbrl = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"></xbrli:xbrl>'''
        sess, xbrl_resp = self._mock_session_and_xbrl(xbrl)

        with patch("tools.nse_tools._nse_session", return_value=sess), \
             patch("requests.get", return_value=xbrl_resp):
            result = json.loads(get_shareholding_detail.run(symbol="TCS"))

        self.assertEqual(result["promoters"], [])
        self.assertEqual(result["shareholder_categories"], [])
        self.assertNotIn("error", result)

    def test_colliding_context_ids_across_categories_are_dropped_not_misattributed(self) -> None:
        # Regression test for an adversarial-review finding: unlike
        # get_mf_holdings (whose name/category dicts only ever collect
        # MutualFunds-tagged contexts, so a foreign category's context id
        # could never land in the same dict), this generalized walk is
        # document-wide across every category — a context literally named
        # "D_5" (Mutual Funds) and a different context literally named "5"
        # (Insurance Companies) both reduce to the same base id "5" after
        # stripping "D_", which a plain dict would let the later-processed
        # category silently clobber. Both must be dropped — never guess
        # which one the shared percentage fact actually belongs to.
        ns_di = "http://xbrl.org/2006/xbrldi"
        xbrl = f'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:di="{ns_di}">
          <xbrli:context id="D_5">
            <xbrli:scenario><di:typedMember><MutualFundsMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <xbrli:context id="5">
            <xbrli:scenario><di:typedMember><InsuranceCompaniesMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <xbrli:context id="D_9">
            <xbrli:scenario><di:typedMember><MutualFundsMember/></di:typedMember></xbrli:scenario>
          </xbrli:context>
          <NameOfTheShareholder contextRef="D_5">Colliding MF</NameOfTheShareholder>
          <NameOfTheShareholder contextRef="5">Colliding Insurer</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="5">0.02</ShareholdingAsAPercentageOfTotalNumberOfShares>
          <NameOfTheShareholder contextRef="D_9">Unambiguous Fund</NameOfTheShareholder>
          <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="9">0.03</ShareholdingAsAPercentageOfTotalNumberOfShares>
        </xbrli:xbrl>'''
        sess, xbrl_resp = self._mock_session_and_xbrl(xbrl)

        with patch("tools.nse_tools._nse_session", return_value=sess), \
             patch("requests.get", return_value=xbrl_resp):
            result = json.loads(get_shareholding_detail.run(symbol="TCS"))

        all_names = {p["name"] for p in result["promoters"]}
        for cat in result["shareholder_categories"]:
            all_names |= {h["name"] for h in cat["holders"]}

        self.assertNotIn("Colliding MF", all_names)
        self.assertNotIn("Colliding Insurer", all_names)
        self.assertIn("Unambiguous Fund", all_names)


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
            {"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://nsearchives.nseindia.com/results.xml"},
        ]
        xbrl = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <BasicEarningsPerEquityShare contextRef="D1" unitRef="INR">42.5</BasicEarningsPerEquityShare>
        </xbrli:xbrl>'''
        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()
        xbrl_resp.url = "https://nsearchives.nseindia.com/x.xml"

        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", return_value=xbrl_resp):
            result = get_nse_basic_ratios("TCS")

        self.assertEqual(result["eps"], 42.5)
        self.assertEqual(result["source"], "nse_xbrl")
        self.assertEqual(result["as_of_date"], "2026-01-01")

    def test_prefers_financial_results_filing_over_other_categories(self) -> None:
        filings = [
            {"date": "2026-02-01", "desc": "Board Meeting Intimation", "xbrl": "https://nsearchives.nseindia.com/board.xml"},
            {"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://nsearchives.nseindia.com/results.xml"},
        ]
        xbrl = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <BasicEarningsPerEquityShare contextRef="D1">10.0</BasicEarningsPerEquityShare>
        </xbrli:xbrl>'''
        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()
        xbrl_resp.url = "https://nsearchives.nseindia.com/x.xml"

        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", return_value=xbrl_resp) as mock_get:
            result = get_nse_basic_ratios("TCS")

        self.assertEqual(result["eps"], 10.0)
        mock_get.assert_called_once_with(
            "https://nsearchives.nseindia.com/results.xml",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=20,
        )

    def test_unrecognized_tag_names_return_empty_dict(self) -> None:
        filings = [{"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://nsearchives.nseindia.com/results.xml"}]
        xbrl = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <SomeUnrelatedFact contextRef="D1">99</SomeUnrelatedFact>
        </xbrli:xbrl>'''
        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()
        xbrl_resp.url = "https://nsearchives.nseindia.com/x.xml"

        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", return_value=xbrl_resp):
            self.assertEqual(get_nse_basic_ratios("TCS"), {})

    def test_non_numeric_eps_fact_does_not_raise(self) -> None:
        filings = [{"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://nsearchives.nseindia.com/results.xml"}]
        xbrl = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <BasicEarningsPerEquityShare contextRef="D1">not-a-number</BasicEarningsPerEquityShare>
        </xbrli:xbrl>'''
        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()
        xbrl_resp.url = "https://nsearchives.nseindia.com/x.xml"

        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", return_value=xbrl_resp):
            self.assertEqual(get_nse_basic_ratios("TCS"), {})

    def test_xbrl_fetch_failure_returns_empty_dict_not_raise(self) -> None:
        filings = [{"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://nsearchives.nseindia.com/results.xml"}]
        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", side_effect=ConnectionError("boom")):
            self.assertEqual(get_nse_basic_ratios("TCS"), {})

    def test_xbrl_url_off_nseindia_host_is_rejected(self) -> None:
        filings = [{"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://evil.example/x.xml"}]
        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get") as mock_get:
            self.assertEqual(get_nse_basic_ratios("TCS"), {})
        mock_get.assert_not_called()

    def test_xbrl_redirect_off_nseindia_host_is_rejected(self) -> None:
        filings = [{"date": "2026-01-01", "desc": "Financial Results", "xbrl": "https://nsearchives.nseindia.com/results.xml"}]
        xbrl_resp = MagicMock()
        xbrl_resp.url = "https://evil.example/x.xml"
        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", return_value=xbrl_resp):
            self.assertEqual(get_nse_basic_ratios("TCS"), {})

    def test_picks_the_chronologically_newest_filing_not_lexically_last(self) -> None:
        # Regression test: NSE's own dd-Mon-yyyy date format does not sort
        # lexically in calendar order (same drift nse_insider_trades.py's
        # _parse_pit_date already documents/fixes for its own date field).
        # "05-Feb-2026" is chronologically newer than "20-Jan-2026" but
        # lexically SMALLER ("0" < "2"), so a raw string sort would
        # (wrongly) treat the January filing as more recent.
        filings = [
            {"date": "20-Jan-2026", "desc": "Financial Results", "xbrl": "https://nsearchives.nseindia.com/jan.xml"},
            {"date": "05-Feb-2026", "desc": "Financial Results", "xbrl": "https://nsearchives.nseindia.com/feb.xml"},
        ]
        xbrl = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <BasicEarningsPerEquityShare contextRef="D1">99.0</BasicEarningsPerEquityShare>
        </xbrli:xbrl>'''
        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl.encode()
        xbrl_resp.url = "https://nsearchives.nseindia.com/x.xml"

        with patch("tools.nse_tools._nse_session", return_value=self._filings_session(filings)), \
             patch("requests.get", return_value=xbrl_resp) as mock_get:
            result = get_nse_basic_ratios("TCS")

        self.assertEqual(result["as_of_date"], "05-Feb-2026")
        mock_get.assert_called_once_with(
            "https://nsearchives.nseindia.com/feb.xml",
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
