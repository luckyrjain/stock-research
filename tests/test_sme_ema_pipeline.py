import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

import sme_ema_pipeline
from sme_ema_pipeline import _compute_ema_signals, _compute_liquidity, _fetch_ohlcv, _STORE_DAYS


def _make_result(closes: list[float], volumes: list[float] | None = None) -> dict:
    start = date(2026, 1, 1)
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(closes))])
    data = {"Close": closes}
    if volumes is not None:
        data["Volume"] = volumes
    df = pd.DataFrame(data, index=idx)
    return {"symbol": "TESTSME", "exchange": "NSE", "df": df}


def _fake_yf_history(**cols) -> pd.DataFrame:
    idx = pd.to_datetime([date(2026, 1, 1) + timedelta(days=i) for i in range(len(next(iter(cols.values()))))])
    return pd.DataFrame(cols, index=idx)


class FetchOhlcvTest(unittest.TestCase):
    """EMA/cross detection must keep working even if yfinance ever omits
    Volume for some ticker — only the liquidity figure should be lost, not
    the whole fetch (see _compute_liquidity, which already tolerates a
    missing Volume column)."""

    def test_keeps_close_and_volume_when_both_present(self) -> None:
        hist = _fake_yf_history(Close=[10.0, 11.0], Volume=[100.0, 200.0], Open=[9.0, 10.0])
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = hist
        with patch.object(sme_ema_pipeline.yf, "Ticker", return_value=fake_ticker):
            result = _fetch_ohlcv({"symbol": "ABC", "exchange": "NSE"})
        self.assertNotIn("error", result)
        self.assertIn("Volume", result["df"].columns)
        self.assertEqual(list(result["df"]["Volume"]), [100.0, 200.0])

    def test_succeeds_without_error_when_volume_column_missing(self) -> None:
        hist = _fake_yf_history(Close=[10.0, 11.0], Open=[9.0, 10.0])
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = hist
        with patch.object(sme_ema_pipeline.yf, "Ticker", return_value=fake_ticker):
            result = _fetch_ohlcv({"symbol": "ABC", "exchange": "NSE"})
        self.assertNotIn("error", result)
        self.assertNotIn("Volume", result["df"].columns)
        self.assertEqual(list(result["df"]["Close"]), [10.0, 11.0])


class ComputeEmaSignalsTest(unittest.TestCase):
    def test_golden_cross_detected_once_on_v_shaped_recovery(self) -> None:
        # 60 days falling, then 60 days rising strongly: EMA20 crosses above EMA50 once.
        closes = [200.0 - i for i in range(60)] + [140.0 + 3.0 * i for i in range(60)]
        rows = _compute_ema_signals(_make_result(closes))

        golden = [r for r in rows if r["cross"] == "golden"]
        death = [r for r in rows if r["cross"] == "death"]
        self.assertEqual(len(golden), 1)
        self.assertEqual(len(death), 0)
        # On the cross day EMA20 is above EMA50, and it is the first stored day above.
        self.assertGreater(golden[0]["ema20"], golden[0]["ema50"])
        first_above = next(r for r in rows if r["ema20"] > r["ema50"])
        self.assertEqual(first_above["trade_date"], golden[0]["trade_date"])

    def test_death_cross_detected_once_on_peak_and_decline(self) -> None:
        closes = [100.0 + i for i in range(60)] + [160.0 - 2.0 * i for i in range(60)]
        rows = _compute_ema_signals(_make_result(closes))

        golden = [r for r in rows if r["cross"] == "golden"]
        death = [r for r in rows if r["cross"] == "death"]
        self.assertEqual(len(death), 1)
        self.assertEqual(len(golden), 0)
        self.assertLess(death[0]["ema20"], death[0]["ema50"])

    def test_only_last_store_days_rows_are_returned(self) -> None:
        closes = [100.0 + (i % 7) for i in range(250)]
        rows = _compute_ema_signals(_make_result(closes))
        self.assertEqual(len(rows), _STORE_DAYS)

    def test_short_series_returns_all_rows(self) -> None:
        closes = [100.0 + i for i in range(40)]
        rows = _compute_ema_signals(_make_result(closes))
        self.assertEqual(len(rows), 40)

    def test_error_result_returns_empty_list(self) -> None:
        self.assertEqual(_compute_ema_signals({"error": "no data", "symbol": "X"}), [])


