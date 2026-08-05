import json
import unittest
from unittest.mock import MagicMock, patch

from tools.bse_shareholding import _plausible_holding_pct, get_shareholding_detail


def _ixbrl(*facts: str) -> str:
    """Wraps a handful of ix:nonNumeric/ix:nonFraction fact strings in the
    same xmlns declarations a real BSE filing carries — only the two
    namespaces this module actually reads (ix, in-bse-shp) are load-bearing
    for parsing, but a real document declares several more; kept minimal."""
    return (
        "<html xmlns:ix='http://www.xbrl.org/2013/inlineXBRL' "
        "xmlns:in-bse-shp='http://www.bseindia.com/xbrl/shp/2025-10-31/in-bse-shp'>"
        "<body>" + "".join(facts) + "</body></html>"
    )


def _name_fact(context: str, value: str) -> str:
    return f"<ix:nonNumeric name='in-bse-shp:NameOfTheShareholder' contextRef='{context}'>{value}</ix:nonNumeric>"


def _promoter_type_fact(context: str, value: str = "Promoter") -> str:
    return f"<ix:nonNumeric name='in-bse-shp:TypeOfPromoterShareholding' contextRef='{context}'>{value}</ix:nonNumeric>"


def _pct_fact(context: str, value: str) -> str:
    return (
        f"<ix:nonFraction name='in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares' "
        f"contextRef='{context}'>{value}</ix:nonFraction>"
    )


class PlausibleHoldingPctTest(unittest.TestCase):
    def test_value_already_in_percent_form_is_not_rescaled(self) -> None:
        # The exact bug this function exists to prevent: BSE's percentage
        # facts are already percent-form ('1.00' means 1%), unlike NSE's
        # equivalent fact. tools.nse_tools._percent_from_ambiguous_value's
        # "value <= 1 means it's a fraction, multiply by 100" heuristic
        # turned a real 1% holding into a fabricated 100% one.
        self.assertEqual(_plausible_holding_pct(1.0, plausible_max=100.0), 1.0)
        self.assertEqual(_plausible_holding_pct(0.5, plausible_max=100.0), 0.5)

    def test_above_ceiling_is_dropped(self) -> None:
        self.assertIsNone(_plausible_holding_pct(31.0, plausible_max=30.0))

    def test_negative_is_dropped(self) -> None:
        self.assertIsNone(_plausible_holding_pct(-1.0, plausible_max=30.0))


