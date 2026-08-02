import unittest
from unittest.mock import MagicMock, patch

from tools.nse_fii_dii_tools import _to_float, get_fii_dii_flow


class ToFloatTest(unittest.TestCase):
    def test_none_returns_none(self) -> None:
        self.assertIsNone(_to_float(None))

    def test_comma_separated_number_converts(self) -> None:
        self.assertEqual(_to_float("1,234.56"), 1234.56)

    def test_negative_number_converts(self) -> None:
        self.assertEqual(_to_float("-987.65"), -987.65)

    def test_unparseable_string_returns_none_not_raise(self) -> None:
        self.assertIsNone(_to_float("N/A"))

    def test_implausibly_large_value_is_dropped_not_trusted(self) -> None:
        # Regression test: signals/macro.py's ±500/±3000 Cr thresholds
        # assume netValue is already in ₹ Crore, which isn't verifiable
        # against a live NSE response in this sandbox. A single day's net
        # flow far beyond any plausible real figure is more likely a unit
        # mismatch than a genuine number, so it's dropped rather than fed
        # to the signal engine as if it were trustworthy.
        self.assertIsNone(_to_float("500000000"))

    def test_plausible_large_value_still_converts(self) -> None:
        self.assertEqual(_to_float("45000.5"), 45000.5)


class GetFiiDiiFlowTest(unittest.TestCase):
    def _session(self, json_data):
        sess = MagicMock()
        resp = MagicMock()
        resp.json.return_value = json_data
        sess.get.return_value = resp
        return sess

    def test_parses_fii_and_dii_rows(self) -> None:
        rows = [
            {"category": "FII/FPI", "date": "24-Jul-2026", "netValue": "1,234.56"},
            {"category": "DII", "date": "24-Jul-2026", "netValue": "-567.89"},
        ]
        with patch("tools.nse_fii_dii_tools._nse_session", return_value=self._session(rows)):
            result = get_fii_dii_flow()
        self.assertEqual(result["date"], "24-Jul-2026")
        self.assertEqual(result["fii_net_cr"], 1234.56)
        self.assertEqual(result["dii_net_cr"], -567.89)

    def test_missing_dii_row_leaves_it_none(self) -> None:
        rows = [{"category": "FII/FPI", "date": "24-Jul-2026", "netValue": "500"}]
        with patch("tools.nse_fii_dii_tools._nse_session", return_value=self._session(rows)):
            result = get_fii_dii_flow()
        self.assertEqual(result["fii_net_cr"], 500.0)
        self.assertIsNone(result["dii_net_cr"])

    def test_empty_list_returns_error(self) -> None:
        with patch("tools.nse_fii_dii_tools._nse_session", return_value=self._session([])):
            result = get_fii_dii_flow()
        self.assertIn("error", result)

    def test_non_list_response_returns_error_not_raise(self) -> None:
        with patch("tools.nse_fii_dii_tools._nse_session", return_value=self._session({"unexpected": True})):
            result = get_fii_dii_flow()
        self.assertIn("error", result)

    def test_no_matching_categories_returns_error(self) -> None:
        rows = [{"category": "Something Else", "date": "24-Jul-2026", "netValue": "100"}]
        with patch("tools.nse_fii_dii_tools._nse_session", return_value=self._session(rows)):
            result = get_fii_dii_flow()
        self.assertIn("error", result)

    def test_network_exception_returns_error_not_raise(self) -> None:
        sess = MagicMock()
        sess.get.side_effect = ConnectionError("boom")
        with patch("tools.nse_fii_dii_tools._nse_session", return_value=sess):
            result = get_fii_dii_flow()
        self.assertIn("error", result)

    def test_malformed_net_value_becomes_none_not_raise(self) -> None:
        rows = [
            {"category": "FII/FPI", "date": "24-Jul-2026", "netValue": "garbage"},
            {"category": "DII", "date": "24-Jul-2026", "netValue": "200"},
        ]
        with patch("tools.nse_fii_dii_tools._nse_session", return_value=self._session(rows)):
            result = get_fii_dii_flow()
        self.assertIsNone(result["fii_net_cr"])
        self.assertEqual(result["dii_net_cr"], 200.0)


if __name__ == "__main__":
    unittest.main()
