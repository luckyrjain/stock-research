import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

import sme_ema_pipeline
from sme_ema_pipeline import (
    _compute_ema_signals, _compute_liquidity, _compute_rsi, _compute_volume_spike,
    _extract_market_cap, _fetch_ohlcv, _safe_market_cap_cr, _RSI_PERIOD,
    _VOLUME_SPIKE_WINDOW_DAYS, _STORE_DAYS,
)


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


class SetupDbStampsAlembicHeadTest(unittest.TestCase):
    """--setup-db bypasses Alembic entirely (metadata.create_all directly) —
    without stamping head afterward, a fresh database ends up with every
    table but no alembic_version row, so a subsequent `alembic upgrade
    head` fails because the tables it wants to CREATE already exist. See
    db.models.stamp_alembic_head's own docstring and the deep gap analysis
    finding this closes."""

    def test_setup_db_calls_create_all_then_stamps_head(self) -> None:
        engine = MagicMock()
        with patch("sme_ema_pipeline.metadata") as mock_metadata, \
             patch("db.models.stamp_alembic_head") as mock_stamp:
            sme_ema_pipeline.setup_db(engine)
        mock_metadata.create_all.assert_called_once_with(engine)
        mock_stamp.assert_called_once_with()

    def test_reset_db_also_stamps_head_after_recreating_tables(self) -> None:
        # Regression test: --reset-db previously called metadata.drop_all(),
        # which would take down every table in the app's shared MetaData()
        # (including, since this session, real personal financial data in
        # the Portfolio Aggregator's tables) just to reset this pipeline's
        # own two tables. Scoped to sme_stocks/ema_signals specifically, the
        # same fix screener_pipeline.py --reset-db already has.
        with patch("sme_ema_pipeline.get_engine", return_value=MagicMock()), \
             patch("sme_ema_pipeline.sme_stocks") as mock_sme_stocks, \
             patch("sme_ema_pipeline.ema_signals") as mock_ema_signals, \
             patch("db.models.stamp_alembic_head") as mock_stamp, \
             patch("sys.argv", ["sme_ema_pipeline.py", "--reset-db"]), \
             patch("sme_ema_pipeline.init_error_tracking"):
            sme_ema_pipeline.main()
        mock_sme_stocks.drop.assert_called_once()
        mock_sme_stocks.create.assert_called_once()
        mock_ema_signals.drop.assert_called_once()
        mock_ema_signals.create.assert_called_once()
        mock_stamp.assert_called_once_with()


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

    def test_early_cross_before_convergence_margin_is_suppressed_even_when_total_history_clears_the_threshold(self) -> None:
        # Regression test for an adversarial-review finding: has_enough_history
        # only checked the TOTAL fetched series length (>= _MIN_HISTORY_DAYS),
        # not whether the specific bar where a cross actually fires has that
        # much real history behind IT. A stock whose total history just
        # clears the 75-day threshold can still have an early cross (EMA50
        # warmed up on far fewer bars than the stock's eventual total) land
        # inside the stored window -- exactly the "recency-weighted average,
        # not a converged EMA" scenario _MIN_HISTORY_DAYS exists to exclude.
        # 20 days falling, then a steep 60-day rise (80 days total, clears
        # the 75-day has_enough_history threshold) -- the actual EMA20/EMA50
        # crossover fires well before day 74 (position _MIN_HISTORY_DAYS - 1)
        # since the reversal is sharp. No cross should be reported anywhere.
        closes = [200.0 - i for i in range(20)] + [181.0 + 5.0 * i for i in range(60)]
        self.assertEqual(len(closes), 80)  # sanity: total clears _MIN_HISTORY_DAYS (75)

        rows = _compute_ema_signals(_make_result(closes))
        crosses = [r for r in rows if r["cross"] is not None]
        self.assertEqual(crosses, [])

    def test_rsi14_and_volume_spike_flow_through_to_stored_rows(self) -> None:
        # Exercises the real row-assembly path (df["rsi14"]/df["volume_spike"]
        # -> _safe_float / pd.notna -> the returned dict), not just the pure
        # _compute_rsi/_compute_volume_spike helpers in isolation — a wiring
        # bug here (wrong column read, inverted bool cast) wouldn't be caught
        # by the isolated ComputeRsiTest/ComputeVolumeSpikeTest suites alone.
        closes = [100.0 + i for i in range(40)]
        volumes = [1_000.0] * 39 + [10_000.0]  # spike only on the final day
        rows = _compute_ema_signals(_make_result(closes, volumes))

        # First _RSI_PERIOD rows have no RSI yet (not enough history).
        self.assertIsNone(rows[0]["rsi14"])
        self.assertIsNotNone(rows[-1]["rsi14"])
        self.assertGreater(rows[-1]["rsi14"], 0)
        self.assertLessEqual(rows[-1]["rsi14"], 100)

        # First _VOLUME_SPIKE_WINDOW_DAYS rows have no average yet -> None,
        # never silently False.
        self.assertIsNone(rows[0]["volume_spike"])
        self.assertTrue(rows[-1]["volume_spike"])
        # A day with volume in line with its trailing average is not a spike.
        steady_day = rows[-2]
        self.assertFalse(steady_day["volume_spike"])

    def test_volume_spike_is_none_without_volume_column(self) -> None:
        closes = [100.0 + i for i in range(40)]
        rows = _compute_ema_signals(_make_result(closes))  # no volumes passed
        self.assertTrue(all(r["volume_spike"] is None for r in rows))


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

    def test_fewer_than_20_days_of_history_returns_none(self) -> None:
        # Regression test for an adversarial-review finding: a newly-listed
        # NSE Emerge/BSE SME stock (this pipeline's own target universe)
        # routinely has well under 20 days of history. Averaging over
        # whatever's there (previously df.tail(20) with no floor check)
        # silently mislabeled a short average as a "20d" figure -- e.g. a
        # 3-day-old stock's listing-day volume spike dominating what gets
        # stored and rendered as avg_volume_20d/avg_turnover_20d. Same
        # min-periods convention _compute_volume_spike() already applies.
        closes  = [100.0, 102.0, 105.0]
        volumes = [5_000_000.0, 200_000.0, 180_000.0]
        result = _compute_liquidity(_make_result(closes, volumes))
        self.assertIsNone(result)


