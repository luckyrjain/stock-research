import unittest
from unittest.mock import MagicMock, patch

from tools.macro_context_tools import _parse_percent, get_macro_context


class ParsePercentTest(unittest.TestCase):
    def test_extracts_percent_value(self) -> None:
        self.assertEqual(_parse_percent("6.50%"), 6.5)

    def test_extracts_negative_percent(self) -> None:
        self.assertEqual(_parse_percent("-0.5%"), -0.5)

    def test_no_percent_returns_none(self) -> None:
        self.assertIsNone(_parse_percent("no number here"))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(_parse_percent(""))

    def test_none_input_returns_none_not_raise(self) -> None:
        self.assertIsNone(_parse_percent(None))


def _html_with_table(rows: list[tuple[str, str]]) -> str:
    body = "".join(f"<tr><td>{label}</td><td>{value}</td></tr>" for label, value in rows)
    return f"<html><body><table>{body}</table></body></html>"


class GetMacroContextTest(unittest.TestCase):
    def _response(self, html: str):
        resp = MagicMock()
        resp.text = html
        resp.raise_for_status.return_value = None
        return resp

    def test_parses_repo_rate_and_cpi(self) -> None:
        html = _html_with_table([("Policy Repo Rate", "6.50%"), ("CPI Inflation", "5.10%")])
        with patch("tools.macro_context_tools.requests.get", return_value=self._response(html)):
            result = get_macro_context()
        self.assertEqual(result["repo_rate_pct"], 6.5)
        self.assertEqual(result["cpi_inflation_pct"], 5.1)

    def test_missing_cpi_row_leaves_it_none(self) -> None:
        html = _html_with_table([("Policy Repo Rate", "6.50%")])
        with patch("tools.macro_context_tools.requests.get", return_value=self._response(html)):
            result = get_macro_context()
        self.assertEqual(result["repo_rate_pct"], 6.5)
        self.assertIsNone(result["cpi_inflation_pct"])

    def test_no_matching_rows_returns_both_none_not_error(self) -> None:
        html = _html_with_table([("Something Unrelated", "1.00%")])
        with patch("tools.macro_context_tools.requests.get", return_value=self._response(html)):
            result = get_macro_context()
        self.assertIsNone(result["repo_rate_pct"])
        self.assertIsNone(result["cpi_inflation_pct"])
        self.assertNotIn("error", result)

    def test_network_exception_returns_error_not_raise(self) -> None:
        with patch("tools.macro_context_tools.requests.get", side_effect=ConnectionError("boom")):
            result = get_macro_context()
        self.assertIn("error", result)

    def test_http_error_status_returns_error_not_raise(self) -> None:
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("500 server error")
        with patch("tools.macro_context_tools.requests.get", return_value=resp):
            result = get_macro_context()
        self.assertIn("error", result)

    def test_first_matching_row_wins_when_duplicated(self) -> None:
        html = _html_with_table([
            ("Policy Repo Rate", "6.50%"),
            ("Policy Repo Rate (old)", "6.25%"),
        ])
        with patch("tools.macro_context_tools.requests.get", return_value=self._response(html)):
            result = get_macro_context()
        self.assertEqual(result["repo_rate_pct"], 6.5)


if __name__ == "__main__":
    unittest.main()
