import unittest
from datetime import datetime

from tools._nse_dates import parse_nse_date


class ParseNseDateTest(unittest.TestCase):
    def test_dmy_abbrev_month_with_time(self) -> None:
        self.assertEqual(parse_nse_date("15-Jan-2024 10:30"), datetime(2024, 1, 15, 10, 30))

    def test_dmy_abbrev_month(self) -> None:
        self.assertEqual(parse_nse_date("15-Jan-2024"), datetime(2024, 1, 15))

    def test_ymd_dashes(self) -> None:
        self.assertEqual(parse_nse_date("2024-01-15"), datetime(2024, 1, 15))

    def test_dmy_slashes(self) -> None:
        self.assertEqual(parse_nse_date("15/01/2024"), datetime(2024, 1, 15))

    def test_dmy_dashes_numeric(self) -> None:
        self.assertEqual(parse_nse_date("15-01-2024"), datetime(2024, 1, 15))

    def test_unparseable_returns_none(self) -> None:
        self.assertIsNone(parse_nse_date("not a date"))

    def test_custom_formats_restrict_matching(self) -> None:
        # A caller that never listed "%d-%m-%Y" among its own formats must
        # not have it sneak in via the shared default.
        self.assertIsNone(parse_nse_date("15-01-2024", ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y")))


if __name__ == "__main__":
    unittest.main()
