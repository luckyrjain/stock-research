import os
import unittest
from unittest.mock import MagicMock, patch

import email_sender


class SendMagicLinkEmailTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {
            k: os.environ.pop(k, None)
            for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_USE_TLS")
        }

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_returns_false_and_never_raises_without_smtp_host(self) -> None:
        result = email_sender.send_magic_link_email("user@example.com", "https://example.com/auth/verify?token=x")
        self.assertFalse(result)

    def test_sends_via_smtp_with_starttls_and_login(self) -> None:
        os.environ["SMTP_HOST"] = "smtp.example.com"
        os.environ["SMTP_USER"] = "apikey"
        os.environ["SMTP_PASSWORD"] = "secret"

        fake_server = MagicMock()
        fake_smtp_cls = MagicMock()
        fake_smtp_cls.return_value.__enter__.return_value = fake_server

        with patch("smtplib.SMTP", fake_smtp_cls):
            result = email_sender.send_magic_link_email("user@example.com", "https://example.com/auth/verify?token=x")

        self.assertTrue(result)
        fake_server.starttls.assert_called_once()
        fake_server.login.assert_called_once_with("apikey", "secret")
        fake_server.send_message.assert_called_once()
        sent_msg = fake_server.send_message.call_args[0][0]
        self.assertEqual(sent_msg["To"], "user@example.com")
        self.assertIn("verify?token=x", sent_msg.get_content())

    def test_skips_login_without_credentials(self) -> None:
        os.environ["SMTP_HOST"] = "smtp.example.com"

        fake_server = MagicMock()
        fake_smtp_cls = MagicMock()
        fake_smtp_cls.return_value.__enter__.return_value = fake_server

        with patch("smtplib.SMTP", fake_smtp_cls):
            email_sender.send_magic_link_email("user@example.com", "https://x/y")

        fake_server.login.assert_not_called()

    def test_skips_starttls_when_smtp_use_tls_is_false(self) -> None:
        os.environ["SMTP_HOST"] = "smtp.example.com"
        os.environ["SMTP_USE_TLS"] = "false"

        fake_server = MagicMock()
        fake_smtp_cls = MagicMock()
        fake_smtp_cls.return_value.__enter__.return_value = fake_server

        with patch("smtplib.SMTP", fake_smtp_cls):
            email_sender.send_magic_link_email("user@example.com", "https://x/y")

        fake_server.starttls.assert_not_called()

    def test_returns_false_and_never_raises_on_smtp_error(self) -> None:
        os.environ["SMTP_HOST"] = "smtp.example.com"

        with patch("smtplib.SMTP", side_effect=RuntimeError("connection refused")):
            result = email_sender.send_magic_link_email("user@example.com", "https://x/y")

        self.assertFalse(result)


class SendWatchlistAlertEmailTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {
            k: os.environ.pop(k, None)
            for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_USE_TLS")
        }

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def _alert(self, **overrides) -> dict:
        base = {
            "symbol": "TCS", "old_recommendation": "HOLD",
            "new_recommendation": "BUY", "confidence": "high",
        }
        base.update(overrides)
        return base

    def test_returns_false_without_alerts(self) -> None:
        os.environ["SMTP_HOST"] = "smtp.example.com"
        result = email_sender.send_watchlist_alert_email("user@example.com", [])
        self.assertFalse(result)

    def test_returns_false_and_never_raises_without_smtp_host(self) -> None:
        result = email_sender.send_watchlist_alert_email("user@example.com", [self._alert()])
        self.assertFalse(result)

    def test_sends_digest_listing_every_alert(self) -> None:
        os.environ["SMTP_HOST"] = "smtp.example.com"
        fake_server = MagicMock()
        fake_smtp_cls = MagicMock()
        fake_smtp_cls.return_value.__enter__.return_value = fake_server

        alerts = [self._alert(symbol="TCS"), self._alert(symbol="INFY", old_recommendation="BUY", new_recommendation="SELL")]
        with patch("smtplib.SMTP", fake_smtp_cls):
            result = email_sender.send_watchlist_alert_email("user@example.com", alerts)

        self.assertTrue(result)
        sent_msg = fake_server.send_message.call_args[0][0]
        body = sent_msg.get_content()
        self.assertIn("TCS: HOLD -> BUY", body)
        self.assertIn("INFY: BUY -> SELL", body)
        self.assertEqual(sent_msg["To"], "user@example.com")

    def test_returns_false_and_never_raises_on_smtp_error(self) -> None:
        os.environ["SMTP_HOST"] = "smtp.example.com"
        with patch("smtplib.SMTP", side_effect=RuntimeError("connection refused")):
            result = email_sender.send_watchlist_alert_email("user@example.com", [self._alert()])
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