class GetShareholdingDetailTest(unittest.TestCase):
    def _mock_responses(self, xbrl_path: str, xbrl_body: str, redirected_url: str | None = None):
        index_resp = MagicMock()
        index_resp.json.return_value = {
            "Table": [
                {"qtrid": 129, "filing_date_time": "2026-04-20T16:15:52.253", "xbrlurl": "/old.html"},
                {"qtrid": 130, "filing_date_time": "2026-07-09T12:57:13.293", "xbrlurl": xbrl_path},
            ],
        }
        index_resp.raise_for_status.return_value = None

        xbrl_resp = MagicMock()
        xbrl_resp.content = xbrl_body.encode()
        xbrl_resp.url = redirected_url or f"https://www.bseindia.com{xbrl_path}"
        xbrl_resp.raise_for_status.return_value = None
        return index_resp, xbrl_resp

    def test_no_filings_returns_error(self) -> None:
        index_resp = MagicMock()
        index_resp.json.return_value = {"Table": []}
        index_resp.raise_for_status.return_value = None
        with patch("tools.bse_shareholding.requests.get", return_value=index_resp):
            result = json.loads(get_shareholding_detail("506579"))
        self.assertIn("error", result)

    def test_network_failure_returns_error_not_raise(self) -> None:
        with patch("tools.bse_shareholding.requests.get", side_effect=ConnectionError("boom")):
            result = json.loads(get_shareholding_detail("506579"))
        self.assertIn("error", result)

    def test_xbrl_url_off_bseindia_host_is_rejected(self) -> None:
        index_resp = MagicMock()
        index_resp.json.return_value = {"Table": [{"qtrid": 1, "xbrlurl": "https://evil.example/x.html"}]}
        index_resp.raise_for_status.return_value = None
        with patch("tools.bse_shareholding.requests.get", return_value=index_resp) as mock_get:
            result = json.loads(get_shareholding_detail("506579"))
        # Only the index call happened — the malformed/absolute path must
        # never reach a second requests.get for the XBRL file itself.
        mock_get.assert_called_once()
        self.assertIn("error", result)

    def test_xbrl_redirect_off_bseindia_host_is_rejected(self) -> None:
        index_resp, xbrl_resp = self._mock_responses(
            "/XBRLFILES/x.html", _ixbrl(), redirected_url="https://evil.example/x.html",
        )
        with patch("tools.bse_shareholding.requests.get", side_effect=[index_resp, xbrl_resp]):
            result = json.loads(get_shareholding_detail("506579"))
        self.assertIn("error", result)

    def test_picks_the_highest_qtrid_not_response_order(self) -> None:
        xbrl = _ixbrl(
            _name_fact("D_IndividualsOrHUF_Context1", "Solo Promoter"),
            _promoter_type_fact("D_IndividualsOrHUF_Context1"),
            _pct_fact("IndividualsOrHUF_Context1", "51.00"),
        )
        index_resp, xbrl_resp = self._mock_responses("/latest.html", xbrl)
        with patch("tools.bse_shareholding.requests.get", side_effect=[index_resp, xbrl_resp]) as mock_get:
            result = json.loads(get_shareholding_detail("506579"))
        self.assertEqual(result["as_of_date"], "2026-07-09T12:57:13.293")
        # Second call must target the higher-qtrid filing's own path.
        self.assertEqual(mock_get.call_args_list[1].args[0], "https://www.bseindia.com/latest.html")

    def test_promoter_and_category_holder_are_grouped_correctly(self) -> None:
        xbrl = _ixbrl(
            _name_fact("D_IndividualsOrHUF_Context1", "Founder Family"),
            _promoter_type_fact("D_IndividualsOrHUF_Context1"),
            _pct_fact("IndividualsOrHUF_Context1", "25.59"),
            _name_fact("D_MutualFundsOrUTI_Context1", "Sample Mutual Fund"),
            _pct_fact("MutualFundsOrUTI_Context1", "2.62"),
        )
        index_resp, xbrl_resp = self._mock_responses("/shp.html", xbrl)
        with patch("tools.bse_shareholding.requests.get", side_effect=[index_resp, xbrl_resp]):
            result = json.loads(get_shareholding_detail("506579"))

        self.assertEqual(len(result["promoters"]), 1)
        self.assertEqual(result["promoters"][0]["name"], "Founder Family")
        self.assertAlmostEqual(result["promoters"][0]["holding_pct"], 25.59)

        categories = {c["category"]: c["holders"] for c in result["shareholder_categories"]}
        self.assertIn("Mutual Funds Or UTI", categories)
        self.assertEqual(categories["Mutual Funds Or UTI"][0]["name"], "Sample Mutual Fund")
        self.assertAlmostEqual(categories["Mutual Funds Or UTI"][0]["holding_pct"], 2.62)

    def test_shareholder_with_no_percent_fact_is_skipped_not_erroring(self) -> None:
        xbrl = _ixbrl(
            _name_fact("D_IndividualsOrHUF_Context1", "No Percent Row"),
            _promoter_type_fact("D_IndividualsOrHUF_Context1"),
            # No matching ShareholdingAsAPercentageOfTotalNumberOfShares fact.
        )
        index_resp, xbrl_resp = self._mock_responses("/shp.html", xbrl)
        with patch("tools.bse_shareholding.requests.get", side_effect=[index_resp, xbrl_resp]):
            result = json.loads(get_shareholding_detail("506579"))
        self.assertEqual(result["promoters"], [])

    def test_non_promoter_category_capped_at_30_percent(self) -> None:
        xbrl = _ixbrl(
            _name_fact("D_OtherNonInstitutions_Context1", "Implausible Holder"),
            _pct_fact("OtherNonInstitutions_Context1", "45.00"),
        )
        index_resp, xbrl_resp = self._mock_responses("/shp.html", xbrl)
        with patch("tools.bse_shareholding.requests.get", side_effect=[index_resp, xbrl_resp]):
            result = json.loads(get_shareholding_detail("506579"))
        self.assertEqual(result["shareholder_categories"], [])


if __name__ == "__main__":
    unittest.main()
