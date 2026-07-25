import unittest
from unittest.mock import MagicMock, patch

from tools.nifty500_tools import get_nifty500_constituents


class GetNifty500ConstituentsTest(unittest.TestCase):
    def _csv_response(self, csv_text: str) -> MagicMock:
        resp = MagicMock()
        resp.text = csv_text
        resp.raise_for_status.return_value = None
        return resp

    def test_parses_symbol_company_industry_isin(self) -> None:
        csv_text = (
            "Company Name,Industry,Symbol,Series,ISIN Code\n"
            "Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\n"
            "Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018\n"
        )
        session = MagicMock()
        session.get.return_value = self._csv_response(csv_text)
        with patch("tools.nifty500_tools._is_fresh", return_value=False), \
             patch("tools.nifty500_tools.requests.Session", return_value=session), \
             patch("tools.nifty500_tools._MIN_PLAUSIBLE_COUNT", 1), \
             patch("tools.nifty500_tools._save_cache"):
            stocks = get_nifty500_constituents(force=True)

        self.assertEqual(len(stocks), 2)
        self.assertEqual(stocks[0], {
            "symbol": "TCS",
            "company_name": "Tata Consultancy Services Ltd.",
            "industry": "Information Technology",
            "isin": "INE467B01029",
        })
        self.assertEqual(stocks[1]["symbol"], "RELIANCE")

    def test_row_with_missing_symbol_is_skipped(self) -> None:
        csv_text = (
            "Company Name,Industry,Symbol,Series,ISIN Code\n"
            "No Symbol Co.,Some Industry,,EQ,INE000A00000\n"
            "TCS Inc.,Information Technology,TCS,EQ,INE467B01029\n"
        )
        session = MagicMock()
        session.get.return_value = self._csv_response(csv_text)
        with patch("tools.nifty500_tools._is_fresh", return_value=False), \
             patch("tools.nifty500_tools.requests.Session", return_value=session), \
             patch("tools.nifty500_tools._MIN_PLAUSIBLE_COUNT", 1), \
             patch("tools.nifty500_tools._save_cache"):
            stocks = get_nifty500_constituents(force=True)
        self.assertEqual(len(stocks), 1)
        self.assertEqual(stocks[0]["symbol"], "TCS")

    def test_fetch_failure_falls_back_to_stale_cache(self) -> None:
        with patch("tools.nifty500_tools._is_fresh", return_value=False), \
             patch("tools.nifty500_tools.requests.Session", side_effect=ConnectionError("boom")), \
             patch("tools.nifty500_tools._CACHE_PATH") as fake_path:
            fake_path.exists.return_value = True
            fake_path.read_text.return_value = '[{"symbol": "STALE"}]'
            stocks = get_nifty500_constituents(force=True)
        self.assertEqual(stocks, [{"symbol": "STALE"}])

    def test_fetch_failure_with_no_cache_returns_empty_list(self) -> None:
        with patch("tools.nifty500_tools._is_fresh", return_value=False), \
             patch("tools.nifty500_tools.requests.Session", side_effect=ConnectionError("boom")), \
             patch("tools.nifty500_tools._CACHE_PATH") as fake_path:
            fake_path.exists.return_value = False
            stocks = get_nifty500_constituents(force=True)
        self.assertEqual(stocks, [])

    def test_fresh_cache_is_used_without_a_network_call(self) -> None:
        with patch("tools.nifty500_tools._is_fresh", return_value=True), \
             patch("tools.nifty500_tools._CACHE_PATH") as fake_path, \
             patch("tools.nifty500_tools.requests.Session") as session_cls:
            fake_path.read_text.return_value = '[{"symbol": "CACHED"}]'
            stocks = get_nifty500_constituents()
        self.assertEqual(stocks, [{"symbol": "CACHED"}])
        session_cls.assert_not_called()

    def test_empty_csv_body_falls_back_to_stale_cache(self) -> None:
        csv_text = "Company Name,Industry,Symbol,Series,ISIN Code\n"
        session = MagicMock()
        session.get.return_value = self._csv_response(csv_text)
        with patch("tools.nifty500_tools._is_fresh", return_value=False), \
             patch("tools.nifty500_tools.requests.Session", return_value=session), \
             patch("tools.nifty500_tools._CACHE_PATH") as fake_path:
            fake_path.exists.return_value = True
            fake_path.read_text.return_value = '[{"symbol": "STALE"}]'
            stocks = get_nifty500_constituents(force=True)
        self.assertEqual(stocks, [{"symbol": "STALE"}])

    def test_truncated_response_falls_back_to_stale_cache_rather_than_caching_it(self) -> None:
        # Regression test: a response with a handful of rows (e.g. a partial
        # download or a rate-limited/paginated reply) is nonempty, so the old
        # "if stocks:" check would treat it as a complete, cacheable universe
        # — silently truncating the screener's coverage to a few symbols
        # instead of the real ~500. It must be treated the same as a failed
        # fetch: fall back to a stale cache if one exists, and never be saved.
        csv_text = (
            "Company Name,Industry,Symbol,Series,ISIN Code\n"
            "Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\n"
        )
        session = MagicMock()
        session.get.return_value = self._csv_response(csv_text)
        with patch("tools.nifty500_tools._is_fresh", return_value=False), \
             patch("tools.nifty500_tools.requests.Session", return_value=session), \
             patch("tools.nifty500_tools._save_cache") as mock_save, \
             patch("tools.nifty500_tools._CACHE_PATH") as fake_path:
            fake_path.exists.return_value = True
            fake_path.read_text.return_value = '[{"symbol": "STALE"}]'
            stocks = get_nifty500_constituents(force=True)
        self.assertEqual(stocks, [{"symbol": "STALE"}])
        mock_save.assert_not_called()

    def test_truncated_response_with_no_stale_cache_returns_empty_list(self) -> None:
        csv_text = (
            "Company Name,Industry,Symbol,Series,ISIN Code\n"
            "Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\n"
        )
        session = MagicMock()
        session.get.return_value = self._csv_response(csv_text)
        with patch("tools.nifty500_tools._is_fresh", return_value=False), \
             patch("tools.nifty500_tools.requests.Session", return_value=session), \
             patch("tools.nifty500_tools._save_cache") as mock_save, \
             patch("tools.nifty500_tools._CACHE_PATH") as fake_path:
            fake_path.exists.return_value = False
            stocks = get_nifty500_constituents(force=True)
        self.assertEqual(stocks, [])
        mock_save.assert_not_called()

    def test_full_size_response_is_cached_normally(self) -> None:
        rows = "\n".join(f"Company {i} Ltd.,Some Industry,SYM{i},EQ,INE{i:09d}" for i in range(450))
        csv_text = "Company Name,Industry,Symbol,Series,ISIN Code\n" + rows + "\n"
        session = MagicMock()
        session.get.return_value = self._csv_response(csv_text)
        with patch("tools.nifty500_tools._is_fresh", return_value=False), \
             patch("tools.nifty500_tools.requests.Session", return_value=session), \
             patch("tools.nifty500_tools._save_cache") as mock_save:
            stocks = get_nifty500_constituents(force=True)
        self.assertEqual(len(stocks), 450)
        mock_save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
