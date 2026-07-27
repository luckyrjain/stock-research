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
import rate_limiter

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


class StartupConfigWarningsTest(unittest.TestCase):
    """A handful of misconfigurations (no LLM provider key, a non-default
    ALLOWED_ORIGINS with no TRUSTED_PROXY_SECRET) previously degraded with
    zero log signal — see api._log_startup_config_warnings's own docstring
    for the deep gap analysis finding this closes."""

    def test_warns_when_no_llm_provider_is_configured(self) -> None:
        with patch("crew._configured_providers", return_value=[]), \
             patch("api.log_event") as mock_log:
            api._log_startup_config_warnings()
        events = [c.args[1] for c in mock_log.call_args_list]
        self.assertIn("startup_no_llm_provider_configured", events)

    def test_no_warning_when_a_provider_is_configured(self) -> None:
        with patch("crew._configured_providers", return_value=["anthropic"]), \
             patch("api.log_event") as mock_log:
            api._log_startup_config_warnings()
        events = [c.args[1] for c in mock_log.call_args_list]
        self.assertNotIn("startup_no_llm_provider_configured", events)

    def test_warns_when_non_default_origin_has_no_proxy_secret(self) -> None:
        with patch("crew._configured_providers", return_value=["anthropic"]), \
             patch("api._ALLOWED_ORIGINS", ["https://alphapulse.example.com"]), \
             patch("api._TRUSTED_PROXY_SECRET", None), \
             patch("api.log_event") as mock_log:
            api._log_startup_config_warnings()
        events = [c.args[1] for c in mock_log.call_args_list]
        self.assertIn("startup_trusted_proxy_secret_unset", events)

    def test_no_warning_when_proxy_secret_is_set(self) -> None:
        with patch("crew._configured_providers", return_value=["anthropic"]), \
             patch("api._ALLOWED_ORIGINS", ["https://alphapulse.example.com"]), \
             patch("api._TRUSTED_PROXY_SECRET", "shared-secret"), \
             patch("api.log_event") as mock_log:
            api._log_startup_config_warnings()
        events = [c.args[1] for c in mock_log.call_args_list]
        self.assertNotIn("startup_trusted_proxy_secret_unset", events)

    def test_no_warning_for_default_localhost_origin_even_without_secret(self) -> None:
        # The default single-host local-dev setup never needed the secret —
        # only flag deployments that look non-default.
        with patch("crew._configured_providers", return_value=["anthropic"]), \
             patch("api._ALLOWED_ORIGINS", ["http://localhost:3000"]), \
             patch("api._TRUSTED_PROXY_SECRET", None), \
             patch("api.log_event") as mock_log:
            api._log_startup_config_warnings()
        events = [c.args[1] for c in mock_log.call_args_list]
        self.assertNotIn("startup_trusted_proxy_secret_unset", events)


class LlmConcurrencyCeilingTest(unittest.TestCase):
    def setUp(self) -> None:
        rate_limiter._memory_slots.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_slots.clear()

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
        self.assertEqual(rate_limiter._memory_slots.get("llm_concurrency", 0), 0)


class RateLimitHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        rate_limiter._memory_calls.clear()

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


class ClientIpTrustedProxyTest(unittest.TestCase):
    # Every browser request reaches this backend via the Next.js proxy
    # routes, server-to-server — request.client.host is always the Next.js
    # server's own IP, never the real visitor's, which would otherwise
    # collapse every per-IP rate limiter into one shared bucket for the
    # whole site. _client_ip() only trusts X-Forwarded-For when the caller
    # also presents the matching X-Internal-Proxy-Secret — these tests cover
    # both the trust and (more importantly) the distrust paths, since an
    # untrusted caller must not be able to spoof its way around its own
    # rate limit or frame another IP into being blocked.
    def _make_request(self, client_host: str, headers: dict) -> MagicMock:
        req = MagicMock()
        req.client.host = client_host
        req.headers.get.side_effect = lambda key, default=None: headers.get(key.lower(), default)
        return req

    def test_no_secret_configured_ignores_forwarded_for(self) -> None:
        req = self._make_request("10.0.0.5", {"x-forwarded-for": "203.0.113.5"})
        with patch("api._TRUSTED_PROXY_SECRET", None):
            self.assertEqual(api._client_ip(req), "10.0.0.5")

    def test_matching_secret_trusts_forwarded_for(self) -> None:
        req = self._make_request("10.0.0.5", {
            "x-forwarded-for": "203.0.113.5",
            "x-internal-proxy-secret": "s3cr3t",
        })
        with patch("api._TRUSTED_PROXY_SECRET", "s3cr3t"):
            self.assertEqual(api._client_ip(req), "203.0.113.5")

    def test_mismatched_secret_falls_back_to_client_host(self) -> None:
        req = self._make_request("10.0.0.5", {
            "x-forwarded-for": "203.0.113.5",
            "x-internal-proxy-secret": "wrong-value",
        })
        with patch("api._TRUSTED_PROXY_SECRET", "s3cr3t"):
            self.assertEqual(api._client_ip(req), "10.0.0.5")

    def test_trusted_caller_with_multi_value_chain_falls_back(self) -> None:
        # A single-hop reverse proxy in "replace" mode (the only configuration
        # this feature documents/supports — see docs/deployment.md) always
        # produces exactly one IP here. More than one usually means an
        # "append" mode misconfiguration (e.g. nginx's
        # $proxy_add_x_forwarded_for) that lets a caller-supplied
        # X-Forwarded-For survive alongside the real one — and since a caller
        # can set this header directly on a request to the reverse proxy, the
        # leftmost entry in that case would be the attacker's own claimed
        # value, not the one the proxy actually observed. Refuse to trust an
        # ambiguous chain rather than blindly take the first entry.
        req = self._make_request("10.0.0.5", {
            "x-forwarded-for": "203.0.113.5, 10.0.0.1",
            "x-internal-proxy-secret": "s3cr3t",
        })
        with patch("api._TRUSTED_PROXY_SECRET", "s3cr3t"):
            self.assertEqual(api._client_ip(req), "10.0.0.5")

    def test_trusted_caller_with_blank_forwarded_header_falls_back(self) -> None:
        req = self._make_request("10.0.0.5", {
            "x-forwarded-for": ", 203.0.113.5",
            "x-internal-proxy-secret": "s3cr3t",
        })
        with patch("api._TRUSTED_PROXY_SECRET", "s3cr3t"):
            self.assertEqual(api._client_ip(req), "10.0.0.5")

    def test_trusted_caller_without_forwarded_header_falls_back(self) -> None:
        req = self._make_request("10.0.0.5", {"x-internal-proxy-secret": "s3cr3t"})
        with patch("api._TRUSTED_PROXY_SECRET", "s3cr3t"):
            self.assertEqual(api._client_ip(req), "10.0.0.5")

    def test_rate_limit_buckets_by_trusted_forwarded_ip_not_proxy_ip(self) -> None:
        # The end-to-end payoff of the above: two different real visitors
        # behind the same Next.js proxy (same client.host) must get
        # independent rate-limit buckets when their forwarded IPs differ.
        rate_limiter._memory_calls.clear()
        req_a = self._make_request("10.0.0.5", {
            "x-forwarded-for": "203.0.113.1",
            "x-internal-proxy-secret": "s3cr3t",
        })
        req_b = self._make_request("10.0.0.5", {
            "x-forwarded-for": "203.0.113.2",
            "x-internal-proxy-secret": "s3cr3t",
        })
        with patch("api._TRUSTED_PROXY_SECRET", "s3cr3t"), patch("api.time.monotonic", return_value=500.0):
            api._rate_limit(req_a, "bucket_trusted", max_calls=1, window_seconds=60)
            # Same proxy IP, different real visitor — must not be blocked.
            api._rate_limit(req_b, "bucket_trusted", max_calls=1, window_seconds=60)
            with self.assertRaises(Exception):
                api._rate_limit(req_a, "bucket_trusted", max_calls=1, window_seconds=60)


