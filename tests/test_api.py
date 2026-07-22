import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from fastapi.testclient import TestClient

import api
import cache

client = TestClient(api.app)


class _FakeConn:
    """Fake SQLAlchemy connection: returns queued results in call order."""

    def __init__(self, results: list) -> None:
        self._results = list(results)

    def execute(self, *_args, **_kwargs):
        return self._results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _fake_sme_engine(rows, total_monitored, golden_now, last_run):
    rows_result = MagicMock()
    rows_result.mappings.return_value.fetchall.return_value = rows
    total_result = MagicMock()
    total_result.scalar.return_value = total_monitored
    golden_result = MagicMock()
    golden_result.scalar.return_value = golden_now
    last_run_result = MagicMock()
    last_run_result.scalar.return_value = last_run

    conn = _FakeConn([rows_result, total_result, golden_result, last_run_result])
    engine = MagicMock()
    engine.connect.return_value = conn
    return engine


class ApiSmokeTest(unittest.TestCase):
    def test_root_reports_service_name(self) -> None:
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["service"], "AlphaPulse API")

    def test_health(self) -> None:
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)


class RateLimitHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def test_allows_up_to_max_calls_then_blocks(self) -> None:
        req = MagicMock()
        req.client.host = "9.9.9.9"
        with patch("api.time.monotonic", return_value=1000.0):
            for _ in range(3):
                api._rate_limit(req, "bucket_a", max_calls=3, window_seconds=60)
            with self.assertRaises(Exception) as ctx:
                api._rate_limit(req, "bucket_a", max_calls=3, window_seconds=60)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_window_expiry_allows_new_calls(self) -> None:
        req = MagicMock()
        req.client.host = "9.9.9.9"
        with patch("api.time.monotonic", side_effect=[0.0, 0.0, 100.0]):
            api._rate_limit(req, "bucket_b", max_calls=2, window_seconds=10)
            api._rate_limit(req, "bucket_b", max_calls=2, window_seconds=10)
            # third call happens after the 10s window has elapsed — should not raise
            api._rate_limit(req, "bucket_b", max_calls=2, window_seconds=10)

    def test_different_ips_have_independent_buckets(self) -> None:
        req_a = MagicMock()
        req_a.client.host = "1.1.1.1"
        req_b = MagicMock()
        req_b.client.host = "2.2.2.2"
        with patch("api.time.monotonic", return_value=500.0):
            api._rate_limit(req_a, "bucket_c", max_calls=1, window_seconds=60)
            # different IP, same bucket name — should not be blocked
            api._rate_limit(req_b, "bucket_c", max_calls=1, window_seconds=60)
            with self.assertRaises(Exception):
                api._rate_limit(req_a, "bucket_c", max_calls=1, window_seconds=60)


class AnalyseEndpointRateLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def test_429_when_over_limit_without_running_pipeline(self) -> None:
        # Pre-seed the bucket at the max so the request is rejected before any
        # of the (unmocked, network-touching) analysis pipeline ever runs.
        api._RATE_LIMIT_CALLS["analyse:testclient"] = [api.time.monotonic()] * 20
        resp = client.get("/api/analyse/TCS")
        self.assertEqual(resp.status_code, 429)


class MarketPicksForceRateLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def test_429_when_force_rescan_over_limit(self) -> None:
        api._RATE_LIMIT_CALLS["market_picks_force:testclient"] = [api.time.monotonic()] * 3
        resp = client.get("/api/market-picks?force=true")
        self.assertEqual(resp.status_code, 429)


