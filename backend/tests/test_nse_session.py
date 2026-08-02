import unittest
from unittest.mock import MagicMock, patch

from tools._nse_session import get_nse_session


class GetNseSessionTest(unittest.TestCase):
    def test_sets_default_headers(self) -> None:
        with patch("tools._nse_session.requests.Session") as session_cls, \
             patch("tools._nse_session.time.sleep"):
            session = MagicMock()
            session_cls.return_value = session
            get_nse_session()

        session.headers.update.assert_called_once()
        headers = session.headers.update.call_args[0][0]
        self.assertIn("Windows NT 10.0", headers["User-Agent"])
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Referer"], "https://www.nseindia.com")

    def test_primes_with_a_get_to_nse_homepage(self) -> None:
        with patch("tools._nse_session.requests.Session") as session_cls, \
             patch("tools._nse_session.time.sleep") as mock_sleep:
            session = MagicMock()
            session_cls.return_value = session
            get_nse_session(timeout=5)

        session.get.assert_called_once_with("https://www.nseindia.com", timeout=5)
        mock_sleep.assert_called_once_with(0.5)

    def test_priming_failure_is_swallowed_and_session_still_returned(self) -> None:
        with patch("tools._nse_session.requests.Session") as session_cls, \
             patch("tools._nse_session.time.sleep") as mock_sleep:
            session = MagicMock()
            session.get.side_effect = ConnectionError("boom")
            session_cls.return_value = session
            result = get_nse_session()

        self.assertIs(result, session)
        mock_sleep.assert_not_called()  # never reached — the raising get() aborts the try block first

    def test_custom_accept_overrides_default(self) -> None:
        with patch("tools._nse_session.requests.Session") as session_cls, \
             patch("tools._nse_session.time.sleep"):
            session = MagicMock()
            session_cls.return_value = session
            get_nse_session(accept="text/csv,application/json,*/*")

        headers = session.headers.update.call_args[0][0]
        self.assertEqual(headers["Accept"], "text/csv,application/json,*/*")

    def test_extra_headers_are_merged_in(self) -> None:
        with patch("tools._nse_session.requests.Session") as session_cls, \
             patch("tools._nse_session.time.sleep"):
            session = MagicMock()
            session_cls.return_value = session
            get_nse_session(extra_headers={"Accept-Language": "en-US,en;q=0.9"})

        headers = session.headers.update.call_args[0][0]
        self.assertEqual(headers["Accept-Language"], "en-US,en;q=0.9")
        self.assertEqual(headers["Accept"], "application/json")  # default still present

    def test_sleep_after_prime_can_be_disabled(self) -> None:
        with patch("tools._nse_session.requests.Session") as session_cls, \
             patch("tools._nse_session.time.sleep") as mock_sleep:
            session = MagicMock()
            session_cls.return_value = session
            get_nse_session(sleep_after_prime=0)

        mock_sleep.assert_not_called()

    def test_returns_a_real_session_instance_end_to_end(self) -> None:
        # No mocking of requests.Session itself here — only the network
        # call inside it — to catch a regression where get_nse_session
        # stops actually returning a usable requests.Session.
        with patch("requests.Session.get", side_effect=ConnectionError("no network in tests")):
            session = get_nse_session(sleep_after_prime=0)
        import requests
        self.assertIsInstance(session, requests.Session)


if __name__ == "__main__":
    unittest.main()
