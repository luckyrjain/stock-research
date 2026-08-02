import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from tools.eod_sources import (
    download_bhavcopy, parse_bhavcopy, parse_equity_master,
    parse_nav_all, fetch_scheme_history,
)

# Real sec_bhavdata_full header/format: values carry leading spaces, and
# DELIV_QTY / DELIV_PER are " -" for series without delivery reporting.
BHAVCOPY_FIXTURE = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
TCS, EQ, 03-Jul-2026, 3900.00, 3910.00, 3955.00, 3890.00, 3940.00, 3945.50, 3931.20, 1234567, 48540.12, 45678, 654321, 53.00
SBIN, BE, 03-Jul-2026, 810.00, 812.00, 820.00, 808.00, 815.00, 816.25, 814.90, 999999, 8148.91, 12345, 500000, 50.01
SOMEBOND, N1, 03-Jul-2026, 100.00, 100.00, 100.00, 100.00, 100.00, 100.00, 100.00, 10, 0.01, 2, -, -
NODELIV, BZ, 03-Jul-2026, 55.00, 55.00, 56.00, 54.00, 55.50, 55.75, 55.40, 4321, 2.41, 87, -, -
BADROW, EQ, 03-Jul-2026, oops, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1
"""

EQUITY_MASTER_FIXTURE = """SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE
TCS,Tata Consultancy Services Limited, EQ, 25-AUG-2004, 1, 1, INE467B01029, 1
SBIN,State Bank of India, EQ, 01-MAR-1995, 1, 1, INE062A01020, 1
"""


class ParseBhavcopyTest(unittest.TestCase):
    def test_allowed_series_kept_and_junk_filtered(self) -> None:
        out = parse_bhavcopy(BHAVCOPY_FIXTURE)
        symbols = {r["symbol"] for r in out["rows"]}
        self.assertEqual(symbols, {"TCS", "SBIN", "NODELIV"})
        self.assertEqual(out["skipped_series"], 1)   # SOMEBOND (N1)
        self.assertEqual(out["malformed"], 1)        # BADROW

    def test_row_values_parsed(self) -> None:
        out = parse_bhavcopy(BHAVCOPY_FIXTURE)
        tcs = next(r for r in out["rows"] if r["symbol"] == "TCS")
        self.assertEqual(tcs["trade_date"], date(2026, 7, 3))
        self.assertEqual(tcs["close"], 3945.50)
        self.assertEqual(tcs["prev_close"], 3900.00)
        self.assertEqual(tcs["volume"], 1234567)
        self.assertEqual(tcs["trades"], 45678)
        self.assertEqual(tcs["delivery_pct"], 53.00)
        self.assertEqual(tcs["turnover_lacs"], 48540.12)

    def test_dash_delivery_fields_become_none(self) -> None:
        out = parse_bhavcopy(BHAVCOPY_FIXTURE)
        nodeliv = next(r for r in out["rows"] if r["symbol"] == "NODELIV")
        self.assertIsNone(nodeliv["delivery_qty"])
        self.assertIsNone(nodeliv["delivery_pct"])


class ParseEquityMasterTest(unittest.TestCase):
    def test_master_rows_parsed(self) -> None:
        rows = parse_equity_master(EQUITY_MASTER_FIXTURE)
        self.assertEqual(len(rows), 2)
        tcs = next(r for r in rows if r["symbol"] == "TCS")
        self.assertEqual(tcs["company_name"], "Tata Consultancy Services Limited")
        self.assertEqual(tcs["isin"], "INE467B01029")
        self.assertEqual(tcs["listing_date"], date(2004, 8, 25))
        self.assertEqual(tcs["face_value"], 1.0)


class DownloadBhavcopyTest(unittest.TestCase):
    def _response(self, status: int, text: str) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        return resp

    @patch("tools.eod_sources._archive_path")
    def test_404_means_missing(self, mock_path: MagicMock) -> None:
        mock_path.return_value.exists.return_value = False
        session = MagicMock()
        session.get.return_value = self._response(404, "")
        out = download_bhavcopy(date(2026, 7, 4), session)
        self.assertEqual(out["status"], "missing")

    @patch("tools.eod_sources.make_nse_session")
    @patch("tools.eod_sources._archive_path")
    def test_persistent_bot_block_is_error(self, mock_path: MagicMock, mock_make: MagicMock) -> None:
        mock_path.return_value.exists.return_value = False
        html = self._response(200, "<!DOCTYPE html><html>Access Denied</html>")
        session = MagicMock()
        session.get.return_value = html
        retry_session = MagicMock()
        retry_session.get.return_value = html
        mock_make.return_value = retry_session
        out = download_bhavcopy(date(2026, 7, 3), session)
        self.assertEqual(out["status"], "error")
        self.assertIn("bot-block", out["error"])
        mock_make.assert_called_once()   # retry used a fresh session

    @patch("tools.eod_sources._archive_path")
    def test_ok_csv_is_archived_and_returned(self, mock_path: MagicMock) -> None:
        mock_path.return_value.exists.return_value = False
        session = MagicMock()
        session.get.return_value = self._response(200, BHAVCOPY_FIXTURE)
        out = download_bhavcopy(date(2026, 7, 3), session)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["csv"], BHAVCOPY_FIXTURE)
        mock_path.return_value.write_text.assert_called_once_with(BHAVCOPY_FIXTURE)

    @patch("tools.eod_sources._archive_path")
    def test_degenerate_200_body_is_error_and_not_archived(self, mock_path: MagicMock) -> None:
        mock_path.return_value.exists.return_value = False
        session = MagicMock()
        session.get.return_value = self._response(200, "garbage")
        out = download_bhavcopy(date(2026, 7, 3), session)
        self.assertEqual(out["status"], "error")
        self.assertIn("unexpected bhavcopy body", out["error"])
        mock_path.return_value.write_text.assert_not_called()


NAVALL_FIXTURE = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Large Cap Fund)

Axis Mutual Fund

120465;INF846K01EW2;INF846K01EX0;Axis Bluechip Fund - Growth;58.1234;03-Jul-2026
120466;INF846K01EY8;-;Axis Bluechip Fund - IDCW;18.4321;03-Jul-2026
119551;INF209K01VA3;-;Some Other Fund - Growth;N.A.;03-Jul-2026
"""


