import json
import unittest
from unittest.mock import MagicMock, patch

import screener_pipeline
from signals.models import Signal


def _stock(symbol: str, industry: str = "Information Technology") -> dict:
    return {"symbol": symbol, "company_name": f"{symbol} Ltd", "industry": industry, "isin": None}


def _fake_quote_tool(payload: dict) -> MagicMock:
    tool = MagicMock()
    tool.run.return_value = json.dumps(payload)
    return tool


class FetchOneTest(unittest.TestCase):
    def test_combines_quote_and_technical_metrics(self) -> None:
        quote = {
            "company_name": "TCS Ltd", "primary_exchange": "NSE", "sector": "Technology",
            "current_price": 3500.0, "pe_ratio": 28.5, "market_cap_cr": 1250000.0,
            "avg_volume_10d": 2500000.0,
        }
        tech = Signal("technical", "BULLISH_TREND", 0.5, {"rsi14": 62.3, "ema20_above_ema50": True})
        with patch("tools.nse_tools.get_stock_quote", _fake_quote_tool(quote)), \
             patch("signals.technical.technical_signal", return_value=tech):
            result = screener_pipeline._fetch_one(_stock("TCS"))

        self.assertNotIn("error", result)
        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(result["company_name"], "TCS Ltd")
        self.assertEqual(result["exchange"], "NSE")
        self.assertEqual(result["nse_industry"], "Information Technology")
        self.assertEqual(result["sector"], "Technology")
        self.assertEqual(result["pe_ratio"], 28.5)
        self.assertEqual(result["rsi14"], 62.3)
        self.assertEqual(result["ema_trend"], "bullish")

    def test_bearish_trend_from_ema_posture(self) -> None:
        quote = {"current_price": 100.0}
        tech = Signal("technical", "BEARISH_TREND", -0.4, {"rsi14": 25.0, "ema20_above_ema50": False})
        with patch("tools.nse_tools.get_stock_quote", _fake_quote_tool(quote)), \
             patch("signals.technical.technical_signal", return_value=tech):
            result = screener_pipeline._fetch_one(_stock("XYZ"))
        self.assertEqual(result["ema_trend"], "bearish")

    def test_unknown_technical_signal_leaves_trend_and_rsi_null(self) -> None:
        # technical_signal returns UNKNOWN (no rsi14/ema20_above_ema50 in meta)
        # when there isn't enough price history — never guessed.
        quote = {"current_price": 100.0}
        tech = Signal("technical", "UNKNOWN", 0, {"closes_available": 10})
        with patch("tools.nse_tools.get_stock_quote", _fake_quote_tool(quote)), \
             patch("signals.technical.technical_signal", return_value=tech):
            result = screener_pipeline._fetch_one(_stock("NEWIPO"))
        self.assertIsNone(result["rsi14"])
        self.assertIsNone(result["ema_trend"])

    def test_quote_error_returns_error_result(self) -> None:
        with patch("tools.nse_tools.get_stock_quote", _fake_quote_tool({"error": "No data found for XYZ"})):
            result = screener_pipeline._fetch_one(_stock("XYZ"))
        self.assertIn("error", result)
        self.assertEqual(result["symbol"], "XYZ")

    def test_exception_returns_error_result_not_raise(self) -> None:
        with patch("tools.nse_tools.get_stock_quote", side_effect=RuntimeError("boom")):
            result = screener_pipeline._fetch_one(_stock("XYZ"))
        self.assertIn("error", result)
        self.assertEqual(result["symbol"], "XYZ")


class RunHealthSignalTest(unittest.TestCase):
    """run()'s return value drives whether the scheduled CI workflow (and the
    API's refresh endpoint logging) treats a run as healthy — see
    _MAX_ACCEPTABLE_ERROR_RATE. Mocks every I/O boundary (DB, constituent-list
    fetch, per-stock fetch) to isolate just that decision logic, same
    approach as tests/test_sme_ema_pipeline.py's RunHealthSignalTest."""

    def setUp(self) -> None:
        patches = [
            patch.object(screener_pipeline, "get_engine", return_value=MagicMock()),
            patch.object(screener_pipeline, "_upsert_stocks"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_empty_constituent_list_returns_false(self) -> None:
        with patch.object(screener_pipeline, "get_nifty500_constituents", return_value=[]):
            self.assertFalse(screener_pipeline.run())

    def test_high_error_rate_returns_false(self) -> None:
        stocks = [_stock(f"SYM{i}") for i in range(10)]

        def _fetch(stock):
            idx = int(stock["symbol"].removeprefix("SYM"))
            if idx < 6:  # 60% error rate, above the 50% threshold
                return {"error": "no data", "symbol": stock["symbol"]}
            return {"symbol": stock["symbol"]}

        with patch.object(screener_pipeline, "get_nifty500_constituents", return_value=stocks), \
             patch.object(screener_pipeline, "_fetch_one", side_effect=_fetch):
            self.assertFalse(screener_pipeline.run())

    def test_low_error_rate_returns_true(self) -> None:
        stocks = [_stock(f"SYM{i}") for i in range(10)]

        def _fetch(stock):
            if stock["symbol"] == "SYM0":  # 10% error rate, well under the threshold
                return {"error": "no data", "symbol": stock["symbol"]}
            return {"symbol": stock["symbol"]}

        with patch.object(screener_pipeline, "get_nifty500_constituents", return_value=stocks), \
             patch.object(screener_pipeline, "_fetch_one", side_effect=_fetch):
            self.assertTrue(screener_pipeline.run())


if __name__ == "__main__":
    unittest.main()
