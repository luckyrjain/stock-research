import unittest
from unittest.mock import patch

from tools.sme_tools import _norm_company_name, get_all_sme_stocks


class NormCompanyNameTest(unittest.TestCase):
    def test_strips_common_suffixes_and_normalizes_case(self) -> None:
        self.assertEqual(_norm_company_name("ABC Industries Limited"), "ABC")
        self.assertEqual(_norm_company_name("abc industries ltd"), "ABC")

    def test_none_and_empty_return_empty_string(self) -> None:
        self.assertEqual(_norm_company_name(None), "")
        self.assertEqual(_norm_company_name(""), "")

    def test_strips_punctuation_and_collapses_whitespace(self) -> None:
        self.assertEqual(_norm_company_name("A.B.C.  Foods   Pvt.  Co."), "A B C FOODS")


class GetAllSmeStocksDedupTest(unittest.TestCase):
    def _run(self, nse: list, bse: list) -> list:
        with patch("tools.sme_tools.fetch_nse_emerge_stocks", return_value=nse), \
             patch("tools.sme_tools.fetch_bse_sme_stocks", return_value=bse):
            return get_all_sme_stocks()

    def test_isin_match_dedups_bse_record(self) -> None:
        nse = [{"symbol": "ABC", "name": "ABC Corp", "isin": "INE000A01001", "series": "SM", "exchange": "NSE"}]
        bse = [{"symbol": "543210", "name": "Something Else", "isin": "INE000A01001", "series": "M", "exchange": "BSE"}]
        merged = self._run(nse, bse)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["exchange"], "NSE")

    def test_name_match_dedups_when_nse_has_no_isin(self) -> None:
        # This is the actual production scenario: NSE Emerge never returns an ISIN.
        nse = [{"symbol": "ABC", "name": "ABC Industries Limited", "isin": None, "series": "SM", "exchange": "NSE"}]
        bse = [{"symbol": "543210", "name": "ABC Industries Ltd", "isin": "INE999Z99999", "series": "M", "exchange": "BSE"}]
        merged = self._run(nse, bse)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["exchange"], "NSE")

    def test_different_companies_are_not_merged(self) -> None:
        nse = [{"symbol": "ABC", "name": "ABC Industries Limited", "isin": None, "series": "SM", "exchange": "NSE"}]
        bse = [{"symbol": "543210", "name": "XYZ Foods Limited", "isin": None, "series": "M", "exchange": "BSE"}]
        merged = self._run(nse, bse)
        self.assertEqual(len(merged), 2)

    def test_missing_names_on_both_sides_keeps_bse_record_without_crashing(self) -> None:
        nse = [{"symbol": "ABC", "name": None, "isin": None, "series": "SM", "exchange": "NSE"}]
        bse = [{"symbol": "543210", "name": None, "isin": None, "series": "M", "exchange": "BSE"}]
        merged = self._run(nse, bse)
        self.assertEqual(len(merged), 2)

    def test_no_bse_stocks_returns_nse_only(self) -> None:
        nse = [{"symbol": "ABC", "name": "ABC Industries", "isin": None, "series": "SM", "exchange": "NSE"}]
        merged = self._run(nse, [])
        self.assertEqual(merged, nse)


if __name__ == "__main__":
    unittest.main()
