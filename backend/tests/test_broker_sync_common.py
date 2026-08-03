"""Tests for portfolio/broker_sync_common.py's call_with_backoff() — the
shared retry helper every broker sync module wraps its raw network calls
with (kite.holdings/trades, HDFC/Paytm's requests-based fetches). Real
sleeps are avoided via a tiny base_delay_seconds, not by mocking time.sleep,
so these also prove the actual sleep-then-retry control flow runs, not just
that time.sleep was called."""
import unittest
from unittest.mock import MagicMock

import requests

from portfolio.broker_sync_common import call_with_backoff


class _HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"http {status_code}")
        self.response = MagicMock(status_code=status_code)


class _KiteException(Exception):
    """Mirrors kiteconnect's own KiteException shape: a `.code` attribute
    directly on the exception, no `.response` at all — unlike `requests`'
    HTTPError, which carries the status on `.response.status_code`."""
    def __init__(self, code):
        super().__init__(f"kite error {code}")
        self.code = code


class CallWithBackoffTest(unittest.TestCase):
    def test_succeeds_on_first_attempt_no_retry(self):
        fn = MagicMock(return_value="ok")
        result = call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 1)

    def test_retries_transient_failure_then_succeeds(self):
        fn = MagicMock(side_effect=[requests.exceptions.ConnectionError("blip"), "ok"])
        result = call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 2)

    def test_exhausts_all_attempts_then_raises_last_exception(self):
        fn = MagicMock(side_effect=requests.exceptions.Timeout("still down"))
        with self.assertRaises(requests.exceptions.Timeout):
            call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(fn.call_count, 3)

    def test_5xx_http_error_is_retried(self):
        fn = MagicMock(side_effect=[_HttpError(503), "ok"])
        result = call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 2)

    def test_4xx_http_error_is_not_retried(self):
        """An expired token or bad request needs a reconnect, not three
        retries of the exact same failure — this is what keeps a doomed
        sync from wasting ~(1+2)s of backoff sleep before the 422/404 the
        caller needs anyway to prompt a reconnect."""
        fn = MagicMock(side_effect=_HttpError(401))
        with self.assertRaises(_HttpError):
            call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(fn.call_count, 1)

    def test_kiteconnect_style_exception_code_attribute_is_honored(self):
        """kiteconnect's own KiteException carries `.code` directly (no
        `.response`) — a real gap this test guards against: without
        checking `.code` too, TokenException(code=403) would fall through
        to "no status attached" and get retried as if it were transient,
        wasting ~3s of backoff sleep on an auth failure that a retry can
        never fix."""
        fn = MagicMock(side_effect=_KiteException(403))
        with self.assertRaises(_KiteException):
            call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(fn.call_count, 1)

    def test_kiteconnect_style_5xx_code_is_retried(self):
        fn = MagicMock(side_effect=[_KiteException(503), "ok"])
        result = call_with_backoff(fn, max_attempts=3, base_delay_seconds=0.001)
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 2)


if __name__ == "__main__":
    unittest.main()