class ValidateSymbolEndpointTest(unittest.TestCase):
    """Covers validate_symbol's branch-heavy paths: ISIN resolution (CSV hit and
    yfinance fallback), the BSE-forced path, and the NSE/BSE/Screener fallback chain.
    All network-touching helpers are mocked at the api.* boundary.
    """

    def test_overlong_symbol_returns_422(self) -> None:
        # This endpoint deliberately accepts more input shapes than
        # _TICKER_RE (ISINs, numeric BSE codes, hyphenated Screener slugs),
        # so it can't apply that same regex — but it previously had no
        # length bound at all before passing the value to yfinance/
        # Screener/NSE lookups below.
        resp = client.get(f"/api/validate/{'A' * 41}")
        self.assertEqual(resp.status_code, 422)

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
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()

    def test_429_when_over_limit_without_running_pipeline(self) -> None:
        # Pre-seed the bucket at the max so the request is rejected before any
        # of the (unmocked, network-touching) analysis pipeline ever runs.
        rate_limiter._memory_calls["analyse:testclient"] = [api.time.monotonic()] * 20
        resp = client.get("/api/analyse/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_invalid_symbol_returns_422(self) -> None:
        # Regression test: this endpoint used to skip the _TICKER_RE check
        # every sibling symbol-taking endpoint enforces — a ".." symbol could
        # reach cache.save("..", "news"/"price_history", ...) and write
        # outside the intended output/ tree. %2e%2e (not a literal "..") is
        # used so the test client's own URL-resolution logic doesn't collapse
        # the dot-segment before the request is even sent — Starlette's
        # router percent-decodes it back to ".." for the symbol param, same
        # as a real request through a non-normalizing intermediary would.
        resp = client.get("/api/analyse/%2e%2e")
        self.assertEqual(resp.status_code, 422)


class MarketPicksForceRateLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        rate_limiter._memory_calls.clear()
        rate_limiter._memory_locks.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()
        rate_limiter._memory_locks.clear()

    def test_429_when_force_rescan_over_limit(self) -> None:
        rate_limiter._memory_calls["market_picks_force:testclient"] = [api.time.monotonic()] * 3
        resp = client.get("/api/market-picks?force=true")
        self.assertEqual(resp.status_code, 429)
        # 429 must not have left the lock claimed — same "409 takes priority
        # over 429" ordering as the SME/screener refresh endpoints, and the
        # lock is acquired first, so a rejected request must release it.
        self.assertFalse(rate_limiter.is_locked("market_picks_refresh"))

    def test_already_running_returns_409(self) -> None:
        # Regression test: /api/market-picks?force=true previously had no
        # single-run lock at all, unlike /api/sme-signals/refresh and
        # /api/screener/refresh — two overlapping full pipeline runs
        # (the weekly cron's HTTP trigger racing a user's "Fresh scan"
        # click) could both proceed, doubling real LLM/scraping cost on
        # the most expensive pipeline in the app.
        rate_limiter._memory_locks["market_picks_refresh"] = True
        resp = client.get("/api/market-picks?force=true")
        self.assertEqual(resp.status_code, 409)


class RemainingEndpointsRateLimitTest(unittest.TestCase):
    """/api/prices, /api/validate, and the two history endpoints previously had
    no rate limit at all — /api/prices was the worst offender (up to 50
    yfinance calls per request, at unbounded request rate).
    """

    def setUp(self) -> None:
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()

    def test_429_on_prices_when_over_limit(self) -> None:
        rate_limiter._memory_calls["prices:testclient"] = [api.time.monotonic()] * 30
        resp = client.get("/api/prices?symbols=TCS")
        self.assertEqual(resp.status_code, 429)

    def test_429_on_validate_when_over_limit(self) -> None:
        rate_limiter._memory_calls["validate:testclient"] = [api.time.monotonic()] * 30
        resp = client.get("/api/validate/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_429_on_prices_history_when_over_limit(self) -> None:
        rate_limiter._memory_calls["prices_history:testclient"] = [api.time.monotonic()] * 60
        resp = client.get("/api/prices/history/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_429_on_market_picks_history_when_over_limit(self) -> None:
        rate_limiter._memory_calls["market_picks_history:testclient"] = [api.time.monotonic()] * 60
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
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()

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
        rate_limiter._memory_calls["market_picks_status:testclient"] = [api.time.monotonic()] * 60
        resp = client.get("/api/market-picks/status")
        self.assertEqual(resp.status_code, 429)


class FetchNiftyClosesCacheSharingTest(unittest.TestCase):
    """_fetch_nifty_closes() is shared by two independent callers with very
    different range shapes: the picks-history alpha stat (a range spanning
    the whole snapshot archive, only ever growing) and the per-stock Nifty
    benchmark endpoint (a ~180-day trailing window that differs per stock).
    A naive exact-range cache key means these two essentially never agree on
    a range and evict each other's entry on every single call — these tests
    lock in the coverage-based fix (cached range must merely *contain* the
    requested one, not match it) so that regression can't come back silently."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-nifty-cache-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)

    def _fake_ticker(self, closes: dict[str, float]) -> MagicMock:
        idx = pd.to_datetime(list(closes.keys()))
        df = pd.DataFrame({"Close": list(closes.values())}, index=idx)
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = df
        return fake_ticker

    def test_narrower_request_inside_cached_range_is_served_from_cache(self) -> None:
        wide = {f"2026-01-{d:02d}": 20000.0 + d for d in range(1, 29)}
        with patch("yfinance.Ticker", return_value=self._fake_ticker(wide)):
            api._fetch_nifty_closes("2026-01-01", "2026-01-28")

        # A different caller's narrower, non-identical range is fully
        # contained by what's already cached — must NOT hit yfinance again.
        with patch("yfinance.Ticker", side_effect=AssertionError("should be served from cache")):
            result = api._fetch_nifty_closes("2026-01-10", "2026-01-20")
        self.assertEqual(result["2026-01-10"], wide["2026-01-10"])
        self.assertEqual(len(result), 28)

    def test_uncovered_range_triggers_union_refetch_not_narrow_refetch(self) -> None:
        first = {f"2026-01-{d:02d}": 20000.0 + d for d in range(10, 21)}
        with patch("yfinance.Ticker", return_value=self._fake_ticker(first)):
            api._fetch_nifty_closes("2026-01-10", "2026-01-20")

        # A second caller's range only partially overlaps — extends earlier.
        # The re-fetch must cover the UNION (2026-01-01..2026-01-20), not
        # just the newly requested slice, so future requests for the middle
        # of the old range stay servable from cache too.
        second = {f"2026-01-{d:02d}": 20000.0 + d for d in range(1, 21)}
        fake_ticker = self._fake_ticker(second)
        with patch("yfinance.Ticker", return_value=fake_ticker):
            result = api._fetch_nifty_closes("2026-01-01", "2026-01-15")
        call_kwargs = fake_ticker.history.call_args.kwargs
        self.assertEqual(call_kwargs["start"], "2026-01-01")
        self.assertEqual(call_kwargs["end"], "2026-01-21")  # end-exclusive, one day past 2026-01-20
        self.assertEqual(len(result), 20)

    def test_yfinance_failure_returns_empty_dict(self) -> None:
        with patch("yfinance.Ticker", side_effect=RuntimeError("outage")):
            result = api._fetch_nifty_closes("2026-01-01", "2026-01-10")
        self.assertEqual(result, {})

    def test_old_shape_cache_entry_without_start_end_keys_does_not_crash(self) -> None:
        # A cache file written before the coverage-based scheme existed (the
        # old {"range": "...", "closes": {...}} shape) must not KeyError on
        # its first read post-deploy — it should just be treated as
        # non-covering and trigger a normal fresh fetch.
        import cache as cache_module

        cache_module.save("NSEI", "index_history", {"range": "2025-01-01:2025-01-10", "closes": {"2025-01-01": 100.0}})
        fresh = {f"2026-01-{d:02d}": 20000.0 + d for d in range(1, 11)}
        with patch("yfinance.Ticker", return_value=self._fake_ticker(fresh)):
            result = api._fetch_nifty_closes("2026-01-01", "2026-01-10")
        self.assertEqual(len(result), 10)
        self.assertEqual(result["2026-01-01"], fresh["2026-01-01"])


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

    def test_missing_previous_close_yields_null_change_pct_not_zero(self) -> None:
        # Regression test: a genuine flat day and "previous_close wasn't
        # available" are different facts — the latter must not be
        # fabricated as a confident 0.0% change.
        fast_info = MagicMock()
        fast_info.last_price = 100.0
        fast_info.previous_close = None
        fake_ticker = MagicMock()
        fake_ticker.fast_info = fast_info

        with patch("yfinance.Ticker", return_value=fake_ticker):
            resp = client.get("/api/prices?symbols=TCS")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["prices"]["TCS"]["price"], 100.0)
        self.assertIsNone(body["prices"]["TCS"]["change_pct"])

    def test_ns_suffix_exception_still_tries_bo_suffix(self) -> None:
        # Regression test: a genuine BSE-only symbol (never listed on NSE,
        # or delisted from it) can make the .NS attempt raise outright
        # rather than just return empty data — that must not prevent the
        # .BO attempt from running and finding a real price.
        def _ticker(sym: str):
            m = MagicMock()
            if sym.endswith(".NS"):
                raise ConnectionError("boom")
            m.fast_info = MagicMock(last_price=50.0, previous_close=45.0)
            return m

        with patch("yfinance.Ticker", side_effect=_ticker):
            resp = client.get("/api/prices?symbols=SMALLCAP")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["prices"]["SMALLCAP"]["price"], 50.0)

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

    def test_invalid_symbol_returns_422(self) -> None:
        # Regression test: this endpoint used to pass the raw path segment
        # straight to get_price_series() with no _TICKER_RE check, unlike
        # every sibling symbol-taking endpoint — a ".." symbol could reach
        # cache.save(sym, ...) and write outside the intended output/ tree.
        # %2e%2e (not a literal "..") is used so the test client's own
        # URL-resolution logic doesn't collapse the dot-segment before the
        # request is even sent — see AnalyseEndpointRateLimitTest's identical
        # test for the full rationale.
        resp = client.get("/api/prices/history/%2e%2e")
        self.assertEqual(resp.status_code, 422)

    def test_benchmark_omitted_by_default(self) -> None:
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = self._fake_history_df(10)
        with patch("yfinance.Ticker", return_value=fake_ticker):
            resp = client.get("/api/prices/history/TCS?days=30")
        self.assertEqual(resp.status_code, 200)
        # Opt-in: no "benchmark" key at all (not even null) unless requested —
        # most callers of this endpoint (e.g. quarterly-trend sparklines) aren't
        # plotting a price series, so a Nifty comparison would be meaningless.
        self.assertNotIn("benchmark", resp.json())

    def test_benchmark_true_computes_relative_performance(self) -> None:
        stock_df = self._fake_history_df(10)  # closes: 100 .. 109, a +9% move
        nifty_df = pd.DataFrame(
            {"Close": [20000.0 + i * 20 for i in range(10)]},  # 20000 -> 20180, a +0.9% move
            index=pd.date_range("2026-01-01", periods=10, freq="D"),
        )

        def _ticker_side_effect(sym: str):
            m = MagicMock()
            m.history.return_value = nifty_df if sym == "^NSEI" else stock_df
            return m

        with patch("yfinance.Ticker", side_effect=_ticker_side_effect):
            resp = client.get("/api/prices/history/TCS?days=30&benchmark=true")
        self.assertEqual(resp.status_code, 200)
        bench = resp.json()["benchmark"]
        self.assertIsNotNone(bench)
        self.assertAlmostEqual(bench["stock_change_pct"], 9.0, places=1)
        self.assertAlmostEqual(bench["nifty_change_pct"], 0.9, places=1)
        self.assertAlmostEqual(bench["alpha_pct"], bench["stock_change_pct"] - bench["nifty_change_pct"], places=2)

    def test_benchmark_null_when_nifty_fetch_fails(self) -> None:
        stock_df = self._fake_history_df(10)

        def _ticker_side_effect(sym: str):
            if sym == "^NSEI":
                raise RuntimeError("yfinance outage")
            m = MagicMock()
            m.history.return_value = stock_df
            return m

        with patch("yfinance.Ticker", side_effect=_ticker_side_effect):
            resp = client.get("/api/prices/history/TCS?days=30&benchmark=true")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["benchmark"])

    def test_benchmark_null_when_series_too_short(self) -> None:
        empty_ticker = MagicMock()
        empty_ticker.history.return_value = pd.DataFrame()
        with patch("yfinance.Ticker", return_value=empty_ticker):
            resp = client.get("/api/prices/history/NOSUCHSYMBOL?benchmark=true")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["benchmark"])


class PeersEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-peers-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()

    def test_invalid_symbol_returns_422(self) -> None:
        resp = client.get("/api/peers/bad symbol")
        self.assertEqual(resp.status_code, 422)

    def test_rate_limited_returns_429(self) -> None:
        rate_limiter._memory_calls["peers:testclient"] = [api.time.monotonic()] * 30
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
        with patch("tools.screener_tools.get_peer_comparison", fake_tool), \
             patch("scraper_error_counters.record_scraper_error") as mock_record:
            resp = client.get("/api/peers/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["self"])
        self.assertEqual(body["peers"], [])
        self.assertEqual(body["percentiles"], {})
        # A real scrape failure must be counted — this is the one thing
        # that tells "Screener.in broke" apart from "this symbol has no
        # peer data", since both otherwise return the exact same shape.
        mock_record.assert_called_once_with("peers", symbol="TCS")

    def test_a_real_result_never_touches_the_error_counter(self) -> None:
        # The flip side of the test above — don't manufacture noise from
        # the expected common case (a real, successful scrape).
        raw = json.dumps({
            "symbol": "TCS", "self": {"name": "TCS", "slug": "TCS", "values": {}},
            "peers": [], "sector_median": None,
        })
        fake_tool = MagicMock()
        fake_tool.run.return_value = raw
        with patch("tools.screener_tools.get_peer_comparison", fake_tool), \
             patch("scraper_error_counters.record_scraper_error") as mock_record:
            resp = client.get("/api/peers/TCS")
        self.assertEqual(resp.status_code, 200)
        mock_record.assert_not_called()

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

    def test_includes_absolute_anchor_when_valuation_band_present(self) -> None:
        raw = json.dumps({
            "symbol": "TCS",
            "self": {"name": "TCS", "slug": "TCS", "values": {"P/E": "24"}},
            "peers": [],
            "sector_median": None,
            "valuation_band": {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [20.0, 22.0, 26.0]},
        })
        fake_tool = MagicMock()
        fake_tool.run.return_value = raw
        with patch("tools.screener_tools.get_peer_comparison", fake_tool):
            resp = client.get("/api/peers/TCS")
        self.assertEqual(resp.status_code, 200)
        anchor = resp.json()["absolute_anchor"]
        self.assertEqual(anchor["current_pe"], 24.0)
        self.assertEqual(anchor["low"], 20.0)
        self.assertEqual(anchor["high"], 26.0)
        self.assertEqual(anchor["median"], 22.0)
        # 24 beats [20, 22] and ties with nothing among [20, 22, 26] -> 2/3 * 100 = 66.7
        self.assertEqual(anchor["percentile"], 66.7)

    def test_absolute_anchor_null_when_valuation_band_absent(self) -> None:
        raw = json.dumps({
            "symbol": "TCS",
            "self": {"name": "TCS", "slug": "TCS", "values": {"P/E": "24"}},
            "peers": [],
            "sector_median": None,
        })
        fake_tool = MagicMock()
        fake_tool.run.return_value = raw
        with patch("tools.screener_tools.get_peer_comparison", fake_tool):
            resp = client.get("/api/peers/TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["absolute_anchor"])

    def test_absolute_anchor_null_on_tool_error(self) -> None:
        fake_tool = MagicMock()
        fake_tool.run.return_value = json.dumps({"error": "boom", "symbol": "TCS"})
        with patch("tools.screener_tools.get_peer_comparison", fake_tool):
            resp = client.get("/api/peers/TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["absolute_anchor"])

    def test_stale_cache_missing_absolute_anchor_key_backfills_null(self) -> None:
        # A response cached before this field existed has no "absolute_anchor"
        # key at all (not an explicit null) — the endpoint must still return a
        # consistent shape rather than silently omitting the key.
        cache.save("TCS", "peers", {
            "symbol": "TCS",
            "self": {"name": "TCS", "slug": "TCS", "values": {"P/E": "28"}},
            "peers": [],
            "sector_median": None,
            "percentiles": {},
        })
        with patch("tools.screener_tools.get_peer_comparison") as should_not_run:
            resp = client.get("/api/peers/TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("absolute_anchor", resp.json())
        self.assertIsNone(resp.json()["absolute_anchor"])
        should_not_run.run.assert_not_called()


class FinancialsEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-financials-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()

    def test_invalid_symbol_returns_422(self) -> None:
        resp = client.get("/api/financials/bad symbol")
        self.assertEqual(resp.status_code, 422)

    def test_rate_limited_returns_429(self) -> None:
        rate_limiter._memory_calls["financials:testclient"] = [api.time.monotonic()] * 30
        resp = client.get("/api/financials/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_returns_statements_and_caches(self) -> None:
        raw = json.dumps({
            "symbol": "TCS",
            "profit_loss": {"years": ["Mar 2023", "Mar 2024"], "rows": [{"label": "Sales", "values": [100.0, 120.0]}]},
            "balance_sheet": {"years": ["Mar 2023", "Mar 2024"], "rows": [{"label": "Total Assets", "values": [500.0, 550.0]}]},
            "cash_flow": {"years": ["Mar 2023", "Mar 2024"], "rows": [{"label": "Cash from Operating Activity", "values": [80.0, 90.0]}]},
            "concalls": [{"date": "Jul 2026", "transcript_url": "https://www.screener.in/concall/t.pdf"}],
        })
        fake_tool = MagicMock()
        fake_tool.run.return_value = raw
        with patch("tools.screener_tools.get_financial_statements", fake_tool):
            resp = client.get("/api/financials/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "TCS")
        self.assertEqual(body["profit_loss"]["rows"][0]["label"], "Sales")
        self.assertEqual(body["cash_flow"]["rows"][0]["label"], "Cash from Operating Activity")
        self.assertEqual(body["concalls"], [{"date": "Jul 2026", "transcript_url": "https://www.screener.in/concall/t.pdf"}])
        fake_tool.run.assert_called_once()

        # Second call must be served from cache — the scraper must not run again.
        with patch("tools.screener_tools.get_financial_statements") as should_not_run:
            resp2 = client.get("/api/financials/TCS")
        self.assertEqual(resp2.status_code, 200)
        should_not_run.run.assert_not_called()

    def test_tool_error_returns_all_null_payload_not_500(self) -> None:
        fake_tool = MagicMock()
        fake_tool.run.return_value = json.dumps({"error": "boom", "symbol": "TCS"})
        with patch("tools.screener_tools.get_financial_statements", fake_tool), \
             patch("scraper_error_counters.record_scraper_error") as mock_record:
            resp = client.get("/api/financials/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["profit_loss"])
        self.assertIsNone(body["balance_sheet"])
        self.assertIsNone(body["cash_flow"])
        self.assertIsNone(body["dcf"])
        self.assertEqual(body["concalls"], [])
        mock_record.assert_called_once_with("financials", symbol="TCS")

    def test_a_real_result_never_touches_the_error_counter(self) -> None:
        succeeding_tool = MagicMock()
        succeeding_tool.run.return_value = json.dumps({"symbol": "TCS"})
        with patch("tools.screener_tools.get_financial_statements", succeeding_tool), \
             patch("scraper_error_counters.record_scraper_error") as mock_record:
            resp = client.get("/api/financials/TCS")
        self.assertEqual(resp.status_code, 200)
        mock_record.assert_not_called()

    def test_cache_entry_missing_newer_fields_is_backfilled_not_omitted(self) -> None:
        # Regression test for an adversarial-review finding: unlike GET
        # /api/peers/{symbol}'s own cached-hit path (which backfills
        # `absolute_anchor: None` for a response cached before that field
        # existed), this endpoint's cached-hit path used to just strip
        # `_meta` and return the cached dict verbatim. A cache entry written
        # before `dcf`/`concalls` existed (or before any future field is
        # added) would then be served with those keys entirely ABSENT for
        # up to the remaining 24h TTL, rather than the documented null/[]
        # shape every fresh response guarantees.
        cache.save("TCS", "financials", {
            "symbol": "TCS",
            "profit_loss": {"years": ["Mar 2024"], "rows": [{"label": "Sales", "values": [100.0]}]},
            # Deliberately missing "balance_sheet", "cash_flow", "dcf", and
            # "concalls" -- simulating a payload cached before those fields
            # existed on this endpoint.
        })
        with patch("tools.screener_tools.get_financial_statements") as should_not_run:
            resp = client.get("/api/financials/TCS")
        should_not_run.run.assert_not_called()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["profit_loss"]["rows"][0]["label"], "Sales")
        self.assertIn("balance_sheet", body)
        self.assertIsNone(body["balance_sheet"])
        self.assertIn("cash_flow", body)
        self.assertIsNone(body["cash_flow"])
        self.assertIn("dcf", body)
        self.assertIsNone(body["dcf"])
        self.assertIn("concalls", body)
        self.assertEqual(body["concalls"], [])

    def test_tool_error_is_not_cached_so_next_request_retries(self) -> None:
        # Same convention as GET /api/peers/{symbol}: a transient scrape
        # failure must not get locked in as "no financials" for the full
        # 24h TTL — the very next request should hit the scraper again.
        failing_tool = MagicMock()
        failing_tool.run.return_value = json.dumps({"error": "boom", "symbol": "TCS"})
        with patch("tools.screener_tools.get_financial_statements", failing_tool):
            client.get("/api/financials/TCS")
        failing_tool.run.assert_called_once()

        succeeding_tool = MagicMock()
        succeeding_tool.run.return_value = json.dumps({
            "symbol": "TCS",
            "profit_loss": {"years": ["Mar 2024"], "rows": [{"label": "Sales", "values": [100.0]}]},
        })
        with patch("tools.screener_tools.get_financial_statements", succeeding_tool):
            resp = client.get("/api/financials/TCS")
        succeeding_tool.run.assert_called_once()
        self.assertIsNotNone(resp.json()["profit_loss"])

    def test_dcf_computed_when_stock_info_cached(self) -> None:
        cache.save("TCS", "stock_info", {"current_price": 500.0, "market_cap_cr": 50000.0})
        raw = json.dumps({
            "symbol": "TCS",
            "cash_flow": {
                "years": ["2020", "2021", "2022", "2023"],
                "rows": [{"label": "Cash from Operating Activity", "values": [100.0, 140.0, 190.0, 240.0]}],
            },
        })
        fake_tool = MagicMock()
        fake_tool.run.return_value = raw
        with patch("tools.screener_tools.get_financial_statements", fake_tool):
            resp = client.get("/api/financials/TCS")
        self.assertEqual(resp.status_code, 200)
        dcf = resp.json()["dcf"]
        self.assertIsNotNone(dcf)
        self.assertIn(dcf["verdict"], {"Undervalued", "Overvalued", "Fair"})

    def test_dcf_null_without_stock_info_cached(self) -> None:
        raw = json.dumps({
            "symbol": "TCS",
            "cash_flow": {
                "years": ["2020", "2021", "2022", "2023"],
                "rows": [{"label": "Cash from Operating Activity", "values": [100.0, 140.0, 190.0, 240.0]}],
            },
        })
        fake_tool = MagicMock()
        fake_tool.run.return_value = raw
        with patch("tools.screener_tools.get_financial_statements", fake_tool):
            resp = client.get("/api/financials/TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["dcf"])


class ShareholdingDetailEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-shareholding-detail-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()

    def test_invalid_symbol_returns_422(self) -> None:
        resp = client.get("/api/shareholding-detail/bad symbol")
        self.assertEqual(resp.status_code, 422)

    def test_rate_limited_returns_429(self) -> None:
        rate_limiter._memory_calls["shareholding_detail:testclient"] = [api.time.monotonic()] * 30
        resp = client.get("/api/shareholding-detail/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_returns_promoters_and_categories_and_caches(self) -> None:
        raw = json.dumps({
            "symbol": "TCS",
            "as_of_date": "2026-06-30",
            "promoters": [{"name": "Tata Sons Private Limited", "holding_pct": 71.77}],
            "shareholder_categories": [
                {"category": "Mutual Funds", "holders": [{"name": "SBI Nifty 50 ETF", "holding_pct": 1.25}]},
            ],
        })
        fake_tool = MagicMock()
        fake_tool.run.return_value = raw
        with patch("tools.nse_tools.get_shareholding_detail", fake_tool):
            resp = client.get("/api/shareholding-detail/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "TCS")
        self.assertFalse(body["unavailable"])
        self.assertEqual(body["promoters"][0]["name"], "Tata Sons Private Limited")
        self.assertEqual(body["shareholder_categories"][0]["category"], "Mutual Funds")
        fake_tool.run.assert_called_once()

        # Second call must be served from cache — the scraper must not run again.
        with patch("tools.nse_tools.get_shareholding_detail") as should_not_run:
            resp2 = client.get("/api/shareholding-detail/TCS")
        self.assertEqual(resp2.status_code, 200)
        self.assertFalse(resp2.json()["unavailable"])
        should_not_run.run.assert_not_called()

    def test_tool_error_returns_unavailable_flag_not_500(self) -> None:
        fake_tool = MagicMock()
        fake_tool.run.return_value = json.dumps({"error": "boom", "symbol": "TCS"})
        with patch("tools.nse_tools.get_shareholding_detail", fake_tool), \
             patch("scraper_error_counters.record_scraper_error") as mock_record:
            resp = client.get("/api/shareholding-detail/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["unavailable"])
        self.assertEqual(body["promoters"], [])
        self.assertEqual(body["shareholder_categories"], [])
        mock_record.assert_called_once_with("shareholding_detail", symbol="TCS")

    def test_a_real_result_never_touches_the_error_counter(self) -> None:
        succeeding_tool = MagicMock()
        succeeding_tool.run.return_value = json.dumps({"symbol": "TCS", "promoters": [], "shareholder_categories": []})
        with patch("tools.nse_tools.get_shareholding_detail", succeeding_tool), \
             patch("scraper_error_counters.record_scraper_error") as mock_record:
            resp = client.get("/api/shareholding-detail/TCS")
        self.assertEqual(resp.status_code, 200)
        mock_record.assert_not_called()

    def test_tool_error_is_not_cached_so_next_request_retries(self) -> None:
        failing_tool = MagicMock()
        failing_tool.run.return_value = json.dumps({"error": "boom", "symbol": "TCS"})
        with patch("tools.nse_tools.get_shareholding_detail", failing_tool):
            client.get("/api/shareholding-detail/TCS")
        failing_tool.run.assert_called_once()

        succeeding_tool = MagicMock()
        succeeding_tool.run.return_value = json.dumps({
            "symbol": "TCS", "promoters": [{"name": "Promoter X", "holding_pct": 50.0}], "shareholder_categories": [],
        })
        with patch("tools.nse_tools.get_shareholding_detail", succeeding_tool):
            resp = client.get("/api/shareholding-detail/TCS")
        succeeding_tool.run.assert_called_once()
        self.assertEqual(resp.json()["promoters"][0]["name"], "Promoter X")

    def test_cached_entry_predating_unavailable_flag_backfills_false(self) -> None:
        # A response cached before `unavailable` existed must still read
        # back as unavailable: false, not an absent/undefined key.
        cache.save("TCS", "shareholding_detail", {
            "symbol": "TCS", "as_of_date": "2026-01-01", "promoters": [], "shareholder_categories": [],
        })
        resp = client.get("/api/shareholding-detail/TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["unavailable"])


class InsiderActivityEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-insider-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()

    def test_invalid_symbol_returns_422(self) -> None:
        resp = client.get("/api/insider-activity/bad symbol")
        self.assertEqual(resp.status_code, 422)

    def test_rate_limited_returns_429(self) -> None:
        rate_limiter._memory_calls["insider_activity:testclient"] = [api.time.monotonic()] * 30
        resp = client.get("/api/insider-activity/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_combines_and_caches_both_sources(self) -> None:
        fake_insider = {"symbol": "TCS", "trades": [{"person": "R Kumar", "action": "BUY"}]}
        fake_bulk = {"symbol": "TCS", "deals": [{"client": "Big Fund", "action": "SELL"}]}
        with patch("tools.nse_insider_trades.fetch_insider_trades_for_symbol", return_value=fake_insider) as insider_fn, \
             patch("tools.nse_bulk_block_deals.fetch_bulk_block_deals_for_symbol", return_value=fake_bulk) as bulk_fn:
            resp = client.get("/api/insider-activity/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "TCS")
        self.assertEqual(body["insider_trades"], fake_insider["trades"])
        self.assertEqual(body["bulk_block_deals"], fake_bulk["deals"])
        insider_fn.assert_called_once_with("TCS")
        bulk_fn.assert_called_once_with("TCS")

        # Second call must be served from cache — neither scraper reruns.
        with patch("tools.nse_insider_trades.fetch_insider_trades_for_symbol") as should_not_run_insider, \
             patch("tools.nse_bulk_block_deals.fetch_bulk_block_deals_for_symbol") as should_not_run_bulk:
            resp2 = client.get("/api/insider-activity/TCS")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json(), body)
        should_not_run_insider.assert_not_called()
        should_not_run_bulk.assert_not_called()

    def test_both_empty_returns_empty_lists_not_error(self) -> None:
        with patch("tools.nse_insider_trades.fetch_insider_trades_for_symbol",
                   return_value={"symbol": "TCS", "trades": []}), \
             patch("tools.nse_bulk_block_deals.fetch_bulk_block_deals_for_symbol",
                   return_value={"symbol": "TCS", "deals": []}):
            resp = client.get("/api/insider-activity/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["insider_trades"], [])
        self.assertEqual(body["bulk_block_deals"], [])
        # A legitimately-empty day is NOT "unavailable" — see the
        # unavailable-vs-empty regression test below for the failure case
        # this distinction exists to fix.
        self.assertFalse(body["insider_trades_unavailable"])
        self.assertFalse(body["bulk_block_deals_unavailable"])

    def test_one_source_failing_does_not_take_down_the_other(self) -> None:
        # Both underlying tool functions are documented to never raise, but
        # this endpoint has its own defensive try/except around each — a
        # future violation of that contract in one source must not 500 the
        # whole response when the other source is fine.
        fake_bulk = {"symbol": "TCS", "deals": [{"client": "Big Fund", "action": "SELL"}]}
        with patch("tools.nse_insider_trades.fetch_insider_trades_for_symbol",
                   side_effect=RuntimeError("NSE schema drifted")), \
             patch("tools.nse_bulk_block_deals.fetch_bulk_block_deals_for_symbol", return_value=fake_bulk):
            resp = client.get("/api/insider-activity/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["insider_trades"], [])
        self.assertEqual(body["bulk_block_deals"], fake_bulk["deals"])

    def test_unavailable_flag_distinguishes_a_real_failure_from_a_quiet_day(self) -> None:
        # Regression test for the deep gap analysis finding: a genuine NSE
        # failure and "no insider trades today" previously both collapsed
        # to the same empty [] with nothing in the response telling them
        # apart. insider_trades_unavailable must be True on a real failure.
        with patch("tools.nse_insider_trades.fetch_insider_trades_for_symbol",
                   return_value={"symbol": "TCS", "error": "NSE request failed"}), \
             patch("tools.nse_bulk_block_deals.fetch_bulk_block_deals_for_symbol",
                   return_value={"symbol": "TCS", "deals": []}), \
             patch("scraper_error_counters.record_scraper_error") as mock_record:
            resp = client.get("/api/insider-activity/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["insider_trades"], [])
        self.assertTrue(body["insider_trades_unavailable"])
        self.assertFalse(body["bulk_block_deals_unavailable"])
        # Only the section that genuinely failed is counted — a real,
        # empty bulk_block_deals result must not also register as an error.
        mock_record.assert_called_once_with("insider_trades", symbol="TCS")

        # A genuine failure must not be cached as a confident "no activity"
        # answer for the full 24h TTL — retried on the next request instead.
        self.assertIsNone(cache.load("TCS", "insider_activity"))

    def test_bulk_block_deals_error_is_counted_independently(self) -> None:
        with patch("tools.nse_insider_trades.fetch_insider_trades_for_symbol",
                   return_value={"symbol": "TCS", "trades": []}), \
             patch("tools.nse_bulk_block_deals.fetch_bulk_block_deals_for_symbol",
                   return_value={"symbol": "TCS", "error": "NSE request failed"}), \
             patch("scraper_error_counters.record_scraper_error") as mock_record:
            resp = client.get("/api/insider-activity/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["insider_trades_unavailable"])
        self.assertTrue(body["bulk_block_deals_unavailable"])
        mock_record.assert_called_once_with("bulk_block_deals", symbol="TCS")


class StreetConsensusEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-street-consensus-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()

    def test_invalid_symbol_returns_422(self) -> None:
        resp = client.get("/api/street-consensus/bad symbol")
        self.assertEqual(resp.status_code, 422)

    def test_rate_limited_returns_429(self) -> None:
        rate_limiter._memory_calls["street_consensus:testclient"] = [api.time.monotonic()] * 30
        resp = client.get("/api/street-consensus/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_fetches_and_caches(self) -> None:
        fake_articles = {"symbol": "TCS", "articles": [{"title": "TCS gets Trendlyne buy upgrade", "url": "https://x/a"}]}
        fake_numeric = {
            "symbol": "TCS", "analyst_count": 30, "consensus_rating": "BUY",
            "mean_target_price": 4500.0, "target_upside_pct": 10.5, "source_url": "https://trendlyne.com/equity/1/TCS/tcs/",
        }
        with patch("tools.trendlyne_agent.fetch_trendlyne_consensus_for_symbol", return_value=fake_articles) as fn_articles, \
             patch("tools.trendlyne_scraper.fetch_trendlyne_numeric_consensus", return_value=fake_numeric) as fn_numeric:
            resp = client.get("/api/street-consensus/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "TCS")
        self.assertEqual(body["articles"], fake_articles["articles"])
        self.assertEqual(body["numeric_consensus"], fake_numeric)
        fn_articles.assert_called_once_with("TCS")
        fn_numeric.assert_called_once_with("TCS")

        # Second call must be served from cache — neither scraper reruns.
        with patch("tools.trendlyne_agent.fetch_trendlyne_consensus_for_symbol") as should_not_run_articles, \
             patch("tools.trendlyne_scraper.fetch_trendlyne_numeric_consensus") as should_not_run_numeric:
            resp2 = client.get("/api/street-consensus/TCS")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json(), body)
        should_not_run_articles.assert_not_called()
        should_not_run_numeric.assert_not_called()

    def test_no_articles_returns_empty_list_not_error(self) -> None:
        with patch("tools.trendlyne_agent.fetch_trendlyne_consensus_for_symbol",
                   return_value={"symbol": "TCS", "articles": []}), \
             patch("tools.trendlyne_scraper.fetch_trendlyne_numeric_consensus",
                   return_value={"symbol": "TCS", "analyst_count": None, "consensus_rating": None,
                                 "mean_target_price": None, "target_upside_pct": None, "source_url": None}):
            resp = client.get("/api/street-consensus/TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["articles"], [])

    def test_numeric_consensus_fetch_failure_does_not_break_articles(self) -> None:
        # Isolated sub-fetch, same convention as insider_activity's two
        # independent sources — a numeric-scrape exception must not take
        # down the whole endpoint.
        fake_articles = {"symbol": "TCS", "articles": [{"title": "x", "url": "https://x/a"}]}
        with patch("tools.trendlyne_agent.fetch_trendlyne_consensus_for_symbol", return_value=fake_articles), \
             patch("tools.trendlyne_scraper.fetch_trendlyne_numeric_consensus", side_effect=RuntimeError("boom")):
            resp = client.get("/api/street-consensus/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["articles"], fake_articles["articles"])
        self.assertIsNone(body["numeric_consensus"])

    def test_articles_fetch_failure_does_not_break_numeric_consensus(self) -> None:
        # Symmetric with test_numeric_consensus_fetch_failure_does_not_break_articles
        # above — an articles-fetch exception must not take down a
        # successful numeric_consensus result via asyncio.gather's
        # first-exception-wins behavior.
        fake_numeric = {
            "symbol": "TCS", "analyst_count": 10, "consensus_rating": "BUY",
            "mean_target_price": 100.0, "target_upside_pct": 5.0,
            "source_url": "https://trendlyne.com/equity/1/TCS/tcs/",
        }
        with patch("tools.trendlyne_agent.fetch_trendlyne_consensus_for_symbol", side_effect=RuntimeError("boom")), \
             patch("tools.trendlyne_scraper.fetch_trendlyne_numeric_consensus", return_value=fake_numeric), \
             patch("scraper_error_counters.record_scraper_error") as mock_record:
            resp = client.get("/api/street-consensus/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["articles"], [])
        self.assertEqual(body["numeric_consensus"], fake_numeric)
        self.assertTrue(body["articles_unavailable"])
        self.assertFalse(body["numeric_consensus_unavailable"])
        # A raised exception (not a returned {"error": ...} dict) still
        # counts — the except clause around _fetch_articles records it too.
        mock_record.assert_not_called()  # exception path logs via log_event, not the counter — see _fetch_articles

    def test_numeric_consensus_error_key_is_not_leaked_into_response_or_cache(self) -> None:
        # Regression test: fetch_trendlyne_numeric_consensus's own internal
        # failure path adds an "error" key carrying a raw exception string
        # to its result dict. cache._is_failed_payload() only inspects a
        # top-level "error" key, not one nested inside numeric_consensus,
        # so without api.py stripping it first this would both leak
        # internal exception text to callers and get cached for 24h.
        fake_articles = {"symbol": "TCS", "articles": []}
        fake_numeric_with_error = {
            "symbol": "TCS", "analyst_count": None, "consensus_rating": None,
            "mean_target_price": None, "target_upside_pct": None, "source_url": None,
            "error": "connection refused: internal-host-detail",
        }
        with patch("tools.trendlyne_agent.fetch_trendlyne_consensus_for_symbol", return_value=fake_articles), \
             patch("tools.trendlyne_scraper.fetch_trendlyne_numeric_consensus", return_value=fake_numeric_with_error), \
             patch("scraper_error_counters.record_scraper_error") as mock_record:
            resp = client.get("/api/street-consensus/TCS")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertNotIn("error", body["numeric_consensus"])
        self.assertNotIn("internal-host-detail", resp.text)
        self.assertTrue(body["numeric_consensus_unavailable"])
        mock_record.assert_called_once_with("trendlyne_numeric_consensus", symbol="TCS")

        # A genuine scrape failure must not be cached as if it were a real
        # "no consensus data" answer — see the "only cache on full success"
        # comment in get_street_consensus. Retried on the next request
        # instead of locking a failure in for the full 24h TTL.
        self.assertIsNone(cache.load("TCS", "street_consensus"))


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


class ValuationAnchorHelperTest(unittest.TestCase):
    def test_computes_band_and_percentile(self) -> None:
        self_row = {"values": {"P/E": "24"}}
        band = {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [20.0, 22.0, 26.0]}
        result = api._compute_valuation_anchor(self_row, band)
        self.assertEqual(result["current_pe"], 24.0)
        self.assertEqual(result["low"], 20.0)
        self.assertEqual(result["high"], 26.0)
        self.assertEqual(result["median"], 22.0)
        self.assertEqual(result["percentile"], 66.7)

    def test_no_self_row_returns_none(self) -> None:
        band = {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [20.0, 22.0, 26.0]}
        self.assertIsNone(api._compute_valuation_anchor(None, band))

    def test_no_valuation_band_returns_none(self) -> None:
        self_row = {"values": {"P/E": "24"}}
        self.assertIsNone(api._compute_valuation_anchor(self_row, {}))

    def test_fewer_than_three_years_returns_none(self) -> None:
        self_row = {"values": {"P/E": "24"}}
        band = {"years": ["Mar 2023", "Mar 2024"], "pe": [22.0, 26.0]}
        self.assertIsNone(api._compute_valuation_anchor(self_row, band))

    def test_no_pe_column_in_self_row_returns_none(self) -> None:
        self_row = {"values": {"ROCE %": "52"}}
        band = {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [20.0, 22.0, 26.0]}
        self.assertIsNone(api._compute_valuation_anchor(self_row, band))

    def test_unparseable_pe_value_returns_none(self) -> None:
        # Column is present but its value doesn't parse as a number (e.g.
        # Screener rendered "-" for a loss-making quarter) — distinct code
        # path from the column being absent entirely (above).
        self_row = {"values": {"P/E": "-"}}
        band = {"years": ["Mar 2022", "Mar 2023", "Mar 2024"], "pe": [20.0, 22.0, 26.0]}
        self.assertIsNone(api._compute_valuation_anchor(self_row, band))


class SmeSignalsEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def test_rate_limited_returns_429(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rate_limiter._memory_calls["sme_signals:testclient"] = [api.time.monotonic()] * 60
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

    def test_bse_row_isin_flows_through_to_response(self) -> None:
        # A BSE row's own scrip code isn't a directly analyzable ticker (see
        # sme-signals/page.tsx) — the frontend deep-links via isin instead, so
        # it must survive the SELECT -> response round trip untouched.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = _fake_sme_engine(
            rows=[{"symbol": "543212", "exchange": "BSE", "cross": "golden", "isin": "INE123A01011"}],
            total_monitored=1, golden_now=1, last_run=None,
        )
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"][0]["isin"], "INE123A01011")

    def test_nse_row_isin_is_null(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = _fake_sme_engine(
            rows=[{"symbol": "ABC", "exchange": "NSE", "cross": "golden", "isin": None}],
            total_monitored=1, golden_now=1, last_run=None,
        )
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals")

        self.assertIsNone(resp.json()["signals"][0]["isin"])

    def test_bse_row_isin_flows_through_to_regime_view_response(self) -> None:
        # test_regime_view_query_omits_cross_type_filter only checks the SQL
        # text contains "s.isin" — that alone wouldn't catch a wrong alias
        # (e.g. "s.isin AS isin_code") changing the actual response key.
        # Assert the real end-to-end shape for the regime path specifically.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = _fake_sme_engine(
            rows=[{"symbol": "543212", "exchange": "BSE", "cross": None, "isin": "INE123A01011"}],
            total_monitored=1, golden_now=1, last_run=None,
        )
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals?view=regime")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"][0]["isin"], "INE123A01011")

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
        self.assertIn("s.isin", captured_sql[0])

    def test_crosses_view_query_keeps_cross_type_filter(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = _fake_sme_engine(rows=[], total_monitored=0, golden_now=0, last_run=None)
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/sme-signals?view=crosses")
        self.assertEqual(resp.status_code, 200)

    def test_crosses_view_query_selects_isin(self) -> None:
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
            resp = client.get("/api/sme-signals?view=crosses")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("s.isin", captured_sql[0])


class SmeSignalHistoryEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def test_rate_limited_returns_429(self) -> None:
        # Previously unrate-limited despite being a fully anonymous,
        # unbounded DB query — see the deep gap analysis this fix closes.
        rate_limiter._memory_calls["sme_signal_history:testclient"] = [api.time.monotonic()] * 60
        resp = client.get("/api/sme-signals/ABC/history")
        self.assertEqual(resp.status_code, 429)

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
        rate_limiter._memory_locks.clear()
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        rate_limiter._memory_locks.clear()
        rate_limiter._memory_calls.clear()

    def test_missing_database_url_returns_503(self) -> None:
        resp = client.post("/api/sme-signals/refresh")
        self.assertEqual(resp.status_code, 503)

    def test_already_refreshing_returns_409(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rate_limiter._memory_locks["sme_refresh"] = True
        resp = client.post("/api/sme-signals/refresh")
        self.assertEqual(resp.status_code, 409)

    def test_rate_limited_returns_429_before_starting_pipeline(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rate_limiter._memory_calls["sme_refresh:testclient"] = [api.time.monotonic()] * 3
        resp = client.post("/api/sme-signals/refresh")
        self.assertEqual(resp.status_code, 429)
        # Must not have left the lock claimed or launched anything.
        self.assertFalse(rate_limiter.is_locked("sme_refresh"))

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
        self.assertTrue(rate_limiter.is_locked("sme_refresh"))
        mocked_create_task.assert_called_once()


def _fake_screener_engine(rows, total, total_monitored, industries, last_run):
    rows_result = MagicMock()
    rows_result.mappings.return_value.fetchall.return_value = rows
    total_result = MagicMock()
    total_result.scalar.return_value = total
    total_monitored_result = MagicMock()
    total_monitored_result.scalar.return_value = total_monitored
    industries_result = MagicMock()
    industries_result.scalars.return_value.all.return_value = industries
    last_run_result = MagicMock()
    last_run_result.scalar.return_value = last_run

    conn = _FakeConn([rows_result, total_result, total_monitored_result, industries_result, last_run_result])
    engine = MagicMock()
    engine.connect.return_value = conn
    return engine


class ScreenerEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def test_rate_limited_returns_429(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rate_limiter._memory_calls["screener:testclient"] = [api.time.monotonic()] * 60
        resp = client.get("/api/screener")
        self.assertEqual(resp.status_code, 429)

    def test_missing_database_url_returns_503(self) -> None:
        resp = client.get("/api/screener")
        self.assertEqual(resp.status_code, 503)

    def test_invalid_ema_trend_returns_422_even_without_db(self) -> None:
        resp = client.get("/api/screener?ema_trend=sideways")
        self.assertEqual(resp.status_code, 422)

    def test_invalid_sort_column_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.get("/api/screener?sort=company_name")
        self.assertEqual(resp.status_code, 422)

    def test_invalid_order_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.get("/api/screener?order=sideways")
        self.assertEqual(resp.status_code, 422)

    def test_returns_stocks_shape_with_mocked_engine(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = _fake_screener_engine(
            rows=[{"symbol": "TCS", "pe_ratio": 28.5, "ema_trend": "bullish"}],
            total=1, total_monitored=500,
            industries=["Information Technology", "Banking"],
            last_run="2026-07-20T00:00:00",
        )
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/screener")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["total_monitored"], 500)
        self.assertEqual(body["industries"], ["Information Technology", "Banking"])
        self.assertEqual(body["stocks"][0]["symbol"], "TCS")
        self.assertFalse(body["refreshing"])

    def test_db_error_returns_sanitized_503(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("api._get_db_engine", side_effect=RuntimeError("connection refused: password exposed")):
            resp = client.get("/api/screener")
        self.assertEqual(resp.status_code, 503)
        self.assertNotIn("password", resp.text)
        self.assertNotIn("exposed", resp.text)

    def test_refreshing_flag_reflects_lock_state(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        fake_engine = _fake_screener_engine(rows=[], total=0, total_monitored=0, industries=[], last_run=None)
        rate_limiter._memory_locks["screener_refresh"] = True
        try:
            with patch("api._get_db_engine", return_value=fake_engine):
                resp = client.get("/api/screener")
            self.assertTrue(resp.json()["refreshing"])
        finally:
            rate_limiter._memory_locks.pop("screener_refresh", None)


class ScreenerRefreshEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        rate_limiter._memory_locks.clear()
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        rate_limiter._memory_locks.clear()
        rate_limiter._memory_calls.clear()

    def test_missing_database_url_returns_503(self) -> None:
        resp = client.post("/api/screener/refresh")
        self.assertEqual(resp.status_code, 503)

    def test_already_refreshing_returns_409(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rate_limiter._memory_locks["screener_refresh"] = True
        resp = client.post("/api/screener/refresh")
        self.assertEqual(resp.status_code, 409)

    def test_rate_limited_returns_429_before_starting_pipeline(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rate_limiter._memory_calls["screener_refresh:testclient"] = [api.time.monotonic()] * 3
        resp = client.post("/api/screener/refresh")
        self.assertEqual(resp.status_code, 429)
        self.assertFalse(rate_limiter.is_locked("screener_refresh"))

    def test_accepted_when_under_limit_and_not_already_running(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"

        def _close_and_stub(coro):
            coro.close()
            return MagicMock()

        with patch("api.asyncio.create_task", side_effect=_close_and_stub) as mocked_create_task:
            resp = client.post("/api/screener/refresh")
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(rate_limiter.is_locked("screener_refresh"))
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
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

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
        rate_limiter._memory_calls["watchlist_read:testclient"] = [api.time.monotonic()] * 120
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
        existing_result = MagicMock()
        existing_result.first.return_value = None
        insert_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "", "exchange": "BSE", "addedAt": "2026-01-01T00:00:00"},
        ]
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([lock_result, count_result, existing_result, insert_result])
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
        existing_result = MagicMock()
        existing_result.first.return_value = None
        insert_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "Tata Consultancy Services", "exchange": "NSE",
             "addedAt": "2026-01-01T00:00:00"},
        ]
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([lock_result, count_result, existing_result, insert_result])
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
        existing_result = MagicMock()
        existing_result.first.return_value = None
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([lock_result, count_result, existing_result])
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.post("/api/watchlist", json={"client_id": "client-abc", "symbol": "TCS"})
        self.assertEqual(resp.status_code, 422)

    def test_post_over_cap_but_symbol_already_starred_is_allowed(self) -> None:
        # Regression test: a re-add of a symbol the owner already has
        # (double-click, retry after a flaky response, frontend re-sync) is
        # a harmless ON CONFLICT ... DO NOTHING no-op — it must not be
        # rejected just because the owner happens to already be at cap,
        # same exemption routes/positions.py's own identical cap check has.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar.return_value = api._MAX_WATCHLIST_ITEMS_PER_CLIENT
        existing_result = MagicMock()
        existing_result.first.return_value = (1,)
        insert_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "Tata Consultancy Services", "exchange": "NSE",
             "addedAt": "2026-01-01T00:00:00"},
        ]
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([lock_result, count_result, existing_result, insert_result])
        fake_engine.connect.return_value = _FakeConn([rows_result])
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.post("/api/watchlist", json={"client_id": "client-abc", "symbol": "TCS"})
        self.assertEqual(resp.status_code, 200)

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


class WatchlistCalendarEndpointTest(unittest.TestCase):
    """Read-aggregation over each symbol's already-cached `filings` (no
    DATABASE_URL needed for that half) plus, independently, a same-day
    recommendation-change / price-move flag via
    verdict_history.detect_recent_changes() (degrades to both None without
    DATABASE_URL, same as every other verdict_history-backed read)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-watchlist-calendar-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()

    def test_no_symbols_returns_empty_entries(self) -> None:
        resp = client.get("/api/watchlist/calendar?symbols=")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["entries"], [])

    def test_symbol_with_no_cached_filings_contributes_nothing(self) -> None:
        resp = client.get("/api/watchlist/calendar?symbols=TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["entries"], [])

    def test_symbol_with_classifiable_filing_is_included(self) -> None:
        cache.save("TCS", "filings", {"symbol": "TCS", "filings": [
            {
                "title": "Board Meeting Intimation for considering financial results",
                "desc": "The Board will meet on 15-08-2026 to consider financial results.",
                "date": "2026-07-20",
                "category": "Board Meeting",
                "attachment": None,
            },
        ]})
        resp = client.get("/api/watchlist/calendar?symbols=TCS")
        self.assertEqual(resp.status_code, 200)
        entries = resp.json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["symbol"], "TCS")
        # No DATABASE_URL configured in this test — the verdict-history half
        # degrades to null, not an error, and doesn't block the filings half.
        self.assertIsNone(entries[0]["recommendation_change"])
        self.assertIsNone(entries[0]["price_move"])

    def test_symbol_with_notable_price_move_is_included_without_filings(self) -> None:
        move = {"old_price": 100.0, "new_price": 115.0, "change_pct": 15.0}
        with patch("verdict_history.detect_recent_changes", return_value={"recommendation_change": None, "price_move": move}):
            resp = client.get("/api/watchlist/calendar?symbols=TCS")
        self.assertEqual(resp.status_code, 200)
        entries = resp.json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["price_move"], move)
        self.assertEqual(entries[0]["next_results_date"], None)
        self.assertEqual(entries[0]["corporate_actions"], [])

    def test_symbol_with_recommendation_change_is_included_without_filings(self) -> None:
        change = {"old_recommendation": "HOLD", "new_recommendation": "SELL", "confidence": "HIGH"}
        with patch("verdict_history.detect_recent_changes", return_value={"recommendation_change": change, "price_move": None}):
            resp = client.get("/api/watchlist/calendar?symbols=TCS")
        self.assertEqual(resp.status_code, 200)
        entries = resp.json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["recommendation_change"], change)

    def test_notable_change_sorts_before_filings_only_entries(self) -> None:
        cache.save("AAA", "filings", {"symbol": "AAA", "filings": [
            {"title": "Board Meeting Intimation for considering financial results",
             "desc": "The Board will meet on 15-08-2026 to consider financial results.",
             "date": "2026-07-20", "category": "Board Meeting", "attachment": None},
        ]})
        move = {"old_price": 100.0, "new_price": 90.0, "change_pct": -10.0}

        def fake_changes(sym, *_a, **_kw):
            return {"recommendation_change": None, "price_move": move if sym == "ZZZ" else None}

        with patch("verdict_history.detect_recent_changes", side_effect=fake_changes):
            resp = client.get("/api/watchlist/calendar?symbols=AAA,ZZZ")
        entries = resp.json()["entries"]
        self.assertEqual([e["symbol"] for e in entries], ["ZZZ", "AAA"])

    def test_symbol_with_only_routine_filings_contributes_nothing(self) -> None:
        cache.save("TCS", "filings", {"symbol": "TCS", "filings": [
            {"title": "Newspaper publication", "desc": "", "date": "2026-07-20", "category": "Other", "attachment": None},
        ]})
        resp = client.get("/api/watchlist/calendar?symbols=TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["entries"], [])

    def test_invalid_symbols_are_filtered_out(self) -> None:
        resp = client.get("/api/watchlist/calendar?symbols=bad symbol!,TCS")
        self.assertEqual(resp.status_code, 200)  # invalid entries dropped, not a 422 — best-effort endpoint

    def test_rate_limited_returns_429(self) -> None:
        rate_limiter._memory_calls["watchlist_calendar:testclient"] = [api.time.monotonic()] * 30
        resp = client.get("/api/watchlist/calendar?symbols=TCS")
        self.assertEqual(resp.status_code, 429)


class _SqlRecordingConn:
    """Fake SQLAlchemy connection: returns queued results in call order and
    records the SQL text + bound params of every execute() call."""

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.queries: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None, *_args, **_kwargs):
        self.queries.append((str(stmt), params or {}))
        return self._results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class WatchlistAccountLinkingTest(unittest.TestCase):
    """A valid session always wins over client_id — see api.py's
    _resolve_watchlist_owner. These tests cover the account-backed identity
    path added alongside the pre-existing anonymous client_id one above."""

    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def test_get_with_valid_session_queries_by_user_id_ignoring_client_id(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "Tata Consultancy Services", "exchange": "NSE",
             "addedAt": "2026-01-01T00:00:00"},
        ]
        conn = _SqlRecordingConn([rows_result])
        fake_engine = MagicMock()
        fake_engine.connect.return_value = conn

        with patch("api._get_db_engine", return_value=fake_engine), \
             patch("auth.get_user_for_session", return_value={"id": 42, "email": "user@example.com"}):
            resp = client.get(
                "/api/watchlist?client_id=client-abc",
                headers={"Authorization": "Bearer sometoken"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"][0]["symbol"], "TCS")
        query_text, params = conn.queries[0]
        self.assertIn("user_id = :owner_value", query_text)
        self.assertNotIn("client_id", query_text)
        self.assertEqual(params, {"owner_value": 42})

    def test_get_without_session_falls_back_to_client_id(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = []
        conn = _SqlRecordingConn([rows_result])
        fake_engine = MagicMock()
        fake_engine.connect.return_value = conn

        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/watchlist?client_id=client-abc")

        self.assertEqual(resp.status_code, 200)
        query_text, params = conn.queries[0]
        self.assertIn("client_id = :owner_value", query_text)
        self.assertEqual(params, {"owner_value": "client-abc"})

    def test_get_with_expired_session_falls_back_to_client_id(self) -> None:
        # An invalid/expired bearer token isn't a 401 on this endpoint — it
        # just isn't treated as identifying anyone, same as no token at all.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = []
        conn = _SqlRecordingConn([rows_result])
        fake_engine = MagicMock()
        fake_engine.connect.return_value = conn

        with patch("api._get_db_engine", return_value=fake_engine), \
             patch("auth.get_user_for_session", return_value=None):
            resp = client.get(
                "/api/watchlist?client_id=client-abc",
                headers={"Authorization": "Bearer expired-token"},
            )

        self.assertEqual(resp.status_code, 200)
        query_text, _params = conn.queries[0]
        self.assertIn("client_id", query_text)

    def test_get_without_session_or_client_id_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.get("/api/watchlist")
        self.assertEqual(resp.status_code, 422)

    def test_post_with_valid_session_inserts_by_user_id_no_client_id_needed(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        existing_result = MagicMock()
        existing_result.first.return_value = None
        insert_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "", "exchange": "NSE", "addedAt": "2026-01-01T00:00:00"},
        ]
        begin_conn = _SqlRecordingConn([lock_result, count_result, existing_result, insert_result])
        connect_conn = _SqlRecordingConn([rows_result])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = begin_conn
        fake_engine.connect.return_value = connect_conn

        with patch("api._get_db_engine", return_value=fake_engine), \
             patch("auth.get_user_for_session", return_value={"id": 42, "email": "user@example.com"}):
            # No client_id in the body at all — the account identity is sufficient.
            resp = client.post(
                "/api/watchlist", json={"symbol": "tcs"},
                headers={"Authorization": "Bearer sometoken"},
            )

        self.assertEqual(resp.status_code, 200)
        insert_query, insert_params = begin_conn.queries[3]
        self.assertIn("INSERT INTO watchlist_items (user_id", insert_query)
        self.assertIn("ON CONFLICT (user_id, symbol)", insert_query)
        self.assertEqual(insert_params["owner_value"], 42)

    def test_post_without_session_or_client_id_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post("/api/watchlist", json={"symbol": "TCS"})
        self.assertEqual(resp.status_code, 422)

    def test_post_over_cap_for_account_returns_422(self) -> None:
        # Mirrors WatchlistEndpointsTest.test_post_over_cap_returns_422 for the
        # account-owned path — the cap must bind per-owner, not just per-client_id.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar.return_value = api._MAX_WATCHLIST_ITEMS_PER_CLIENT
        existing_result = MagicMock()
        existing_result.first.return_value = None
        begin_conn = _SqlRecordingConn([lock_result, count_result, existing_result])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = begin_conn

        with patch("api._get_db_engine", return_value=fake_engine), \
             patch("auth.get_user_for_session", return_value={"id": 42, "email": "user@example.com"}):
            resp = client.post(
                "/api/watchlist", json={"symbol": "TCS"},
                headers={"Authorization": "Bearer sometoken"},
            )

        self.assertEqual(resp.status_code, 422)
        count_query, count_params = begin_conn.queries[1]
        self.assertIn("user_id = :owner_value", count_query)
        self.assertEqual(count_params["owner_value"], 42)

    def test_delete_with_valid_session_deletes_by_user_id(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        delete_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = []
        begin_conn = _SqlRecordingConn([delete_result])
        connect_conn = _SqlRecordingConn([rows_result])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = begin_conn
        fake_engine.connect.return_value = connect_conn

        with patch("api._get_db_engine", return_value=fake_engine), \
             patch("auth.get_user_for_session", return_value={"id": 42, "email": "user@example.com"}):
            resp = client.delete("/api/watchlist/TCS", headers={"Authorization": "Bearer sometoken"})

        self.assertEqual(resp.status_code, 200)
        delete_query, delete_params = begin_conn.queries[0]
        self.assertIn("user_id = :owner_value", delete_query)
        self.assertEqual(delete_params["owner_value"], 42)

    def test_delete_without_session_or_client_id_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.delete("/api/watchlist/TCS")
        self.assertEqual(resp.status_code, 422)


class WatchlistClaimEndpointTest(unittest.TestCase):
    """POST /api/watchlist/claim — the opt-in escape hatch for this app's
    "no migration on sign-in" default (see routes/watchlist.py's own
    docstring). Only ever called by the post-sign-in "claim your data"
    prompt, so it requires a valid session rather than silently no-op-ing."""

    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def test_without_session_returns_401(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post("/api/watchlist/claim", json={"client_id": "client-abc"})
        self.assertEqual(resp.status_code, 401)

    def test_with_expired_session_returns_401(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("auth.get_user_for_session", return_value=None):
            resp = client.post(
                "/api/watchlist/claim", json={"client_id": "client-abc"},
                headers={"Authorization": "Bearer expired-token"},
            )
        self.assertEqual(resp.status_code, 401)

    def test_invalid_client_id_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post(
            "/api/watchlist/claim", json={"client_id": "not valid!!"},
            headers={"Authorization": "Bearer sometoken"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_happy_path_claims_rows_and_returns_counts(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        delete_conflicts_result = MagicMock()
        count_existing_result = MagicMock()
        count_existing_result.scalar.return_value = 1
        update_result = MagicMock()
        update_result.rowcount = 3
        count_skipped_result = MagicMock()
        count_skipped_result.scalar.return_value = 0
        begin_conn = _SqlRecordingConn([
            lock_result, delete_conflicts_result, count_existing_result,
            update_result, count_skipped_result,
        ])
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "Tata Consultancy Services", "exchange": "NSE",
             "addedAt": "2026-01-01T00:00:00"},
        ]
        connect_conn = _SqlRecordingConn([rows_result])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = begin_conn
        fake_engine.connect.return_value = connect_conn

        with patch("api._get_db_engine", return_value=fake_engine), \
             patch("auth.get_user_for_session", return_value={"id": 42, "email": "user@example.com"}):
            resp = client.post(
                "/api/watchlist/claim", json={"client_id": "client-abc"},
                headers={"Authorization": "Bearer sometoken"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["claimed"], 3)
        self.assertEqual(body["skipped_over_cap"], 0)
        self.assertEqual(body["items"][0]["symbol"], "TCS")
        # Regression test: the advisory lock key here MUST exactly match the
        # key add_to_watchlist() takes for the same account
        # (f"watchlist:{owner[0]}:{owner[1]}", i.e. "watchlist:user:42") —
        # an earlier version of claim_anonymous_rows_sync used a distinct
        # "watchlist_claim:<id>" prefix that looked like a deliberate own
        # namespace but actually meant a concurrent claim and add never
        # serialized against each other at all, letting the per-account cap
        # be silently exceeded. See routes/_shared.py's own docstring.
        lock_query, lock_params = begin_conn.queries[0]
        self.assertIn("pg_advisory_xact_lock", lock_query)
        self.assertEqual(lock_params["lock_key"], "watchlist:user:42")
        # The row cap check runs against the account's own existing rows,
        # not the anonymous client_id's.
        count_query, count_params = begin_conn.queries[2]
        self.assertIn("user_id = :user_id", count_query)
        self.assertEqual(count_params["user_id"], 42)
        update_query, update_params = begin_conn.queries[3]
        self.assertIn("SET client_id = NULL, user_id = :user_id", update_query)
        self.assertEqual(update_params["client_id"], "client-abc")


class PositionsEndpointsTest(unittest.TestCase):
    """"I bought this" positions — same ownership/validation shape as
    watchlist_items (see WatchlistEndpointsTest above), now backed by
    Postgres instead of pure localStorage so a position survives a browser
    switch once the user is signed in."""

    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def test_get_missing_database_url_returns_503(self) -> None:
        resp = client.get("/api/positions?client_id=client-abc")
        self.assertEqual(resp.status_code, 503)

    def test_get_invalid_client_id_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.get(f"/api/positions?client_id={'x' * 100}")
        self.assertEqual(resp.status_code, 422)

    def test_get_returns_items_with_mocked_engine(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "Tata Consultancy Services", "exchange": "NSE",
             "entry_price": 3500.0, "target_price": 3800.0, "stop_loss": 3300.0, "shares": None,
             "bought_at": "2026-01-01T00:00:00"},
        ]
        fake_engine = MagicMock()
        fake_engine.connect.return_value = _FakeConn([rows_result])
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/positions?client_id=client-abc")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["symbol"], "TCS")
        self.assertIsNone(body["items"][0]["shares"])

    def test_get_db_error_returns_sanitized_503(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("api._get_db_engine", side_effect=RuntimeError("connection refused: password exposed")):
            resp = client.get("/api/positions?client_id=client-abc")
        self.assertEqual(resp.status_code, 503)
        self.assertNotIn("password", resp.json()["detail"])

    def test_get_rate_limited_returns_429(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rate_limiter._memory_calls["positions_read:testclient"] = [api.time.monotonic()] * 120
        resp = client.get("/api/positions?client_id=client-abc")
        self.assertEqual(resp.status_code, 429)

    def test_post_missing_database_url_returns_503(self) -> None:
        resp = client.post("/api/positions", json={"client_id": "client-abc", "symbol": "TCS"})
        self.assertEqual(resp.status_code, 503)

    def test_post_invalid_symbol_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post("/api/positions", json={"client_id": "client-abc", "symbol": "bad symbol!"})
        self.assertEqual(resp.status_code, 422)

    def test_post_invalid_exchange_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post("/api/positions", json={
            "client_id": "client-abc", "symbol": "TCS", "exchange": "XYZ",
        })
        self.assertEqual(resp.status_code, 422)

    def test_post_adds_position_with_mocked_engine(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        existing_result = MagicMock()
        existing_result.first.return_value = None
        insert_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "Tata Consultancy Services", "exchange": "NSE",
             "entry_price": 3500.0, "target_price": 3800.0, "stop_loss": 3300.0, "shares": None,
             "bought_at": "2026-01-01T00:00:00"},
        ]
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([lock_result, count_result, existing_result, insert_result])
        fake_engine.connect.return_value = _FakeConn([rows_result])

        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.post("/api/positions", json={
                "client_id": "client-abc", "symbol": "tcs", "company": "Tata Consultancy Services",
                "exchange": "NSE", "entry_price": 3500.0, "target_price": 3800.0, "stop_loss": 3300.0,
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"][0]["symbol"], "TCS")

    def test_post_over_cap_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar.return_value = api._MAX_POSITIONS_PER_CLIENT
        existing_result = MagicMock()
        existing_result.first.return_value = None
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([lock_result, count_result, existing_result])
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.post("/api/positions", json={"client_id": "client-abc", "symbol": "TCS"})
        self.assertEqual(resp.status_code, 422)

    def test_post_over_cap_but_symbol_already_exists_is_allowed(self) -> None:
        # A re-mark-as-bought on an existing position is an UPDATE via
        # ON CONFLICT, not a new row — it must not be blocked by the cap.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar.return_value = api._MAX_POSITIONS_PER_CLIENT
        existing_result = MagicMock()
        existing_result.first.return_value = (1,)
        insert_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = []
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([lock_result, count_result, existing_result, insert_result])
        fake_engine.connect.return_value = _FakeConn([rows_result])
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.post("/api/positions", json={"client_id": "client-abc", "symbol": "TCS"})
        self.assertEqual(resp.status_code, 200)

    def test_patch_updates_shares(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        update_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "", "exchange": "NSE",
             "entry_price": 3500.0, "target_price": None, "stop_loss": None, "shares": 10.0,
             "bought_at": "2026-01-01T00:00:00"},
        ]
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([update_result])
        fake_engine.connect.return_value = _FakeConn([rows_result])
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.patch("/api/positions/TCS", json={"client_id": "client-abc", "shares": 10})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"][0]["shares"], 10.0)

    def test_patch_negative_shares_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.patch("/api/positions/TCS", json={"client_id": "client-abc", "shares": -5})
        self.assertEqual(resp.status_code, 422)

    def test_patch_null_shares_clears_it(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        update_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = []
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([update_result])
        fake_engine.connect.return_value = _FakeConn([rows_result])
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.patch("/api/positions/TCS", json={"client_id": "client-abc", "shares": None})
        self.assertEqual(resp.status_code, 200)

    def test_patch_invalid_symbol_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.patch("/api/positions/bad%20symbol", json={"client_id": "client-abc", "shares": 5})
        self.assertEqual(resp.status_code, 422)

    def test_delete_invalid_symbol_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.delete("/api/positions/bad%20symbol?client_id=client-abc")
        self.assertEqual(resp.status_code, 422)

    def test_delete_removes_position_with_mocked_engine(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        delete_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = []
        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn([delete_result])
        fake_engine.connect.return_value = _FakeConn([rows_result])
        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.delete("/api/positions/TCS?client_id=client-abc")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"], [])

    def test_delete_missing_database_url_returns_503(self) -> None:
        resp = client.delete("/api/positions/TCS?client_id=client-abc")
        self.assertEqual(resp.status_code, 503)


class PositionsAccountLinkingTest(unittest.TestCase):
    """A valid session always wins over client_id, same as watchlist — see
    WatchlistAccountLinkingTest above for the equivalent watchlist coverage."""

    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def test_get_with_valid_session_queries_by_user_id_ignoring_client_id(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = []
        conn = _SqlRecordingConn([rows_result])
        fake_engine = MagicMock()
        fake_engine.connect.return_value = conn

        with patch("api._get_db_engine", return_value=fake_engine), \
             patch("auth.get_user_for_session", return_value={"id": 42, "email": "user@example.com"}):
            resp = client.get(
                "/api/positions?client_id=client-abc",
                headers={"Authorization": "Bearer sometoken"},
            )

        self.assertEqual(resp.status_code, 200)
        query_text, params = conn.queries[0]
        self.assertIn("user_id = :owner_value", query_text)
        self.assertNotIn("client_id", query_text)
        self.assertEqual(params, {"owner_value": 42})

    def test_get_without_session_falls_back_to_client_id(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = []
        conn = _SqlRecordingConn([rows_result])
        fake_engine = MagicMock()
        fake_engine.connect.return_value = conn

        with patch("api._get_db_engine", return_value=fake_engine):
            resp = client.get("/api/positions?client_id=client-abc")

        self.assertEqual(resp.status_code, 200)
        query_text, params = conn.queries[0]
        self.assertIn("client_id = :owner_value", query_text)
        self.assertEqual(params, {"owner_value": "client-abc"})

    def test_get_without_session_or_client_id_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.get("/api/positions")
        self.assertEqual(resp.status_code, 422)

    def test_post_with_valid_session_inserts_by_user_id(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        existing_result = MagicMock()
        existing_result.first.return_value = None
        insert_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = []
        begin_conn = _SqlRecordingConn([lock_result, count_result, existing_result, insert_result])
        connect_conn = _SqlRecordingConn([rows_result])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = begin_conn
        fake_engine.connect.return_value = connect_conn

        with patch("api._get_db_engine", return_value=fake_engine), \
             patch("auth.get_user_for_session", return_value={"id": 42, "email": "user@example.com"}):
            resp = client.post(
                "/api/positions",
                json={"symbol": "TCS"},
                headers={"Authorization": "Bearer sometoken"},
            )

        self.assertEqual(resp.status_code, 200)
        insert_query, insert_params = begin_conn.queries[-1]
        self.assertIn("user_id", insert_query)
        self.assertEqual(insert_params["owner_value"], 42)

    def test_delete_with_valid_session_deletes_by_user_id(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        delete_result = MagicMock()
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = []
        begin_conn = _SqlRecordingConn([delete_result])
        connect_conn = _SqlRecordingConn([rows_result])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = begin_conn
        fake_engine.connect.return_value = connect_conn

        with patch("api._get_db_engine", return_value=fake_engine), \
             patch("auth.get_user_for_session", return_value={"id": 42, "email": "user@example.com"}):
            resp = client.delete("/api/positions/TCS", headers={"Authorization": "Bearer sometoken"})

        self.assertEqual(resp.status_code, 200)
        delete_query, delete_params = begin_conn.queries[0]
        self.assertIn("user_id = :owner_value", delete_query)
        self.assertEqual(delete_params["owner_value"], 42)

    def test_delete_without_session_or_client_id_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.delete("/api/positions/TCS")
        self.assertEqual(resp.status_code, 422)


class PositionsClaimEndpointTest(unittest.TestCase):
    """POST /api/positions/claim — same opt-in escape hatch as
    WatchlistClaimEndpointTest above, for the positions table."""

    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def test_without_session_returns_401(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post("/api/positions/claim", json={"client_id": "client-abc"})
        self.assertEqual(resp.status_code, 401)

    def test_with_expired_session_returns_401(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        with patch("auth.get_user_for_session", return_value=None):
            resp = client.post(
                "/api/positions/claim", json={"client_id": "client-abc"},
                headers={"Authorization": "Bearer expired-token"},
            )
        self.assertEqual(resp.status_code, 401)

    def test_invalid_client_id_returns_422(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        resp = client.post(
            "/api/positions/claim", json={"client_id": "not valid!!"},
            headers={"Authorization": "Bearer sometoken"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_happy_path_claims_rows_and_returns_counts(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        lock_result = MagicMock()
        delete_conflicts_result = MagicMock()
        count_existing_result = MagicMock()
        count_existing_result.scalar.return_value = 0
        update_result = MagicMock()
        update_result.rowcount = 2
        count_skipped_result = MagicMock()
        count_skipped_result.scalar.return_value = 1
        begin_conn = _SqlRecordingConn([
            lock_result, delete_conflicts_result, count_existing_result,
            update_result, count_skipped_result,
        ])
        rows_result = MagicMock()
        rows_result.mappings.return_value.fetchall.return_value = [
            {"symbol": "TCS", "company": "", "exchange": "NSE",
             "entry_price": None, "target_price": None, "stop_loss": None,
             "shares": None, "bought_at": "2026-01-01T00:00:00"},
        ]
        connect_conn = _SqlRecordingConn([rows_result])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = begin_conn
        fake_engine.connect.return_value = connect_conn

        with patch("api._get_db_engine", return_value=fake_engine), \
             patch("auth.get_user_for_session", return_value={"id": 42, "email": "user@example.com"}):
            resp = client.post(
                "/api/positions/claim", json={"client_id": "client-abc"},
                headers={"Authorization": "Bearer sometoken"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["claimed"], 2)
        self.assertEqual(body["skipped_over_cap"], 1)
        self.assertEqual(body["items"][0]["symbol"], "TCS")
        # Regression test: the advisory lock key here MUST exactly match the
        # key add_position() takes for the same account — see
        # WatchlistClaimEndpointTest's matching test for the full history of
        # why this specific assertion exists.
        lock_query, lock_params = begin_conn.queries[0]
        self.assertIn("pg_advisory_xact_lock", lock_query)
        self.assertEqual(lock_params["lock_key"], "positions:user:42")
        update_query, update_params = begin_conn.queries[3]
        self.assertIn("SET client_id = NULL, user_id = :user_id", update_query)
        self.assertEqual(update_params["client_id"], "client-abc")
        self.assertEqual(update_params["user_id"], 42)


class VerdictHistoryEndpointTest(unittest.TestCase):
    """Read-only aggregation over verdict_history.load_history() — degrades to
    an empty list rather than an error, same philosophy as /api/consolidated."""

    def setUp(self) -> None:
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()

    def test_invalid_symbol_returns_422(self) -> None:
        resp = client.get("/api/verdict-history/bad symbol")
        self.assertEqual(resp.status_code, 422)

    def test_rate_limited_returns_429(self) -> None:
        rate_limiter._memory_calls["verdict_history:testclient"] = [api.time.monotonic()] * 60
        resp = client.get("/api/verdict-history/TCS")
        self.assertEqual(resp.status_code, 429)

    def test_returns_empty_history_when_nothing_stored(self) -> None:
        with patch("verdict_history.load_history", return_value=[]), \
             patch.object(api, "_fetch_live_price_sync", return_value={}):
            resp = client.get("/api/verdict-history/TCS")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"symbol": "TCS", "history": [], "win_rate": None, "scored_count": 0},
        )

    def test_returns_history_scored_against_live_price(self) -> None:
        fake_history = [
            {"date": "2026-07-01", "recommendation": "HOLD", "confidence": "MEDIUM", "current_price": 100.0, "signal_score": 2.0},
            {"date": "2026-07-10", "recommendation": "SELL", "confidence": "HIGH", "current_price": 120.0, "signal_score": -4.0},
            {"date": "2026-07-24", "recommendation": "BUY", "confidence": "HIGH", "current_price": 110.5, "signal_score": 6.0},
        ]
        # Live price is up from every stored snapshot: BUY should score a win,
        # SELL a loss, HOLD stays unscored (no directional claim to grade).
        with patch("verdict_history.load_history", return_value=fake_history), \
             patch.object(api, "_fetch_live_price_sync", return_value={"price": 121.0, "change_pct": 0.5}):
            resp = client.get("/api/verdict-history/tcs")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "TCS")
        self.assertEqual(len(body["history"]), 3)

        hold, sell, buy = body["history"]
        self.assertIsNone(hold["outcome"])
        self.assertIsNotNone(hold["return_since_pct"])  # observed fact, scored or not

        self.assertEqual(sell["outcome"], "loss")   # price rose past a SELL call
        self.assertEqual(buy["outcome"], "win")      # price rose past a BUY call

        self.assertEqual(body["scored_count"], 2)
        self.assertEqual(body["win_rate"], 50.0)

    def test_skips_live_price_fetch_when_fewer_than_two_entries(self) -> None:
        # VerdictTimeline itself never renders below 2 stored days — no point
        # spending an extra yfinance call on a response nothing will use.
        fake_history = [
            {"date": "2026-07-24", "recommendation": "BUY", "confidence": "HIGH", "current_price": 110.5, "signal_score": 6.0},
        ]
        with patch("verdict_history.load_history", return_value=fake_history), \
             patch.object(api, "_fetch_live_price_sync") as fetch_live:
            resp = client.get("/api/verdict-history/TCS")
        body = resp.json()
        fetch_live.assert_not_called()
        self.assertIsNone(body["history"][0]["return_since_pct"])
        self.assertIsNone(body["history"][0]["outcome"])
        self.assertIsNone(body["win_rate"])
        self.assertEqual(body["scored_count"], 0)

    def test_returns_history_unscored_when_live_price_unavailable(self) -> None:
        fake_history = [
            {"date": "2026-07-01", "recommendation": "HOLD", "confidence": "MEDIUM", "current_price": 100.0, "signal_score": 2.0},
            {"date": "2026-07-24", "recommendation": "BUY", "confidence": "HIGH", "current_price": 110.5, "signal_score": 6.0},
        ]
        with patch("verdict_history.load_history", return_value=fake_history), \
             patch.object(api, "_fetch_live_price_sync", return_value={}) as fetch_live:
            resp = client.get("/api/verdict-history/TCS")
        body = resp.json()
        fetch_live.assert_called_once_with("TCS")
        for entry in body["history"]:
            self.assertIsNone(entry["return_since_pct"])
            self.assertIsNone(entry["outcome"])
        self.assertIsNone(body["win_rate"])
        self.assertEqual(body["scored_count"], 0)


class ScoreVerdictHistoryTest(unittest.TestCase):
    """Pure-function coverage for the win/loss scoring logic, independent of
    the HTTP layer."""

    def test_buy_win_and_loss(self) -> None:
        history = [{"recommendation": "BUY", "current_price": 100.0}]
        up = api._score_verdict_history(history, 110.0)
        down = api._score_verdict_history(history, 90.0)
        self.assertEqual(up[0]["outcome"], "win")
        self.assertEqual(down[0]["outcome"], "loss")

    def test_sell_win_and_loss(self) -> None:
        history = [{"recommendation": "SELL", "current_price": 100.0}]
        down = api._score_verdict_history(history, 90.0)
        up = api._score_verdict_history(history, 110.0)
        self.assertEqual(down[0]["outcome"], "win")
        self.assertEqual(up[0]["outcome"], "loss")

    def test_hold_is_never_scored(self) -> None:
        history = [{"recommendation": "HOLD", "current_price": 100.0}]
        scored = api._score_verdict_history(history, 150.0)
        self.assertIsNone(scored[0]["outcome"])
        self.assertIsNotNone(scored[0]["return_since_pct"])

    def test_missing_prices_yield_null_fields(self) -> None:
        no_entry_price = api._score_verdict_history([{"recommendation": "BUY", "current_price": None}], 100.0)
        no_live_price = api._score_verdict_history([{"recommendation": "BUY", "current_price": 100.0}], None)
        self.assertIsNone(no_entry_price[0]["return_since_pct"])
        self.assertIsNone(no_entry_price[0]["outcome"])
        self.assertIsNone(no_live_price[0]["return_since_pct"])
        self.assertIsNone(no_live_price[0]["outcome"])

    def test_exactly_flat_is_unscored(self) -> None:
        history = [{"recommendation": "BUY", "current_price": 100.0}]
        scored = api._score_verdict_history(history, 100.0)
        self.assertEqual(scored[0]["return_since_pct"], 0)
        self.assertIsNone(scored[0]["outcome"])

    def test_rounding_induced_flat_is_also_unscored(self) -> None:
        # A genuinely nonzero move that rounds to 0.00% is treated the same
        # as a literally-identical price — the graded outcome stays
        # consistent with the 0.00% the UI actually displays, rather than
        # grading on a move too small to show up in the rounded number.
        history = [{"recommendation": "BUY", "current_price": 100.0}]
        scored = api._score_verdict_history(history, 100.003)
        self.assertEqual(scored[0]["return_since_pct"], 0)
        self.assertIsNone(scored[0]["outcome"])


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
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        api._DB_ENGINE = None
        rate_limiter._memory_calls.clear()

    def test_invalid_symbol_returns_422(self) -> None:
        resp = client.get("/api/consolidated/bad symbol")
        self.assertEqual(resp.status_code, 422)

    def test_rate_limited_returns_429(self) -> None:
        rate_limiter._memory_calls["consolidated:testclient"] = [api.time.monotonic()] * 30
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
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        rate_limiter._memory_calls.clear()

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
        rate_limiter._memory_calls["auth_request_link:testclient"] = [api.time.monotonic()] * 5
        resp = client.post("/api/auth/request-link", json={"email": "user@example.com"})
        self.assertEqual(resp.status_code, 429)

    def test_email_rate_limited_returns_429_even_from_a_fresh_ip(self) -> None:
        # Per-IP limiting alone doesn't stop an attacker with rotating IPs
        # from email-bombing one victim's inbox — the target address itself
        # must also be capped, independent of caller IP.
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rate_limiter._memory_calls["auth_request_link_email:victim@example.com"] = [api.time.monotonic()] * 5
        resp = client.post("/api/auth/request-link", json={"email": "victim@example.com"})
        self.assertEqual(resp.status_code, 429)

    def test_email_rate_limit_is_scoped_per_address(self) -> None:
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rate_limiter._memory_calls["auth_request_link_email:someone-else@example.com"] = [api.time.monotonic()] * 5
        with patch("auth.create_magic_link", return_value="raw-token"), \
             patch("email_sender.send_magic_link_email", return_value=True):
            resp = client.post("/api/auth/request-link", json={"email": "user@example.com"})
        self.assertEqual(resp.status_code, 200)


class AuthVerifyEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.pop("DATABASE_URL", None)
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is not None:
            os.environ["DATABASE_URL"] = self._db_url
        rate_limiter._memory_calls.clear()

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
        rate_limiter._memory_calls["auth_verify:testclient"] = [api.time.monotonic()] * 20
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


class ApiKeyManagementEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._db_url

    def test_create_requires_session(self) -> None:
        resp = client.post("/api/api-keys", json={"label": "x"})
        self.assertEqual(resp.status_code, 401)

    def test_create_returns_key_once(self) -> None:
        created = {
            "id": 1, "key": "apk_rawsecret", "key_prefix": "apk_rawsec",
            "label": "my script", "created_at": "2026-01-01T00:00:00Z",
        }
        with patch("auth.get_user_for_session", return_value={"id": 7, "email": "a@b.com"}), \
             patch("auth.create_api_key", return_value=created) as create_key:
            resp = client.post(
                "/api/api-keys",
                json={"label": "my script"},
                headers={"Authorization": "Bearer sometoken"},
            )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json(), created)
        create_key.assert_called_once_with(7, "my script")

    def test_create_db_error_returns_503(self) -> None:
        with patch("auth.get_user_for_session", return_value={"id": 7, "email": "a@b.com"}), \
             patch("auth.create_api_key", side_effect=RuntimeError("connection refused")):
            resp = client.post(
                "/api/api-keys",
                json={"label": "x"},
                headers={"Authorization": "Bearer sometoken"},
            )
        self.assertEqual(resp.status_code, 503)

    def test_create_strips_and_caps_label(self) -> None:
        with patch("auth.get_user_for_session", return_value={"id": 7, "email": "a@b.com"}), \
             patch("auth.create_api_key", return_value={
                 "id": 1, "key": "apk_x", "key_prefix": "apk_x", "label": None, "created_at": "now",
             }) as create_key:
            client.post(
                "/api/api-keys",
                json={"label": "   "},
                headers={"Authorization": "Bearer sometoken"},
            )
        create_key.assert_called_once_with(7, None)

    def test_list_requires_session(self) -> None:
        resp = client.get("/api/api-keys")
        self.assertEqual(resp.status_code, 401)

    def test_list_rate_limited_returns_429(self) -> None:
        # Previously unrate-limited despite requiring only a session, unlike
        # its own sibling POST /api/api-keys (20/hr) — see the deep gap
        # analysis this fix closes.
        rate_limiter._memory_calls["api_keys_list:testclient"] = [api.time.monotonic()] * 60
        resp = client.get("/api/api-keys", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 429)

    def test_list_returns_keys_for_current_user(self) -> None:
        keys = [{
            "id": 1, "key_prefix": "apk_ab", "label": None,
            "created_at": "2026-01-01T00:00:00Z", "last_used_at": None, "revoked_at": None,
        }]
        with patch("auth.get_user_for_session", return_value={"id": 7, "email": "a@b.com", "tier": "free"}), \
             patch("auth.list_api_keys", return_value=keys) as list_keys:
            resp = client.get("/api/api-keys", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["keys"], keys)
        self.assertEqual(body["tier"], "free")
        self.assertEqual(body["usage"], {"calls": 0, "limit": 100, "window_seconds": 3600})
        list_keys.assert_called_once_with(7)

    def test_list_usage_reflects_pro_tier_limit(self) -> None:
        keys = []
        with patch("auth.get_user_for_session", return_value={"id": 7, "email": "a@b.com", "tier": "pro"}), \
             patch("auth.list_api_keys", return_value=keys):
            resp = client.get("/api/api-keys", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["tier"], "pro")
        self.assertEqual(body["usage"]["limit"], 1000)

    def test_list_missing_tier_defaults_to_free(self) -> None:
        # A session dict without "tier" (e.g. a stale test double, or a
        # not-yet-migrated row) must not crash — falls back to 'free'.
        with patch("auth.get_user_for_session", return_value={"id": 7, "email": "a@b.com"}), \
             patch("auth.list_api_keys", return_value=[]):
            resp = client.get("/api/api-keys", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["tier"], "free")
        self.assertEqual(resp.json()["usage"]["limit"], 100)

    def test_list_db_error_returns_503(self) -> None:
        with patch("auth.get_user_for_session", return_value={"id": 7, "email": "a@b.com"}), \
             patch("auth.list_api_keys", side_effect=RuntimeError("connection refused")):
            resp = client.get("/api/api-keys", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 503)

    def test_revoke_requires_session(self) -> None:
        resp = client.delete("/api/api-keys/1")
        self.assertEqual(resp.status_code, 401)

    def test_revoke_rate_limited_returns_429(self) -> None:
        rate_limiter._memory_calls["api_keys_revoke:testclient"] = [api.time.monotonic()] * 60
        resp = client.delete("/api/api-keys/1", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 429)

    def test_revoke_success(self) -> None:
        with patch("auth.get_user_for_session", return_value={"id": 7, "email": "a@b.com"}), \
             patch("auth.revoke_api_key", return_value=True) as revoke_key:
            resp = client.delete("/api/api-keys/5", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})
        revoke_key.assert_called_once_with(7, 5)

    def test_revoke_not_found_returns_404(self) -> None:
        with patch("auth.get_user_for_session", return_value={"id": 7, "email": "a@b.com"}), \
             patch("auth.revoke_api_key", return_value=False):
            resp = client.delete("/api/api-keys/999", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 404)

    def test_revoke_cannot_target_another_users_key(self) -> None:
        # auth.revoke_api_key itself scopes the UPDATE to (id, user_id) — this
        # test just confirms api.py always passes the *session's* user_id
        # through, never a client-supplied one, so cross-account revocation
        # isn't reachable at the endpoint layer either.
        with patch("auth.get_user_for_session", return_value={"id": 7, "email": "a@b.com"}), \
             patch("auth.revoke_api_key", return_value=False) as revoke_key:
            client.delete("/api/api-keys/5", headers={"Authorization": "Bearer sometoken"})
        revoke_key.assert_called_once_with(7, 5)

    def test_revoke_db_error_returns_503(self) -> None:
        with patch("auth.get_user_for_session", return_value={"id": 7, "email": "a@b.com"}), \
             patch("auth.revoke_api_key", side_effect=RuntimeError("connection refused")):
            resp = client.delete("/api/api-keys/5", headers={"Authorization": "Bearer sometoken"})
        self.assertEqual(resp.status_code, 503)


class ConsolidatedV1EndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self._db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        rate_limiter._memory_calls.clear()

    def tearDown(self) -> None:
        if self._db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._db_url
        rate_limiter._memory_calls.clear()

    def test_missing_api_key_header_returns_401(self) -> None:
        resp = client.get("/api/v1/consolidated/TCS")
        self.assertEqual(resp.status_code, 401)

    def test_invalid_api_key_returns_401(self) -> None:
        with patch("auth.get_user_for_api_key", return_value=None):
            resp = client.get("/api/v1/consolidated/TCS", headers={"X-API-Key": "bogus"})
        self.assertEqual(resp.status_code, 401)

    def test_missing_database_url_returns_401(self) -> None:
        os.environ.pop("DATABASE_URL", None)
        resp = client.get("/api/v1/consolidated/TCS", headers={"X-API-Key": "apk_x"})
        self.assertEqual(resp.status_code, 401)

    def test_invalid_symbol_returns_422(self) -> None:
        with patch("auth.get_user_for_api_key", return_value={"user_id": 7}):
            resp = client.get("/api/v1/consolidated/not-a-symbol!!", headers={"X-API-Key": "apk_x"})
        self.assertEqual(resp.status_code, 422)

    def test_valid_key_returns_consolidated_payload(self) -> None:
        with patch("auth.get_user_for_api_key", return_value={"user_id": 7}), \
             patch("cache.load", return_value=None), \
             patch.object(api, "_load_picks_cache", return_value=None):
            resp = client.get("/api/v1/consolidated/TCS", headers={"X-API-Key": "apk_x"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "TCS")
        self.assertIsNone(body["analysis"])
        self.assertIsNone(body["market_pick"])

    def test_rate_limit_is_keyed_by_user_not_ip(self) -> None:
        rate_limiter._memory_calls["api_v1:7"] = [api.time.monotonic()] * 100
        with patch("auth.get_user_for_api_key", return_value={"user_id": 7, "tier": "free"}), \
             patch.object(api, "_consolidated_payload") as payload_fn:
            resp = client.get("/api/v1/consolidated/TCS", headers={"X-API-Key": "apk_x"})
        self.assertEqual(resp.status_code, 429)
        payload_fn.assert_not_called()

    def test_pro_tier_gets_a_higher_limit(self) -> None:
        # 100 prior calls would exhaust the free-tier limit (see the test
        # above) but must not exhaust pro's higher one.
        rate_limiter._memory_calls["api_v1:7"] = [api.time.monotonic()] * 100
        with patch("auth.get_user_for_api_key", return_value={"user_id": 7, "tier": "pro"}), \
             patch("cache.load", return_value=None), \
             patch.object(api, "_load_picks_cache", return_value=None):
            resp = client.get("/api/v1/consolidated/TCS", headers={"X-API-Key": "apk_x"})
        self.assertEqual(resp.status_code, 200)

    def test_unrecognized_tier_falls_back_to_free_limit(self) -> None:
        rate_limiter._memory_calls["api_v1:7"] = [api.time.monotonic()] * 100
        with patch("auth.get_user_for_api_key", return_value={"user_id": 7, "tier": "made-up-tier"}), \
             patch.object(api, "_consolidated_payload") as payload_fn:
            resp = client.get("/api/v1/consolidated/TCS", headers={"X-API-Key": "apk_x"})
        self.assertEqual(resp.status_code, 429)
        payload_fn.assert_not_called()

    def test_missing_tier_key_falls_back_to_free_limit(self) -> None:
        rate_limiter._memory_calls["api_v1:7"] = [api.time.monotonic()] * 100
        with patch("auth.get_user_for_api_key", return_value={"user_id": 7}), \
             patch.object(api, "_consolidated_payload") as payload_fn:
            resp = client.get("/api/v1/consolidated/TCS", headers={"X-API-Key": "apk_x"})
        self.assertEqual(resp.status_code, 429)
        payload_fn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