class PricesEndpointTest(unittest.TestCase):
    def test_returns_price_for_known_symbol(self) -> None:
        fast_info = MagicMock()
        fast_info.last_price = 100.0
        fast_info.previous_close = 90.0
        fake_ticker = MagicMock()
        fake_ticker.fast_info = fast_info

        with patch("yfinance.Ticker", return_value=fake_ticker):
            resp = client.get("/api/prices?symbols=TCS")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("TCS", body["prices"])
        self.assertEqual(body["prices"]["TCS"]["price"], 100.0)
        self.assertAlmostEqual(body["prices"]["TCS"]["change_pct"], 11.11, places=1)

    def test_unknown_symbol_returns_empty_entry(self) -> None:
        fast_info = MagicMock()
        fast_info.last_price = None
        fast_info.previous_close = None
        fake_ticker = MagicMock()
        fake_ticker.fast_info = fast_info

        with patch("yfinance.Ticker", return_value=fake_ticker):
            resp = client.get("/api/prices?symbols=NOSUCHSYMBOL")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["prices"]["NOSUCHSYMBOL"], {})

    def test_caps_symbol_list_at_50(self) -> None:
        symbols = ",".join(f"SYM{i}" for i in range(60))
        fake_ticker = MagicMock()
        fake_ticker.fast_info = MagicMock(last_price=None, previous_close=None)
        with patch("yfinance.Ticker", return_value=fake_ticker) as mocked:
            resp = client.get(f"/api/prices?symbols={symbols}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["prices"]), 50)


class PriceHistoryEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-price-history-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)

    def _fake_history_df(self, n: int = 40) -> pd.DataFrame:
        idx = pd.date_range("2026-01-01", periods=n, freq="D")
        return pd.DataFrame({"Close": [100.0 + i for i in range(n)]}, index=idx)

    def test_returns_series_from_yfinance_and_caches_it(self) -> None:
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = self._fake_history_df(40)

        with patch("yfinance.Ticker", return_value=fake_ticker):
            resp = client.get("/api/prices/history/TCS?days=180")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "TCS")
        self.assertEqual(body["exchange"], "NSE")
        self.assertEqual(len(body["closes"]), 40)
        self.assertEqual(body["closes"][0], 100.0)

        # second call must be served from cache — no yfinance access needed at all.
        with patch("yfinance.Ticker", side_effect=AssertionError("should not hit yfinance again")):
            resp2 = client.get("/api/prices/history/TCS?days=180")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(len(resp2.json()["closes"]), 40)

    def test_falls_back_to_bse_when_nse_has_no_data(self) -> None:
        empty_ticker = MagicMock()
        empty_ticker.history.return_value = pd.DataFrame()
        bse_ticker = MagicMock()
        bse_ticker.history.return_value = self._fake_history_df(10)

        def _ticker_side_effect(sym: str):
            return empty_ticker if sym.endswith(".NS") else bse_ticker

        with patch("yfinance.Ticker", side_effect=_ticker_side_effect):
            resp = client.get("/api/prices/history/SOMESTOCK?days=30")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["exchange"], "BSE")

    def test_no_data_on_either_exchange_returns_empty_series(self) -> None:
        empty_ticker = MagicMock()
        empty_ticker.history.return_value = pd.DataFrame()

        with patch("yfinance.Ticker", return_value=empty_ticker):
            resp = client.get("/api/prices/history/NOSUCHSYMBOL")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["closes"], [])
        self.assertIsNone(body["exchange"])

    def test_days_query_param_is_bounded(self) -> None:
        resp = client.get("/api/prices/history/TCS?days=1000")
        self.assertEqual(resp.status_code, 422)
        resp2 = client.get("/api/prices/history/TCS?days=1")
        self.assertEqual(resp2.status_code, 422)


class SmeSignalsEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._SME_ENGINE = None

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._SME_ENGINE = None

    def test_invalid_direction_returns_422_even_without_db(self) -> None:
        resp = client.get("/api/sme-signals?direction=sideways")
        self.assertEqual(resp.status_code, 422)

    def test_missing_database_url_returns_503(self) -> None:
        resp = client.get("/api/sme-signals")
        self.assertEqual(resp.status_code, 503)

    def test_returns_signals_shape_with_mocked_engine(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = _fake_sme_engine(
            rows=[{"symbol": "ABC", "cross": "golden"}],
            total_monitored=120,
            golden_now=42,
            last_run="2026-07-20T00:00:00",
        )
        with patch("api._get_sme_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals?lookback=5&direction=golden")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total_monitored"], 120)
        self.assertEqual(body["golden_now"], 42)
        self.assertEqual(len(body["signals"]), 1)
        self.assertEqual(body["signals"][0]["symbol"], "ABC")

    def test_db_error_returns_sanitized_503(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("api._get_sme_engine", side_effect=RuntimeError("connection refused: password exposed")):
            resp = client.get("/api/sme-signals")

        self.assertEqual(resp.status_code, 503)
        # The raw exception (which could leak DSN/credentials) must not reach the client.
        self.assertNotIn("password", resp.text)
        self.assertNotIn("exposed", resp.text)


class SmeSignalHistoryEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._SME_ENGINE = None

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._SME_ENGINE = None

    def _fake_history_engine(self, series_rows, stock_row):
        series_result = MagicMock()
        series_result.mappings.return_value.fetchall.return_value = series_rows
        stock_result = MagicMock()
        stock_result.mappings.return_value.first.return_value = stock_row

        conn = _FakeConn([series_result, stock_result])
        engine = MagicMock()
        engine.connect.return_value = conn
        return engine

    def test_missing_database_url_returns_503(self) -> None:
        resp = client.get("/api/sme-signals/ABC/history")
        self.assertEqual(resp.status_code, 503)

    def test_returns_series_with_mocked_engine(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        series = [
            {"trade_date": "2026-05-01", "close_price": 10.0, "ema20": 9.5, "ema50": 9.0, "cross": None},
            {"trade_date": "2026-05-02", "close_price": 10.5, "ema20": 9.8, "ema50": 9.1, "cross": "golden"},
        ]
        fake_engine = self._fake_history_engine(series, {"name": "ABC Corp", "exchange": "NSE"})
        with patch("api._get_sme_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals/abc/history")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "ABC")
        self.assertEqual(body["name"], "ABC Corp")
        self.assertEqual(len(body["series"]), 2)
        self.assertEqual(body["series"][1]["cross"], "golden")

    def test_unknown_symbol_returns_404(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = self._fake_history_engine([], None)
        with patch("api._get_sme_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals/NOSUCH/history")
        self.assertEqual(resp.status_code, 404)

    def test_db_error_returns_sanitized_503(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("api._get_sme_engine", side_effect=RuntimeError("connection refused: password exposed")):
            resp = client.get("/api/sme-signals/ABC/history")
        self.assertEqual(resp.status_code, 503)
        self.assertNotIn("password", resp.text)


class SmeRefreshEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._SME_REFRESHING = False
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._SME_REFRESHING = False
        api._RATE_LIMIT_CALLS.clear()

    def test_missing_database_url_returns_503(self) -> None:
        resp = client.post("/api/sme-signals/refresh")
        self.assertEqual(resp.status_code, 503)

    def test_already_refreshing_returns_409(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        api._SME_REFRESHING = True
        resp = client.post("/api/sme-signals/refresh")
        self.assertEqual(resp.status_code, 409)

    def test_rate_limited_returns_429_before_starting_pipeline(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        api._RATE_LIMIT_CALLS["sme_refresh:testclient"] = [api.time.monotonic()] * 3
        resp = client.post("/api/sme-signals/refresh")
        self.assertEqual(resp.status_code, 429)
        # Must not have flipped the refreshing flag or launched anything.
        self.assertFalse(api._SME_REFRESHING)

    def test_accepted_when_under_limit_and_not_already_running(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"

        # Prevent the background pipeline from actually being scheduled — we're
        # testing the endpoint's synchronous contract (flag flip + 202 + task
        # scheduled), not the pipeline itself (covered by test_sme_ema_pipeline.py).
        # Closing the coroutine instead of leaving it dangling avoids an
        # "unawaited coroutine" ResourceWarning.
        def _close_and_stub(coro):
            coro.close()
            return MagicMock()

        with patch("api.asyncio.create_task", side_effect=_close_and_stub) as mocked_create_task:
            resp = client.post("/api/sme-signals/refresh")
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(api._SME_REFRESHING)
        mocked_create_task.assert_called_once()


if __name__ == "__main__":
    unittest.main()