class ParseNavAllTest(unittest.TestCase):
    def test_only_held_schemes_kept(self) -> None:
        rows = parse_nav_all(NAVALL_FIXTURE, {"120465"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scheme_code"], "120465")
        self.assertEqual(rows[0]["nav"], 58.1234)
        self.assertEqual(rows[0]["nav_date"], date(2026, 7, 3))
        self.assertEqual(rows[0]["scheme_name"], "Axis Bluechip Fund - Growth")

    def test_na_nav_skipped(self) -> None:
        rows = parse_nav_all(NAVALL_FIXTURE, {"119551"})
        self.assertEqual(rows, [])

    def test_section_and_blank_lines_ignored(self) -> None:
        rows = parse_nav_all(NAVALL_FIXTURE, {"120465", "120466"})
        self.assertEqual(len(rows), 2)


class FetchSchemeHistoryTest(unittest.TestCase):
    @patch("tools.eod_sources.requests.get")
    def test_history_rows_filtered_by_since(self, mock_get: MagicMock) -> None:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "meta": {"scheme_name": "Axis Bluechip Fund - Growth"},
            "data": [
                {"date": "03-07-2026", "nav": "58.12340"},
                {"date": "02-07-2026", "nav": "57.90000"},
                {"date": "01-01-2020", "nav": "30.00000"},
            ],
        }
        out = fetch_scheme_history("120465", since=date(2026, 1, 1))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["rows"]), 2)
        self.assertEqual(out["rows"][0]["nav_date"], date(2026, 7, 3))
        self.assertEqual(out["rows"][0]["nav"], 58.1234)
        self.assertEqual(out["rows"][0]["scheme_name"], "Axis Bluechip Fund - Growth")

    @patch("tools.eod_sources.requests.get")
    def test_http_error_returns_error_dict(self, mock_get: MagicMock) -> None:
        mock_get.return_value.status_code = 500
        out = fetch_scheme_history("120465", since=date(2026, 1, 1))
        self.assertEqual(out["status"], "error")


if __name__ == "__main__":
    unittest.main()