class ComputeRsiTest(unittest.TestCase):
    def test_first_period_rows_are_nan(self) -> None:
        close = pd.Series([100.0 + i for i in range(30)])
        rsi = _compute_rsi(close)
        self.assertTrue(rsi.iloc[:_RSI_PERIOD].isna().all())

    def test_steadily_rising_series_approaches_100(self) -> None:
        close = pd.Series([100.0 + i for i in range(40)])
        rsi = _compute_rsi(close)
        self.assertAlmostEqual(rsi.iloc[-1], 100.0, places=1)

    def test_steadily_falling_series_approaches_0(self) -> None:
        close = pd.Series([200.0 - i for i in range(40)])
        rsi = _compute_rsi(close)
        self.assertAlmostEqual(rsi.iloc[-1], 0.0, places=1)

    def test_values_bounded_0_to_100(self) -> None:
        close = pd.Series([100.0 + 5 * ((-1) ** i) * (i % 7) for i in range(60)])
        rsi = _compute_rsi(close)
        valid = rsi.dropna()
        self.assertTrue((valid >= 0).all())
        self.assertTrue((valid <= 100).all())

    def test_flat_price_is_neutral_not_nan(self) -> None:
        close = pd.Series([100.0] * 30)
        rsi = _compute_rsi(close)
        self.assertAlmostEqual(rsi.iloc[-1], 50.0, places=1)


class ComputeVolumeSpikeTest(unittest.TestCase):
    def test_first_window_rows_are_nan(self) -> None:
        df = pd.DataFrame({"Close": [10.0] * 30, "Volume": [100.0] * 30})
        spike = _compute_volume_spike(df)
        self.assertTrue(spike.iloc[:_VOLUME_SPIKE_WINDOW_DAYS - 1].isna().all())

    def test_flags_volume_more_than_double_trailing_average(self) -> None:
        volumes = [100.0] * 25 + [300.0]  # last day is 3x the trailing avg
        df = pd.DataFrame({"Close": [10.0] * 26, "Volume": volumes})
        spike = _compute_volume_spike(df)
        self.assertTrue(bool(spike.iloc[-1]))

    def test_does_not_flag_volume_under_threshold(self) -> None:
        volumes = [100.0] * 25 + [150.0]  # last day is only 1.5x the trailing avg
        df = pd.DataFrame({"Close": [10.0] * 26, "Volume": volumes})
        spike = _compute_volume_spike(df)
        self.assertFalse(bool(spike.iloc[-1]))

    def test_missing_volume_column_returns_all_nan(self) -> None:
        df = pd.DataFrame({"Close": [10.0] * 25})
        spike = _compute_volume_spike(df)
        self.assertTrue(spike.isna().all())


class SafeMarketCapTest(unittest.TestCase):
    def test_returns_market_cap_in_cr(self) -> None:
        fake_ticker = MagicMock()
        fake_ticker.fast_info.market_cap = 1_500_000_000  # ₹150 Cr
        self.assertAlmostEqual(_safe_market_cap_cr(fake_ticker), 150.0, places=2)

    def test_missing_market_cap_returns_none(self) -> None:
        fake_ticker = MagicMock()
        fake_ticker.fast_info.market_cap = None
        self.assertIsNone(_safe_market_cap_cr(fake_ticker))

    def test_exception_returns_none_not_raise(self) -> None:
        fake_ticker = MagicMock()
        type(fake_ticker).fast_info = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertIsNone(_safe_market_cap_cr(fake_ticker))


class ExtractMarketCapTest(unittest.TestCase):
    def test_error_result_returns_none(self) -> None:
        self.assertIsNone(_extract_market_cap({"error": "no data", "symbol": "X"}))

    def test_missing_market_cap_returns_none(self) -> None:
        self.assertIsNone(_extract_market_cap({"symbol": "ABC", "df": None}))

    def test_present_market_cap_is_extracted(self) -> None:
        result = _extract_market_cap({"symbol": "ABC", "df": None, "market_cap_cr": 42.5})
        self.assertEqual(result, {"symbol": "ABC", "market_cap_cr": 42.5})


def _stock(symbol: str) -> dict:
    return {"symbol": symbol, "name": symbol, "isin": None, "series": "SM", "exchange": "NSE"}


class UpsertStocksTest(unittest.TestCase):
    def test_conflict_update_refreshes_exchange_and_series(self) -> None:
        # Regression test for an adversarial-review finding: the ON CONFLICT
        # clause only refreshed name/isin/fetched_at, silently leaving a
        # stale exchange/series value in place forever even though every
        # pipeline run re-fetches and passes fresh values for both --
        # inconsistent with screener_pipeline.py's own sibling
        # _upsert_stocks(), which refreshes every column on conflict.
        engine = MagicMock()
        conn = MagicMock()
        engine.begin.return_value.__enter__.return_value = conn

        sme_ema_pipeline._upsert_stocks(engine, [_stock("ABC")])

        sql_text = str(conn.execute.call_args[0][0])
        conflict_clause = sql_text.split("ON CONFLICT", 1)[1]
        self.assertIn("exchange", conflict_clause)
        self.assertIn("series", conflict_clause)


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
            patch.object(sme_ema_pipeline, "_upsert_market_cap"),
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
