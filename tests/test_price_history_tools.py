import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

import cache
from tools.price_history_tools import get_price_series


class GetPriceSeriesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-price-series-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)

    def _fake_df(self, n: int = 40, start_price: float = 100.0) -> pd.DataFrame:
        idx = pd.date_range("2026-01-01", periods=n, freq="D")
        return pd.DataFrame({"Close": [start_price + i for i in range(n)]}, index=idx)

    def test_fetches_from_nse_and_caches(self) -> None:
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = self._fake_df(60)

        with patch("yfinance.Ticker", return_value=fake_ticker):
            result = get_price_series("TCS", days=180)

        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(result["exchange"], "NSE")
        self.assertEqual(len(result["closes"]), 60)

        with patch("yfinance.Ticker", side_effect=AssertionError("should not refetch")):
            cached = get_price_series("TCS", days=180)
        self.assertEqual(cached["closes"], result["closes"])

    def test_uppercases_and_strips_symbol(self) -> None:
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = self._fake_df(10)
        with patch("yfinance.Ticker", return_value=fake_ticker):
            result = get_price_series("  tcs  ")
        self.assertEqual(result["symbol"], "TCS")

    def test_falls_back_to_bse(self) -> None:
        empty_ticker = MagicMock()
        empty_ticker.history.return_value = pd.DataFrame()
        bse_ticker = MagicMock()
        bse_ticker.history.return_value = self._fake_df(10)

        def _side_effect(sym: str):
            return empty_ticker if sym.endswith(".NS") else bse_ticker

        with patch("yfinance.Ticker", side_effect=_side_effect):
            result = get_price_series("SOMESTOCK")
        self.assertEqual(result["exchange"], "BSE")

    def test_no_data_anywhere_returns_empty_series_not_error(self) -> None:
        empty_ticker = MagicMock()
        empty_ticker.history.return_value = pd.DataFrame()
        with patch("yfinance.Ticker", return_value=empty_ticker):
            result = get_price_series("NOSUCHSTOCK")
        self.assertEqual(result, {"symbol": "NOSUCHSTOCK", "exchange": None, "dates": [], "closes": []})

    def test_network_exception_falls_through_to_empty_series(self) -> None:
        with patch("yfinance.Ticker", side_effect=ConnectionError("boom")):
            result = get_price_series("TCS")
        self.assertEqual(result["closes"], [])

    def test_stale_cache_below_five_closes_is_refetched(self) -> None:
        cache.save("TCS", "price_history", {"symbol": "TCS", "exchange": "NSE", "dates": ["2026-01-01"], "closes": [100.0]})
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = self._fake_df(20)
        with patch("yfinance.Ticker", return_value=fake_ticker):
            result = get_price_series("TCS")
        self.assertEqual(len(result["closes"]), 20)


if __name__ == "__main__":
    unittest.main()
