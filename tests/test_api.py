import json
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


def _fake_sme_engine(rows, total_monitored, golden_now, last_run, hit_rate=None):
    rows_result = MagicMock()
    rows_result.mappings.return_value.fetchall.return_value = rows
    total_result = MagicMock()
    total_result.scalar.return_value = total_monitored
    golden_result = MagicMock()
    golden_result.scalar.return_value = golden_now
    last_run_result = MagicMock()
    last_run_result.scalar.return_value = last_run
    hit_rate_result = MagicMock()
    hit_rate_result.mappings.return_value.first.return_value = hit_rate or {"sample_size": 0, "wins": 0}

    conn = _FakeConn([rows_result, total_result, golden_result, last_run_result, hit_rate_result])
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


class CorsTest(unittest.TestCase):
    def test_allowed_origin_gets_cors_headers(self) -> None:
        resp = client.options(
            "/health",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "http://localhost:3000")

    def test_disallowed_origin_is_rejected(self) -> None:
        resp = client.options(
            "/health",
            headers={"Origin": "http://evil.example.com", "Access-Control-Request-Method": "GET"},
        )
        self.assertNotIn("access-control-allow-origin", {h.lower() for h in resp.headers})

    def test_requests_without_an_origin_header_are_unaffected(self) -> None:
        # The Next.js proxy routes talk to this backend server-to-server — no
        # Origin header, so CORS never applies to that path.
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)


class LlmConcurrencyCeilingTest(unittest.TestCase):
    def setUp(self) -> None:
        api._llm_concurrency_count = 0

    def tearDown(self) -> None:
        api._llm_concurrency_count = 0

    def test_acquire_up_to_limit_then_rejects(self) -> None:
        with patch("api._LLM_CONCURRENCY_LIMIT", 2):
            self.assertTrue(api._acquire_llm_slot())
            self.assertTrue(api._acquire_llm_slot())
            self.assertFalse(api._acquire_llm_slot())

    def test_release_frees_a_slot_for_reuse(self) -> None:
        with patch("api._LLM_CONCURRENCY_LIMIT", 1):
            self.assertTrue(api._acquire_llm_slot())
            self.assertFalse(api._acquire_llm_slot())
            api._release_llm_slot()
            self.assertTrue(api._acquire_llm_slot())

    def test_release_below_zero_is_clamped_not_negative(self) -> None:
        api._release_llm_slot()
        api._release_llm_slot()
        self.assertEqual(api._llm_concurrency_count, 0)


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


class ValidateSymbolEndpointTest(unittest.TestCase):
    """Covers validate_symbol's branch-heavy paths: ISIN resolution (CSV hit and
    yfinance fallback), the BSE-forced path, and the NSE/BSE/Screener fallback chain.
    All network-touching helpers are mocked at the api.* boundary.
    """

    def test_isin_resolved_via_nse_master_falls_through_to_nse_lookup(self) -> None:
        with patch("api._load_isin_map", return_value={"INE009A01021": {"symbol": "TCS"}}), \
             patch("api._autocomplete_sync", return_value=[{"symbol": "TCS", "activeSeries": True}]), \
             patch("api._bse_autocomplete_sync", return_value=[]), \
             patch("api._quote_meta_sync", return_value={"company": "Tata Consultancy Services", "isin": "INE009A01021"}), \
             patch("api._bse_search_by_isin", return_value={}):
            resp = client.get("/api/validate/INE009A01021")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["symbol"], "TCS")
        self.assertEqual(body["exchange"], "NSE")

    def test_isin_not_in_csv_resolves_via_yfinance_bse(self) -> None:
        yf_info = {"symbol": "TAPARIA.BO", "longName": "Taparia Tools Ltd"}
        fake_ticker = MagicMock()
        fake_ticker.info = yf_info
        with patch("api._load_isin_map", return_value={}), \
             patch("yfinance.Ticker", return_value=fake_ticker):
            resp = client.get("/api/validate/INE999Z99999")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["exchange"], "BSE")
        self.assertEqual(body["symbol"], "TAPARIA")

    def test_isin_unresolvable_anywhere_returns_not_found(self) -> None:
        fake_ticker = MagicMock()
        fake_ticker.info = {}
        with patch("api._load_isin_map", return_value={}), \
             patch("yfinance.Ticker", return_value=fake_ticker):
            resp = client.get("/api/validate/INE000000000")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["valid"])
        self.assertFalse(body["found"])

    def test_bse_forced_path_resolves_via_screener(self) -> None:
        with patch("api._screener_company_page_sync",
                    return_value={"nse": None, "bse": "TAPARIA", "company": "Taparia Tools", "isin": "INE123"}):
            resp = client.get("/api/validate/505685?exchange=BSE")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["exchange"], "BSE")
        self.assertEqual(body["symbol"], "TAPARIA")

    def test_bse_forced_path_unresolvable_returns_not_found(self) -> None:
        with patch("api._screener_company_page_sync", return_value={}):
            resp = client.get("/api/validate/NOPE?exchange=BSE")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["valid"])
        self.assertFalse(body["found"])

    def test_nse_exact_match_returns_valid_with_suggestions(self) -> None:
        with patch("api._autocomplete_sync", return_value=[
                {"symbol": "TCS", "activeSeries": True},
                {"symbol": "TCSFIN", "activeSeries": True, "company": "TCS Finance"},
            ]), \
             patch("api._bse_autocomplete_sync", return_value=[]), \
             patch("api._quote_meta_sync", return_value={"company": "Tata Consultancy Services", "isin": "INE009A01021"}), \
             patch("api._bse_search_by_isin", return_value={}):
            resp = client.get("/api/validate/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["symbol"], "TCS")
        self.assertEqual(body["exchange"], "NSE")
        self.assertEqual(len(body["suggestions"]), 1)

    def test_suspended_nse_symbol_is_found_but_not_valid(self) -> None:
        with patch("api._autocomplete_sync", return_value=[{"symbol": "DEADCO", "activeSeries": False}]), \
             patch("api._bse_autocomplete_sync", return_value=[]), \
             patch("api._quote_meta_sync", return_value={"company": "Dead Co", "isin": None}), \
             patch("api._bse_search_by_isin", return_value={}):
            resp = client.get("/api/validate/DEADCO")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["found"])
        self.assertFalse(body["valid"])
        self.assertTrue(body["suspended"])

    def test_nse_miss_falls_back_to_bse_match(self) -> None:
        with patch("api._autocomplete_sync", return_value=[]), \
             patch("api._bse_autocomplete_sync", return_value=[{"symbol": "505685", "company": "Taparia Tools"}]), \
             patch("api._screener_company_page_sync",
                    return_value={"nse": None, "bse": "TAPARIA", "company": "Taparia Tools", "isin": "INE123"}):
            resp = client.get("/api/validate/TAPARIA")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["exchange"], "BSE")

    def test_nse_and_bse_miss_falls_back_to_screener_search(self) -> None:
        with patch("api._autocomplete_sync", return_value=[]), \
             patch("api._bse_autocomplete_sync", return_value=[]), \
             patch("api._screener_search_sync", return_value=[{"url": "/company/500325/"}]), \
             patch("api._screener_company_page_sync",
                    return_value={"nse": "RELIANCE", "bse": None, "company": "Reliance Industries", "isin": "INE002A01018"}):
            resp = client.get("/api/validate/RELIANC")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["symbol"], "RELIANCE")

    def test_nothing_found_returns_not_found(self) -> None:
        with patch("api._autocomplete_sync", return_value=[]), \
             patch("api._bse_autocomplete_sync", return_value=[]), \
             patch("api._screener_search_sync", return_value=[]):
            resp = client.get("/api/validate/ZZZNOTREAL")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["found"])
        self.assertFalse(body["valid"])


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