class ComputeLiquidityTest(unittest.TestCase):
    def test_averages_last_20_trading_days(self) -> None:
        # 30 days of history; only the last 20 (volume=200, close=50) should
        # count — the first 10 days (volume=1000) fall outside the window.
        closes  = [10.0] * 10 + [50.0] * 20
        volumes = [1000.0] * 10 + [200.0] * 20
        result = _compute_liquidity(_make_result(closes, volumes))
        self.assertEqual(result["symbol"], "TESTSME")
        self.assertAlmostEqual(result["avg_volume_20d"], 200.0, places=2)
        self.assertAlmostEqual(result["avg_turnover_20d"], 50.0 * 200.0, places=2)

    def test_error_result_returns_none(self) -> None:
        self.assertIsNone(_compute_liquidity({"error": "no data", "symbol": "X"}))

    def test_missing_volume_column_returns_none(self) -> None:
        self.assertIsNone(_compute_liquidity(_make_result([10.0, 11.0])))

    def test_empty_dataframe_returns_none(self) -> None:
        self.assertIsNone(_compute_liquidity(_make_result([], [])))


def _stock(symbol: str) -> dict:
    return {"symbol": symbol, "name": symbol, "isin": None, "series": "SM", "exchange": "NSE"}


class RunHealthSignalTest(unittest.TestCase):
    """run()'s return value drives whether the scheduled CI workflow (and the
    API's refresh endpoint logging) treats a run as healthy — see
    _MAX_ACCEPTABLE_ERROR_RATE. These tests mock every I/O boundary (DB,
    stock-list fetch, OHLCV fetch) to isolate just that decision logic.
    """

    def setUp(self) -> None:
        patches = [
            patch.object(sme_ema_pipeline, "get_engine", return_value=MagicMock()),
            patch.object(sme_ema_pipeline, "_upsert_stocks"),
            patch.object(sme_ema_pipeline, "_upsert_signals"),
            patch.object(sme_ema_pipeline, "_upsert_liquidity"),
            patch.object(sme_ema_pipeline, "_prune_signals"),
            patch.object(sme_ema_pipeline, "_print_summary"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_empty_stock_list_returns_false(self) -> None:
        with patch.object(sme_ema_pipeline, "get_all_sme_stocks", return_value=[]):
            self.assertFalse(sme_ema_pipeline.run())

    def test_high_ohlcv_error_rate_returns_false(self) -> None:
        stocks = [_stock(f"SYM{i}") for i in range(10)]

        def _fetch(stock):
            # 6/10 fail — 60% error rate, above the 50% threshold.
            idx = int(stock["symbol"].removeprefix("SYM"))
            if idx < 6:
                return {"error": "no data", "symbol": stock["symbol"]}
            return {"symbol": stock["symbol"], "exchange": "NSE", "df": pd.DataFrame({"Close": [1.0, 2.0]})}

        with patch.object(sme_ema_pipeline, "get_all_sme_stocks", return_value=stocks), \
             patch.object(sme_ema_pipeline, "_fetch_ohlcv", side_effect=_fetch):
            self.assertFalse(sme_ema_pipeline.run())

    def test_low_ohlcv_error_rate_returns_true(self) -> None:
        stocks = [_stock(f"SYM{i}") for i in range(10)]

        def _fetch(stock):
            # 1/10 fails — 10% error rate, well under the 50% threshold.
            if stock["symbol"] == "SYM0":
                return {"error": "no data", "symbol": stock["symbol"]}
            return {"symbol": stock["symbol"], "exchange": "NSE", "df": pd.DataFrame({"Close": [1.0, 2.0]})}

        with patch.object(sme_ema_pipeline, "get_all_sme_stocks", return_value=stocks), \
             patch.object(sme_ema_pipeline, "_fetch_ohlcv", side_effect=_fetch):
            self.assertTrue(sme_ema_pipeline.run())


if __name__ == "__main__":
    unittest.main()
