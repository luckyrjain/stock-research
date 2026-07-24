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


if __name__ == "__main__":
    unittest.main()