class RemainingEndpointsRateLimitTest(unittest.TestCase):
    """/api/prices, /api/validate, and the two history endpoints previously had
    no rate limit at all — /api/prices was the worst offender (up to 50
    yfinance calls per request, at unbounded request rate).
    """

    def setUp(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def test_429_on_prices_when_over_limit(self) -> None:
        api._RATE_LIMIT_CALLS["prices:testclient"] = [api.time.monotonic()] * 30
        resp = client.get("/api/prices?symbols=TCS")
        self.assertEqual(resp.status_code, 429)

    def test_429_on_validate_when_over_limit(self) -> None:
        api._RATE_LIMIT_CALLS["validate:testclient"] = [api.time.monotonic()] * 30
        resp = client.get("/api/validate/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_429_on_prices_history_when_over_limit(self) -> None:
        api._RATE_LIMIT_CALLS["prices_history:testclient"] = [api.time.monotonic()] * 60
        resp = client.get("/api/prices/history/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_429_on_market_picks_history_when_over_limit(self) -> None:
        api._RATE_LIMIT_CALLS["market_picks_history:testclient"] = [api.time.monotonic()] * 60
        resp = client.get("/api/market-picks/history")
        self.assertEqual(resp.status_code, 429)


class NextScheduledMarketPicksRunTest(unittest.TestCase):
    def test_before_this_weeks_run_returns_this_monday(self) -> None:
        from datetime import datetime, timezone
        friday = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)
        next_run = api._next_scheduled_market_picks_run(friday)
        self.assertEqual(next_run.isoformat(), "2026-07-27T01:30:00+00:00")

    def test_on_run_day_before_run_time_returns_today(self) -> None:
        from datetime import datetime, timezone
        monday_early = datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc)
        next_run = api._next_scheduled_market_picks_run(monday_early)
        self.assertEqual(next_run.isoformat(), "2026-07-27T01:30:00+00:00")

    def test_on_run_day_after_run_time_returns_next_week(self) -> None:
        from datetime import datetime, timezone
        monday_late = datetime(2026, 7, 27, 2, 0, tzinfo=timezone.utc)
        next_run = api._next_scheduled_market_picks_run(monday_late)
        self.assertEqual(next_run.isoformat(), "2026-08-03T01:30:00+00:00")


class MarketPicksStatusEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def test_returns_cache_metadata_and_next_scheduled_run(self) -> None:
        fake_status = {"last_run_at": "2026-07-20T00:00:00+00:00", "is_fresh": True}
        with patch("market_picks_pipeline.picks_cache_status", return_value=fake_status):
            resp = client.get("/api/market-picks/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["last_run_at"], "2026-07-20T00:00:00+00:00")
        self.assertTrue(body["cache_fresh"])
        self.assertIn("next_scheduled_at", body)

    def test_no_cache_returns_null_last_run(self) -> None:
        fake_status = {"last_run_at": None, "is_fresh": False}
        with patch("market_picks_pipeline.picks_cache_status", return_value=fake_status):
            resp = client.get("/api/market-picks/status")
        body = resp.json()
        self.assertIsNone(body["last_run_at"])
        self.assertFalse(body["cache_fresh"])

    def test_rate_limited_returns_429(self) -> None:
        api._RATE_LIMIT_CALLS["market_picks_status:testclient"] = [api.time.monotonic()] * 60
        resp = client.get("/api/market-picks/status")
        self.assertEqual(resp.status_code, 429)


class MarketPicksHistoryEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-picks-history-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._history_patch = patch.object(api, "_PICKS_HISTORY_DIR", Path(self._tmpdir))
        self._history_patch.start()
        self.addCleanup(self._history_patch.stop)

        # _fetch_nifty_closes() reads/writes the real cache.py module (using
        # "NSEI" as a pseudo-symbol) — isolate it the same way
        # PriceHistoryEndpointTest isolates cache.CACHE_DIR, so a test that
        # mocks yfinance to return real data can't leak into the repo's own
        # output/ directory.
        self._cache_tmpdir = tempfile.mkdtemp(prefix="stock-research-picks-history-cache-test-")
        self.addCleanup(shutil.rmtree, self._cache_tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._cache_tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)

    def _write_snapshot(self, date: str, picks: list) -> None:
        (Path(self._tmpdir) / f"{date}.json").write_text(json.dumps({"date": date, "picks": picks}))

    def _fake_nifty_ticker(self, closes: dict[str, float]) -> MagicMock:
        idx = pd.to_datetime(list(closes.keys()))
        df = pd.DataFrame({"Close": list(closes.values())}, index=idx)
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = df
        return fake_ticker

    def test_no_history_dir_returns_empty(self) -> None:
        shutil.rmtree(self._tmpdir)
        resp = client.get("/api/market-picks/history")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {
            "symbols": [], "snapshot_count": 0,
            "win_rate": None, "tier_stats": {}, "avg_alpha_pct": None,
            "available_dates": [],
        })

    def test_available_dates_lists_every_snapshot_date(self) -> None:
        self._write_snapshot("2026-07-01", [
            {"symbol": "ABC", "confidence": 60, "mention_count": 1, "current_price": 100.0, "recommendation": "BUY"},
        ])
        self._write_snapshot("2026-07-08", [
            {"symbol": "ABC", "confidence": 70, "mention_count": 2, "current_price": 110.0, "recommendation": "BUY"},
        ])
        with patch("yfinance.Ticker", side_effect=ConnectionError("no network in test")):
            resp = client.get("/api/market-picks/history")
        self.assertEqual(resp.json()["available_dates"], ["2026-07-01", "2026-07-08"])

    def test_date_param_returns_that_days_full_snapshot(self) -> None:
        self._write_snapshot("2026-07-01", [
            {"symbol": "ABC", "confidence": 60, "mention_count": 1, "current_price": 100.0, "recommendation": "BUY"},
            {"symbol": "XYZ", "confidence": 40, "mention_count": 2, "current_price": 50.0, "recommendation": "HOLD"},
        ])
        self._write_snapshot("2026-07-08", [
            {"symbol": "ABC", "confidence": 70, "mention_count": 2, "current_price": 110.0, "recommendation": "BUY"},
        ])
        resp = client.get("/api/market-picks/history?date=2026-07-01")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["date"], "2026-07-01")
        self.assertEqual(len(body["picks"]), 2)
        self.assertEqual({p["symbol"] for p in body["picks"]}, {"ABC", "XYZ"})

    def test_date_param_missing_snapshot_returns_404(self) -> None:
        self._write_snapshot("2026-07-01", [
            {"symbol": "ABC", "confidence": 60, "mention_count": 1, "current_price": 100.0, "recommendation": "BUY"},
        ])
        resp = client.get("/api/market-picks/history?date=2026-07-02")
        self.assertEqual(resp.status_code, 404)

    def test_date_param_malformed_returns_422(self) -> None:
        resp = client.get("/api/market-picks/history?date=not-a-date")
        self.assertEqual(resp.status_code, 422)

    def test_date_param_ignores_aggregation_entirely(self) -> None:
        # A malformed sibling snapshot must not affect a valid ?date= lookup —
        # the date path never touches the aggregation loop at all.
        (Path(self._tmpdir) / "2026-06-01.json").write_text("{not valid json")
        self._write_snapshot("2026-07-01", [
            {"symbol": "ABC", "confidence": 60, "mention_count": 1, "current_price": 100.0, "recommendation": "BUY"},
        ])
        resp = client.get("/api/market-picks/history?date=2026-07-01")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["picks"]), 1)

    def test_snapshot_count_preserved_when_no_symbols_have_picks(self) -> None:
        # Regression: 3 valid daily runs happened, each finding zero picks (or
        # picks missing a symbol) — snapshot_count must still reflect that 3
        # runs occurred, not silently drop to 0 just because by_symbol ended
        # up empty.
        self._write_snapshot("2026-07-01", [])
        self._write_snapshot("2026-07-02", [{"confidence": 50, "mention_count": 1}])  # no symbol
        self._write_snapshot("2026-07-03", [])
        resp = client.get("/api/market-picks/history")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["snapshot_count"], 3)
        self.assertEqual(body["symbols"], [])
        self.assertIsNone(body["win_rate"])

    def test_computes_change_pct_across_snapshots(self) -> None:
        self._write_snapshot("2026-07-01", [
            {"symbol": "ABC", "confidence": 60, "mention_count": 2, "current_price": 100.0, "recommendation": "BUY"},
        ])
        self._write_snapshot("2026-07-08", [
            {"symbol": "ABC", "confidence": 75, "mention_count": 4, "current_price": 110.0, "recommendation": "BUY"},
        ])
        with patch("yfinance.Ticker", side_effect=ConnectionError("no network in test")):
            resp = client.get("/api/market-picks/history")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["snapshot_count"], 2)
        self.assertEqual(len(body["symbols"]), 1)
        row = body["symbols"][0]
        self.assertEqual(row["symbol"], "ABC")
        self.assertEqual(row["first_seen"], "2026-07-01")
        self.assertEqual(row["last_seen"], "2026-07-08")
        self.assertEqual(row["times_picked"], 2)
        self.assertAlmostEqual(row["change_pct"], 10.0, places=2)

    def test_legacy_snapshot_without_price_yields_null_change_pct(self) -> None:
        # Snapshots written before current_price/recommendation were added to the schema.
        self._write_snapshot("2026-06-01", [
            {"symbol": "XYZ", "confidence": 50, "mention_count": 1},
        ])
        with patch("yfinance.Ticker", side_effect=ConnectionError("no network in test")):
            resp = client.get("/api/market-picks/history")
        body = resp.json()
        self.assertEqual(len(body["symbols"]), 1)
        self.assertIsNone(body["symbols"][0]["change_pct"])

    def test_malformed_snapshot_file_is_skipped_not_fatal(self) -> None:
        (Path(self._tmpdir) / "2026-07-01.json").write_text("{not valid json")
        self._write_snapshot("2026-07-02", [
            {"symbol": "ABC", "confidence": 60, "mention_count": 2, "current_price": 100.0, "recommendation": "BUY"},
        ])
        with patch("yfinance.Ticker", side_effect=ConnectionError("no network in test")):
            resp = client.get("/api/market-picks/history")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["snapshot_count"], 1)
        self.assertEqual(len(body["symbols"]), 1)

    def test_win_rate_and_tier_stats_computed_across_symbols(self) -> None:
        self._write_snapshot("2026-07-01", [
            {"symbol": "WIN", "confidence": 60, "mention_count": 1, "current_price": 100.0, "recommendation": "BUY"},
            {"symbol": "LOSE", "confidence": 60, "mention_count": 1, "current_price": 100.0, "recommendation": "BUY"},
            {"symbol": "WATCH", "confidence": 50, "mention_count": 1, "current_price": 200.0, "recommendation": "WATCHLIST"},
        ])
        self._write_snapshot("2026-07-08", [
            {"symbol": "WIN", "confidence": 70, "mention_count": 2, "current_price": 110.0, "recommendation": "BUY"},
            {"symbol": "LOSE", "confidence": 55, "mention_count": 2, "current_price": 90.0, "recommendation": "HOLD"},
            {"symbol": "WATCH", "confidence": 55, "mention_count": 2, "current_price": 210.0, "recommendation": "WATCHLIST"},
        ])
        with patch("yfinance.Ticker", side_effect=ConnectionError("no network in test")):
            resp = client.get("/api/market-picks/history")
        body = resp.json()

        # 2 of 3 symbols (WIN, WATCH) are in profit.
        self.assertAlmostEqual(body["win_rate"], 66.7, places=1)

        self.assertEqual(body["tier_stats"]["BUY"]["count"], 2)
        self.assertAlmostEqual(body["tier_stats"]["BUY"]["avg_change_pct"], 0.0, places=1)  # +10, -10
        self.assertAlmostEqual(body["tier_stats"]["BUY"]["win_rate"], 50.0, places=1)

        self.assertEqual(body["tier_stats"]["WATCHLIST"]["count"], 1)
        self.assertAlmostEqual(body["tier_stats"]["WATCHLIST"]["win_rate"], 100.0, places=1)

    def test_alpha_computed_against_mocked_nifty_history(self) -> None:
        self._write_snapshot("2026-07-01", [
            {"symbol": "ABC", "confidence": 60, "mention_count": 1, "current_price": 100.0, "recommendation": "BUY"},
        ])
        self._write_snapshot("2026-07-08", [
            {"symbol": "ABC", "confidence": 70, "mention_count": 2, "current_price": 110.0, "recommendation": "BUY"},
        ])
        # ABC: +10%. Nifty: 20000 -> 20200 = +1%. Alpha should be +9%.
        fake_ticker = self._fake_nifty_ticker({"2026-07-01": 20000.0, "2026-07-08": 20200.0})
        with patch("yfinance.Ticker", return_value=fake_ticker) as mocked:
            resp = client.get("/api/market-picks/history")
        body = resp.json()
        row = body["symbols"][0]
        self.assertAlmostEqual(row["nifty_change_pct"], 1.0, places=2)
        self.assertAlmostEqual(row["alpha_pct"], 9.0, places=2)
        self.assertAlmostEqual(body["avg_alpha_pct"], 9.0, places=2)
        mocked.assert_called_once_with("^NSEI")

        # A second request within the TTL/range must reuse the cache — no second yfinance call.
        with patch("yfinance.Ticker", side_effect=AssertionError("should not hit yfinance again")):
            resp2 = client.get("/api/market-picks/history")
        self.assertAlmostEqual(resp2.json()["symbols"][0]["alpha_pct"], 9.0, places=2)

    def test_nifty_close_falls_back_to_nearest_prior_trading_day(self) -> None:
        self._write_snapshot("2026-07-04", [  # a Saturday — market closed
            {"symbol": "ABC", "confidence": 60, "mention_count": 1, "current_price": 100.0, "recommendation": "BUY"},
        ])
        self._write_snapshot("2026-07-11", [  # a Saturday — market closed
            {"symbol": "ABC", "confidence": 70, "mention_count": 2, "current_price": 110.0, "recommendation": "BUY"},
        ])
        # Only Friday closes are on record; both Saturdays should fall back to them.
        fake_ticker = self._fake_nifty_ticker({"2026-07-03": 20000.0, "2026-07-10": 20200.0})
        with patch("yfinance.Ticker", return_value=fake_ticker):
            resp = client.get("/api/market-picks/history")
        row = resp.json()["symbols"][0]
        self.assertAlmostEqual(row["nifty_change_pct"], 1.0, places=2)

    def test_yfinance_failure_degrades_to_null_alpha_not_error(self) -> None:
        self._write_snapshot("2026-07-01", [
            {"symbol": "ABC", "confidence": 60, "mention_count": 1, "current_price": 100.0, "recommendation": "BUY"},
        ])
        with patch("yfinance.Ticker", side_effect=ConnectionError("boom")):
            resp = client.get("/api/market-picks/history")
        self.assertEqual(resp.status_code, 200)
        row = resp.json()["symbols"][0]
        self.assertIsNone(row["nifty_change_pct"])
        self.assertIsNone(row["alpha_pct"])
        self.assertIsNone(resp.json()["avg_alpha_pct"])


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

    def test_malformed_symbols_are_filtered_before_reaching_yfinance(self) -> None:
        fake_ticker = MagicMock()
        fake_ticker.fast_info = MagicMock(last_price=100.0, previous_close=90.0)
        with patch("yfinance.Ticker", return_value=fake_ticker) as mocked:
            resp = client.get("/api/prices?symbols=TCS,'; DROP TABLE x;--,../../etc/passwd,")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(list(body["prices"].keys()), ["TCS"])
        called_symbols = {call.args[0].rsplit(".", 1)[0] for call in mocked.call_args_list}
        self.assertEqual(called_symbols, {"TCS"})


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


class PeersEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-peers-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def test_invalid_symbol_returns_422(self) -> None:
        resp = client.get("/api/peers/bad symbol")
        self.assertEqual(resp.status_code, 422)

    def test_rate_limited_returns_429(self) -> None:
        api._RATE_LIMIT_CALLS["peers:testclient"] = [api.time.monotonic()] * 30
        resp = client.get("/api/peers/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_returns_peers_with_percentiles_and_caches(self) -> None:
        raw = json.dumps({
            "symbol": "TCS",
            "self": {"name": "TCS", "slug": "TCS", "values": {"P/E": "28", "ROCE %": "52"}},
            "peers": [
                {"name": "Infosys", "slug": "INFY", "values": {"P/E": "25", "ROCE %": "32"}},
                {"name": "Wipro",   "slug": "WIPRO", "values": {"P/E": "20", "ROCE %": "18"}},
            ],
            "sector_median": {"name": "Median", "slug": "", "values": {"P/E": "22", "ROCE %": "28"}},
        })
        fake_tool = MagicMock()
        fake_tool.run.return_value = raw
        with patch("tools.screener_tools.get_peer_comparison", fake_tool):
            resp = client.get("/api/peers/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "TCS")
        self.assertEqual(len(body["peers"]), 2)
        # Mean-rank percentile over [28, 25, 20]: TCS beats both peers (below=2)
        # and ties only with itself (equal=1) -> (2 + 0.5) / 3 * 100 = 83.3
        self.assertEqual(body["percentiles"]["P/E"], 83.3)
        self.assertEqual(body["percentiles"]["ROCE %"], 83.3)
        fake_tool.run.assert_called_once()

        # second call must be served from cache — the scraper must not run again.
        with patch("tools.screener_tools.get_peer_comparison") as should_not_run:
            resp2 = client.get("/api/peers/TCS")
        self.assertEqual(resp2.status_code, 200)
        should_not_run.run.assert_not_called()

    def test_tool_error_returns_empty_payload_not_500(self) -> None:
        fake_tool = MagicMock()
        fake_tool.run.return_value = json.dumps({"error": "boom", "symbol": "TCS"})
        with patch("tools.screener_tools.get_peer_comparison", fake_tool):
            resp = client.get("/api/peers/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["self"])
        self.assertEqual(body["peers"], [])
        self.assertEqual(body["percentiles"], {})

    def test_no_peers_yields_no_percentiles(self) -> None:
        raw = json.dumps({
            "symbol": "TCS",
            "self": {"name": "TCS", "slug": "TCS", "values": {"P/E": "28"}},
            "peers": [],
            "sector_median": None,
        })
        fake_tool = MagicMock()
        fake_tool.run.return_value = raw
        with patch("tools.screener_tools.get_peer_comparison", fake_tool):
            resp = client.get("/api/peers/TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["percentiles"], {})


class PeerPercentileHelperTest(unittest.TestCase):
    def test_tie_is_split_evenly(self) -> None:
        self_row = {"values": {"P/E": "25"}}
        peers = [{"values": {"P/E": "25"}}, {"values": {"P/E": "20"}}]
        result = api._compute_peer_percentiles(self_row, peers)
        # 3 values total [25, 20, 25]; self beats the 20 (below=1) and ties with
        # both its own value and the peer's 25 (equal=2) -> (1 + 1.0) / 3 * 100 = 66.7
        self.assertEqual(result["P/E"], 66.7)

    def test_column_missing_from_all_peers_is_skipped(self) -> None:
        self_row = {"values": {"P/E": "25", "Debt/Eq": "0.1"}}
        peers = [{"values": {"P/E": "20"}}]
        result = api._compute_peer_percentiles(self_row, peers)
        self.assertIn("P/E", result)
        self.assertNotIn("Debt/Eq", result)

    def test_no_self_row_returns_empty(self) -> None:
        self.assertEqual(api._compute_peer_percentiles(None, [{"values": {"P/E": "20"}}]), {})

    def test_no_peers_returns_empty(self) -> None:
        self.assertEqual(api._compute_peer_percentiles({"values": {"P/E": "20"}}, []), {})


class SmeSignalsEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        api._RATE_LIMIT_CALLS.clear()

    def test_rate_limited_returns_429(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        api._RATE_LIMIT_CALLS["sme_signals:testclient"] = [api.time.monotonic()] * 60
        resp = client.get("/api/sme-signals")
        self.assertEqual(resp.status_code, 429)

    def test_invalid_direction_returns_422_even_without_db(self) -> None:
        resp = client.get("/api/sme-signals?direction=sideways")
        self.assertEqual(resp.status_code, 422)

    def test_missing_database_url_returns_503(self) -> None:
        resp = client.get("/api/sme-signals")
        self.assertEqual(resp.status_code, 503)

    def test_returns_signals_shape_with_mocked_engine(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = _fake_sme_engine(
            rows=[{
                "symbol": "ABC", "cross": "golden",
                "avg_volume_20d": 12000.0, "avg_turnover_20d": 240000.0,
            }],
            total_monitored=120,
            golden_now=42,
            last_run="2026-07-20T00:00:00",
        )
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals?lookback=5&direction=golden")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total_monitored"], 120)
        self.assertEqual(body["golden_now"], 42)
        self.assertEqual(len(body["signals"]), 1)
        self.assertEqual(body["signals"][0]["symbol"], "ABC")
        self.assertEqual(body["signals"][0]["avg_volume_20d"], 12000.0)
        self.assertEqual(body["signals"][0]["avg_turnover_20d"], 240000.0)

    def test_db_error_returns_sanitized_503(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("api._get_db_engine", side_effect=RuntimeError("connection refused: password exposed")):
            resp = client.get("/api/sme-signals")

        self.assertEqual(resp.status_code, 503)
        # The raw exception (which could leak DSN/credentials) must not reach the client.
        self.assertNotIn("password", resp.text)
        self.assertNotIn("exposed", resp.text)

    def test_invalid_view_returns_422_even_without_db(self) -> None:
        resp = client.get("/api/sme-signals?view=nonsense")
        self.assertEqual(resp.status_code, 422)

    def test_golden_hit_rate_computed_from_sample_and_wins(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = _fake_sme_engine(
            rows=[], total_monitored=10, golden_now=2, last_run=None,
            hit_rate={"sample_size": 8, "wins": 5},
        )
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals")

        self.assertEqual(resp.status_code, 200)
        hit_rate = resp.json()["golden_hit_rate_90d"]
        self.assertEqual(hit_rate["sample_size"], 8)
        self.assertAlmostEqual(hit_rate["win_rate"], 62.5, places=1)

    def test_golden_hit_rate_is_null_when_no_resolved_sample(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = _fake_sme_engine(
            rows=[], total_monitored=10, golden_now=2, last_run=None,
            hit_rate={"sample_size": 0, "wins": 0},
        )
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals")

        hit_rate = resp.json()["golden_hit_rate_90d"]
        self.assertEqual(hit_rate["sample_size"], 0)
        self.assertIsNone(hit_rate["win_rate"])

    def test_regime_view_query_omits_cross_type_filter(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        captured_sql: list[str] = []

        class _RecordingConn:
            def __init__(self) -> None:
                results = [MagicMock() for _ in range(5)]
                results[0].mappings.return_value.fetchall.return_value = []
                results[1].scalar.return_value = 0
                results[2].scalar.return_value = 0
                results[3].scalar.return_value = None
                results[4].mappings.return_value.first.return_value = {"sample_size": 0, "wins": 0}
                self._results = results

            def execute(self, stmt, *args, **kwargs):
                captured_sql.append(str(stmt))
                return self._results.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        engine = MagicMock()
        engine.connect.return_value = _RecordingConn()
        with patch("api._get_db_engine", return_value=engine):
            resp = client.get("/api/sme-signals?view=regime")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("DISTINCT ON (s.symbol)", captured_sql[0])
        self.assertNotIn("cross_type IS NOT NULL", captured_sql[0])

    def test_crosses_view_query_keeps_cross_type_filter(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = _fake_sme_engine(rows=[], total_monitored=0, golden_now=0, last_run=None)
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals?view=crosses")
        self.assertEqual(resp.status_code, 200)


class SmeSignalHistoryEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None

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
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals/abc/history")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "ABC")
        self.assertEqual(body["name"], "ABC Corp")
        self.assertEqual(len(body["series"]), 2)
        self.assertEqual(body["series"][1]["cross"], "golden")
        self.assertEqual(len(body["cross_events"]), 1)
        self.assertEqual(body["cross_events"][0]["cross"], "golden")

    def test_unknown_symbol_returns_404(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = self._fake_history_engine([], None)
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals/NOSUCH/history")
        self.assertEqual(resp.status_code, 404)

    def test_db_error_returns_sanitized_503(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("api._get_db_engine", side_effect=RuntimeError("connection refused: password exposed")):
            resp = client.get("/api/sme-signals/ABC/history")
        self.assertEqual(resp.status_code, 503)
        self.assertNotIn("password", resp.text)


def _row(date_str: str, close: float, cross: str | None = None) -> dict:
    return {"trade_date": date_str, "close_price": close, "ema20": None, "ema50": None, "cross": cross}


class ComputeCrossEventsTest(unittest.TestCase):
    def test_no_crosses_returns_empty_list(self) -> None:
        series = [_row("2026-01-01", 10.0), _row("2026-01-02", 10.5)]
        self.assertEqual(api._compute_cross_events(series), [])

    def test_full_window_computes_both_forward_returns(self) -> None:
        # A golden cross at index 0, closing at 100; the +10d row (index 10)
        # closes 110 (+10%), the +20d row (index 20) closes 120 (+20%).
        series = [_row(f"2026-01-{i+1:02d}", 100.0 + i, cross="golden" if i == 0 else None) for i in range(25)]
        events = api._compute_cross_events(series)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["cross"], "golden")
        self.assertEqual(event["close_at_cross"], 100.0)
        self.assertAlmostEqual(event["ret_10d_pct"], 10.0, places=2)
        self.assertAlmostEqual(event["ret_20d_pct"], 20.0, places=2)

    def test_insufficient_elapsed_days_yields_null_returns(self) -> None:
        # Cross is only 5 trading days from the end of the series — neither
        # +10d nor +20d has happened yet within the stored window.
        series = [_row(f"2026-01-{i+1:02d}", 100.0, cross="golden" if i == 0 else None) for i in range(6)]
        event = api._compute_cross_events(series)[0]
        self.assertIsNone(event["ret_10d_pct"])
        self.assertIsNone(event["ret_20d_pct"])

    def test_most_recent_cross_first(self) -> None:
        series = [
            _row("2026-01-01", 100.0, cross="golden"),
            _row("2026-01-02", 101.0),
            _row("2026-01-03", 102.0, cross="death"),
        ]
        events = api._compute_cross_events(series)
        self.assertEqual([e["trade_date"] for e in events], ["2026-01-03", "2026-01-01"])

    def test_zero_close_at_cross_does_not_divide_by_zero(self) -> None:
        series = [_row("2026-01-01", 0.0, cross="golden")] + [_row(f"2026-01-{i+2:02d}", 5.0) for i in range(15)]
        event = api._compute_cross_events(series)[0]
        self.assertIsNone(event["ret_10d_pct"])


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


class WatchlistEndpointsTest(unittest.TestCase):
    """No account system exists — client_id is an opaque frontend-generated
    identifier, not a real user. These tests cover validation, the missing-DB
    fail-safe (same pattern as the SME endpoints), and the read/write/delete
    contract against a mocked engine.
    """

    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        api._RATE_LIMIT_CALLS.clear()

    def test_get_missing_database_url_returns_503(self) -> None:
        resp = client.get("/api/watchlist?client_id=client-abc")
        self.assertEqual(resp.status_code, 503)

    def test_get_invalid_client_id_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.get(f"/api/watchlist?client_id={'x' * 100}")
        self.assertEqual(resp.status_code, 422)

    def test_get_client_id_longer_than_db_column_returns_422(self) -> None:
        # watchlist_items.client_id is VARCHAR(36); a longer id must be rejected
        # at validation time rather than reaching the DB as an insert failure.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.get(f"/api/watchlist?client_id={'x' * 37}")
        self.assertEqual(resp.status_code, 422)

    def test_get_returns_items_with_mocked_engine(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "Tata Consultancy Services", "exchange": "NSE",
             "addedAt": "2026-01-01T00:00:00"},
        ]
        fake_engine = MagicMock()
        fake_engine.connect.return_value = _FakeConn([rows_result])
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/watchlist?client_id=client-abc")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["symbol"], "TCS")

    def test_get_db_error_returns_sanitized_503(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("api._get_db_engine", side_effect=RuntimeError("connection refused: password exposed")):
            resp = client.get("/api/watchlist?client_id=client-abc")
        self.assertEqual(resp.status_code, 503)
        self.assertNotIn("password", resp.json()["detail"])

    def test_get_rate_limited_returns_429(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        api._RATE_LIMIT_CALLS["watchlist_read:testclient"] = [api.time.monotonic()] * 120
        resp = client.get("/api/watchlist?client_id=client-abc")
        self.assertEqual(resp.status_code, 429)

    def test_post_missing_database_url_returns_503(self) -> None:
        resp = client.post("/api/watchlist", json={"client_id": "client-abc", "symbol": "TCS"})
        self.assertEqual(resp.status_code, 503)

    def test_post_invalid_symbol_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post("/api/watchlist", json={"client_id": "client-abc", "symbol": "bad symbol!"})
        self.assertEqual(resp.status_code, 422)

    def test_post_invalid_client_id_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post("/api/watchlist", json={"client_id": "not valid!", "symbol": "TCS"})
        self.assertEqual(resp.status_code, 422)

    def test_post_invalid_exchange_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post("/api/watchlist", json={
            "client_id": "client-abc", "symbol": "TCS", "exchange": "XYZ",
        })
        self.assertEqual(resp.status_code, 422)

    def test_post_lowercase_exchange_is_normalized(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        insert_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "", "exchange": "BSE", "addedAt": "2026-01-01T00:00:00"},
        ]
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([lock_result, count_result, insert_result])
        fake_engine.connect.return_value = _FakeConn([rows_result])

        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.post("/api/watchlist", json={
                "client_id": "client-abc", "symbol": "TCS", "exchange": "bse",
            })
        self.assertEqual(resp.status_code, 200)

    def test_post_adds_item_with_mocked_engine(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        insert_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "Tata Consultancy Services", "exchange": "NSE",
             "addedAt": "2026-01-01T00:00:00"},
        ]
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([lock_result, count_result, insert_result])
        fake_engine.connect.return_value = _FakeConn([rows_result])

        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.post("/api/watchlist", json={
                "client_id": "client-abc", "symbol": "tcs",
                "company": "Tata Consultancy Services", "exchange": "NSE",
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"][0]["symbol"], "TCS")

    def test_post_over_cap_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar.return_value = api._MAX_WATCHLIST_ITEMS_PER_CLIENT
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([lock_result, count_result])
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.post("/api/watchlist", json={"client_id": "client-abc", "symbol": "TCS"})
        self.assertEqual(resp.status_code, 422)

    def test_delete_invalid_symbol_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.delete("/api/watchlist/bad%20symbol?client_id=client-abc")
        self.assertEqual(resp.status_code, 422)

    def test_delete_removes_item_with_mocked_engine(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        delete_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = []
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([delete_result])
        fake_engine.connect.return_value = _FakeConn([rows_result])
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.delete("/api/watchlist/TCS?client_id=client-abc")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"], [])

    def test_delete_missing_database_url_returns_503(self) -> None:
        resp = client.delete("/api/watchlist/TCS?client_id=client-abc")
        self.assertEqual(resp.status_code, 503)


class VerdictHistoryEndpointTest(unittest.TestCase):
    """Read-only aggregation over verdict_history.load_history() — degrades to
    an empty list rather than an error, same philosophy as /api/consolidated."""

    def setUp(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        api._RATE_LIMIT_CALLS.clear()

    def test_invalid_symbol_returns_422(self) -> None:
        resp = client.get("/api/verdict-history/bad symbol")
        self.assertEqual(resp.status_code, 422)

    def test_rate_limited_returns_429(self) -> None:
        api._RATE_LIMIT_CALLS["verdict_history:testclient"] = [api.time.monotonic()] * 60
        resp = client.get("/api/verdict-history/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_returns_empty_history_when_nothing_stored(self) -> None:
        with patch("verdict_history.load_history", return_value=[]):
            resp = client.get("/api/verdict-history/TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"symbol": "TCS", "history": []})

    def test_returns_history_with_mocked_load(self) -> None:
        fake_history = [
            {"date": "2026-07-01", "recommendation": "HOLD", "confidence": "MEDIUM", "current_price": 100.0, "signal_score": 2.0},
            {"date": "2026-07-24", "recommendation": "BUY", "confidence": "HIGH", "current_price": 110.5, "signal_score": 6.0},
        ]
        with patch("verdict_history.load_history", return_value=fake_history):
            resp = client.get("/api/verdict-history/tcs")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "TCS")
        self.assertEqual(len(body["history"]), 2)
        self.assertEqual(body["history"][1]["recommendation"], "BUY")


class ConsolidatedEndpointTest(unittest.TestCase):
    """The consolidated view is pure aggregation of what the three pipelines
    have already cached/computed — no new fetching. Each section is
    independently optional (null when that pipeline hasn't run for this
    symbol, or its own cache has gone stale), and a failure in one section
    (e.g. the SME DB) must not take down the other two.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-consolidated-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)

        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        api._RATE_LIMIT_CALLS.clear()

    def test_invalid_symbol_returns_422(self) -> None:
        resp = client.get("/api/consolidated/bad symbol")
        self.assertEqual(resp.status_code, 422)

    def test_rate_limited_returns_429(self) -> None:
        api._RATE_LIMIT_CALLS["consolidated:testclient"] = [api.time.monotonic()] * 30
        resp = client.get("/api/consolidated/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_all_sections_null_when_nothing_cached(self) -> None:
        with patch("api._load_picks_cache", return_value=None):
            resp = client.get("/api/consolidated/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "TCS")
        self.assertIsNone(body["analysis"])
        self.assertIsNone(body["market_pick"])
        self.assertIsNone(body["sme"])

    def test_returns_cached_analysis(self) -> None:
        cache.save("TCS", "analysis", {
            "recommendation": "BUY", "confidence": "High", "summary": "Strong fundamentals.",
        })
        with patch("api._load_picks_cache", return_value=None):
            resp = client.get("/api/consolidated/TCS")
        self.assertEqual(resp.status_code, 200)
        analysis = resp.json()["analysis"]
        self.assertEqual(analysis["recommendation"], "BUY")
        self.assertEqual(analysis["confidence"], "High")
        self.assertIsNotNone(analysis["as_of"])

    def test_returns_market_pick_when_present(self) -> None:
        fake_cache = {
            "picks": [{"symbol": "TCS", "rank": 3, "recommendation": "WATCHLIST", "confidence_score": 61, "summary": "Steady."}],
            "generated_at": "2026-07-20T00:00:00Z",
        }
        with patch("api._load_picks_cache", return_value=fake_cache):
            resp = client.get("/api/consolidated/TCS")
        self.assertEqual(resp.status_code, 200)
        pick = resp.json()["market_pick"]
        self.assertEqual(pick["rank"], 3)
        self.assertEqual(pick["recommendation"], "WATCHLIST")
        self.assertEqual(pick["generated_at"], "2026-07-20T00:00:00Z")

    def test_returns_none_when_symbol_not_in_market_picks(self) -> None:
        fake_cache = {"picks": [{"symbol": "INFY", "rank": 1}], "generated_at": "2026-07-20T00:00:00Z"}
        with patch("api._load_picks_cache", return_value=fake_cache):
            resp = client.get("/api/consolidated/TCS")
        self.assertIsNone(resp.json()["market_pick"])

    def test_returns_sme_regime_when_present(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        row_result = MagicMock()
        row_result.mappings.return_value.first.return_value = {
            "trade_date": "2026-07-18", "cross": "golden", "in_golden_cross": True,
            "name": "Example SME Ltd", "exchange": "BSE",
        }
        fake_engine = MagicMock()
        fake_engine.connect.return_value = _FakeConn([row_result])
        with patch("api._load_picks_cache", return_value=None), \
             patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/consolidated/EXAMPLE")
        self.assertEqual(resp.status_code, 200)
        sme = resp.json()["sme"]
        self.assertEqual(sme["cross"], "golden")
        self.assertTrue(sme["in_golden_cross"])

    def test_sme_query_failure_returns_none_not_500(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("api._load_picks_cache", return_value=None), \
             patch("api._get_db_engine", side_effect=RuntimeError("connection refused")):
            resp = client.get("/api/consolidated/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["sme"])
        # the other two sections must still resolve normally despite the SME failure
        self.assertIn("analysis", body)
        self.assertIn("market_pick", body)

    def test_sme_absent_when_database_url_unset(self) -> None:
        with patch("api._load_picks_cache", return_value=None):
            resp = client.get("/api/consolidated/TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["sme"])


class AuthRequestLinkEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._RATE_LIMIT_CALLS.clear()

    def test_missing_database_url_returns_503(self) -> None:
        resp = client.post("/api/auth/request-link", json={"email": "user@example.com"})
        self.assertEqual(resp.status_code, 503)

    def test_invalid_email_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post("/api/auth/request-link", json={"email": "not-an-email"})
        self.assertEqual(resp.status_code, 422)

    def test_overlong_email_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post("/api/auth/request-link", json={"email": f"{'a' * 320}@example.com"})
        self.assertEqual(resp.status_code, 422)

    def test_valid_email_creates_link_and_sends_email(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("auth.create_magic_link", return_value="raw-token") as create_link, \
             patch("email_sender.send_magic_link_email", return_value=True) as send_email:
            resp = client.post("/api/auth/request-link", json={"email": "User@Example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"sent": True})
        create_link.assert_called_once_with("user@example.com")
        send_email.assert_called_once()
        sent_args = send_email.call_args[0]
        self.assertEqual(sent_args[0], "user@example.com")
        self.assertIn("raw-token", sent_args[1])

    def test_returns_sent_true_even_when_smtp_delivery_fails(self) -> None:
        # Doesn't leak SMTP configuration state to the caller — the link was
        # still created either way.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("auth.create_magic_link", return_value="raw-token"), \
             patch("email_sender.send_magic_link_email", return_value=False):
            resp = client.post("/api/auth/request-link", json={"email": "user@example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"sent": True})

    def test_db_error_returns_sanitized_503(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("auth.create_magic_link", side_effect=RuntimeError("connection refused: password exposed")):
            resp = client.post("/api/auth/request-link", json={"email": "user@example.com"})
        self.assertEqual(resp.status_code, 503)
        self.assertNotIn("password", resp.json()["detail"])

    def test_rate_limited_returns_429(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        api._RATE_LIMIT_CALLS["auth_request_link:testclient"] = [api.time.monotonic()] * 5
        resp = client.post("/api/auth/request-link", json={"email": "user@example.com"})
        self.assertEqual(resp.status_code, 429)

    def test_email_rate_limited_returns_429_even_from_a_fresh_ip(self) -> None:
        # Per-IP limiting alone doesn't stop an attacker with rotating IPs
        # from email-bombing one victim's inbox — the target address itself
        # must also be capped, independent of caller IP.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        api._RATE_LIMIT_CALLS["auth_request_link_email:victim@example.com"] = [api.time.monotonic()] * 5
        resp = client.post("/api/auth/request-link", json={"email": "victim@example.com"})
        self.assertEqual(resp.status_code, 429)

    def test_email_rate_limit_is_scoped_per_address(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        api._RATE_LIMIT_CALLS["auth_request_link_email:someone-else@example.com"] = [api.time.monotonic()] * 5
        with patch("auth.create_magic_link", return_value="raw-token"), \
             patch("email_sender.send_magic_link_email", return_value=True):
            resp = client.post("/api/auth/request-link", json={"email": "user@example.com"})
        self.assertEqual(resp.status_code, 200)


class AuthVerifyEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._RATE_LIMIT_CALLS.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._RATE_LIMIT_CALLS.clear()

    def test_missing_database_url_returns_503(self) -> None:
        resp = client.get("/api/auth/verify?token=abc")
        self.assertEqual(resp.status_code, 503)

    def test_invalid_token_returns_401(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("auth.verify_magic_link", return_value=None):
            resp = client.get("/api/auth/verify?token=bad")
        self.assertEqual(resp.status_code, 401)

    def test_valid_token_returns_user_and_session(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("auth.verify_magic_link", return_value={"id": 1, "email": "user@example.com"}), \
             patch("auth.create_session", return_value="session-token"):
            resp = client.get("/api/auth/verify?token=good")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["user"], {"id": 1, "email": "user@example.com"})
        self.assertEqual(body["session_token"], "session-token")

    def test_db_error_returns_sanitized_503(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("auth.verify_magic_link", side_effect=RuntimeError("connection refused: password exposed")):
            resp = client.get("/api/auth/verify?token=good")
        self.assertEqual(resp.status_code, 503)
        self.assertNotIn("password", resp.json()["detail"])

    def test_rate_limited_returns_429(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        api._RATE_LIMIT_CALLS["auth_verify:testclient"] = [api.time.monotonic()] * 20
        resp = client.get("/api/auth/verify?token=x")
        self.assertEqual(resp.status_code, 429)


class AuthMeEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url

    def test_missing_authorization_header_returns_401(self) -> None:
        resp = client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 401)

    def test_missing_database_url_returns_401(self) -> None:
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 401)

    def test_invalid_session_returns_401(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("auth.get_user_for_session", return_value=None):
            resp = client.get("/api/auth/me", headers={"Authorization": "Bearer badtoken"})
        self.assertEqual(resp.status_code, 401)

    def test_valid_session_returns_user(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("auth.get_user_for_session", return_value={"id": 1, "email": "user@example.com"}):
            resp = client.get("/api/auth/me", headers={"Authorization": "Bearer goodtoken"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"user": {"id": 1, "email": "user@example.com"}})

    def test_malformed_authorization_header_returns_401(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.get("/api/auth/me", headers={"Authorization": "Basic sometoken"})
        self.assertEqual(resp.status_code, 401)


class AuthLogoutEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url

    def test_returns_ok_without_authorization_header(self) -> None:
        resp = client.post("/api/auth/logout")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})

    def test_deletes_session_when_authorized(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("auth.delete_session") as delete_session:
            resp = client.post("/api/auth/logout", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 200)
        delete_session.assert_called_once_with("sometoken")

    def test_returns_ok_even_without_database_url(self) -> None:
        resp = client.post("/api/auth/logout", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
